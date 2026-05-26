# CLAUDE.md — Shannon Memory Service

## What This Is
Shannon is a persistent long-term memory (LTM) service with semantic search.
- Port 8765, systemd service (`shannon.service`)
- SQLite + nomic-embed-text embeddings (768d, via Ollama)
- Scoring: 0.6 × cosine_similarity + 0.4 × recency_decay (48hr half-life)
- 19,258 entries currently indexed

## Current Agent Model
Shannon already has per-agent slicing (`agents` table, `agent_id` field on entries).
But this is per-AGENT (guy, henry, etc.), not per-USER (external customers).

## What We're Building: CaaS (Context as a Service)
Multi-tenant Shannon that external users can rent as persistent context for ANY LLM.

## Key Files
- `shannon/api.py` — FastAPI routes (GET /memory, POST /memory, /memory/search, /context/regenerate)
- `shannon/store.py` — SQLite storage layer
- `shannon/embeddings.py` — nomic-embed-text via Ollama
- `shannon/retrieval.py` — semantic retrieval with scoring
- `shannon/mcp_http.py` — MCP server for Claude Code integration

## Shannon MCP
You have Shannon MCP available. Use it to understand current architecture.
