#!/usr/bin/env python3
"""
Re-ingest YouTube transcripts into Shannon using chapter structure.

Replaces raw ~3KB chunks with chapter-aware entries that include:
- Video title + summary as context header
- One entry per 5-minute section (not arbitrary byte boundaries)
- Tier 3 tag for EC scoring (raw transcript material)

Usage:
    python scripts/reingest-youtube.py [--dry-run] [--delete-old]
"""

import re
import os
import sys
import json
import glob
import hashlib
import argparse
import requests

SHANNON_API = "http://localhost:8765"
YOUTUBE_DIR = os.path.expanduser("~/development/library/youtube")


def parse_transcript(filepath: str) -> dict:
    """Parse a structured YouTube transcript markdown file."""
    with open(filepath) as f:
        content = f.read()

    lines = content.split('\n')
    result = {
        "title": "",
        "channel": "",
        "url": "",
        "published": "",
        "duration": "",
        "summary": "",
        "sections": [],
        "raw_transcript": "",  # for videos without section structure
    }

    # Extract metadata from header
    for line in lines[:10]:
        if line.startswith('# '):
            result["title"] = line[2:].strip()
        elif line.startswith('**Channel:**'):
            result["channel"] = line.split('**Channel:**')[1].strip()
        elif line.startswith('**URL:**'):
            result["url"] = line.split('**URL:**')[1].strip()
        elif line.startswith('**Published:**'):
            result["published"] = line.split('**Published:**')[1].strip()
        elif line.startswith('**Duration:**'):
            result["duration"] = line.split('**Duration:**')[1].strip()

    # Extract summary
    summary_match = re.search(r'## Summary\n\n(.+?)(?=\n## )', content, re.DOTALL)
    if summary_match:
        result["summary"] = summary_match.group(1).strip()

    # Extract sections (### XXmin+ pattern)
    section_pattern = re.compile(r'### (\d+min\+.*?)(?=\n)', re.MULTILINE)
    section_starts = [(m.start(), m.group(1)) for m in section_pattern.finditer(content)]

    if section_starts:
        for i, (start, name) in enumerate(section_starts):
            # Get content until next section or end
            if i + 1 < len(section_starts):
                end = section_starts[i + 1][0]
            else:
                end = len(content)

            section_content = content[start:end].strip()
            # Clean the section name
            clean_name = re.sub(r'<a name=".*?"></a>', '', name).strip()

            result["sections"].append({
                "name": clean_name,
                "content": section_content,
                "char_count": len(section_content),
            })
    else:
        # No sections — treat entire transcript as one chunk
        # Find ## Transcript or ## Full Transcript
        transcript_match = re.search(r'## (?:Full )?Transcript\n(.+)', content, re.DOTALL)
        if transcript_match:
            result["raw_transcript"] = transcript_match.group(1).strip()

    return result


def build_chapter_entry(video: dict, section: dict, section_idx: int, total_sections: int) -> str:
    """Build a Shannon entry for one chapter section."""
    header = f"[YouTube: {video['title']}] [Section {section_idx + 1}/{total_sections}: {section['name']}]\n"
    header += f"Channel: {video['channel']} | Duration: {video['duration']}\n"
    header += f"Source: {video['url']}\n"

    if video['summary']:
        # Include a condensed summary (first 200 chars) for context
        summary_preview = video['summary'][:200]
        if len(video['summary']) > 200:
            summary_preview += "..."
        header += f"Video Summary: {summary_preview}\n"

    header += f"\n---\n\n{section['content']}"
    return header


def build_unsectioned_entry(video: dict, chunk_idx: int, total_chunks: int, chunk_text: str) -> str:
    """Build entry for videos without chapter structure (chunked by ~4000 chars)."""
    header = f"[YouTube: {video['title']}] [Part {chunk_idx + 1}/{total_chunks}]\n"
    header += f"Channel: {video['channel']} | Duration: {video['duration']}\n"
    header += f"Source: {video['url']}\n"

    if video['summary']:
        summary_preview = video['summary'][:200]
        if len(video['summary']) > 200:
            summary_preview += "..."
        header += f"Video Summary: {summary_preview}\n"

    header += f"\n---\n\n{chunk_text}"
    return header


