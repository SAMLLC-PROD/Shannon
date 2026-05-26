"""Shannon CaaS — tenant context export as structured markdown.

GET /tenant/export?format=markdown&topic=TOPIC&limit_tokens=8000

Output is designed to paste directly into ChatGPT/Claude/Gemini as context.
"""

import json
import re
from datetime import datetime, timezone
from typing import Optional

from .store import _connect, read_by_hash


def _ts_to_seconds(ts: str) -> int:
    """Convert MM:SS or HH:MM:SS to integer seconds."""
    parts = ts.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return 0


def _extract_yt_info(body: str, session_id: str) -> Optional[dict]:
    """Extract YouTube video ID and timestamp from entry body text."""
    url_match = re.search(r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})", body)
    if url_match:
        video_id = url_match.group(1)
        ts_match = re.search(r"\[(\d+:\d{2}(?::\d{2})?)\]", body)
        ts_str = ts_match.group(1) if ts_match else "0:00"
        return {
            "video_id": video_id,
            "timestamp_str": ts_str,
            "timestamp_sec": _ts_to_seconds(ts_str),
        }
    # Try youtu.be short links
    short_match = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", body)
    if short_match:
        video_id = short_match.group(1)
        ts_match = re.search(r"\[(\d+:\d{2}(?::\d{2})?)\]", body)
        ts_str = ts_match.group(1) if ts_match else "0:00"
        return {
            "video_id": video_id,
            "timestamp_str": ts_str,
            "timestamp_sec": _ts_to_seconds(ts_str),
        }
    return None


_SKIP_TAGS = frozenset({
    "guy", "henry", "heartbeat", "default", "test",
    "youtube", "transcript", "raw-note",
})

_TIER1_TAGS = frozenset({
    "decision", "architecture", "milestone", "skill",
    "skill-building", "skill-compilation", "lesson-learned",
})


def _classify_entry(tags: list[str], session_id: str) -> str:
    tag_set = {t.lower() for t in tags}
    if "youtube" in tag_set or (session_id or "").startswith("yt-"):
        return "youtube"
    if tag_set & {"decision", "architecture", "milestone"}:
        return "decision"
    if tag_set & {"skill", "skill-building"}:
        return "skill"
    if (session_id or "").startswith("arxiv-"):
        return "paper"
    return "note"


def _cluster_entries(entries: list[dict]) -> dict[str, list[dict]]:
    """Group entries by dominant meaningful tag."""
    clusters: dict[str, list[dict]] = {}
    for entry in entries:
        tags = [t for t in entry.get("tags", []) if t.lower() not in _SKIP_TAGS]
        topic = tags[0] if tags else "general"
        clusters.setdefault(topic, []).append(entry)
    return clusters


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def export_tenant_memory(
    tenant_id: str,
    topic: Optional[str] = None,
    limit_tokens: int = 8000,
    format: str = "markdown",
) -> str:
    """
    Build a structured markdown export of the tenant's memory.
    Respects token budget. Prioritises tier-1 (decisions/skills) entries.
    """
    from .retrieval import retrieve
    from .tenants import get_tenant_stats

    stats = get_tenant_stats(tenant_id)
    result = retrieve(
        tenant_id=tenant_id,
        topic=topic,
        limit_tokens=limit_tokens,
    )
    entries = result.get("entries", [])

    now = datetime.now(timezone.utc)
    topics_covered = sorted({
        t
        for e in entries
        for t in e.get("tags", [])
        if t.lower() not in _SKIP_TAGS
    })[:20]

    lines: list[str] = [
        "# Shannon Memory Export",
        "",
        "## Metadata",
        f"- **Name:** {stats.get('display_name', 'Unknown')}",
        f"- **Export Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"- **Total Entries:** {stats.get('entry_count', 0)} stored, {len(entries)} in this export",
        f"- **Topics:** {', '.join(topics_covered) if topics_covered else 'mixed'}",
    ]
    if topic:
        lines += ["", f"*Filtered by topic: `{topic}`*"]
    lines += ["", "---", ""]

    clusters = _cluster_entries(entries)
    used_tokens = _tokens("\n".join(lines))

    for cluster_topic, cluster_entries in sorted(
        clusters.items(),
        key=lambda kv: (kv[0] not in _TIER1_TAGS, kv[0]),
    ):
        section_header = f"## {cluster_topic.replace('-', ' ').title()}"
        lines.append(section_header)
        lines.append("")

        for entry in cluster_entries:
            body = entry.get("body", "")
            session_id = entry.get("session_id") or ""
            entry_id = entry.get("id", "")
            created = (entry.get("created_at") or "")[:10]
            tags = entry.get("tags", [])
            etype = _classify_entry(tags, session_id)

            item_lines: list[str] = []

            if etype == "youtube":
                yt = _extract_yt_info(body, session_id)
                if yt:
                    title = (
                        session_id
                        .removeprefix("yt-")
                        .rsplit("-", 1)[0]  # strip trailing chunk number
                        .replace("-", " ")
                        .title()
                    )
                    url = f"https://www.youtube.com/watch?v={yt['video_id']}&t={yt['timestamp_sec']}"
                    summary = body.strip().split("\n")[0][:200]
                    item_lines = [
                        f"- [{title}]({url}) @ {yt['timestamp_str']} — {summary}",
                    ]
                else:
                    item_lines = [f"- {body.strip()[:200]}"]

            elif etype == "decision":
                item_lines = [
                    f"### Decision ({created})",
                    "",
                    body.strip()[:800],
                    "",
                ]

            elif etype == "skill":
                item_lines = [f"**Skill ({created}):** {body.strip()[:400]}"]

            elif etype == "paper":
                item_lines = [f"- [{session_id}](https://arxiv.org/abs/{session_id.removeprefix('arxiv-')}) — {body.strip()[:300]}"]

            else:
                source_url = f"https://shannon.latticeproxy.io/source/{entry_id}"
                item_lines = [f"- [Source]({source_url}) — {body.strip()[:300]}"]

            item_lines.append("")
            chunk_cost = _tokens("\n".join(item_lines))
            if used_tokens + chunk_cost > limit_tokens:
                break
            lines.extend(item_lines)
            used_tokens += chunk_cost

        lines.append("")

    if result.get("truncated") or used_tokens >= limit_tokens:
        lines.append(
            f"*Export truncated at ~{limit_tokens} tokens. "
            "Use `?topic=KEYWORD` for a focused export.*"
        )

    return "\n".join(lines)
