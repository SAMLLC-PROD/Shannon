"""
shannon/retrieval.py — Token-budgeted retrieval with multi-pass scoring.

Three-pass "electoral college" retrieval:
  Pass 1: Semantic search (embedding cosine similarity + trust + recency)
  Pass 2: Keyword search (key term presence in body text)
  Pass 3: Graph traversal (session/tag/time neighbors of top-5 results)

Results are merged, deduplicated by content_hash, and re-ranked by
  max(pass1_score, pass2_score) + 0.1 * graph_bonus

Entry scoring (trust-aware formula from Issue #16):
  score = tier_weight * (0.5 * semantic + 0.25 * recency + 0.25 * trust)

Trust weights:
  verified / causal-knowledge / distilled-rule → 1.0
  spurious-correlation / no-causation          → 0.1
  default                                       → 0.5
"""

import json
import math
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from .store import _connect, init_store, read_by_hash, get_superseded_hashes
from .embeddings import semantic_search, compute_embedding, get_embedding, _cosine_similarity

log = logging.getLogger(__name__)

# Default scoring weights (legacy — used only when multi_pass=False)
RELEVANCE_WEIGHT = 0.75
RECENCY_WEIGHT = 0.25

# Trust-aware weights (Issue #16)
TRUST_SEM_WEIGHT = 0.50
TRUST_REC_WEIGHT = 0.25
TRUST_TRUST_WEIGHT = 0.25

# Recency decay: 7-day half-life for knowledge articles
RECENCY_HALF_LIFE_HOURS = 168.0

# Graph traversal bonus
GRAPH_BONUS = 0.10

# Tag overlap bonus
TAG_MATCH_BONUS = 0.10

# Update detection keywords — entries with these get a recency boost
_UPDATE_KEYWORDS = {
    "updated", "revised", "corrected", "new value", "changed to",
    "actually", "turns out", "retested", "re-tested", "confirmed",
    "amendment", "revision", "supersedes", "replaces",
}

# Trust tag sets (Issue #16)
TRUST_HIGH_TAGS = frozenset({
    "verified", "causal-knowledge", "founder", "distilled-rule",
})
TRUST_LOW_TAGS = frozenset({
    "spurious-correlation", "no-causation",
})

# Stopwords for keyword extraction
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "what",
    "when", "where", "who", "why", "how", "which", "that", "this",
    "these", "those", "and", "or", "but", "if", "in", "on", "at",
    "to", "for", "of", "with", "by", "from", "up", "about", "into",
    "through", "during", "before", "after", "above", "below", "not",
    "also", "then", "than", "its", "your", "our", "their", "his", "her",
    "they", "them", "we", "you", "its", "been", "being", "just", "get",
    "got", "use", "used", "using", "any", "all", "more", "most", "out",
    "over", "such", "only", "other", "same", "each", "both", "between",
})

