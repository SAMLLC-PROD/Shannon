# CLAW.md — Shannon LTM System

## What This Is
Shannon is an append-only, content-addressed long-term memory store for AI agents.
Data is stored in SQLite (indexed) + zstd-compressed chunks on disk.
Every chunk gets a unique Zeckendorf-Fibonacci address (collision-free by theorem).
Nothing is ever deleted. Sessions are tracked. Context is time-tiered for retrieval.

---

## File Map
```
shannon/
  store.py       — Core: write/read/stats. SQLite index + disk chunks. DO NOT break API.
  openclaw.py    — Integration: save(), compress_session(), generate_context_file()
  zeckendorf.py  — Addressing: data_to_address(), address_to_str(). Pure math, no I/O.
  qam.py         — QAM constellation pattern generator (visual layer, rarely modified)
  api.py         — FastAPI HTTP wrapper for the store. Agents and profiles defined here.
  llm.py         — LLM utility functions
  agent.py       — Agent-facing logic
  tools.py       — Tool definitions for agent integration
  server.py      — FastAPI app entry point
scripts/
  heartbeat.py   — Periodic Shannon health/stats check
tests/
  test_store.py  — Core store tests (most important)
  test_openclaw.py, test_zeckendorf.py, test_qam.py, test_agent.py, test_llm.py
```

Storage locations (do NOT move or rename):
- `~/.shannon/dictionary/layer_1/index.db` — SQLite index
- `~/.shannon/dictionary/layer_1/chunks/` — compressed .zst files
- `~/.shannon/sessions/YYYY-MM-DD/<session_id>.jsonl` — session logs
- `~/.openclaw/workspace/memory/shannon-context.md` — generated context file

---

## Coding Rules
1. **Never delete entries from the store** — append-only is a hard invariant.
2. **store.py API is stable** — `write()`, `read_by_hash()`, `read_by_address()`, `read_data()`, `stats()`, `get_session_chunks()` must keep their signatures.
3. **Addresses are deterministic** — same data → same address. Never randomize.
4. **Type hints required.** No bare `except:`.
5. **zeckendorf.py is pure math** — no I/O, no imports from the package.
6. **Tests live in `tests/`.** Run before and after every change.

---

## How to Run Tests
```bash
cd ~/development/shannon
source .venv/bin/activate
pytest tests/ -v
```
All tests must pass. If adding a feature, add a test in the matching `test_*.py` file.

---

## Surgical Edit Examples

### Example 1: Add a new field to the entries table
**Wrong:** Rewrite `init_store()` and all callers.
**Right:** Add an `ALTER TABLE` migration path:
```python
# In init_store(), after CREATE TABLE IF NOT EXISTS:
try:
    conn.execute("ALTER TABLE entries ADD COLUMN my_field TEXT DEFAULT ''")
    conn.commit()
except sqlite3.OperationalError:
    pass  # column already exists
```

### Example 2: Change the context window (hot/warm/cold thresholds)
Only touch constants in `openclaw.py`:
```python
# Before:
HOT_HOURS = 48
WARM_DAYS = 7
# After:
HOT_HOURS = 24   # tighten hot window
WARM_DAYS = 14   # extend warm window
```
No other changes needed.

### Example 3: Add a new agent profile to the API
Only touch `api.py`, the `AGENT_PROFILES` dict:
```python
AGENT_PROFILES = {
    "guy": [...],
    "henry": [...],
    "my_new_agent": ["tag1", "tag2", "tag3"],  # ADD HERE
}
```

---

## Common Pitfalls / NEVER DO
- **NEVER** remove or rename `write()`, `read_by_hash()`, `stats()` — callers everywhere.
- **NEVER** change the chunk filename format (`{content_hash}.zst`) — breaks retrieval.
- **NEVER** modify `zeckendorf.py`'s algorithm — addresses become inconsistent.
- **NEVER** set `HAS_ZSTD = True` unconditionally — zstd may not be installed.
- **NEVER** write to `shannon-context.md` directly — use `generate_context_file()`.
- Do not add network calls to `store.py` — it must remain pure local I/O.

---

## Dependencies & Environment
```bash
python >= 3.11
pip install zstandard  # optional but strongly recommended
pip install fastapi uvicorn  # only needed for api.py / server.py
```
Dev: `pip install pytest`
Virtual env: `~/development/shannon/.venv/`
Activate: `source ~/development/shannon/.venv/bin/activate`
