# Task: Electoral College Scoring for Shannon Search

## Goal
Implement tier-weighted search scoring so curated knowledge ranks above raw transcript noise.

## Context
Shannon has ~17,700 entries. ~4,300 are raw YouTube transcript chunks that dominate search results because they outnumber curated project entries (~80). When an agent searches for "ZFS tuning", it gets Android Kotlin transcript fragments instead of actionable knowledge.

## Changes Required

### 1. Add `tier` column to `entries` table (store.py)

Add to schema in `init_store()`:
```sql
ALTER TABLE entries ADD COLUMN tier INTEGER DEFAULT 2;
```
Use `ALTER TABLE` with try/except (SQLite doesn't support IF NOT EXISTS for ALTER). This is backwards-compatible — existing entries get tier 2 (default).

Tier definitions:
- **Tier 1 (skill/decision):** Compiled skills, architecture decisions, milestone entries. Weight: 1.5x
- **Tier 2 (project):** Agent feedback, code reviews, session summaries. Weight: 1.0x (default)
- **Tier 3 (raw):** YouTube transcript chunks, raw notes. Weight: 0.5x

### 2. Update `write()` in store.py

Accept optional `tier` parameter (default 2). Include in INSERT:
```python
def write(data: str, session_id: str = None, tags: List[str] = None, tier: int = 2) -> str:
```
Add `tier` to the INSERT statement columns and values.

### 3. Update scoring in api.py `/memory/search`

Replace the current tag-based boost/penalty logic with tier-weighted scoring:

```python
# Current (REPLACE THIS):
if "knowledge" in tags:
    adjusted_score = min(1.0, score * 1.30)
    ...
elif "baseline" in tags:
    adjusted_score = score * 0.75

# New (tier-weighted):
TIER_WEIGHTS = {1: 1.5, 2: 1.0, 3: 0.5}
tier = row.get("tier", 2) if hasattr(row, "get") else 2
tier_weight = TIER_WEIGHTS.get(tier, 1.0)
adjusted_score = min(1.0, score * tier_weight)
```

The tier column must be fetched in the SQL query. Update the SELECT to include `tier`:
```sql
"SELECT content_hash, created_at, session_id, tags, tier FROM entries ORDER BY created_at DESC LIMIT 2000"
```

### 4. Update POST /memory endpoint in api.py

Accept optional `tier` field in `MemoryPost`:
```python
class MemoryPost(BaseModel):
    body: str
    agent: str
    tags: List[str] = []
    session_id: Optional[str] = None
    tier: int = 2  # ADD THIS
```

Pass tier to `write()`:
```python
write(payload.body, session_id=payload.session_id, tags=tags, tier=payload.tier)
```

### 5. Auto-tier assignment based on tags

In the `post_memory` endpoint, BEFORE calling write, auto-assign tier if not explicitly set:
```python
# Auto-tier: if tier wasn't explicitly set (still default 2), infer from tags
if payload.tier == 2:  # default, check if we should override
    tag_set = set(t.lower() for t in tags)
    if tag_set & {'skill', 'decision', 'architecture', 'milestone', 'skill-compilation'}:
        tier = 1
    elif tag_set & {'youtube', 'transcript', 'raw-note'}:
        tier = 3
    else:
        tier = 2
else:
    tier = payload.tier
```

### 6. Backfill existing entries

Create a new endpoint `POST /memory/backfill-tiers` that:
1. Reads all entries
2. Sets tier based on tags:
   - Has `youtube` or `transcript` tag → tier 3
   - Has `skill`, `decision`, `architecture`, `milestone`, `skill-compilation` tag → tier 1
   - Everything else → tier 2
3. Returns count of entries updated per tier

```python
@app.post("/memory/backfill-tiers")
def backfill_tiers():
    init_store()
    conn = _connect()
    rows = conn.execute("SELECT content_hash, tags FROM entries").fetchall()
    counts = {1: 0, 2: 0, 3: 0}
    for row in rows:
        tags = set(t.lower() for t in json.loads(row["tags"] or "[]"))
        if tags & {'skill', 'decision', 'architecture', 'milestone', 'skill-compilation'}:
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
```

### 7. Update `/memory` (token-budgeted retrieval) endpoint

The `retrieve()` function in store.py also needs to respect tiers. Find the `retrieve()` function and update its scoring to include tier weights in the same way as the search endpoint.

## Files to Modify
1. `shannon/store.py` — schema migration, write() signature, retrieve() scoring
2. `shannon/api.py` — search scoring, POST body, backfill endpoint

## Files NOT to Modify
- `shannon/embeddings.py` — cosine similarity stays the same, tier weighting happens after
- `shannon/zeckendorf.py` — addressing is unchanged
- `shannon/mcp_server.py` — MCP tools call the API, no changes needed

## Testing
After changes, run:
```bash
cd ~/development/shannon
python -m pytest tests/ -v
```

Then manually test:
```bash
# Restart service
sudo systemctl restart shannon

# Health check
curl -s http://localhost:8765/health

# Backfill tiers
curl -s -X POST http://localhost:8765/memory/backfill-tiers | python3 -m json.tool

# Test search — should now rank project entries above transcript noise
curl -s "http://localhost:8765/memory/search?q=ZFS+tuning&limit=5" | python3 -c "
import sys, json
for r in json.load(sys.stdin).get('results', []):
    tags = r.get('tags', [])
    print(f'[{r[\"score\"]:.3f}] tier=? tags={tags[:3]} | {r[\"body\"][:80]}')" 
```

## Completion
When finished:
1. Run tests
2. Git add + commit with message: `feat: electoral college tier-weighted search scoring`
3. Update ~/.openclaw/workspace/HEARTBEAT.md — change your row from ⏳ to ✅
4. Run: `openclaw system event --text "Done: Electoral college tier-weighted scoring — tier column added, search scoring updated, backfill endpoint ready." --mode now`