def chunk_text(text: str, max_chars: int = 4000) -> list:
    """Split text into chunks at paragraph or line boundaries."""
    # Try paragraph splits first
    paragraphs = text.split('\n\n')
    
    # If any paragraph is still huge, re-split those on single newlines
    expanded = []
    for para in paragraphs:
        if len(para) > max_chars:
            expanded.extend(para.split('\n'))
        else:
            expanded.append(para)
    paragraphs = expanded
    
    chunks = []
    current = []
    current_len = 0
    joiner = '\n'

    for para in paragraphs:
        if current_len + len(para) > max_chars and current:
            chunks.append(joiner.join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)

    if current:
        chunks.append(joiner.join(current))

    return chunks


def merge_small_sections(sections: list, min_chars: int = 1500) -> list:
    """Merge very small consecutive sections to avoid tiny entries."""
    if not sections:
        return sections

    merged = [sections[0].copy()]
    for section in sections[1:]:
        if merged[-1]["char_count"] < min_chars:
            # Merge with previous
            merged[-1]["content"] += "\n\n" + section["content"]
            merged[-1]["char_count"] += section["char_count"]
            merged[-1]["name"] += f" + {section['name']}"
        else:
            merged.append(section.copy())

    return merged


def save_to_shannon(body: str, video: dict, section_name: str, dry_run: bool = False) -> bool:
    """Save an entry to Shannon via API."""
    # Build tags
    channel_tag = video["channel"].lower().replace(" ", "-").replace("'", "")
    tags = [
        "youtube",
        "transcript",
        "chapter",  # distinguishes from old raw chunks
        channel_tag,
        "guy",
    ]

    # Add topic tags based on title keywords
    title_lower = video["title"].lower()
    topic_tags = {
        "cryptography": ["crypto", "proof", "zero-knowledge", "snarg", "pcp", "fiat-shamir"],
        "networking": ["network", "dns", "routing", "tcp", "udp", "http"],
        "fpga": ["fpga", "hardware", "accelerator", "pcie"],
        "ml": ["machine learning", "pytorch", "mlops", "deep learning"],
        "android": ["android", "kotlin"],
        "devops": ["aws", "cloudops", "terraform", "docker"],
        "programming": ["programming", "cs50"],
    }
    for tag, keywords in topic_tags.items():
        if any(kw in title_lower for kw in keywords):
            tags.append(tag)

    # Session ID based on video
    safe_title = re.sub(r'[^a-z0-9]+', '-', video["title"].lower())[:60]
    session_id = f"yt-chapter-{safe_title}"

    if dry_run:
        print(f"  [DRY RUN] Would save: {len(body)} chars, tags={tags}")
        return True

    try:
        resp = requests.post(
            f"{SHANNON_API}/memory",
            json={
                "body": body,
                "agent": "guy",
                "tags": tags,
                "session_id": session_id,
                "tier": 3,  # YouTube transcripts = tier 3
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


def delete_old_chunks(dry_run: bool = False):
    """
    Find and count old-style YouTube transcript chunks in Shannon.
    Note: Shannon is append-only, so we can't delete. But we can mark them
    as superseded if needed, or just let the EC tier scoring handle it.
    """
    print("\n=== Old chunk analysis ===")
    resp = requests.get(
        f"{SHANNON_API}/memory/search",
        params={"q": "YouTube transcript chunk Part", "limit": 10},
        timeout=10,
    )
    data = resp.json()
    old_style = [r for r in data.get("results", []) if "chapter" not in r.get("tags", [])]
    print(f"Old-style chunks found in sample: {len(old_style)}")
    print("(Shannon is append-only — old chunks remain but EC scoring deprioritizes them)")
    print("(New chapter entries will score higher due to better structure + same tier 3)")


def main():
    parser = argparse.ArgumentParser(description="Re-ingest YouTube transcripts with chapter structure")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually save to Shannon")
    parser.add_argument("--delete-old", action="store_true", help="Analyze old chunks (can't truly delete)")
    args = parser.parse_args()

    # Health check
    try:
        health = requests.get(f"{SHANNON_API}/health", timeout=5).json()
        print(f"Shannon: {health['entries']} entries, {health['embedding_coverage']}% embedded")
    except Exception as e:
        print(f"Shannon not reachable: {e}")
        sys.exit(1)

    # Find all transcript files
    files = glob.glob(f"{YOUTUBE_DIR}/**/*.md", recursive=True)
    files = [f for f in files if not f.endswith("README.md")]
    print(f"\nFound {len(files)} transcript files\n")

    total_entries = 0
    total_chars = 0

    for filepath in sorted(files):
        video = parse_transcript(filepath)

        if not video["title"]:
            print(f"⚠️  Skipping (no title): {filepath}")
            continue

        # Skip Rick Astley lol
        if "rick astley" in video["title"].lower():
            print(f"🎵 Skipping: {video['title']} (never gonna give this up, but not useful)")
            continue

        print(f"\n📄 {video['title']}")
        print(f"   Channel: {video['channel']}, Duration: {video['duration']}")

        if video["sections"]:
            # Merge small sections
            sections = merge_small_sections(video["sections"])
            print(f"   Sections: {len(video['sections'])} → {len(sections)} (after merging small ones)")

            for i, section in enumerate(sections):
                entry = build_chapter_entry(video, section, i, len(sections))

                # If section is still very large (>6000 chars), split it
                if len(entry) > 6000:
                    sub_chunks = chunk_text(section["content"], max_chars=4000)
                    for j, chunk in enumerate(sub_chunks):
                        sub_section = {
                            "name": f"{section['name']} (part {j+1}/{len(sub_chunks)})",
                            "content": chunk,
                            "char_count": len(chunk),
                        }
                        sub_entry = build_chapter_entry(video, sub_section, i, len(sections))
                        ok = save_to_shannon(sub_entry, video, sub_section["name"], dry_run=args.dry_run)
                        total_entries += 1
                        total_chars += len(sub_entry)
                        status = "✅" if ok else "❌"
                        print(f"   {status} Section {i+1}.{j+1}: {sub_section['name']} ({len(sub_entry)} chars)")
                else:
                    ok = save_to_shannon(entry, video, section["name"], dry_run=args.dry_run)
                    total_entries += 1
                    total_chars += len(entry)
                    status = "✅" if ok else "❌"
                    print(f"   {status} Section {i+1}: {section['name']} ({len(entry)} chars)")

        elif video["raw_transcript"]:
            # No sections — chunk by paragraph boundaries
            chunks = chunk_text(video["raw_transcript"], max_chars=4000)
            print(f"   No sections — chunking into {len(chunks)} parts")

            for i, chunk in enumerate(chunks):
                entry = build_unsectioned_entry(video, i, len(chunks), chunk)
                ok = save_to_shannon(entry, video, f"part-{i+1}", dry_run=args.dry_run)
                total_entries += 1
                total_chars += len(entry)
                status = "✅" if ok else "❌"
                print(f"   {status} Part {i+1}/{len(chunks)} ({len(entry)} chars)")

        else:
            print(f"   ⚠️  No sections or transcript found")

        # Also save the summary as its own tier-2 entry (curated knowledge)
        if video["summary"] and len(video["summary"]) > 100:
            summary_entry = f"[YouTube Summary: {video['title']}]\n"
            summary_entry += f"Channel: {video['channel']} | Duration: {video['duration']}\n"
            summary_entry += f"Source: {video['url']}\n\n"
            summary_entry += video["summary"]

            # Summaries are curated — save as tier 2
            channel_tag = video["channel"].lower().replace(" ", "-").replace("'", "")
            safe_title = re.sub(r'[^a-z0-9]+', '-', video["title"].lower())[:60]

            if not args.dry_run:
                try:
                    requests.post(
                        f"{SHANNON_API}/memory",
                        json={
                            "body": summary_entry,
                            "agent": "guy",
                            "tags": ["youtube", "summary", "knowledge", channel_tag, "guy"],
                            "session_id": f"yt-summary-{safe_title}",
                            "tier": 2,  # Summaries are curated = tier 2
                        },
                        timeout=10,
                    )
                    print(f"   📝 Summary saved as tier 2 ({len(summary_entry)} chars)")
                except Exception as e:
                    print(f"   ❌ Summary save failed: {e}")
            else:
                print(f"   [DRY RUN] Summary: {len(summary_entry)} chars (tier 2)")

    print(f"\n{'='*60}")
    print(f"Total entries: {total_entries}")
    print(f"Total chars: {total_chars:,}")
    print(f"Average entry size: {total_chars // max(total_entries, 1):,} chars")

    if args.delete_old:
        delete_old_chunks(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
