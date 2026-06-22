# Lattice Authored Generation — "Born in Silver"

**Lattice Knowledge Lakehouse • Generation Contract**

*"The agent that knew the structure should not have to guess it back."*

---

## Status

| Field | Value |
|---|---|
| **Status** | SKETCH — structure for ratification, not yet content-complete |
| **Scope** | One seam only: the boundary between knowledge *generators* and the Bronze/Silver/Gold zones |
| **Promotion trigger** | Ron ratifies the shape (door split + Gold-as-render). Then content-complete pass. |
| **Explicitly out of scope** | The extraction path for externally-authored prose. That stays exactly as-is. |

---

## What This Changes

The lakehouse has two ways content can become a Silver envelope, and today they share one door.

- **External / human-written prose** enters Bronze and the **MCM Promoter** *extracts* a Silver envelope via LLM passes. Correct — the prose is the authored source, so deriving structure is the only option.
- **System-generated knowledge** (Gold synthesis, compiled skills, filed query responses) is *flattened to prose* by the generator, stored as prose, and — through the compounding loop — routed **back through the MCM Promoter** to re-derive the structure the generator already had. This is `structure → prose → re-extracted structure`: a redundant ~5–6s extraction pass on self-authored content, and a per-cycle source of classification drift.

This spec adds a second door so generators write structure directly.

| Document | Change |
|---|---|
| `MARKDOWN_CONTEXTUAL_MEMORY.md` | Adds `provenance_class` to the envelope; defines the **Authored Door** that bypasses the Promoter |
| `SHANNON_LTM_V2.md` | Splits `gold_synthesize`; the Reversed Context Pipeline emits a structured `CompiledSkill` instead of a flat `.md` as source |
| `LATTICE_KNOWLEDGE_LAKEHOUSE.md` | Output Filing routes through the Authored Door; **Gold becomes a render of Silver, not a stored source** |
| `SKILL_COMPILE_DEPLOY_LOOP.md` | Skill `.md` becomes a sidecar render over an authoritative structured map |

Depends on, no change required: `RASPUTIN_LAW_BLOCKCHAIN.md` (anchor), Cerberus TPM signing (authored envelopes are signed like any other), `LATTICE_GATEWAY_HIERARCHY_V1.md` (scope/RBAC unchanged).

---

## Part 1: The Two Doors

```python
class ProvenanceClass(Enum):
    EXTRACTED = "extracted"   # Silver derived from prose the system did NOT author.
                              # MCM Promoter runs exactly once, at the boundary.
    AUTHORED  = "authored"    # Silver emitted directly by a generator that
                              # already holds the structure. Promoter never runs.
```

Routing rule, stated once so the orchestration agent never collapses it:

```
if source is external prose  → Door A (Extraction): Bronze → MCM Promoter → Silver
if source is a generator     → Door B (Authored):   Generator → Silver  (no Promoter)
```

`authorship` (existing field: human | ai_generated | collaborative) describes *who wrote*; `provenance_class` describes *which door*. They usually agree, but the door is what the pipeline branches on.

---

## Part 2: The Generation Contract

Every generator implements one method. The return value is structure, never prose.

```python
class AuthoredArtifact:
    envelope:   MarkdownContextualMemory   # fully populated — see MCM spec, all fields
    body:       StructuredBody              # the content AS STRUCTURE (claims, edges,
                                            # typed sections) — NOT a markdown string
    render_hint: str                        # which Gold template renders this for humans
    reasoning_ref: Optional[str]            # Bronze handle to the verbose reasoning
                                            # transcript (append-only, audit only)

class Generator(Protocol):
    def emit(self, ...) -> AuthoredArtifact: ...
```

A single sink commits it:

```python
def commit_authored(art: AuthoredArtifact) -> SilverHash:
    art.envelope.provenance_class = AUTHORED
    silver_hash = silver.store(art.envelope, art.body)   # direct write, no extraction
    if art.reasoning_ref:
        bronze.anchor_transcript(art.reasoning_ref)       # provenance, never re-parsed
    gold.register_render(silver_hash, art.render_hint)    # render is derived + on-demand
    return silver_hash
```

