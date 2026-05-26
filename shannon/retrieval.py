"""
shannon/retrieval.py — Token-budgeted retrieval with combined semantic + recency scoring.

Two-stage "electoral college" retrieval:
  Stage 1: Group entries by source, pick the best-scoring entry per source.
  Stage 2: Rank sources by their best entry's score, fill token budget
           by round-robin across top sources.

This prevents high-volume sources (e.g., a 200-chunk course) from
dominating results over focused, high-density sources (e.g., a 10-chunk
deep-dive series).

Entry scoring:
  score = (relevance_weight × cosine_sim) + (recency_weight × recency_decay)
"""

import json
import math
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from .store import _connect, init_store, read_by_hash, get_superseded_hashes
from .embeddings import semantic_search, compute_embedding, get_embedding, _cosine_similarity

log = logging.getLogger(__name__)

# Default scoring weights
# Knowledge base mode: relevance dominates, recency is tiebreaker
RELEVANCE_WEIGHT = 0.85
RECENCY_WEIGHT = 0.15

# Recency decay: entries lose recency score over time
# Extended to 7 days — knowledge articles shouldn't decay quickly
RECENCY_HALF_LIFE_HOURS = 168.0


def _tokens(text: str) -> int:
    """Rough token estimate (4 chars per token)."""
    return max(1, len(text) // 4)


def _recency_score(created_at: str, now: datetime) -> float:
    """
    Compute recency score with exponential decay.
    Returns 1.0 for just-created, 0.5 at half-life, approaches 0 for old entries.
    """
    try:
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.0
    
    age_hours = max(0, (now - dt).total_seconds() / 3600)
    return math.exp(-0.693 * age_hours / RECENCY_HALF_LIFE_HOURS)


# Regex to strip chunk index suffixes from session IDs.
# Handles both patterns:
#   "yt-dns-101-miniseries-f-42" -> "yt-dns-101-miniseries"
#   "yt-computer-networking-fundamentals-course-164" -> "yt-computer-networking-fundamentals-course"
# Strategy: strip trailing -N or -f-N where N is purely numeric.
_CHUNK_SUFFIX_RE = re.compile(r"(-f)?-\d+$")


def _extract_source(item: dict) -> str:
    """Extract a source identifier from an entry for electoral college grouping.
    
    Sources are the unit of proportional representation. A YouTube video,
    an arXiv paper, a session conversation — each is one "state" that gets
    one vote regardless of how many chunks it has.
    
    Heuristics (in priority order):
    1. YouTube chunks: strip chunk suffix from session_id
    2. arXiv entries: use session_id directly (arxiv-<id>)
    3. Session entries: use session_id (date-based grouping)
    4. Fallback: use content_hash (each entry is its own source)
    """
    tags = item.get("tags", [])
    session_id = item.get("session_id") or ""
    
    # YouTube transcripts: group by video (strip chunk number)
    if "youtube" in tags and session_id.startswith("yt-"):
        return _CHUNK_SUFFIX_RE.sub("", session_id)
    
    # arXiv papers: already one session per paper
    if session_id.startswith("arxiv-"):
        return session_id
    
    # Regular session entries: group by session
    if session_id:
        return session_id
    
    # No session — each entry is its own source
    return item.get("content_hash", "unknown")


def _parse_dt(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def retrieve(
    agent_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    topic: Optional[str] = None,
    limit_tokens: int = 4000,
    recency: str = "all",
    relevance_weight: float = RELEVANCE_WEIGHT,
    recency_weight: float = RECENCY_WEIGHT,
) -> dict:
    """
    Token-budgeted retrieval with combined scoring.

    Args:
        agent_id: filter to entries tagged with this agent (internal use)
        tenant_id: filter to entries owned by this tenant (CaaS use).
                   When set, agent_id is ignored and strict tenant isolation applies.
        topic: semantic query for relevance scoring (if None, recency-only)
        limit_tokens: maximum tokens in response
        recency: time window filter (hot/warm/cold/all)
        relevance_weight: weight for semantic similarity (0-1)
        recency_weight: weight for recency (0-1)

    Returns:
        dict with entries, total_tokens, truncated flag
    """
    init_store()
    now = datetime.now(timezone.utc)

    # Time window filter
    WINDOWS = {
        "hot": (0, 48),
        "warm": (48, 7 * 24),
        "cold": (7 * 24, 30 * 24),
        "all": None,
    }
    window = WINDOWS.get(recency)

    conn = _connect()

    # Build base query with tenant isolation baked into SQL.
    # tenant_id provided  → strict filter: only that tenant's rows
    # tenant_id is None, agent_id set → internal: only null-tenant rows
    # both None           → no filter (admin/compat mode)
    if tenant_id is not None:
        tenant_clause = "AND tenant_id = ?"
        tenant_params: tuple = (tenant_id,)
    elif agent_id is not None:
        # Internal agents only see entries without a tenant_id (null = internal)
        tenant_clause = "AND tenant_id IS NULL"
        tenant_params = ()
    else:
        tenant_clause = ""
        tenant_params = ()

    if window is None:
        rows = conn.execute(
            f"SELECT content_hash, created_at, session_id, tags, tier FROM entries "
            f"WHERE 1=1 {tenant_clause} ORDER BY created_at DESC LIMIT 2000",
            tenant_params,
        ).fetchall()
    else:
        min_h, max_h = window
        newer_than = (now - timedelta(hours=max_h)).isoformat()
        if min_h > 0:
            older_than = (now - timedelta(hours=min_h)).isoformat()
            rows = conn.execute(
                f"SELECT content_hash, created_at, session_id, tags, tier FROM entries "
                f"WHERE created_at < ? AND created_at >= ? {tenant_clause} "
                f"ORDER BY created_at DESC LIMIT 2000",
                (older_than, newer_than, *tenant_params),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT content_hash, created_at, session_id, tags, tier FROM entries "
                f"WHERE created_at >= ? {tenant_clause} "
                f"ORDER BY created_at DESC LIMIT 2000",
                (newer_than, *tenant_params),
            ).fetchall()
    conn.close()

    # Supersession filter — remove entries that have been explicitly
    # superseded by newer entries (retraction/replacement detected at write time).
    superseded = get_superseded_hashes()
    if superseded:
        pre_count = len(rows)
        rows = [r for r in rows if r["content_hash"] not in superseded]
        if len(rows) < pre_count:
            log.debug(
                "Supersession: filtered %d superseded entries",
                pre_count - len(rows),
            )

    # Agent filter (internal only, not used when tenant_id is set).
    # If agent has tagged entries, filter to those.
    # If no entries match the agent tag, return ALL entries (agent sees everything).
    if agent_id and tenant_id is None:
        agent_rows = [
            r for r in rows
            if agent_id in json.loads(r["tags"] or "[]")
        ]
        if agent_rows:
            rows = agent_rows
        # else: keep all rows — agent is an orchestrator or new agent
    
    # Score each entry
    scored = []
    
    # If topic provided, compute query embedding for semantic scoring
    query_vec = None
    if topic:
        query_vec = compute_embedding(topic)
    
    TIER_WEIGHTS = {1: 1.5, 2: 1.0, 3: 0.5}

    for row in rows:
        ch = row["content_hash"]
        rec_score = _recency_score(row["created_at"], now)
        tier = row["tier"] if row["tier"] is not None else 2
        tier_weight = TIER_WEIGHTS.get(tier, 1.0)

        if query_vec:
            # Get entry embedding for semantic score
            entry_vec = get_embedding(ch)
            if entry_vec:
                sem_score = _cosine_similarity(query_vec, entry_vec)
            else:
                sem_score = 0.0  # no embedding — low relevance

            combined = min(1.0, tier_weight * ((relevance_weight * sem_score) + (recency_weight * rec_score)))
        else:
            # No topic — pure recency ranking
            combined = min(1.0, tier_weight * rec_score)
            sem_score = None

        scored.append({
            "content_hash": ch,
            "created_at": row["created_at"],
            "session_id": row["session_id"],
            "tags": json.loads(row["tags"] or "[]"),
            "tier": tier,
            "score": combined,
            "recency_score": rec_score,
            "relevance_score": sem_score,
        })
    
    # Sort by combined score (highest first)
    scored.sort(key=lambda x: x["score"], reverse=True)
    
    # ---- Electoral College: two-stage source-aware retrieval ----
    #
    # Stage 1: Group entries by source. Each source gets ONE vote
    #          (its best-scoring entry). This prevents a 200-chunk
    #          course from drowning out a 10-chunk deep-dive.
    #
    # Stage 2: Rank sources by their best score. Fill token budget
    #          by round-robin across top sources.
    
    source_groups = defaultdict(list)
    for item in scored:
        src = _extract_source(item)
        source_groups[src].append(item)
    
    # Sort each source's entries by score (best first)
    for src in source_groups:
        source_groups[src].sort(key=lambda x: x["score"], reverse=True)
    
    # Rank sources by their best entry's score
    ranked_sources = sorted(
        source_groups.keys(),
        key=lambda src: source_groups[src][0]["score"],
        reverse=True,
    )
    
    log.debug(
        "Electoral college: %d entries -> %d sources. Top 5: %s",
        len(scored),
        len(ranked_sources),
        [(s, round(source_groups[s][0]["score"], 3)) for s in ranked_sources[:5]],
    )
    
    # Stage 2: Round-robin fill from top sources
    # Each round, take the next-best chunk from each source (in rank order)
    # until budget is exhausted or all chunks consumed.
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
                continue  # this source is exhausted
            
            item = items[idx]
            source_cursor[src] += 1
            
            body = read_by_hash(item["content_hash"])
            if not body:
                continue
            t = _tokens(body)
            if total_tokens + t > limit_tokens:
                truncated = True
                continue  # skip, try next source's chunk
            
            kept.append({
                "id": item["content_hash"],
                "session_id": item["session_id"],
                "tags": item["tags"],
                "body": body,
                "created_at": item["created_at"],
                "score": round(item["score"], 4),
                "recency_score": round(item["recency_score"], 4),
                "relevance_score": round(item["relevance_score"], 4) if item["relevance_score"] is not None else None,
                "source": src,
            })
            total_tokens += t
        
        # Early exit if budget is nearly full
        if truncated and total_tokens >= limit_tokens * 0.95:
            break
    
    # Sort final results by score for presentation
    kept.sort(key=lambda x: x["score"], reverse=True)
    
    return {
        "entries": kept,
        "total_tokens": total_tokens,
        "truncated": truncated,
        "scored_count": len(scored),
        "returned_count": len(kept),
        "source_count": len(ranked_sources),
    }
