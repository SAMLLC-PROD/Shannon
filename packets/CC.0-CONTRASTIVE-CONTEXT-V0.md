# Packet: CC.0 — Contrastive Context V0 (what-to / what-not-to)

**Status:** READY FOR FABLE (spec + thin vertical slice)  
**Date drafted:** 2026-07-18  
**Owner implementer:** Fable under Guy  
**Supervisor:** Guy  
**Product owner gates:** Ron  
**Lane:** Shannon + Skill MCP intelligence infra — **not** Lattice fleet / Gate F  
**Repos:**
- Code: `~/development/shannon/`
- Skills surface: Skill MCP (`:8770`) + `~/.openclaw/skills/` / course-to-skill outputs
- Sister packet (corpus): `CC.1-YOUTUBE-HOWTO-SUBSTRATE-TRIAGE.md`

---

## Mission

Capture **positive** and **negative** context as first-class, linkable memory — then use a **contrastive / cross-entropy-style score at decision edges** (tool pick, skill pick, session-review writeback).

Doctrine (lock this wording):

> **Positive context teaches what to do.**  
> **Negative context teaches what not to confuse it with.**  
> **Cross-entropy scores whether a link reduces uncertainty.**  
> **Cosine only says things look alike.**

Do **not** replace Shannon ANN/cosine candidate generation.  
Do **add** contrastive structure + re-rank / veto at the edges that change agent behavior.

---

## Why this exists

Today Shannon retrieval is:

```
score = tier * (0.5 * semantic_cosine + 0.25 * recency + 0.25 * trust)
merge = max(pass1, pass2) + 0.1 * graph_bonus
```

Negative context is thin:
- trust downweight tags (`spurious-correlation`, `no-causation` → 0.1)
- conflict heuristic (shared tags + different numbers + similar text)
- session-review prose when someone remembers to write it
- skill “common mistakes” sections (authored, not linked into retrieval)

Missing:
- first-class **anti-pattern / failed-fix / do-not-use-when** records
- explicit **positive↔negative links** (near-miss pairs)
- outcome-conditioned tool/skill ranking
- session-review → durable contrastive writeback

---

## Non-goals

- Replace embedding cosine / multi-pass retrieval wholesale
- Train a new foundation model
- Full RL / online bandit service in V0
- Lattice validator / MX / Gate F work
- Dumping YouTube transcripts into context windows
- Auto-deleting memories (Guy + Ron only decide retention)

---

## Design locks

### D1 — Two polarities, one schema family
Every actionable memory unit is either:

| polarity | meaning | retrieval effect |
|----------|---------|------------------|
| `positive` | do this / worked / canonical | boost when conditions match |
| `negative` | don’t / failed / spurious / near-miss | downrank, veto, or warn when conditions match |

### D2 — Contrastive pair is the atomic learning object
```text
(condition, positive_action, negative_action, outcome_signal, source)
```
Orphan positives OK. Orphan negatives OK. **Pairs preferred** after session review.

### D3 — Cosine proposes; contrastive disposes
1. Candidate gen: existing semantic + keyword + graph (unchanged)  
2. Re-rank / filter: contrastive score + negative veto  
3. Emit to agent: positives + explicit “avoid” bundle

### D4 — Decision edges only in V0
Instrument and write back at:
1. **tool selection**
2. **skill selection** (Skill MCP match)
3. **session-review** learning writeback

Not every chat turn.

### D5 — Authored Door when structured
Contrastive records are **system-generated structure** → prefer Authored/Silver path mindset (`LATTICE_AUTHORED_GENERATION_V1`).  
Do not flatten to prose and re-extract.

### D6 — Model-agnostic substrates
Anything derived for agents (skills, workflows, negatives) must load the same for Codex / Claude Code / Grok / local.  
No model-specific prompt packs as source of truth.

### D7 — CE-style score, pragmatic implementation
V0 does **not** require a trained CE head on day one.

Accept either:
- **A (preferred bootstrap):** InfoNCE-style / pairwise scorer over (query, pos, neg) using existing embeddings + learned linear head later  
- **B (rules bootstrap):** explicit veto tags + margin re-rank:
  ```
  final = cosine_score
        + α * positive_link_bonus
        - β * negative_link_penalty
        - ∞ if hard_veto_matches(condition)
  ```
