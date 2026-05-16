"""
shannon/retrieval.py — Token-budgeted retrieval with combined semantic + recency scoring.

Instead of filling entries newest-first, scores each entry by:
  score = (relevance_weight × cosine_sim) + (recency_weight × recency_decay)

Then fills token budget with highest-scoring entries.
"""

import json
import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from .store import _connect, init_store, read_by_hash
from .embeddings import semantic_search, compute_embedding, get_embedding, _cosine_similarity

log = logging.getLogger(__name__)

# Default scoring weights
RELEVANCE_WEIGHT = 0.6
RECENCY_WEIGHT = 0.4

# Recency decay: entries lose recency score over time
# Half-life of 48 hours — a 2-day-old entry has 0.5 recency score
RECENCY_HALF_LIFE_HOURS = 48.0


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


def _parse_dt(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def retrieve(
    agent_id: Optional[str] = None,
    topic: Optional[str] = None,
    limit_tokens: int = 4000,
    recency: str = "all",
    relevance_weight: float = RELEVANCE_WEIGHT,
    recency_weight: float = RECENCY_WEIGHT,
) -> dict:
    """
    Token-budgeted retrieval with combined scoring.
    
    Args:
        agent_id: filter to entries tagged with this agent
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
    if window is None:
        rows = conn.execute(
            "SELECT content_hash, created_at, session_id, tags FROM entries "
            "ORDER BY created_at DESC LIMIT 2000"
        ).fetchall()
    else:
        min_h, max_h = window
        newer_than = (now - timedelta(hours=max_h)).isoformat()
        if min_h > 0:
            older_than = (now - timedelta(hours=min_h)).isoformat()
            rows = conn.execute(
                "SELECT content_hash, created_at, session_id, tags FROM entries "
                "WHERE created_at < ? AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 2000",
                (older_than, newer_than),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT content_hash, created_at, session_id, tags FROM entries "
                "WHERE created_at >= ? "
                "ORDER BY created_at DESC LIMIT 2000",
                (newer_than,),
            ).fetchall()
    conn.close()
    
    # Agent filter
    if agent_id:
        rows = [
            r for r in rows
            if agent_id in json.loads(r["tags"] or "[]")
        ]
    
    # Score each entry
    scored = []
    
    # If topic provided, compute query embedding for semantic scoring
    query_vec = None
    if topic:
        query_vec = compute_embedding(topic)
    
    for row in rows:
        ch = row["content_hash"]
        rec_score = _recency_score(row["created_at"], now)
        
        if query_vec:
            # Get entry embedding for semantic score
            entry_vec = get_embedding(ch)
            if entry_vec:
                sem_score = _cosine_similarity(query_vec, entry_vec)
            else:
                sem_score = 0.0  # no embedding — low relevance
            
            combined = (relevance_weight * sem_score) + (recency_weight * rec_score)
        else:
            # No topic — pure recency ranking
            combined = rec_score
            sem_score = None
        
        scored.append({
            "content_hash": ch,
            "created_at": row["created_at"],
            "session_id": row["session_id"],
            "tags": json.loads(row["tags"] or "[]"),
            "score": combined,
            "recency_score": rec_score,
            "relevance_score": sem_score,
        })
    
    # Sort by combined score (highest first)
    scored.sort(key=lambda x: x["score"], reverse=True)
    
    # Fill token budget
    total_tokens = 0
    kept = []
    truncated = False
    
    for item in scored:
        body = read_by_hash(item["content_hash"])
        if not body:
            continue
        t = _tokens(body)
        if total_tokens + t > limit_tokens:
            truncated = True
            continue  # skip this one, try smaller entries
        
        kept.append({
            "id": item["content_hash"],
            "session_id": item["session_id"],
            "tags": item["tags"],
            "body": body,
            "created_at": item["created_at"],
            "score": round(item["score"], 4),
            "recency_score": round(item["recency_score"], 4),
            "relevance_score": round(item["relevance_score"], 4) if item["relevance_score"] is not None else None,
        })
        total_tokens += t
    
    return {
        "entries": kept,
        "total_tokens": total_tokens,
        "truncated": truncated,
        "scored_count": len(scored),
        "returned_count": len(kept),
    }
