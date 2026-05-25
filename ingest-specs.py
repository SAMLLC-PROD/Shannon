#!/usr/bin/env python3
"""
Shannon Spec Ingestion — Chunk and ingest Lattice/Pigeon spec files into Shannon LTM.

Reads .md files from claude-drops and lattice-network/docs/specs/active,
chunks them into ~500-800 token segments (by markdown headers or paragraph breaks),
tags them by source and topic, and POSTs to Shannon (local + server).

Usage:
    python3 ingest-specs.py [--dry-run] [--local-only] [--server-only]
"""

import os
import re
import json
import sys
import hashlib
import time
import urllib.request
import urllib.error
from pathlib import Path

# --- Config ---
SHANNON_LOCAL = "http://localhost:8765"
SHANNON_SERVER = "http://192.168.0.71:8765"
AGENT = "guy"
CHUNK_TARGET = 600   # target tokens per chunk (~4 chars/token)
CHUNK_MAX = 900      # hard max
CHUNK_MIN = 80       # skip tiny fragments

SPEC_DIRS = [
    (Path.home() / "development/library/claude-drops", "claude-drop"),
    (Path.home() / "development/lattice-network/docs/specs/active", "milestone-spec"),
]

# File patterns to include
INCLUDE_PATTERNS = [
    re.compile(r"read-LATTICE_.*\.md$"),
    re.compile(r"read-PIGEON_.*\.md$"),
    re.compile(r"read-SHANNON_.*\.md$"),
    re.compile(r"read-SKILL_.*\.md$"),
    re.compile(r"^m\d+.*\.md$"),          # milestone specs
    re.compile(r"^ironbank.*\.md$"),
    re.compile(r"^LATTICE_.*\.md$"),
    re.compile(r"^lattice-.*\.md$"),
]

# Also grab the Pigeon V2 corpus
EXTRA_FILES = [
    (Path.home() / "development/pigeon/PIgeon V2", "pigeon-v2"),
]