**Where the verbose reasoning goes.** It is captured — the Memory Agent's full cognition lands in Bronze append-only and anchors to the attestation chain like everything else. It is preserved for audit but is *never* an input to extraction. The verbose layer (where the computation lives) is kept; it just stops being the source of truth.

---

## Part 3: The Three Seams

### Seam 1 — `gold_synthesize` (Article Generator)

**Today:** `Silver → Gold markdown article`. The article is the stored artifact; backlinks live as `[[concept]]` text; contradiction detection and the concept map parse prose.

**Change:** split into generation and rendering.

```
synthesize_node()  →  StructuredGoldNode   # canonical
render_article()   :  StructuredGoldNode → markdown   # pure, on view
```

- `StructuredGoldNode` carries synthesized **claims**, **typed edges** (the `[[concept]]` backlinks become real edges), **contradiction flags**, and the **contributing-source list** as structure.
- The Meso pane calls `render_article()` at view time. Article text is **cache, not source** — "article refresh" becomes *re-render*, not re-extract.
- The concept map and contradiction detector read edges and claims directly. No regex over prose.

### Seam 2 — Reversed Context Pipeline (`skill_compile`)

**Today:** `traces → PACER classify → synthesize skill spec → skill .md`. PACER already produces `PROCEDURAL / CONCEPTUAL / EVIDENCE / REFERENCE / ANALOGOUS` sections; they are then flattened to a `.md` that is re-parsed on load.

**Change:** stop discarding the classification.

```python
class CompiledSkill:
    section_map: list[TypedSection]   # PROCEDURAL→DO, CONCEPTUAL→WHEN,
                                      # INPUT constraints, etc. — AUTHORITATIVE
    md_render:   str                  # sidecar, human-editable, hot-reloadable
```

- The structured `section_map` is canonical. The `.md` is a **sidecar render** kept for the legitimate reason — humans hand-edit and hot-reload skills.
- Default load (no human edit): **zero re-parse** — read the structured map.
- If a human edited the `.md`: run **reconciliation** (diff prose → update the structured map), not a blind re-extract. Re-parse cost is paid only when a human actually touched the prose.

### Seam 3 — Output Filing (the compounding loop)

> Margin note / to confirm against current wiring: this assumes filed query responses presently re-enter as Bronze prose and hit the Promoter. If Output Filing already short-circuits to Silver, this seam is just extending that to Seams 1–2.

**Today (assumed):** `query → file → re-query`, with the filed prose routed back through the MCM Promoter.

**Change:** the agent answered the query *with* its supporting structure (the Silver nodes it retrieved and the synthesis it performed). File through the Authored Door.

```
answer + supporting structure  →  emit(AuthoredArtifact)  →  commit_authored()
```

- The envelope records **provenance edges** to the source Silver nodes it synthesized from.
- No Bronze prose round-trip; no Promoter pass. Re-query hits structured nodes directly.
- This is the single change that makes the compounding loop **idempotent instead of drift-accumulating**.

---

## Part 4: Invariants

```
I1  No redundant extraction.
    For AUTHORED artifacts the MCM Promoter LLM extraction path is never invoked.

I2  Idempotent compounding loop.
    Re-ingesting an AUTHORED artifact N times yields one Silver node
    (content-hash dedupe on the structured object); intent, skills, and
    edges are bit-stable across cycles.

I3  Render is downstream of truth.
    render(commit(emit())) preserves every claim, edge, and caveat present
    in emit(). Deterministic templates → byte-identical; LLM render →
    structure-preservation checked by structural diff.

I4  Extraction runs exactly once.
    For EXTRACTED artifacts the Promoter runs at the boundary and never again.

I5  Reasoning is captured, not consumed.
    The verbose transcript is anchored in Bronze and referenced by the
    envelope, but is never fed to extraction.
```

---

## Part 5: Acceptance Tests (proof artifact)

```
T1  no_redundant_extraction
    synthesize a Gold node → assert Promoter extraction NOT called
                          → assert Silver envelope present and complete

T2  idempotent_loop
    file a query response → re-ingest ×10
                          → assert single Silver node
                          → assert zero drift in intent / skills / edges

T3  pure_render
    render same authored node twice
        deterministic template → assert byte-identical markdown
        LLM render            → assert all claims/edges/caveats present (structural diff)

T4  skill_sidecar_reconciliation
    compile skill (no edit) → reload → assert ZERO re-parse
    human-edits .md         → reload → assert reconciliation runs,
                                       structured map matches edit

T5  reasoning_provenance
    emit authored artifact → assert transcript anchored in Bronze (append-only)
                          → assert envelope references it
                          → assert Promoter never reads it
```

