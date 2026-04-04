# AGENT.md

## Identity
name: shannon
version: 0.1.0
status: active
purpose: Persistent long-term memory system for agents — Zeckendorf-Fibonacci addressed SQLite store with time-tiered context compression
owner: ron.lattice

## Entry Points
primary: shannon/openclaw.py — save() and context generation
store: shannon/store.py — SQLite backend
cli: python -m shannon.openclaw — regenerate context file
tests: tests/ — run: pytest tests/

## Key Modules
- shannon/openclaw.py: save() entry point, context file generation (hot/warm/cold tiers)
- shannon/store.py: SQLite store, Zeckendorf addressing, stats()
- shannon/api.py: HTTP API (M24) — FastAPI memory save/query endpoints
- shannon/server.py: Uvicorn entrypoint, serves api.py on SHANNON_API_PORT (default 8765)
- shannon/agent.py: PGN Agent brain — loads LTM context, builds system prompt, calls LLM, saves responses
- shannon/llm.py: LLM interface — Ollama first, Anthropic Claude cloud fallback
- shannon/tools.py: Browser tools — Tavily/SearXNG/DuckDuckGo search, Pigeon BrowserPane synthesis
- shannon/zeckendorf.py: Zeckendorf-Fibonacci address derivation
- shannon/qam.py: QAM constellation dot-pattern encoder

## Dependencies
internal:
  - (none — standalone service)
external:
  - sqlite3: storage
  - zstandard>=0.22: chunk compression
  - fastapi: HTTP API
  - uvicorn: ASGI server
  - pydantic: request/response models
  - anthropic: cloud LLM fallback

## Active Specs
- specs/reference/ARCHITECTURE.md: Zeckendorf-Fibonacci addressing, QAM encoding, tiered compression design

## Current Tasks
(none active — M24 HTTP API Phase 1 complete)

## Recent Changes
- 5db7084 feat: Shannon HTTP API (M24 Phase 1) - memory query endpoint
- a9b341e 2026-03-16 session: watchdog, consensus probe, proof suite, ops page
- d7b96b8 feat: time-tiered context generation (hot/warm/cold)

## Lattice Integration
identity: no direct NFT integration
validators: no
bft_ops: none
pqc_signed: no

## Context File
Output: ~/.openclaw/workspace/memory/shannon-context.md
Regenerate: cd ~/development/shannon && .venv/bin/python -m shannon.openclaw
Schedule: every heartbeat

---

## ⚠️ Critical
- Always use .venv/bin/python (not system python)
- shannon.db lives in repo root — do not commit it (check .gitignore)
- Context file is time-tiered: hot (0-48h full) / warm (3-7d grouped) / cold (7d+ headers)