# --- Argument parsing ---
DRY_RUN = "--dry-run" in sys.argv
LOCAL_ONLY = "--local-only" in sys.argv
SERVER_ONLY = "--server-only" in sys.argv


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English markdown)."""
    return len(text) // 4


def extract_tags(filename: str, content: str) -> list[str]:
    """Extract meaningful tags from filename and content."""
    tags = []
    
    # From filename
    name = filename.lower().replace("read-", "").replace(".md", "").replace("_", "-")
    
    # Categorize
    if "lattice" in name:
        tags.append("lattice")
    if "pigeon" in name:
        tags.append("pigeon")
    if "shannon" in name:
        tags.append("shannon")
    if "ironbank" in name or "vault" in name:
        tags.append("ironbank")
    if "coop" in name or "gateway" in name:
        tags.append("coop-gateway")
    if "token" in name or "payment" in name or "x402" in name:
        tags.append("tokenomics")
    if "video" in name:
        tags.append("video")
    if "social" in name or "charter" in name:
        tags.append("social")
    if "browser" in name:
        tags.append("browser")
    if "dns" in name:
        tags.append("dns")
    if "chain" in name or "cosmos" in name:
        tags.append("native-chain")
    if "attestation" in name or "provenance" in name:
        tags.append("attestation")
    if "sim" in name or "nft" in name or "identity" in name:
        tags.append("identity")
    if "rbac" in name or "ucg" in name or "safety" in name:
        tags.append("safety")
    if "compression" in name:
        tags.append("compression")
    if re.match(r"^m\d+", name):
        milestone = re.match(r"^(m\d+)", name).group(1)
        tags.append(milestone)
    
    tags.append("spec")
    return list(set(tags))


def chunk_markdown(content: str, filename: str) -> list[dict]:
    """Split markdown into semantically meaningful chunks by headers."""
    chunks = []
    
    # Split by ## headers first, then by ### if chunks are too big
    sections = re.split(r'\n(?=##\s)', content)
    
    current_chunk = ""
    current_header = filename
    
    for section in sections:
        # Extract header if present
        header_match = re.match(r'^(#{1,4})\s+(.+)', section.strip())
        if header_match:
            header_text = header_match.group(2).strip()
        else:
            header_text = None
        
        tokens = estimate_tokens(section)
        
        if tokens > CHUNK_MAX:
            # Section too big — split by paragraphs
            if current_chunk:
                chunks.append({"header": current_header, "body": current_chunk.strip()})
                current_chunk = ""
            
            paragraphs = re.split(r'\n\n+', section)
            para_chunk = ""
            for para in paragraphs:
                if estimate_tokens(para_chunk + "\n\n" + para) > CHUNK_TARGET and para_chunk:
                    chunks.append({"header": header_text or current_header, "body": para_chunk.strip()})
                    para_chunk = para
                else:
                    para_chunk = (para_chunk + "\n\n" + para).strip()
            if para_chunk.strip():
                current_chunk = para_chunk
                current_header = header_text or current_header
        
        elif estimate_tokens(current_chunk + "\n\n" + section) > CHUNK_TARGET and current_chunk:
            # Would exceed target — flush current
            chunks.append({"header": current_header, "body": current_chunk.strip()})
            current_chunk = section
            current_header = header_text or current_header
        
        else:
            # Accumulate
            current_chunk = (current_chunk + "\n\n" + section).strip()
            if header_text:
                current_header = header_text
    
    # Flush remainder
    if current_chunk.strip() and estimate_tokens(current_chunk) >= CHUNK_MIN:
        chunks.append({"header": current_header, "body": current_chunk.strip()})
    
    return chunks


def content_hash(body: str) -> str:
    """SHA-256 of content for dedup."""
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def post_to_shannon(url: str, entry: dict) -> bool:
    """POST a memory entry to Shannon."""
    try:
        data = json.dumps(entry).encode()
        req = urllib.request.Request(
            f"{url}/memory",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.status == 200 or resp.status == 201
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"  ✗ POST failed to {url}: {e}", file=sys.stderr)
        return False


def collect_files() -> list[tuple[Path, str]]:
    """Collect all spec files to ingest."""
    files = []
    
    for dir_path, source in SPEC_DIRS:
        if not dir_path.exists():
            print(f"⚠ Directory not found: {dir_path}", file=sys.stderr)
            continue
        
        for f in sorted(dir_path.iterdir()):
            if not f.suffix == ".md":
                continue
            if any(p.match(f.name) for p in INCLUDE_PATTERNS):
                files.append((f, source))
    
    # Extra directories (include all .md files)
    for dir_path, source in EXTRA_FILES:
        if not dir_path.exists():
            continue
        for f in sorted(dir_path.iterdir()):
            if f.suffix == ".md":
                files.append((f, source))
    
    return files


def main():
    files = collect_files()
    print(f"Found {len(files)} spec files to ingest")
    
    targets = []
    if not SERVER_ONLY:
        targets.append(("local", SHANNON_LOCAL))
    if not LOCAL_ONLY:
        targets.append(("server", SHANNON_SERVER))
    
    # Verify targets are up
    for name, url in targets:
        try:
            resp = urllib.request.urlopen(f"{url}/health", timeout=5)
            data = json.loads(resp.read())
            print(f"  ✓ {name} ({url}): {data['entries']} entries, {data.get('embedding_coverage', '?')}% embedded")
        except Exception as e:
            print(f"  ✗ {name} ({url}): DOWN — {e}", file=sys.stderr)
            if not DRY_RUN:
                targets = [(n, u) for n, u in targets if u != url]
    
    if not targets and not DRY_RUN:
        print("No Shannon targets available. Exiting.", file=sys.stderr)
        sys.exit(1)
    
    total_chunks = 0
    total_posted = 0
    total_skipped = 0
    seen_hashes = set()
    
    for filepath, source in files:
        content = filepath.read_text(errors="replace")
        filename = filepath.name
        
        if estimate_tokens(content) < CHUNK_MIN:
            print(f"  skip (too small): {filename}")
            continue
        
        chunks = chunk_markdown(content, filename)
        tags = extract_tags(filename, content)
        
        session_id = f"spec-ingest-{filename.replace('.md', '')}"
        
        print(f"\n📄 {filename} → {len(chunks)} chunks, tags: {tags}")
        
        for i, chunk in enumerate(chunks):
            chash = content_hash(chunk["body"])
            if chash in seen_hashes:
                total_skipped += 1
                continue
            seen_hashes.add(chash)
            
            # Build entry
            header_prefix = f"[{filename}] {chunk['header']}" if chunk["header"] != filename else f"[{filename}]"
            body = f"{header_prefix}\n\n{chunk['body']}"
            
            entry = {
                "body": body,
                "agent": AGENT,
                "tags": tags + [source, f"chunk-{i+1}-of-{len(chunks)}"],
                "session_id": session_id,
            }
            
            tokens = estimate_tokens(body)
            
            if DRY_RUN:
                print(f"  [{i+1}/{len(chunks)}] {tokens}tok — {chunk['header'][:60]}")
                total_chunks += 1
                continue
            
            # POST to all targets
            for name, url in targets:
                ok = post_to_shannon(url, entry)
                if ok:
                    total_posted += 1
                else:
                    print(f"  ✗ Failed: chunk {i+1} to {name}")
            
            total_chunks += 1
            
            # Rate limit — be gentle with embedding compute
            if total_chunks % 5 == 0:
                time.sleep(0.3)
    
    print(f"\n{'=' * 50}")
    print(f"Files processed: {len(files)}")
    print(f"Chunks created:  {total_chunks}")
    print(f"Duplicates skip: {total_skipped}")
    if not DRY_RUN:
        print(f"POSTs sent:      {total_posted} ({total_posted // max(len(targets),1)} per target)")
    else:
        print("(DRY RUN — nothing posted)")
    
    # Trigger embedding backfill
    if not DRY_RUN:
        print("\nTriggering embedding backfill...")
        for name, url in targets:
            try:
                req = urllib.request.Request(f"{url}/embeddings/backfill", method="POST",
                                            data=b'{}', headers={"Content-Type": "application/json"})
                resp = urllib.request.urlopen(req, timeout=10)
                print(f"  ✓ {name}: backfill triggered")
            except Exception as e:
                print(f"  ✗ {name}: backfill failed — {e}")


if __name__ == "__main__":
    main()