Ship **B** first if faster; leave interface for **A**.

---

## Data model (V0)

### `ContrastRecord`
```python
class ContrastRecord(BaseModel):
    id: str                      # content hash or uuid
    polarity: Literal["positive", "negative"]
    kind: Literal[
        "procedure",             # how-to / skill step
        "decision",              # architecture choice
        "tool_choice",           # tool routing
        "anti_pattern",          # known bad method
        "failed_fix",            # attempted fix that failed
        "spurious",              # correlated but not causal
        "vuln_pattern",          # insecure pattern
        "verification",          # how to prove it worked
    ]
    title: str
    body: str                    # concise; judgment not essay
    conditions: list[str]        # when this applies ("deploy on 2GB NYC", "air-gapped", ...)
    tags: list[str]              # include polarity helpers below
    agent: str = "guy"
    session_id: str | None = None
    source: Literal[
        "session_review", "skill", "manual", "youtube_substrate",
        "tool_outcome", "distillation", "import"
    ]
    # optional link fields
    pair_id: str | None = None           # shared id tying pos+neg
    counters_id: str | None = None       # id of opposite polarity record
    skill_id: str | None = None
    tool_name: str | None = None
    confidence: float = 0.5              # 0-1
    outcome: Literal["worked", "failed", "unknown", "spurious"] | None = None
```

### Required tags (normalize on write)
**Positive:** `contrast:positive` + one of `procedure|decision|tool_choice|verification`  
**Negative:** `contrast:negative` + one of `anti_pattern|failed_fix|spurious|vuln_pattern|do_not_use_when`

Also set legacy trust tags when applicable:
- negatives that are spurious → also `spurious-correlation`, `no-causation` (keep old trust path working)
- verified positives → `verified` or `distilled-rule` when earned

### `ContrastLink` (graph edge)
```python
class ContrastLink(BaseModel):
    src_id: str
    dst_id: str
    rel: Literal[
        "COUNTERS",          # neg counters pos (or vice versa)
        "NEAR_MISS",         # looked right, failed
        "SUPERSEDES",
        "REQUIRES",
        "VETO_WHEN",         # if src matches, veto dst
        "DERIVED_FROM",
    ]
    conditions: list[str] = []
    weight: float = 1.0
```

### Store
V0 minimum (pick one, document choice in RESULTS):
1. **Shannon entries + tags + optional `links` JSON table/column** (fastest path), or  
2. Small sidecar SQLite `contrast_links` in Shannon data dir

Prefer (1) if links table is heavy; must still support query: “given entry ids, return COUNTERS/VETO_WHEN neighbors.”

---

## API / module surface

Add under `shannon/` (names flexible if cleaner):

```
shannon/contrast.py          # model + score + veto
shannon/session_contrast.py  # session-review → ContrastRecord emitter helpers
# wire into retrieval.py re-rank (feature-flagged)
# API routes (minimal):
  POST /contrast            # upsert ContrastRecord
  POST /contrast/link       # upsert ContrastLink
  GET  /contrast/for_query  # candidates + avoid bundle
  POST /contrast/outcome    # tool/skill outcome feedback
```

### Re-rank interface
```python
def apply_contrast(
    query: str,
    candidates: list[Candidate],
    *,
    enable_veto: bool = True,
) -> ContrastRanking:
    """
    Returns:
      ranked: candidates with final_score
      avoid:  negative records agent should see explicitly
      vetos:  ids removed or hard-downranked
    """
```

Feature flag: `SHANNON_CONTRAST_RERANK=1` (default off until tests green).

### Skill MCP hook (thin)
Document contract only in V0 if code touch is large:
- `skill_match` results may attach `avoid: []` from contrast negatives tagged with `skill_id`
- No requirement to rewrite Skill MCP core in CC.0 if Shannon side lands first

---

## Session-review writeback (required behavior)

Extend session-review skill output so each significant learning moment can emit:

```markdown
## Contrast: <short title>
polarity: positive | negative
kind: ...
conditions: [...]
outcome: worked | failed | ...
counters: <optional opposite title>
body: ...
```

