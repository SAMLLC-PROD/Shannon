# M24 Phase 2: Shannon Memory Service — Build Spec

**Status:** Building
**Started:** 2026-05-16
**Depends on:** Shannon LTM v1 (M7) ✅, V-Index proxy ✅

---

## Goal

Turn Shannon from a library (imported by OpenClaw) into an always-running systemd service
with per-agent memory slices and token-budgeted semantic retrieval.

## What Exists

- `shannon/store.py` — Zeckendorf-Fibonacci SQLite store, 518 entries, 0.56MB
- `shannon/api.py` — FastAPI with GET/POST /memory, /memory/search, /health
- `shannon/server.py` — uvicorn launcher on port 8765
- `shannon/openclaw.py` — save() + context regeneration (used by heartbeats)
- Agent profiles hardcoded in api.py (guy, henry, nightwatch, archie)
- V-Index grounding proxy on port 11435 (entity-based fact retrieval)

## What To Build

### 1. Semantic Search (replace keyword search)

Current `/memory/search` does case-insensitive substring match — O(n) scan of all entries.
Replace with embedding-based semantic search.

**Implementation:**
- Add `embeddings` table to SQLite: `(content_hash TEXT, embedding BLOB)`
- On write(): compute embedding via Ollama (`nomic-embed-text` or `all-minilm`)
- On search(): embed query, cosine similarity against stored embeddings, top-K
- Fallback: if Ollama down, fall back to current keyword search
- Batch backfill: script to embed all 518 existing entries

**Files:** `shannon/embeddings.py` (new), modify `store.py` + `api.py`

### 2. Per-Agent Memory Slices

Current: flat tag-based filtering with hardcoded AGENT_PROFILES dict.
Replace with dynamic agent registration + proper slice isolation.

**Implementation:**
- `agents` table: `(agent_id TEXT PK, display_name TEXT, tag_profile TEXT, created_at TEXT)`
- `POST /agents` — register new agent with tag profile
- `GET /agents` — list registered agents
- Auto-register on first POST /memory with unknown agent
- Agent profile stored in DB, not hardcoded
- Add `agent_id` column to entries table (explicit ownership, not just tag intersection)

**Files:** modify `api.py`, `store.py`

### 3. Token-Budgeted Retrieval

Current: fill entries newest-first until token budget hit. Misses relevant older entries.

**Implementation:**
- Combine recency score + semantic relevance score
- `score = (relevance_weight * cosine_sim) + (recency_weight * recency_decay)`
- Default weights: relevance=0.6, recency=0.4
- Sort by combined score, fill token budget with highest-scoring entries
- Caller specifies: `limit_tokens`, optional `topic` (semantic query), optional `recency`

**Files:** modify `api.py`, new scoring logic in `shannon/retrieval.py`

### 4. Topic-Based Retrieval

Current: topic filter is exact tag match only.

**Implementation:**
- If `topic` param provided, use it as semantic query against embeddings
- Return entries most semantically similar to topic, regardless of tags
- Combine with agent slice filter (agent's entries only)
- Example: `GET /memory?agent=guy&topic=pigeon-browser&limit_tokens=4000`

**Files:** modify `api.py`, use `embeddings.py`

### 5. Systemd Service

**Implementation:**
- `shannon.service` systemd unit file
- Runs on port 8765 (configurable via SHANNON_API_PORT)
- Restart=on-failure, After=network.target
- Log to journal
- Health check: `GET /health` returns entry count + embedding status

**Files:** `shannon.service` (new), install script

### 6. Context Regeneration Endpoint

Current: `python -m shannon.openclaw` run from shell. 
Add API endpoint so OpenClaw heartbeat can trigger it via HTTP.

**Implementation:**
- `POST /context/regenerate` — runs the openclaw.py context generation
- Returns: path to generated file, entry count, time taken
- Async (returns immediately with task ID, poll for completion)

**Files:** modify `api.py`, wire `openclaw.py` regeneration

---

## API Surface (Final)

```
GET  /health                          — status + entry count + embedding status
GET  /memory?agent=X&topic=Y&limit_tokens=N&recency=hot|warm|cold|all
POST /memory                          — {body, agent, tags[], session_id}
GET  /memory/search?q=X&agent=X&limit=N  — semantic search
GET  /agents                          — list registered agents  
POST /agents                          — register agent with tag profile
POST /context/regenerate              — trigger context file regeneration
```

## Build Order

1. `shannon/embeddings.py` — embedding compute + store + similarity
2. Backfill script — embed all 518 existing entries
3. `shannon/retrieval.py` — combined scoring (semantic + recency)
4. Update `api.py` — wire semantic search, per-agent DB, token-budgeted retrieval
5. `shannon.service` — systemd unit
6. Test everything
7. Deploy + verify heartbeat integration still works

## Estimated Time: 2-3 hours

---

_Spec: Guy Shannon, 2026-05-16_
