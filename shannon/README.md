# Shannon Module Map

| File | Role |
|------|------|
| `__init__.py` | Package metadata — version `0.1.0`, author |
| `openclaw.py` | `save()` entry point and context file generation (hot/warm/cold time-tiered tiers) |
| `store.py` | SQLite append-only store — Zeckendorf addressing, `stats()`, zstandard compression |
| `zeckendorf.py` | Zeckendorf-Fibonacci address derivation (SHA-256 → integer → non-consecutive Fibonacci sum) |
| `qam.py` | QAM constellation dot-pattern encoder — visual address representation in 2D grid |
| `agent.py` | PGN Agent brain — loads LTM context, builds system prompt, calls LLM, saves responses |
| `llm.py` | LLM interface — Ollama (local) first, Anthropic Claude cloud fallback |
| `tools.py` | Browser tools — search (Tavily → SearXNG → DuckDuckGo), synthesize results for Pigeon BrowserPane |
| `api.py` | FastAPI HTTP API — memory save/query endpoints (M24) |
| `server.py` | Uvicorn entrypoint — serves `api.py` on `SHANNON_API_PORT` (default 8765) |
