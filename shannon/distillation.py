"""
shannon/distillation.py — Memory distillation (Issue #18).

Scans entries for repeated patterns, extracts common assertions,
and saves them as tier-1 'distilled-rule' entries.

API:
  GET  /rules?agent=X        — list rules
  DELETE /rules/{entry_id}   — soft-delete rule
  POST /distill?agent=X      — trigger manual scan
"""

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from .store import _connect, write, read_by_hash, init_store

# Tags to exclude from distillation (spurious / already processed)
_EXCLUDE_TAGS = frozenset({
    "no-causation",
    "spurious-correlation",
    "rule-deleted",
    "distilled-rule",
})

# Generic agent/system tags ignored when computing meaningful overlap
_GENERIC_TAGS = frozenset({
    "guy", "henry", "heartbeat", "default", "test",
    "youtube", "transcript", "arxiv",
})


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _jaccard_words(text1: str, text2: str) -> float:
    """Jaccard similarity on 4+ character words."""
    w1 = set(re.findall(r"\b[a-z]{4,}\b", text1.lower()))
    w2 = set(re.findall(r"\b[a-z]{4,}\b", text2.lower()))
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)


# ---------------------------------------------------------------------------
# Pattern scanning
# ---------------------------------------------------------------------------

def scan_for_patterns(agent_id: str, days: int = 30) -> list:
    """
    Group entries by tag overlap + text similarity.

    Returns candidate groups of 3+ similar entries.
    Excludes entries tagged 'no-causation' or 'spurious-correlation'.
    """
    init_store()
    conn = _connect()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = conn.execute(
        "SELECT content_hash, tags FROM entries "
        "WHERE created_at >= ? AND tenant_id IS NULL "
        "ORDER BY created_at DESC LIMIT 5000",
        (cutoff,),
    ).fetchall()
    conn.close()

    generic_plus_agent = _GENERIC_TAGS | {agent_id}

    entries = []
    for row in rows:
        tags = json.loads(row["tags"] or "[]")
        tags_lower = {t.lower() for t in tags}
        if agent_id not in tags:
            continue
        if tags_lower & _EXCLUDE_TAGS:
            continue
        entries.append({
            "content_hash": row["content_hash"],
            "tags_lower": tags_lower,
        })

    if len(entries) < 3:
        return []

    # --- Union-Find for tag-based clustering ---
    parent = {e["content_hash"]: e["content_hash"] for e in entries}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        parent[find(x)] = find(y)

    for i, e1 in enumerate(entries):
        m1 = e1["tags_lower"] - generic_plus_agent
        if not m1:
            continue
        for e2 in entries[i + 1:]:
            m2 = e2["tags_lower"] - generic_plus_agent
            if len(m1 & m2) >= 2:
                union(e1["content_hash"], e2["content_hash"])

    # Build tag clusters
    tag_clusters: dict = defaultdict(list)
    for entry in entries:
        tag_clusters[find(entry["content_hash"])].append(entry["content_hash"])

    result_groups = []

    for root, hashes in tag_clusters.items():
        if len(hashes) < 3:
            continue

        # Load bodies
        bodies: dict = {}
        for ch in hashes:
            body = read_by_hash(ch) or ""
            bodies[ch] = body

        # Sub-cluster by Jaccard > 0.4
        sub_parent = {ch: ch for ch in hashes}

        def sub_find(x: str) -> str:
            while sub_parent[x] != x:
                sub_parent[x] = sub_parent[sub_parent[x]]
                x = sub_parent[x]
            return x

        def sub_union(x: str, y: str) -> None:
            sub_parent[sub_find(x)] = sub_find(y)

        hash_list = list(hashes)
        for i, h1 in enumerate(hash_list):
            for h2 in hash_list[i + 1:]:
                if _jaccard_words(bodies[h1], bodies[h2]) >= 0.4:
                    sub_union(h1, h2)

        sub_clusters: dict = defaultdict(list)
        for ch in hash_list:
            sub_clusters[sub_find(ch)].append(ch)

        for sub_root, sub_hashes in sub_clusters.items():
            if len(sub_hashes) >= 3:
                result_groups.append({
                    "entry_ids": sub_hashes,
                    "bodies": [bodies[h] for h in sub_hashes],
                    "count": len(sub_hashes),
                })

    return result_groups