---

## Part 6: Parked Questions (defer — do not resolve before build)

1. **Gold render engine** — deterministic template vs LLM-render-with-invariants. Templates give I3-byte-identical for free and cost nothing; LLM render reads better but must pass structural diff. *Leaning template-first, LLM render as an opt-in `render_hint` for prose-heavy article types.* Needs Ron's call.
2. **Reasoning transcript retention** — always store full, or sample/summarize for high-volume generators. Audit value vs Bronze growth.
3. **Reconciliation algorithm** (Seam 2) — prose-diff → structured-map update. Out of scope for this sketch; spec separately when skill hand-editing is built.

---

## Part 7: Body Schemas (content-complete)

The V1 contract has generators return `AuthoredArtifact { envelope, body, render_hint, reasoning_ref }`. Parts 1–6 settled the envelope — it is the existing MCM object with `provenance_class` added. This part completes `body`.

The reconciliation is the whole story: **`StructuredBody` and `StructuredGoldNode` are not new infrastructure. They are `LATTICE_KNOWLEDGE_GRAPH_V1` nodes and Links, emitted at synthesis time instead of re-derived.** Born-in-Silver and the graph re-anchoring are the same move from opposite ends — the graph *requires* every node to carry typed provenance; born-in-Silver has the generator *emit* that provenance directly. The round-trip we removed in Part 3 is precisely the step that would otherwise force the system to **guess back** the `DERIVED_FROM` and `CONTRADICTS` links the generator already held.

### 7.1 — Edges are canonical Links, not a new vocabulary

Every relationship the body asserts is a Knowledge Graph Link type. No bespoke edge fields.

| Body needs to express | Link type (per KG v1) | Propagation it inherits |
|---|---|---|
| synthesized from this source | `DERIVED_FROM` | source change → derived marked needs-review |
| sources conflict | `CONTRADICTS` (symmetric) | **both endpoints → SUSPECT** |
| `[[concept]]` backlink | `REFERENCES` | minimal; optional notify |
| this version replaces that one | `SUPERSEDED_BY` | children re-pointed; old archived |
| doc → section → subsection | `PARENT_OF` | parent change cascades suspicion |

The generator *declares* the links; `commit_authored` *attests* them (ML-DSA-87 `creator_signature`) and the graph's suspect-propagation machinery takes over.

### 7.2 — Claim (the atomic unit)

```python
class Claim:
    claim_id:   bytes32              # content-addressed
    text:       str                  # ONE proposition
    pacer_type: InformationType      # PROCEDURAL|CONCEPTUAL|EVIDENCE|REFERENCE|ANALOGOUS
    confidence: float                # generator's confidence in this claim
    conditions: list[str]            # load-bearing qualifiers — the "only if X"
    supports:   list[bytes32]        # DERIVED_FROM targets grounding this claim
```

`conditions` closes the loop from the design conversation that started this thread: caveats are **first-class and addressable**, so prose-flattening can never silently drop the qualifier — and invariant I3 (render preserves caveats) becomes mechanically checkable.

### 7.3 — StructuredBody

```python
class BodySection:
    section_id:        bytes32           # SHARED KEY with envelope.structure SectionMap
    heading:           str
    information_types: list[InformationType]
    claim_order:       list[bytes32]     # claims in this section, ordered
    parent_section:    Optional[bytes32] # PARENT_OF within the doc

class StructuredBody:
    body_id:        bytes32              # content hash of the structured body
    sections:       list[BodySection]
    claims:         dict[bytes32, Claim]
    asserted_links: list[GraphLink]      # DERIVED_FROM | CONTRADICTS | REFERENCES | SUPERSEDED_BY
```

### 7.4 — Reconciliation against the real MCM envelope fields

Every field the Promoter would *extract*, the generator *emits*. Metadata stays in the envelope; content (claims + their links) moves into the body; `section_id` is the join key.