# Conflict detection
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_CHUNK_SUFFIX_RE = re.compile(r"(-f)?-\d+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _recency_score(created_at: str, now: datetime) -> float:
    try:
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.0
    age_hours = max(0, (now - dt).total_seconds() / 3600)
    return math.exp(-0.693 * age_hours / RECENCY_HALF_LIFE_HOURS)


def _parse_dt(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _trust_weight(tags: list) -> float:
    """Return trust weight for an entry based on its tags (Issue #16)."""
    tag_set = {t.lower() for t in tags}
    if tag_set & TRUST_HIGH_TAGS:
        return 1.0
    if tag_set & TRUST_LOW_TAGS:
        return 0.1
    return 0.5


def _extract_keywords(query: str) -> list:
    """Extract meaningful keywords from a query string (for pass 2)."""
    words = re.findall(r"\b[a-z]{3,}\b", query.lower())
    return [w for w in words if w not in _STOPWORDS]


def _keyword_score(keywords: list, body: str) -> float:
    """Score an entry body by keyword presence (0–1)."""
    if not keywords or not body:
        return 0.0
    body_lower = body.lower()
    hits = sum(1 for kw in keywords if kw in body_lower)
    return hits / len(keywords)


def _graph_expand(top5_items: list, all_scored: list) -> set:
    """
    Pass 3: find entries related to top-5 results by:
      - same session_id
      - 2+ overlapping tags
      - created within 5 minutes
    Returns set of related content_hashes.
    """
    if not top5_items:
        return set()

    related: set = set()
    top5_hashes = {item["content_hash"] for item in top5_items}

    for item in top5_items:
        session_id = item.get("session_id")
        tags = set(item.get("tags", []))
        created_at = _parse_dt(item.get("created_at", ""))
        window_start = created_at - timedelta(minutes=5)
        window_end = created_at + timedelta(minutes=5)

        for other in all_scored:
            ch = other["content_hash"]
            if ch in top5_hashes or ch in related:
                continue

            other_session = other.get("session_id")
            other_tags = set(other.get("tags", []))
            other_dt = _parse_dt(other.get("created_at", ""))

            if session_id and other_session == session_id:
                related.add(ch)
            elif len(tags & other_tags) >= 2:
                related.add(ch)
            elif window_start <= other_dt <= window_end:
                related.add(ch)

    return related


def _detect_conflicts(entries: list) -> list:
    """
    Post-retrieval conflict detection (Issue #17).

    Heuristic: same tags + different numeric values + similar text = conflict.

    Returns list of conflict group dicts:
      [{"conflict_group_id": str, "entry_ids": [str...], "shared_tags": [...], "reason": str}]
    """
    conflict_groups: list = []
    group_map: dict = {}  # entry_id -> conflict_group_id
    processed: set = set()

    for i, e1 in enumerate(entries):
        for e2 in entries[i + 1:]:
            pair_key = (e1["id"], e2["id"])
            if pair_key in processed:
                continue
            processed.add(pair_key)

            tags1 = {t.lower() for t in e1.get("tags", [])}
            tags2 = {t.lower() for t in e2.get("tags", [])}
            _generic = {"guy", "henry", "heartbeat", "default", "test",
                        "youtube", "transcript", "arxiv"}
            meaningful = (tags1 & tags2) - _generic
            if not meaningful:
                continue

            body1 = e1.get("body", "")
            body2 = e2.get("body", "")
            nums1 = set(_NUMBER_RE.findall(body1))
            nums2 = set(_NUMBER_RE.findall(body2))

            if not nums1 or not nums2:
                continue
            diff_nums = nums1.symmetric_difference(nums2)
            if not diff_nums:
                continue  # identical numbers → no conflict

            # Similar text check (Jaccard on 4+ char words)
            words1 = set(re.findall(r"\b[a-z]{4,}\b", body1.lower()))
            words2 = set(re.findall(r"\b[a-z]{4,}\b", body2.lower()))
            union = words1 | words2
            if not union:
                continue
            jaccard = len(words1 & words2) / len(union)
            if jaccard < 0.15:
                continue

            id1, id2 = e1["id"], e2["id"]

            # Group management
            if id1 in group_map and id2 in group_map:
                g1, g2 = group_map[id1], group_map[id2]
                if g1 != g2:
                    # Merge g2 into g1
                    for cg in conflict_groups:
                        if cg["conflict_group_id"] == g2:
                            for eid in cg["entry_ids"]:
                                group_map[eid] = g1
                            for cg2 in conflict_groups:
                                if cg2["conflict_group_id"] == g1:
                                    for eid in cg["entry_ids"]:
                                        if eid not in cg2["entry_ids"]:
                                            cg2["entry_ids"].append(eid)
                            conflict_groups.remove(cg)
                            break
            elif id1 in group_map:
                gid = group_map[id1]
                group_map[id2] = gid
                for cg in conflict_groups:
                    if cg["conflict_group_id"] == gid and id2 not in cg["entry_ids"]:
                        cg["entry_ids"].append(id2)
            elif id2 in group_map:
                gid = group_map[id2]
                group_map[id1] = gid
                for cg in conflict_groups:
                    if cg["conflict_group_id"] == gid and id1 not in cg["entry_ids"]:
                        cg["entry_ids"].append(id1)
            else:
                gid = str(uuid.uuid4())[:8]
                group_map[id1] = gid
                group_map[id2] = gid
                conflict_groups.append({
                    "conflict_group_id": gid,
                    "entry_ids": [id1, id2],
                    "shared_tags": list(meaningful)[:5],
                    "reason": f"Numeric discrepancy: {list(diff_nums)[:3]}",
                })

    return conflict_groups


def _persist_conflict_groups(conflicts: list) -> None:
    """Write conflict_group_ids to entries table (only if not already set)."""
    if not conflicts:
        return
    conn = _connect()
    for cg in conflicts:
        gid = cg["conflict_group_id"]
        for entry_id in cg["entry_ids"]:
            conn.execute(
                "UPDATE entries SET conflict_group_id = ? "
                "WHERE content_hash = ? AND conflict_group_id IS NULL",
                (gid, entry_id),
            )
    conn.commit()
    conn.close()


def _extract_source(item: dict) -> str:
    """Extract source identifier for electoral college grouping."""
    tags = item.get("tags", [])
    session_id = item.get("session_id") or ""

    if "youtube" in tags and session_id.startswith("yt-"):
        return _CHUNK_SUFFIX_RE.sub("", session_id)
    if session_id.startswith("arxiv-"):
        return session_id
    if session_id:
        return session_id
    return item.get("content_hash", "unknown")


# ---------------------------------------------------------------------------
# Main retrieval function
# ---------------------------------------------------------------------------

def retrieve(
    agent_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    topic: Optional[str] = None,
    limit_tokens: int = 4000,
    recency: str = "all",
    relevance_weight: float = RELEVANCE_WEIGHT,
    recency_weight: float = RECENCY_WEIGHT,
    multi_pass: bool = True,
) -> dict:
    """
    Token-budgeted retrieval with multi-pass scoring.

    Args:
        agent_id: filter to entries tagged with this agent (internal use)
        tenant_id: filter to entries owned by this tenant (CaaS use)
        topic: semantic query for relevance scoring (if None, recency-only)
        limit_tokens: maximum tokens in response
        recency: time window filter (hot/warm/cold/all)
        relevance_weight: legacy compat (ignored when trust scoring active)
        recency_weight: legacy compat (ignored when trust scoring active)
        multi_pass: enable keyword + graph passes (default True)

    Returns:
        dict with entries, total_tokens, truncated, synthesis metadata, conflicts
    """
    init_store()
    now = datetime.now(timezone.utc)

    WINDOWS = {
        "hot": (0, 48),
        "warm": (48, 7 * 24),
        "cold": (7 * 24, 30 * 24),
        "all": None,
    }
    window = WINDOWS.get(recency)

    conn = _connect()

    if tenant_id is not None:
        tenant_clause = "AND tenant_id = ?"
        tenant_params: tuple = (tenant_id,)
    elif agent_id is not None:
        tenant_clause = "AND tenant_id IS NULL"
        tenant_params = ()
    else:
        tenant_clause = ""
        tenant_params = ()

    _sel = (
        "SELECT content_hash, created_at, session_id, tags, tier, "
        "COALESCE(superseded_by, '') as superseded_by FROM entries "
    )

    if window is None:
        rows = conn.execute(
            _sel + f"WHERE 1=1 {tenant_clause} ORDER BY created_at DESC LIMIT 2000",
            tenant_params,
        ).fetchall()
    else:
        min_h, max_h = window
        newer_than = (now - timedelta(hours=max_h)).isoformat()
        if min_h > 0:
            older_than = (now - timedelta(hours=min_h)).isoformat()
            rows = conn.execute(
                _sel + f"WHERE created_at < ? AND created_at >= ? {tenant_clause} "
                "ORDER BY created_at DESC LIMIT 2000",
                (older_than, newer_than, *tenant_params),
            ).fetchall()
        else:
            rows = conn.execute(
                _sel + f"WHERE created_at >= ? {tenant_clause} "
                "ORDER BY created_at DESC LIMIT 2000",
                (newer_than, *tenant_params),
            ).fetchall()
    conn.close()

    # Filter entries superseded via the existing supersedes mechanism
    superseded = get_superseded_hashes()
    if superseded:
        rows = [r for r in rows if r["content_hash"] not in superseded]

    # Agent filter (internal only)
    if agent_id and tenant_id is None:
        agent_rows = [
            r for r in rows
            if agent_id in json.loads(r["tags"] or "[]")
        ]
        if agent_rows:
            rows = agent_rows

    # --- Scoring loop ---
    scored = []
    query_vec = None
    keywords: list = []

    if topic:
        query_vec = compute_embedding(topic)
        keywords = _extract_keywords(topic)
        topic_words = set(topic.lower().split())
    else:
        topic_words = set()

    TIER_WEIGHTS = {1: 1.5, 2: 1.0, 3: 0.5}

    for row in rows:
        ch = row["content_hash"]
        rec_score = _recency_score(row["created_at"], now)
        tier = row["tier"] if row["tier"] is not None else 2
        tier_weight = TIER_WEIGHTS.get(tier, 1.0)
        tags = json.loads(row["tags"] or "[]")
        superseded_by = row["superseded_by"]  # non-empty → conflict-resolved loser

        body = read_by_hash(ch) or ""
        body_lower = body.lower()

        # Update detection
        update_boost = 0.15 if any(kw in body_lower for kw in _UPDATE_KEYWORDS) else 0.0
        boosted_recency = min(1.0, rec_score + update_boost)

        # Tag match bonus
        tag_bonus = 0.0
        if topic and tags:
            tag_set_lower = {t.lower() for t in tags}
            overlap = tag_set_lower & topic_words
            if overlap:
                tag_bonus = TAG_MATCH_BONUS * min(len(overlap), 3)

        # Trust weight (Issue #16)
        trust = _trust_weight(tags)

        if query_vec:
            entry_vec = get_embedding(ch)
            sem_score = _cosine_similarity(query_vec, entry_vec) if entry_vec else 0.0

            # Trust-aware scoring formula (Issue #16):
            # 0.5 * semantic + 0.25 * recency + 0.25 * trust
            combined = min(1.0, tier_weight * (
                (TRUST_SEM_WEIGHT * sem_score) +
                (TRUST_REC_WEIGHT * boosted_recency) +
                (TRUST_TRUST_WEIGHT * trust) +
                tag_bonus
            ))
        else:
            sem_score = None
            combined = min(1.0, tier_weight * (boosted_recency + tag_bonus))

        # Keyword score (pass 2) — computed now, applied later in multi-pass
        kw_score = _keyword_score(keywords, body) if keywords else 0.0

        # Penalty for conflict-resolved losers (0.3x)
        if superseded_by:
            combined *= 0.3
            kw_score *= 0.3

        # Additional penalty for very low trust entries (spurious contamination fix)
        if trust <= 0.2:
            combined *= 0.3
            kw_score *= 0.3

        scored.append({
            "content_hash": ch,
            "created_at": row["created_at"],
            "session_id": row["session_id"],
            "tags": tags,
            "tier": tier,
            "score": combined,
            "recency_score": rec_score,
            "relevance_score": sem_score,
            "keyword_score": kw_score,
            "trust_weight": trust,
            "graph_bonus": 0,
        })

    # --- Multi-pass re-ranking (Issue #19) ---
    passes_used = []

    if topic and query_vec:
        passes_used.append("semantic")

    if multi_pass and topic and keywords:
        passes_used.append("keyword")

        # Sort for graph expansion (best semantic first)
        scored.sort(key=lambda x: x["score"], reverse=True)
        top5 = scored[:5]

        # Pass 3: graph traversal
        graph_hashes = _graph_expand(top5, scored)
        if graph_hashes:
            passes_used.append("graph")

        for item in scored:
            ch = item["content_hash"]
            gb = 1 if ch in graph_hashes else 0
            item["graph_bonus"] = gb
            # Re-rank: max(semantic_score, keyword_score) + 0.1 * graph_bonus
            item["score"] = min(1.05, max(item["score"], item["keyword_score"]) + GRAPH_BONUS * gb)

    # Prioritise distilled rules (Issue #18) — always float to top
    rules = [item for item in scored if "distilled-rule" in item["tags"] and "rule-deleted" not in item["tags"]]
    non_rules = [item for item in scored if "distilled-rule" not in item["tags"] or "rule-deleted" in item["tags"]]

    # Sort each group
    rules.sort(key=lambda x: x["score"], reverse=True)
    non_rules.sort(key=lambda x: x["score"], reverse=True)
    scored = rules + non_rules

    # --- Electoral College: two-stage source-aware selection ---
    source_groups: dict = defaultdict(list)
    for item in scored:
        src = _extract_source(item)
        source_groups[src].append(item)

    for src in source_groups:
        source_groups[src].sort(key=lambda x: x["score"], reverse=True)

    ranked_sources = sorted(
        source_groups.keys(),
        key=lambda src: source_groups[src][0]["score"],
        reverse=True,
    )

    log.debug(
        "Electoral college: %d entries -> %d sources. Passes: %s. Top 5: %s",
        len(scored),
        len(ranked_sources),
        passes_used,
        [(s, round(source_groups[s][0]["score"], 3)) for s in ranked_sources[:5]],
    )

    total_tokens = 0
    kept = []
    truncated = False
    source_cursor = {src: 0 for src in ranked_sources}
    max_rounds = max((len(v) for v in source_groups.values()), default=0)

    for round_num in range(max_rounds):
        for src in ranked_sources:
            items = source_groups[src]
            idx = source_cursor[src]
            if idx >= len(items):
                continue

            item = items[idx]
            source_cursor[src] += 1

            body = read_by_hash(item["content_hash"])
            if not body:
                continue
            t = _tokens(body)
            if total_tokens + t > limit_tokens:
                truncated = True
                continue

            kept.append({
                "id": item["content_hash"],
                "session_id": item["session_id"],
                "tags": item["tags"],
                "body": body,
                "created_at": item["created_at"],
                "score": round(item["score"], 4),
                "recency_score": round(item["recency_score"], 4),
                "relevance_score": round(item["relevance_score"], 4) if item["relevance_score"] is not None else None,
                "trust_weight": round(item["trust_weight"], 4),
                "graph_bonus": item["graph_bonus"],
                "source": _extract_source(item),
            })
            total_tokens += t

        if truncated and total_tokens >= limit_tokens * 0.95:
            break

    kept.sort(key=lambda x: x["score"], reverse=True)

    # --- Post-retrieval conflict detection (Issue #17) ---
    conflicts: list = []
    if multi_pass and kept:
        conflicts = _detect_conflicts(kept)
        if conflicts:
            _persist_conflict_groups(conflicts)

    # Synthesis metadata (Issue #19)
    synthesis = {
        "entry_count": len(kept),
        "session_count": len({e["session_id"] for e in kept if e.get("session_id")}),
        "passes_used": passes_used or ["recency"],
    }

    return {
        "entries": kept,
        "total_tokens": total_tokens,
        "truncated": truncated,
        "scored_count": len(scored),
        "returned_count": len(kept),
        "source_count": len(ranked_sources),
        "synthesis": synthesis,
        "conflicts": conflicts,
    }