# ---------------------------------------------------------------------------
# Rule distillation (extractive)
# ---------------------------------------------------------------------------

def distill_rule(entries_text: list) -> str:
    """
    Extractive summary: find the sentence most shared across entries.

    Uses word-overlap scoring to find the common assertion.
    """
    if not entries_text:
        return ""
    if len(entries_text) == 1:
        sentences = re.split(r"[.!?]\s+", entries_text[0].strip())
        return next((s.strip() for s in sentences if len(s.strip()) > 20), entries_text[0][:200])

    # Split all entries into sentences
    all_sentences = []
    for text in entries_text:
        for s in re.split(r"[.!?]\s+", text.strip()):
            s = s.strip()
            if len(s) > 20:
                all_sentences.append(s)

    if not all_sentences:
        return entries_text[0][:200]

    best_sentence = ""
    best_score = -1

    for i, sent in enumerate(all_sentences):
        words_i = set(re.findall(r"\b[a-z]{4,}\b", sent.lower()))
        if len(words_i) < 3:
            continue
        score = sum(
            1 for j, other in enumerate(all_sentences)
            if j != i and len(words_i) > 0
            and len(words_i & set(re.findall(r"\b[a-z]{4,}\b", other.lower()))) / len(words_i) > 0.4
        )
        if score > best_score:
            best_score = score
            best_sentence = sent

    return best_sentence or all_sentences[0][:200]


# ---------------------------------------------------------------------------
# Save / list / delete rules
# ---------------------------------------------------------------------------

def save_rule(agent_id: str, rule_text: str, source_entry_ids: list) -> str:
    """
    Save a distilled rule as a tier-1 entry tagged 'distilled-rule'.
    Returns the content_hash of the saved rule.
    """
    body = (
        f"[DISTILLED RULE] {rule_text}\n\n"
        f"Agent: {agent_id}\n"
        f"Source entries ({len(source_entry_ids)}): "
        f"{', '.join(source_entry_ids[:10])}"
    )
    session_id = f"distilled-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

    write(
        body,
        session_id=session_id,
        tags=["distilled-rule", agent_id],
        tier=1,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def list_rules(agent_id: Optional[str] = None) -> list:
    """List all non-deleted distilled rules."""
    init_store()
    conn = _connect()

    if agent_id:
        rows = conn.execute(
            "SELECT content_hash, created_at, session_id, tags FROM entries "
            "WHERE tags LIKE '%distilled-rule%' AND tags LIKE ? "
            "AND (tags NOT LIKE '%rule-deleted%') "
            "ORDER BY created_at DESC",
            (f'%"{agent_id}"%',),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT content_hash, created_at, session_id, tags FROM entries "
            "WHERE tags LIKE '%distilled-rule%' "
            "AND (tags NOT LIKE '%rule-deleted%') "
            "ORDER BY created_at DESC",
        ).fetchall()

    conn.close()

    results = []
    for row in rows:
        body = read_by_hash(row["content_hash"]) or ""
        results.append({
            "id": row["content_hash"],
            "created_at": row["created_at"],
            "session_id": row["session_id"],
            "tags": json.loads(row["tags"] or "[]"),
            "body": body,
        })
    return results


def delete_rule(entry_id: str) -> bool:
    """Soft-delete a rule by adding 'rule-deleted' to its tags."""
    init_store()
    conn = _connect()

    row = conn.execute(
        "SELECT tags FROM entries WHERE content_hash = ?", (entry_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    tags = json.loads(row["tags"] or "[]")
    if "distilled-rule" not in tags:
        conn.close()
        return False

    if "rule-deleted" not in tags:
        tags.append("rule-deleted")
        conn.execute(
            "UPDATE entries SET tags = ? WHERE content_hash = ?",
            (json.dumps(tags), entry_id),
        )
        conn.commit()

    conn.close()
    return True
