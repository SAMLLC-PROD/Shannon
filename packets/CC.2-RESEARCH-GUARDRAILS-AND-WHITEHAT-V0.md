# Packet: CC.2 — Research Guardrails + Secure-Code / Local White-Hat V0 (spec)

**Status:** SPEC READY (design locks from Ron notes 2026-07-18)  
**Depends on:** CC.0 (contrastive context), CC.1 (youtube substrates)  
**Lane:** Agent workflow infra — not Lattice fleet  
**Implementer later:** Fable under Guy after CC.0/CC.1 P0 extracts land  

---

## Mission

Two sibling workflows Ron asked for:

1. **Focused Online Research Workflow** — agents can study the web **without rabbit holes**  
2. **Secure-by-Design + Local White-Hat Loop** — code-review / vuln-class knowledge continuously checked against **our** codebases  

Both must emit **workflow + guardrails + negatives**, model-agnostic.

---

## Part A — Anti-rabbit-hole research

### Problem
Autonomous web research without a charter tends to:
- widen query scope every hop (“related interesting thing”)
- never declare done
- burn tokens/time
- contaminate memory with low-trust tangents

Agents don’t “run away” with intent — they **optimize local curiosity** because nothing scores *focus* or *exit*.

### Design locks

#### R1 — Charter before browse
No open-web tool use until a `ResearchCharter` exists:

```python
class ResearchCharter(BaseModel):
    question: str                 # single primary question
    success_criteria: list[str]   # what “answered” means
    in_scope: list[str]
    out_of_scope: list[str]       # explicit anti-goals
    max_hops: int = 8             # page/tool calls
    max_minutes: int = 15
    allow_domains: list[str] = [] # empty = any, still hop-capped
    deny_domains: list[str] = []
    output_artifact: str          # path or schema of deliverable
```

#### R2 — Budget is hard law
Hard stop when any trips:
- hop count ≥ max_hops  
- wall clock ≥ max_minutes  
- primary question marked answered against success_criteria  
- **wander score** ≥ threshold (see R3)

On hard stop: **mandatory synthesize + exit**. No “one more link.”

#### R3 — Wander detector
Each hop scores:
```
wander = novelty_away_from_charter + topic_drift + diminishing_return
```
Signals:
- embedding distance of page summary vs charter.question rising  
- new entities not in in_scope  
- repeat domain/pattern with no new claim  
- tool result doesn’t reduce uncertainty on success_criteria (CE-style)

If wander high → response policy:
1. **Refocus prompt** (once): restate charter, drop tangent  
2. If still high → **force exit + write partial answer + open questions**

#### R4 — No silent memory pollution
Research writes only:
- charter  
- claim log with source URLs  
- open questions  
- contrast negatives for “rabbit hole attractors” discovered  

Not raw page dumps into Shannon.

#### R5 — Autonomy levels
| level | behavior |
|-------|----------|
| L0 | human paste sources only |
| L1 | agent may fetch URLs human listed |
| L2 | agent may search within charter + budgets |
| L3 | multi-charter program (requires Ron GO) |

Default for unsupervised: **L1 or L2 with hard budgets**.  
There is no “L∞ free-range.”

#### R6 — Response when rabbit hole starts
Agent must emit a structured event, not hide it:
```
RESEARCH_WANDER
  drift: ...
  last_useful_claim: ...
  action: refocus | exit_synthesize
```

Human-visible. Logged. Optional contrast negative: “do not chase X when charter is Y.”

### Deliverable shape
```
research-guardrails/
  workflow.md
  SKILL.md
  negatives.md
  checks.md
```

---

## Part B — Secure code workflow + local white-hat agent

### Problem
Security knowledge in videos dies as entertainment unless it becomes:
1. a **vuln class checklist** at design/code time  
2. a **recurring audit** against our repos  
3. **contrast negatives** so agents stop reintroducing the class  

### Design locks

#### S1 — Vuln class cards (not exploit kits)
From code-review / security videos extract:
```
VulnClass:
  id, name, description
  bad_pattern (structural)
  good_pattern
  detection_hints (grep/semgrep/tests — defensive)
  conditions (language, stack)
  sources (video timestamps)
```
**Forbidden:** ready-to-run exploit payloads, weaponized attack recipes.

#### S2 — Design-time gate
Secure-by-design workflow steps:
1. threat boundaries (trust edges)  
2. authn/z model  
3. data classification  
4. fail-closed defaults  
5. abuse cases  
6. verification plan  

Maps to FORGE/RMF thinking where relevant; keep lightweight for app code.

#### S3 — Compare-to-ours loop
When a video identifies bad structure:
```
for each VulnClass:
  scan target repos (lattice-*, shannon, pigeon, etc.)
  → findings
  → if clean: record evidence
  → if hit: ticket + contrast negative + optional patch packet
```

#### S4 — Local white-hat agent (scope)
**Is:** scheduled/on-demand reviewer using class cards + static checks + test gaps  
**Is not:** unsolicited internet offensive ops, random third-party hacking, bypass of Ron’s prod gates  

Runs **local / our repos / our staging** only unless Ron expands scope in writing.

#### S5 — Continuous ≠ noisy
Cadence options:
- on PR / pre-commit packet  
- weekly repo sweep  
- after extracting new VulnClass from CC.1  

Findings go to: fix log + Shannon contrast + optional GITFLOW work order later.

### Deliverable shape
```
secure-code-workflow/
  workflow.md          # design-time secure path
  SKILL.md
  negatives.md
  vuln_classes/        # cards
  whitehat_agent.md    # runbook for local reviewer agent
  checks.md
```

---

## Relationship to CC.0 / CC.1

| Source | Feeds |
|--------|-------|
| CC.1 videos (research focus) | Part A skill + negatives |
| CC.1 code review / phdsec / secure disclosure | Part B vuln class cards |
| CC.0 | store pos/neg pairs from findings; veto bad patterns at skill/tool edges |
| Session review | when we ship a vuln, write failed_fix + counters |

---

## Implementation order (when GO)

1. Spec ratification (this doc) — **now**  
2. CC.1 extract P0 research + design + code-review videos → draft workflows  
3. Stand up ResearchCharter + hop budget in one agent path (OpenClaw tool policy or skill)  
4. Seed 5–10 VulnClass cards from first code-review extracts  
5. One white-hat sweep against `shannon` or `lattice-proxy` as pilot  
6. Wire findings into CC.0 contrast  

---

## Success criteria (eventual)

- [ ] Agent cannot start open web research without charter  
- [ ] Wander triggers refocus or forced synthesize  
- [ ] At least one secure-code workflow skill installed  
- [ ] ≥5 vuln class cards with compare-to-ours checks  
- [ ] One pilot white-hat report on a SAMLLC repo  
- [ ] No exploit PoCs in repo artifacts  

---

## Ron notes anchor
`library/youtube/intake/RON-NOTES-2026-07-18.md`
