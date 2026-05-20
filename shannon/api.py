"""Shannon HTTP API — FastAPI wrapper for the Shannon memory service."""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from .store import _connect, write, read_by_hash, stats, init_store
from .embeddings import (
    embed_and_store, semantic_search, backfill_all,
    embedding_stats, init_embeddings, compute_embedding,
)
from .retrieval import retrieve

log = logging.getLogger(__name__)

app = FastAPI(title="Shannon Memory Service", version="2.0")


# ---------------------------------------------------------------------------
# Agent management
# ---------------------------------------------------------------------------

def _init_agents_table():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            display_name TEXT,
            tag_profile TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _ensure_agent(agent_id: str) -> None:
    """Auto-register agent if not exists."""
    _init_agents_table()
    conn = _connect()
    exists = conn.execute(
        "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO agents (agent_id, display_name, tag_profile, created_at) VALUES (?, ?, ?, ?)",
            (agent_id, agent_id, "[]", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        log.info("Auto-registered agent: %s", agent_id)
    conn.close()


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    s = stats()
    try:
        e = embedding_stats()
    except Exception:
        e = {"embedded": 0, "coverage": 0}
    return {
        "status": "ok",
        "version": "2.0",
        "entries": s["total_entries"],
        "embeddings": e["embedded"],
        "embedding_coverage": e["coverage"],
    }


# ---------------------------------------------------------------------------
# GET /memory — token-budgeted retrieval with semantic scoring
# ---------------------------------------------------------------------------

@app.get("/memory")
def get_memory(
    agent: str = Query(..., description="Agent ID"),
    topic: Optional[str] = Query(None, description="Semantic search topic"),
    limit_tokens: int = Query(4000, description="Max tokens to return"),
    recency: str = Query("all", description="Time window: hot/warm/cold/all"),
):
    init_store()
    _ensure_agent(agent)
    
    result = retrieve(
        agent_id=agent,
        topic=topic,
        limit_tokens=limit_tokens,
        recency=recency,
    )
    
    return {
        "agent": agent,
        "topic": topic,
        **result,
    }


# ---------------------------------------------------------------------------
# POST /memory — write + auto-embed
# ---------------------------------------------------------------------------

class MemoryPost(BaseModel):
    body: str
    agent: str
    tags: List[str] = []
    session_id: Optional[str] = None
    tier: int = 2


@app.post("/memory")
def post_memory(payload: MemoryPost, background_tasks: BackgroundTasks):
    init_store()
    _ensure_agent(payload.agent)
    
    tags = list(payload.tags)
    if payload.agent not in tags:
        tags.append(payload.agent)

    # Auto-tier: infer from tags if caller left default
    if payload.tier == 2:
        tag_set = set(t.lower() for t in tags)
        if tag_set & {'skill', 'skill-building', 'decision', 'architecture', 'milestone', 'skill-compilation', 'course-to-skill', 'claude-drop', 'project-setup', 'lesson-learned'}:
            tier = 1
        elif tag_set & {'youtube', 'transcript', 'raw-note'}:
            tier = 3
        else:
            tier = 2
    else:
        tier = payload.tier

    write(payload.body, session_id=payload.session_id, tags=tags, tier=tier)
    content_hash = hashlib.sha256(payload.body.encode("utf-8")).hexdigest()
    
    # Embed in background (non-blocking)
    background_tasks.add_task(embed_and_store, content_hash, payload.body)
    
    return {"id": content_hash, "ok": True}


# ---------------------------------------------------------------------------
# GET /memory/search — semantic search
# ---------------------------------------------------------------------------

@app.get("/memory/search")
def search_memory(
    q: str = Query(..., description="Search query"),
    agent: Optional[str] = Query(None, description="Filter to agent"),
    limit: int = Query(10, description="Max results"),
):
    init_store()
    now = datetime.now(timezone.utc)
    
    conn = _connect()
    rows = conn.execute(
        "SELECT content_hash, created_at, session_id, tags, tier FROM entries "
        "ORDER BY created_at DESC LIMIT 2000"
    ).fetchall()
    conn.close()
    
    # Agent filter
    if agent:
        _ensure_agent(agent)
        rows = [
            r for r in rows
            if agent in json.loads(r["tags"] or "[]")
        ]
    
    # Try semantic search first
    content_hashes = [r["content_hash"] for r in rows]
    sem_results = semantic_search(q, content_hashes, top_k=limit)
    
    if sem_results:
        # Build response from semantic results
        # Knowledge articles get a boost over quiz/baseline entries
        results = []
        hash_to_row = {r["content_hash"]: r for r in rows}
        for ch, score in sem_results:
            row = hash_to_row.get(ch)
            if not row:
                continue
            body = read_by_hash(ch) or ""
            tags = json.loads(row["tags"] or "[]")
            # Tier-weighted scoring: tier 1 (skill/decision) boosts, tier 3 (raw) penalizes
            TIER_WEIGHTS = {1: 1.5, 2: 1.0, 3: 0.5}
            tier = row["tier"] if row["tier"] is not None else 2
            tier_weight = TIER_WEIGHTS.get(tier, 1.0)
            adjusted_score = min(1.0, score * tier_weight)
            results.append({
                "id": ch,
                "session_id": row["session_id"],
                "tags": tags,
                "body": body,
                "created_at": row["created_at"],
                "score": round(adjusted_score, 4),
            })
        # Re-sort after score adjustment
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:limit]
        return {"results": results, "count": len(results), "method": "semantic"}
    
    # Fallback: keyword search
    q_lower = q.lower()
    results = []
    for row in rows:
        body = read_by_hash(row["content_hash"]) or ""
        if q_lower in body.lower() or any(q_lower in t.lower() for t in json.loads(row["tags"] or "[]")):
            results.append({
                "id": row["content_hash"],
                "session_id": row["session_id"],
                "tags": json.loads(row["tags"] or "[]"),
                "body": body,
                "created_at": row["created_at"],
                "score": None,
            })
        if len(results) >= limit:
            break
    
    return {"results": results, "count": len(results), "method": "keyword"}


# ---------------------------------------------------------------------------
# GET /agents — list registered agents
# ---------------------------------------------------------------------------

@app.get("/agents")
def list_agents():
    init_store()
    _init_agents_table()
    conn = _connect()
    rows = conn.execute("SELECT * FROM agents ORDER BY agent_id").fetchall()
    conn.close()
    
    # Count entries per agent
    main_conn = _connect()
    agents = []
    for row in rows:
        count = main_conn.execute(
            "SELECT COUNT(*) as c FROM entries WHERE tags LIKE ?",
            (f'%"{row["agent_id"]}"%',),
        ).fetchone()["c"]
        agents.append({
            "agent_id": row["agent_id"],
            "display_name": row["display_name"],
            "tag_profile": json.loads(row["tag_profile"] or "[]"),
            "created_at": row["created_at"],
            "entry_count": count,
        })
    main_conn.close()
    
    return {"agents": agents}


# ---------------------------------------------------------------------------
# POST /agents — register agent
# ---------------------------------------------------------------------------

class AgentPost(BaseModel):
    agent_id: str
    display_name: Optional[str] = None
    tag_profile: List[str] = []


@app.post("/agents")
def register_agent(payload: AgentPost):
    init_store()
    _init_agents_table()
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO agents (agent_id, display_name, tag_profile, created_at) VALUES (?, ?, ?, ?)",
        (
            payload.agent_id,
            payload.display_name or payload.agent_id,
            json.dumps(payload.tag_profile),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "agent_id": payload.agent_id}


# ---------------------------------------------------------------------------
# POST /context/regenerate — trigger context file regeneration
# ---------------------------------------------------------------------------

@app.post("/context/regenerate")
def regenerate_context():
    """Run the openclaw context regeneration."""
    import time
    start = time.monotonic()
    
    try:
        from .openclaw import generate_context_file
        path = generate_context_file()
        elapsed = round(time.monotonic() - start, 2)
        return {"ok": True, "path": str(path), "elapsed_seconds": elapsed}
    except ImportError:
        # generate_context_file might not exist — call the module
        import subprocess
        result = subprocess.run(
            ["python", "-m", "shannon.openclaw"],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        elapsed = round(time.monotonic() - start, 2)
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-500:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
            "elapsed_seconds": elapsed,
        }


# ---------------------------------------------------------------------------
# POST /embeddings/backfill — embed all un-embedded entries
# ---------------------------------------------------------------------------

@app.post("/embeddings/backfill")
def run_backfill(background_tasks: BackgroundTasks):
    """Start embedding backfill in background."""
    background_tasks.add_task(_backfill_task)
    return {"ok": True, "message": "Backfill started in background. Check /health for progress."}


def _backfill_task():
    result = backfill_all()
    log.info("Backfill complete: %s", result)


# ---------------------------------------------------------------------------
# POST /memory/backfill-tiers — assign tier to all existing entries
# ---------------------------------------------------------------------------

@app.post("/memory/backfill-tiers")
def backfill_tiers():
    init_store()
    conn = _connect()
    rows = conn.execute("SELECT content_hash, tags FROM entries").fetchall()
    counts = {1: 0, 2: 0, 3: 0}
    for row in rows:
        tags = set(t.lower() for t in json.loads(row["tags"] or "[]"))
        if tags & {'skill', 'skill-building', 'decision', 'architecture', 'milestone', 'skill-compilation', 'course-to-skill', 'claude-drop', 'project-setup', 'lesson-learned'}:
            tier = 1
        elif tags & {'youtube', 'transcript', 'raw-note'}:
            tier = 3
        else:
            tier = 2
        conn.execute("UPDATE entries SET tier = ? WHERE content_hash = ?", (tier, row["content_hash"]))
        counts[tier] += 1
    conn.commit()
    conn.close()
    return {"updated": counts, "total": sum(counts.values())}


# ---------------------------------------------------------------------------
# GET /embeddings/stats
# ---------------------------------------------------------------------------

@app.get("/embeddings/stats")
def get_embedding_stats():
    return embedding_stats()
