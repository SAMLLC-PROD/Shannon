# Packet: CC.1 — YouTube / Library How-To Substrate Triage

**Status:** READY FOR INTAKE + TRIAGE (Fable extract after Ron transcripts land)  
**Date drafted:** 2026-07-18  
**Owner implementer:** Fable under Guy (extraction passes)  
**Supervisor:** Guy  
**Product owner:** Ron (supplies new videos / transcription priorities)  
**Depends on:** course-to-skill doctrine; pairs with `CC.0-CONTRASTIVE-CONTEXT-V0.md`  
**Lane:** Knowledge substrates — **not** Lattice fleet  

---

## Mission

Turn the YouTube research library (and new transcripts Ron adds) into **model-agnostic how-to substrates** for:

1. **system design**
2. **programming**
3. **code security**
4. **vulnerability protection** (known methods → defenses / review checks)

Each substrate is a clean **reasoning + design workflow** any implementer can load (Codex, Claude Code, Grok, local) — not a transcript dump and not a model-specific prompt pack.

Doctrine lock (2026-07-12 reasoning substrates + course-to-skill):

> Humans use videos to fill gaps. Agents already know lots.  
> Bottleneck = **selection/application under uncertainty**.  
> Extract: workflow + baselines + domain cases + **negatives**.  
> Drop: syntax/API trivia every LLM already has.

---

## Non-goals

- Stuffing full VTTs into Shannon or agent context  
- Building a video player product  
- Training specialist weights in this packet  
- Claiming security completeness from YouTube alone  
- Offensive exploit authoring / weaponized PoCs  
  - **Allowed:** defensive patterns, secure design checks, known-vuln *classes*, mitigation verification  
  - **Forbidden in outputs:** ready-to-run exploit payloads, step-by-step attack recipes aimed at harm

---

## Pipeline

```
Ron URLs / local media
  → yt-fetch OR manual transcript drop
      → library/youtube/<Channel>/<Title>.md
  → triage board (this packet's TRIAGE.md)
      → bucket + priority + extract?
  → course-to-skill filter
      → substrate pack per topic
  → optional ContrastRecord positives/negatives (CC.0)
  → Skill MCP import / ~/.openclaw/skills/
```

### Transcription (Ron + Guy ops)

Existing tool:
```bash
cd ~/development/library/yt-fetch
python fetch.py <url> [<url> ...]
python fetch.py --file urls.txt
```

Output root: `~/development/library/youtube/`

**Staging for this wave:**
```
~/development/library/youtube/intake/
  README.md           # how to drop URLs
  urls-pending.txt    # Ron pastes URLs (one per line)
  TRIAGE.md           # living board
  SUBSTRATES/         # emitted packs
```

If captions fail: drop `.vtt` / `.srt` / cleaned `.md` into `intake/manual/` and note it on the triage board.

---

## Buckets

| Bucket id | Include | Exclude |
|-----------|---------|---------|
| `system-design` | boundaries, tradeoffs, failure domains, API/data contracts, multi-service topology | pure product marketing |
| `programming` | judgment-heavy patterns, testing strategy, refactor order, concurrency pitfalls | language syntax primers |
| `code-security` | secure defaults, authn/z, secrets, input trust boundaries, supply chain | generic “use HTTPS” filler |
| `vuln-protection` | vuln **classes**, review checklists, mitigations, verification | exploit PoCs / weaponization |
| `adjacent-keep` | useful but outside 4 (LLM training, UE5, ZK theory) | — |
| `drop` | entertainment, no agent value, pure trivia | — |

A single video may map to **multiple** buckets; split substrates rather than one mega-skill.

---

## Substrate pack format (emit target)

For each accepted extract:

```
library/youtube/intake/SUBSTRATES/<bucket>/<slug>/
  workflow.md      # clean reasoning / design loop
  SKILL.md         # course-to-skill skeleton (triggers, pattern, mistakes, verify, deviate)
  negatives.md     # what-not-to-do / known bad methods / footguns
  sources.md       # video titles + paths + timestamps worth citing
  checks.md        # verification commands / acceptance tests (defensive only)
```

