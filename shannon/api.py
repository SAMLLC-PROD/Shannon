"""Shannon HTTP API — FastAPI wrapper for the Shannon SQLite store."""

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .store import _connect, write, read_by_hash, stats, init_store

app = FastAPI(title="Shannon Memory API", version="1.0")

AGENT_PROFILES = {
    "guy": ["milestone", "architecture", "decision", "lesson", "ron", "spec"],
    "henry": ["network", "validator", "task", "playbook", "deploy", "ops"],
    "nightwatch": ["topology", "anomaly", "threshold", "incident", "alert"],
    "archie": ["test", "proof", "requirement", "tech-gap", "m22"],
}

VALID_AGENTS = set(AGENT_PROFILES.keys())

# (min_hours_ago, max_hours_ago) — None means unbounded
RECENCY_WINDOWS = {
    "hot":  (0,        48),
    "warm": (48,       7 * 24),
    "cold": (7 * 24,   30 * 24),
    "all":  None,
}


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _parse_dt(ts: str, fallback: datetime) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return fallback


def _row_to_entry(row, body: str, now: datetime) -> dict:
    dt = _parse_dt(row["created_at"], now)
    age_hours = (now - dt).total_seconds() / 3600
    return {
        "id": row["content_hash"],
        "session_id": row["session_id"],
        "tags": json.loads(row["tags"] or "[]"),
        "body": body,
        "created_at": row["created_at"],
        "age_hours": round(age_hours, 2),
    }


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    s = stats()
    return {"status": "ok", "entries": s["total_entries"], "version": "1.0"}


# ---------------------------------------------------------------------------
# GET /memory
# ---------------------------------------------------------------------------

@app.get("/memory")
def get_memory(
    agent: str = Query(...),
    topic: Optional[str] = Query(None),
    limit_tokens: int = Query(2000),
    recency: str = Query("hot"),
):
    if agent not in VALID_AGENTS:
        raise HTTPException(400, f"Unknown agent '{agent}'. Valid: {sorted(VALID_AGENTS)}")
    if recency not in RECENCY_WINDOWS:
        raise HTTPException(400, f"Unknown recency '{recency}'. Valid: {list(RECENCY_WINDOWS)}")

    init_store()
    now = datetime.now(timezone.utc)

    conn = _connect()
    window = RECENCY_WINDOWS[recency]
    if window is None:
        rows = conn.execute(
            "SELECT content_hash, created_at, session_id, tags FROM entries "
            "ORDER BY created_at DESC LIMIT 1000"
        ).fetchall()
    else:
        min_h, max_h = window
        older_than = (now - timedelta(hours=min_h)).isoformat() if min_h > 0 else None
        newer_than = (now - timedelta(hours=max_h)).isoformat()
        if older_than:
            rows = conn.execute(
                "SELECT content_hash, created_at, session_id, tags FROM entries "
                "WHERE created_at < ? AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 1000",
                (older_than, newer_than),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT content_hash, created_at, session_id, tags FROM entries "
                "WHERE created_at >= ? "
                "ORDER BY created_at DESC LIMIT 1000",
                (newer_than,),
            ).fetchall()
    conn.close()

    # Filter: tags must intersect agent profile OR include the agent name itself
    profile_tags = set(AGENT_PROFILES[agent]) | {agent}
    filtered = [r for r in rows if set(json.loads(r["tags"] or "[]")) & profile_tags]

    # Optional topic filter
    if topic:
        filtered = [r for r in filtered if topic in json.loads(r["tags"] or "[]")]

    # rows are newest-first; truncate oldest first to honour token budget
    total_tokens = 0
    kept = []
    truncated = False
    for row in filtered:
        body = read_by_hash(row["content_hash"]) or ""
        t = _tokens(body)
        if total_tokens + t > limit_tokens:
            truncated = True
            break
        kept.append((row, body))
        total_tokens += t

    return {
        "agent": agent,
        "entries": [_row_to_entry(r, b, now) for r, b in kept],
        "total_tokens": total_tokens,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# POST /memory
# ---------------------------------------------------------------------------

class MemoryPost(BaseModel):
    body: str
    agent: str
    tags: List[str] = []
    session_id: Optional[str] = None


@app.post("/memory")
def post_memory(payload: MemoryPost):
    if payload.agent not in VALID_AGENTS:
        raise HTTPException(400, f"Unknown agent '{payload.agent}'. Valid: {sorted(VALID_AGENTS)}")

    tags = list(payload.tags)
    if payload.agent not in tags:
        tags.append(payload.agent)

    write(payload.body, session_id=payload.session_id, tags=tags)
    content_hash = hashlib.sha256(payload.body.encode("utf-8")).hexdigest()
    return {"id": content_hash, "ok": True}


# ---------------------------------------------------------------------------
# GET /memory/search
# ---------------------------------------------------------------------------

@app.get("/memory/search")
def search_memory(
    q: str = Query(...),
    agent: Optional[str] = Query(None),
    limit: int = Query(10),
):
    if agent and agent not in VALID_AGENTS:
        raise HTTPException(400, f"Unknown agent '{agent}'. Valid: {sorted(VALID_AGENTS)}")

    init_store()
    conn = _connect()
    # Pull recent entries; LIKE on tags gives a cheap pre-filter
    rows = conn.execute(
        "SELECT content_hash, created_at, session_id, tags FROM entries "
        "ORDER BY created_at DESC LIMIT 2000"
    ).fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    q_lower = q.lower()

    if agent:
        profile_tags = set(AGENT_PROFILES[agent]) | {agent}
        rows = [r for r in rows if set(json.loads(r["tags"] or "[]")) & profile_tags]

    results = []
    for row in rows:
        tags_list = json.loads(row["tags"] or "[]")
        # Check tags first (cheap), then body (expensive)
        tag_hit = any(q_lower in t.lower() for t in tags_list)
        if tag_hit:
            body = read_by_hash(row["content_hash"]) or ""
            results.append(_row_to_entry(row, body, now))
        else:
            body = read_by_hash(row["content_hash"]) or ""
            if q_lower in body.lower():
                results.append(_row_to_entry(row, body, now))
        if len(results) >= limit:
            break

    return {"results": results, "count": len(results)}