Emitter (Fable implements helper; Guy/skill calls it):
- POST positive and/or negative `ContrastRecord`
- If both exist → same `pair_id` + `COUNTERS` link
- Tags include `session-review`, `contrast:*`

Update `~/.openclaw/skills/session-review/SKILL.md` with the Contrast block + save examples.

---

## Tool / skill outcome feedback (minimal)

```python
class OutcomeEvent(BaseModel):
    decision_type: Literal["tool", "skill"]
    name: str                 # tool or skill id
    task_fingerprint: str     # short normalized task text or hash
    success: bool
    notes: str | None = None
```

On success: reinforce positive tool_choice/procedure (bump confidence or add evidence line).  
On failure: create/merge `failed_fix` or `anti_pattern` negative with condition = task fingerprint summary.

V0 may log outcomes to Shannon even if auto-pair is naive.

---

## Tests (must ship)

```
tests/test_contrast_model.py       # schema, tags normalize
tests/test_contrast_rerank.py      # bonus/penalty/veto behavior
tests/test_contrast_session.py     # review block → records + pair link
tests/test_contrast_flags.py       # flag off = identical to old ranking path
```

Reuse spirit of trust tests: spurious/negative must not outrank verified positive for causal queries.

### Acceptance scenarios
1. Query causal topic → verified positive ranks above spurious negative baseline  
2. Hard `VETO_WHEN` removes or buries banned procedure  
3. Session-review pair write creates pos+neg with same `pair_id`  
4. Flag off → no change to legacy scores  
5. `GET /contrast/for_query` returns non-empty `avoid` when negatives match

---

## Deliverables

| # | Artifact |
|---|----------|
| 1 | This packet executed → code + tests in shannon |
| 2 | `docs/CONTRASTIVE_CONTEXT_V0.md` operator summary |
| 3 | session-review skill updated with Contrast block |
| 4 | `packets/RESULTS-CC.0.md` (what shipped, flag default, gaps) |
| 5 | Optional: wiki stub `~/development/wiki/concepts/contrastive-context.md` |

---

## Implementation order (Fable)

1. Schema + tag normalization + store/API  
2. Rules re-rank + veto (`apply_contrast`) + feature flag  
3. Session-review emitter + skill doc update  
4. Outcome endpoint (even if callers are stubbed)  
5. Tests + RESULTS  
6. Stop — do **not** start CE trained head unless Ron GO for CC.0b

---

## Success criteria

- [ ] ContrastRecord + ContrastLink persisted and queryable  
- [ ] Negatives populate deliberately (not only trust=0.1 accidents)  
- [ ] Re-rank feature-flagged; default off until Guy enables  
- [ ] Session-review can write pos/neg pairs in one flow  
- [ ] Tests green  
- [ ] No Lattice fleet changes  
- [ ] No secrets committed  

When finished:
```bash
openclaw system event --text "Done: CC.0 Contrastive Context V0" --mode now
```

---

## Follow-ons (out of this packet)

| ID | Title |
|----|-------|
| CC.0b | Learned CE/InfoNCE re-rank head on outcome data |
| CC.1 | YouTube/library how-to substrate triage + course-to-skill |
| CC.2 | Skill MCP native avoid-bundle in `skill_match` |
| CC.3 | Nightwatch/Henry outcome → contrast writeback |
| CC.4 | Authored Door Silver emit for contrast artifacts |

---

## Context anchors (read if needed)

- `shannon/shannon/retrieval.py` — current scoring  
- `shannon/specs/LATTICE_AUTHORED_GENERATION_V1.md` — structured emit  
- `~/.openclaw/skills/session-review/SKILL.md`  
- `~/.openclaw/skills/course-to-skill/SKILL.md`  
- Shannon memory 2026-05-27: trust scoring + spurious baseline  
- Shannon memory 2026-07-12: reasoning substrates (workflow + baselines, not dumps)

---

## Hard constraints

- Guy + Ron only decide mass memory delete  
- Prefer `trash` over destructive rm in any cleanup  
- Keep V0 small enough for one Fable pass  
- If scope creeps: cut CC.2/CC.0b, ship schema+rerank+session writeback  