`SKILL.md` must follow course-to-skill skeleton.  
`negatives.md` should be CC.0-importable (title, conditions, body, kind=`anti_pattern` or `vuln_pattern`).

---

## Decomposition filter (mandatory)

| Source content | Destination |
|----------------|-------------|
| Syntax, API docs, definitions LLM knows | **DROP** |
| When to use X (judgment) | SKILL triggers / workflow gates |
| Canonical pattern | SKILL canonical pattern + workflow |
| Common mistakes / war stories | SKILL mistakes + **negatives.md** |
| Multi-step deterministic verify | checks.md and/or MCP tool later |
| Exploit recipe | **DROP / refuse** — rewrite as defensive class + mitigation only |

Filter question: *Does an LLM already know this?* If yes, drop.

---

## Current library snapshot (2026-07-18)

**Already present (partial):**
- Vizuara LLM-from-scratch → skill exists (`llm-from-scratch`)
- AI Engineer: production agents, structured gen, fine-tune, Pydantic
- freeCodeCamp: DP, UE5 beginners
- a16z: agent git / devtools
- MIT OCW: interactive proofs / ZK / SNARGs (crypto theory — Lattice-adjacent, not appsec how-to)
- UE5 channels (game building — adjacent)
- David Andre open skills notes
- Production AI agents skill already derived from some of this

**Gaps vs stated goals:**
- Thin on dedicated **system design** series
- Thin on **appsec / vuln class** courses (OWASP-style, secure SDLC, threat modeling)
- Programming judgment scattered (stronger in AI Engineer + DP than general backend)

Triage must mark gaps explicitly so Ron can queue the right URLs.

---

## Fable tasks (when GO for extract)

### Phase A — Board only (can run now)
1. Inventory `library/youtube/**/*.md` titles (skip giant VTT bodies).  
2. Write `intake/TRIAGE.md` rows: path, channel, bucket(s), priority P0–P3, extract yes/no, notes.  
3. Gap list: missing topics under the 4 buckets.  
4. Do **not** bulk-embed full transcripts into Shannon.

### Phase B — After Ron fills `urls-pending.txt` / new transcripts
1. Run yt-fetch (or process manual drops).  
2. Re-triage new rows.  
3. Extract **P0 first** only (max N=3 packs per Fable pass unless Ron expands).  
4. Emit substrate packs.  
5. If CC.0 landed: POST contrast positives/negatives for mistakes.  
6. Optionally install SKILL.md into Skill MCP / openclaw skills (Guy reviews before global install).

### Phase C — Stop conditions
- P0 packs done OR  
- Blocked on missing transcripts OR  
- Scope creep into fleet work → stop and report

---

## TRIAGE.md row schema

```markdown
| id | title | path_or_url | bucket(s) | pri | extract | status | notes |
|----|-------|-------------|-----------|-----|---------|--------|-------|
| YT-001 | ... | youtube/... | code-security | P0 | yes | pending | ... |
```

Status: `pending | transcribed | triaged | extracted | dropped | blocked`

---

## Success criteria

- [ ] `intake/` staging exists with README + urls-pending + TRIAGE  
- [ ] Full library titles triaged into buckets (Phase A)  
- [ ] Gap list for Ron’s next transcription queue  
- [ ] ≥1 P0 substrate pack extracted as vertical slice when GO (Phase B)  
- [ ] Packs are model-agnostic and include **negatives**  
- [ ] No exploit PoCs in outputs  
- [ ] RESULTS note: `packets/RESULTS-CC.1.md`

When Phase A finished:
```bash
openclaw system event --text "Done: CC.1 Phase A YouTube triage board" --mode now
```

When first P0 substrate emitted:
```bash
openclaw system event --text "Done: CC.1 P0 substrate pack <slug>" --mode now
```

---

## Ron checklist (human)

1. Paste new video URLs into `library/youtube/intake/urls-pending.txt`  
2. Star priority with `# P0` comment on the line if needed  
3. Tell Guy “fetch pending” or “triage only”  
4. Review extracted `negatives.md` / SKILL before making a skill global default  

---

## Hard constraints

- Defensive security only in emitted skills  
- No Lattice Gate F coupling  
- Don’t auto-mass-install skills without Guy review  
- Prefer small packs over encyclopedias  