| MCM envelope field | Today (Promoter extracts) | Born-in-Silver (generator emits) | Home |
|---|---|---|---|
| `summary_one_line/paragraph/detailed` | LLM summary pass | emitted directly | envelope |
| `skills`, `answers_questions`, `cannot_answer` | LLM extraction | emitted directly | envelope |
| `entities`, `concepts`, `keywords` | LLM extraction | emitted directly | envelope |
| `structure` (SectionMap + PACER) | parse + LLM classify | emitted; indexes `body.sections` by shared `section_id` | envelope ↔ body |
| `references_documents`, `referenced_by` | link parse | `REFERENCES` links | `body.asserted_links` (flat list = derived view) |
| `supersedes`, `superseded_by` | — | `SUPERSEDED_BY` link | `body.asserted_links` |
| `intent_metadata` | LLM extraction | emitted directly | envelope |
| `embedding_summary`, `embedding_questions` | encode | encode (unchanged) | envelope |
| `authorship` | classifier | `= ai_generated` ⇒ `AUTHORED` | envelope |

### 7.5 — StructuredGoldNode

```python
class StructuredGoldNode:
    node:                 GraphNodeRef            # node_type = CONTEXT (NOT a 6th type),
                                                  # scope from sources, ML-DSA-87 signature
    envelope:             MarkdownContextualMemory # provenance_class = AUTHORED
    body:                 StructuredBody
    contributing_sources: list[bytes32]           # → one DERIVED_FROM link each
    contradictions:       list[GraphLink]         # CONTRADICTS, detected AT synthesis
    coverage:             CoverageRecord          # staleness / gap signal (health checker)
    render_hint:          str
```

A Gold synthesis is a **CONTEXT node** — deliberately not a new node type, per the graph's "resist splitting" discipline — whose creator is the Memory Agent and whose many `DERIVED_FROM` parents are the Silver sources it synthesized. Contradiction detection emits `CONTRADICTS` *at the moment of synthesis*, so both endpoints go SUSPECT through the graph's existing propagation instead of becoming a prose note nobody re-checks. Backlinks are `REFERENCES`. The concept map and contradiction view read links; nothing regexes prose.

`render_hint` is the *only* field the parked render-engine question (Part 6 #1) touches — and it changes nothing above. The body is render-agnostic, so this pass stands whether Gold renders by deterministic template or LLM-with-invariants.

### 7.6 — Invariants (additions)

```
I6  Edges are canonical.
    Every relationship a generator asserts is a KG Link type; no bespoke edge schema.

I7  Contradictions propagate.
    A CONTRADICTS emitted at synthesis triggers graph suspect propagation on BOTH
    endpoints — no silent prose contradiction.

I8  Provenance is emitted, not recovered.
    contributing_sources are DERIVED_FROM links written at synthesis; re-query
    traverses them and never re-extracts "synthesized from" from prose.

I9  Caveats are addressable.
    Every claim condition is a first-class field; render preserves all of them
    (checkable extension of I3).
```

### 7.7 — Acceptance tests (additions)

```
T6  edges_are_links
    synthesize a multi-source node → assert all relationships are KG Link instances
                                   → assert zero bespoke edge records

T7  contradiction_propagation
    feed two conflicting contributing sources → assert CONTRADICTS link emitted
                                              → assert both endpoints suspect_status = SUSPECT

T8  provenance_traversal
    re-query a Gold node → assert "synthesized from" answered by DERIVED_FROM traversal
                        → assert Promoter NOT invoked

T9  caveat_preservation
    claim with conditions=["only when unlocked"] → render
        deterministic template → byte-match qualifier present
        LLM render            → structural-diff qualifier present
```

### Parked (carried forward)

- **Flat-list compatibility** — replace `references_documents` / `supersedes` flat lists with typed Links outright, or keep them as derived views over `asserted_links` for back-compat. *Leaning derived views.* Needs Ron's call.
- Render engine (Part 6 #1) remains open and is unaffected by this pass.

---

**One door for prose the system did not write; one door for knowledge it did. The Promoter stops re-deriving what the generator already knew, the compounding loop stops drifting, and Gold becomes a window onto Silver rather than a second copy of it.**
