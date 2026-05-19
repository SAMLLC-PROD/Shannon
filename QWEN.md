# Shannon LTM — Coding Agent Instructions

## Shannon Memory (DO THIS FIRST)

Before starting any task, query Shannon MCP for context:
```
mcp__shannon__memory_search query="<topic relevant to your task>" limit=5
mcp__shannon__memory_search query="agent-feedback <topic>" limit=3
```
Run 2-4 searches on different aspects. Shannon has 17,000+ entries of project history and lessons learned.

After completing work, save outcomes:
```
mcp__shannon__memory_save body="<what happened>" agent="qwen-code" tags=["<topic>"] session_id="YYYY-MM-DD-<topic>"
```

## What This Is

Shannon is an append-only, content-addressed long-term memory store for AI agents.
- SQLite index + zstd-compressed chunks on disk
- Zeckendorf-Fibonacci addressing (collision-free)
- HTTP API on port 8765 (systemd service)
- MCP server for tool-use integration
- Embedding model: nomic-embed-text via Ollama (port 11434)

## Key Files
- `shannon/store.py` — Core store. **DO NOT break the write/read/stats API.**
- `shannon/api.py` — FastAPI HTTP wrapper. Agents and profiles defined here.
- `shannon/embeddings.py` — Embedding generation + semantic search.
- `shannon/mcp_server.py` — MCP server (stdio, line-delimited JSON).
- `shannon/mcp_lsp_bridge.py` — Content-Length framing bridge for Rust MCP clients.
- `shannon/openclaw.py` — OpenClaw integration: save(), compress_session(), generate_context_file().
- `shannon/zeckendorf.py` — Addressing math. Pure functions, no I/O.
- Data: `~/.shannon/dictionary/layer_1/` (index.db, embeddings.db, chunks/)

## Rules
- Be direct. Execute tasks, don't explain plans.
- Write complete, production-quality code.
- Verify before claiming — run commands, check output.
- Read existing code before writing new code.
- All tests must pass: `pytest tests/ -v`
- Don't break the store.py or api.py public APIs without discussion.
- Embeddings use nomic-embed-text (768d, truncate input to 1500 chars for 512-token context).

## Deployment
- Local: `systemctl --user start shannon` (workstation)
- R740xd: `ron@192.168.0.71`, same systemd user service, port 8765
- Both instances share the same codebase and data format

## Completion Signal
When finished: `openclaw system event --text "Done: <summary>" --mode now`
