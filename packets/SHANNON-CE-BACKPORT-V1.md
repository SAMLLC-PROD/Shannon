# PACKET — SHANNON-CE-BACKPORT-V1

GO: 2026-10-04 Ron (operator)
SWE: Claude Code, model claude-opus-5-5
TE: Ron (review before merge)
Cwd: ~/development/shannon
Branch: ce-backport (new, off main)
When done: land-notify + commit, do not push

## Why

The CE build pass (CE-FIX-OPUS55-V1 + CE-V2-GAPS-001) found four real bugs that exist
verbatim in the origin Shannon. They are not CE-specific. This packet ports them back
plus the model allow-list concept (softer than CE — keeps local flexibility).

Reference diffs live in:
  ~/development/ce-deliverable/software/shannon/shannon/

Do not copy files wholesale. Read the CE diff, apply the same logic to the origin code.
The origin Shannon has more features; don't clobber them.

---

## BACKPORT D2 — requirements.txt + mcp pin

**File:** `requirements.txt`

Current state: only `zstandard>=0.22`.

Add the full runtime dependencies. Check `pyproject.toml` and `shannon/server.py` imports
to confirm the full list, then add anything missing. At minimum:

```
fastapi>=0.100.0
uvicorn[standard]>=0.23.0
pydantic>=2.0.0
httpx>=0.24.0
zstandard>=0.22
```

**File:** `pyproject.toml`

Find `mcp>=1.0` and change to `mcp>=1.0,<2`. Same FastMCP rename issue that broke the
CE version will break a fresh install here.

Check `requirements-dev.txt` too — add pytest dependencies if missing.

---

## BACKPORT D3 — store.py migration runs at startup, not only on write

**File:** `shannon/store.py`

Current bug: `init_store()` is called from `write()` (line ~108). A fresh database
hits `recall()` or `search()` before any write — the `tenant_id`, `tier`, `profile_id`,
and other columns don't exist yet, causing a 500.

Fix: audit every `ALTER TABLE` in `store.py`. Extract all of them into a
`_run_migrations(conn)` helper. Call `_run_migrations(conn)` inside `init_store()`
immediately after the `CREATE TABLE IF NOT EXISTS` block.

The inline migration guards in `write()` can stay as safety nets but must not be
the first time those columns exist on a clean DB.

After the fix: a brand-new `.shannon/shannon.db` created by `init_store()` must already
have all columns before any write is called. Verify with a quick test.

**Add to `tests/` (or the existing test file if one covers store):**

```python
def test_init_store_has_all_columns():
    """Fresh DB from init_store() must have tenant_id and other late columns."""
    import sqlite3, tempfile, os
    with tempfile.TemporaryDirectory() as d:
        os.environ["SHANNON_HOME"] = d
        from shannon import store
        store._DB = None  # reset cached path if any
        store.init_store()
        conn = sqlite3.connect(os.path.join(d, "shannon.db"))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
        conn.close()
        for col in ("tenant_id", "tier", "profile_id", "conflict_group_id"):
            assert col in cols, f"Column {col!r} missing after init_store()"
```

Adapt DB path injection to however the origin Shannon locates its DB
(likely `SHANNON_HOME` env var pointing at the `.shannon/` dir).

---

## BACKPORT D4 — server.py binds 127.0.0.1 by default

**File:** `shannon/server.py` line ~10:

Change:
```python
uvicorn.run(app, host="0.0.0.0", ...)
```
To:
```python
uvicorn.run(app, host=os.getenv("SHANNON_BIND_HOST", "127.0.0.1"), ...)
```

Our Shannon runs on the homelab behind the firewall so this is low urgency, but
`127.0.0.1` is the right default. Operators who need network access set
`SHANNON_BIND_HOST=0.0.0.0`.

Check `shannon.service` (systemd unit) — if it passes `--host 0.0.0.0` explicitly,
update that too. If the service needs network access for R740xd → workstation traffic,
add `SHANNON_BIND_HOST=0.0.0.0` to the service's `Environment=` line rather than
hardcoding in the source.

---

## BACKPORT D5a + concept from CE-V2-GAPS-001 — llm.py model gating

**File:** `shannon/llm.py`

Two changes, in order of importance:

### 1. Gate the Anthropic cloud fallback (D5a — straight port)

Add near the top:
```python
SHANNON_ALLOW_CLOUD = os.environ.get("SHANNON_ALLOW_CLOUD", "").lower() in ("1", "true", "yes")
```

In `chat_async()`, `chat_stream_async()`, and `chat()`: wrap the Anthropic fallback
block so it only runs when `SHANNON_ALLOW_CLOUD` is true. If Ollama fails and cloud
is not allowed, raise:
```python
RuntimeError("Ollama unavailable and SHANNON_ALLOW_CLOUD not set — refusing cloud fallback")
```

Our local Shannon runs `SHANNON_ALLOW_CLOUD` unset by default. Anyone who wants
cloud sets it explicitly. This is not a breaking change for our setup since we
have Ollama running.

### 2. Soft model allow-list (CE-V2-GAPS-001 concept, NOT the CE strict defaults)

The CE version hard-errors on any model not in a two-model list. We want the concept
(no silent substitution to arbitrary models) without CE's strict defaults.

Add:
```python
SHANNON_ALLOWED_MODELS_ENV = os.environ.get("SHANNON_ALLOWED_MODELS", "").strip()

def allowed_models() -> list[str]:
    """
    When SHANNON_ALLOWED_MODELS is set, only those model ids are accepted.
    When unset (our default), any available Ollama model is fine — we're
    experimenting locally. CE operators set an explicit list.
    """
    if SHANNON_ALLOWED_MODELS_ENV:
        return [x.strip() for x in SHANNON_ALLOWED_MODELS_ENV.split(",") if x.strip()]
    return []  # empty = unrestricted
```

In `_resolve_model()`: if `allowed_models()` is non-empty, check the requested model
against it before substituting. If it's not on the list, raise `RuntimeError` instead
of silently falling back. If `allowed_models()` is empty, keep the current graceful
fallback behavior (unrestricted local use).

This means:
- Our homelab (no `SHANNON_ALLOWED_MODELS` set): behaves exactly as today
- A restricted deployment (env var set): hard-errors on unknown models
- No breaking change for Guy's daily use

Do NOT port CE's `UnknownModelError`/`ModelUnavailableError` classes unless they fit
naturally — keep it simple with `RuntimeError` for now.

---

## Test

Run the existing test suite after each change to confirm no regressions:

```bash
cd ~/development/shannon
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -20
```

If tests don't exist or the suite is sparse, note it but don't block on it.
The D3 store test above is the most important new one — add it.

---

## Commit

```bash
git add -A
git commit -m "CE backport: D2 deps, D3 migrations at startup, D4 loopback default, D5a cloud gate, soft allow-list

- requirements.txt: full deps + mcp<2 pin
- store.py: all migrations run in init_store() before first request
- server.py: SHANNON_BIND_HOST defaulting to 127.0.0.1
- llm.py: SHANNON_ALLOW_CLOUD gate on Anthropic fallback
- llm.py: SHANNON_ALLOWED_MODELS soft allow-list (unrestricted when unset)
"
```

Do NOT push. Do NOT restart the running Shannon service. Ron reviews before merge to main.

---

## Land notify

```bash
~/.openclaw/workspace/scripts/land-notify.sh "Shannon CE backport committed: D2+D3+D4+D5a+allow-list" now
```
