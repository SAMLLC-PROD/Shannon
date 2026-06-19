# Shannon Memory Service — Operations Guide

**Version:** 2.0
**Date:** 2026-06-18
**Authors:** Ron Peterson & Guy Shannon
**Audience:** Everyone — CaaS users, agents, integrators, and humans

---

## 1. What Shannon Is

Shannon is a **persistent long-term memory service** for AI agents, applications, and humans. It solves one problem: **context is lost when sessions end.** Shannon writes memories to disk, embeds them for semantic search, and retrieves them based on meaning and recency — so every new session can start with relevant context from every previous one.

Named for Claude Shannon, father of information theory.

**Shannon works for anyone who needs to remember things across sessions:**

| Use Case | Who | What Gets Stored |
|----------|-----|-----------------|
| **Project engineering** | AI coding agents, dev teams | Architecture decisions, error resolutions, milestones, lessons learned |
| **Personal assistant** | Life-management agents, personal AI | Daily activities, relationships, preferences, schedules, life logistics |
| **Knowledge management** | Researchers, mechanics, specialists | Domain expertise, procedures, supplier info, part numbers, reference material |
| **Team context** | Collaborative agents, workgroups | Shared decisions, meeting outcomes, project status, handoff notes |

### What Shannon Is NOT

- **Not a raw data dump.** Shannon stores *distilled knowledge*, not unprocessed streams. Raw keystrokes, OCR dumps, and full email threads don't go in directly — but summaries of what they *mean* absolutely do. What counts as "distilled" depends on your use case (see Section 4).
- **Not a vector-only store.** Shannon combines semantic embeddings with recency decay, trust weighting, tier prioritization, and graph traversal. Raw cosine similarity is just one input.
- **Not a replacement for files.** Code, configs, and structured data belong in git repos and filesystems. Shannon stores the *context around* those things — "why we chose this architecture," "what broke when we tried X," "the customer prefers this approach."

---

## 2. The Problem Shannon Solves

Every AI conversation ends the same way: the context window fills, compression happens, and details disappear. The richer the session, the more painful the loss. Humans have the same problem — tribal knowledge walks out the door when someone leaves.

Shannon makes memory **persistent, searchable, and scoped:**

| Without Shannon | With Shannon |
|----------------|-------------|
| Agent forgets everything each session | Agent loads relevant context from all prior sessions |
| User repeats themselves constantly | Key decisions and preferences are recalled automatically |
| No institutional memory across agents | Multiple agents share a memory backbone (isolated by identity) |
| Context window is the only memory | Semantically indexed, infinitely expandable memory |
| Knowledge trapped in one person's head | Distilled expertise retrievable by anyone with access |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Client (Agent, App, Human, MCP-compatible tool)            │
│                                                             │
│  MCP (stdio)  ──or──  HTTP REST  ──or──  Bearer Token       │
└──────┬──────────────────────┬──────────────────┬────────────┘
       │                      │                  │
       ▼                      ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│  Shannon Memory Service  (FastAPI, port 8765)               │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  REST API    │  │  Retrieval   │  │  Embeddings      │   │
│  │  (api.py)    │  │  Engine      │  │  (mxbai-embed)   │   │
│  │              │  │  3-pass:     │  │  1024-dim        │   │
│  │  /health     │  │  semantic +  │  │  via Ollama      │   │
│  │  /memory     │  │  keyword +   │  │                  │   │
│  │  /agents     │  │  graph       │  │                  │   │
│  │  /rules      │  │              │  │                  │   │
│  │  /distill    │  │              │  │                  │   │
│  │  /tenant/*   │  │              │  │                  │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Storage Layer                                       │   │
│  │  SQLite: index.db (entries, agents, tenants, profiles│   │
│  │  SQLite: embeddings.db (vectors, 1024-dim)           │   │
│  │  Filesystem: chunks/ (raw text, Zeckendorf-addressed)│   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Tenant Isolation                                    │   │
│  │  Bearer tokens → tenant-scoped data                  │   │
│  │  Profile tokens → profile-scoped within tenant       │   │
│  │  Agent tags → agent-scoped (internal use)            │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Key internals:**

- **Zeckendorf-Fibonacci Addressing:** Every entry gets a unique address derived from its content hash via Zeckendorf's theorem (unique sum of non-consecutive Fibonacci numbers). Deterministic, collision-free by mathematical proof.
- **QAM Constellation Encoding:** Visual dot patterns for each entry, inspired by RF QAM modulation. Makes the dictionary human-inspectable.
- **Layered Growth:** Layer 1 provides 2^100 positions. Additional layers compound the address space without re-indexing existing entries.
- **Embedding Model:** `mxbai-embed-large` (1024 dimensions) via Ollama.

---

## 4. What to Store — Your Role Determines Your Data Model

This is the most important section. **Data quality determines retrieval quality.** But "quality" depends entirely on what you're *using Shannon for*. A project-engineering agent and a personal-life agent have completely different storage profiles — and the same raw input (keystrokes, screenshots, emails) might be noise for one and gold for the other.

### The Core Rule

**Always pre-process before saving.** Raw data in, garbage retrievals out. But pre-processing means different things for different use cases:

- A *project agent* distills code decisions, architecture choices, and error resolutions
- A *personal agent* distills daily activities, relationships, schedules, and life context
- A *domain specialist* distills procedures, part numbers, supplier relationships, and field experience
- All users **distill** before storing — but they're distilling different source material

### Storage Profiles by Use Case

#### 🔧 Project / Engineering

Building software, tracking architecture, managing technical infrastructure.

| ✅ Save | Examples |
|---------|----------|
| **Technical decisions** | "Chose FastAPI over Flask because of async support and auto-docs" |
| **Milestones** | "Deployed 7 validators across 3 continents on 2026-02-24" |
| **Lessons learned** | "ANTHROPIC_BASE_URL in bashrc poisoned all API calls — now use proxy-on/proxy-off" |
| **Architecture** | "Shannon uses 3-pass retrieval: semantic → keyword → graph" |
| **Error resolutions** | "VPN split-tunnel issue: NordVPN blocks LAN traffic unless split-tunnel excludes 192.168.0.0/24" |
| **Project context** | "Lattice Network = quantum-safe Byzantine consensus for secure AI internet" |

| ❌ Don't Save | Why |
|-------------|-----|
| Raw code diffs | That's what git is for |
| Build logs verbatim | Ephemeral — save the *fix*, not the error stream |
| Every conversation turn | Save conclusions and decisions, not the back-and-forth |

#### 🏠 Personal Assistant

Managing daily life, personal context, human relationships.

| ✅ Save | Examples | Pre-Processing |
|---------|----------|----------------|
| **Activity patterns** | "Ron worked in Excel on the RMA spreadsheet for 3 hours" | Keystroke/app usage → summarize into activity blocks, don't store raw keystrokes |
| **People & relationships** | "Sarah = wife. Kids: [names]. School pickup at 3:15 PM" | Contacts/conversations → extract relationships, preferences, schedules |
| **Life logistics** | "Early dismissal Thursday, dentist appointment June 25 at 2pm" | Emails/calendar → extract dates, actions, obligations |
| **Receipts & purchases** | "Bought Samsung 860 QVO 1TB from Amazon, $89, for backup drive" | Screenshots/emails → extract what, when, how much |
| **Preferences & habits** | "Drinks coffee black. Prefers window seat. Hates phone calls." | Observed patterns → distilled preferences |
| **Device & environment state** | "Windows PC: C: drive at 31GB free, backup to D: started 2026-06-18" | System snapshots → save when state matters (pre-migration, pre-wipe, diagnostics) |
| **Photo/screenshot context** | "Screenshot of kids' school calendar for fall semester" | OCR/description → save the *meaning*, reference the file by path |
| **Health & wellbeing** | "Mentioned back pain from the desk setup" | Conversations → extract health-relevant observations |
| **Emotional context** | "Frustrated with the Proxmox NIC issue, stayed up until 2am" | Situational awareness → helps calibrate tone and timing |

| ❌ Don't Save | Why |
|-------------|-----|
| **Raw keystroke streams** | "a-s-d-f-space-t-h-e" is noise. "Spent 2 hours writing the patent draft" is signal. |
| **Unprocessed OCR dumps** | A wall of OCR text with layout artifacts is unsearchable. Describe what the screenshot *means*. |
| **Every email verbatim** | Save the action item: "School: early dismissal Thursday." Not the full thread. |
| **Idle/sleep/lock events** | "Screen locked at 3:47pm" is not a memory. "Away from 3-5pm (school pickup)" is. |

#### 🔩 Domain Specialist (Mechanic, Researcher, Tradesperson)

Capturing hard-won expertise, procedures, supplier relationships, field knowledge.

| ✅ Save | Examples | Pre-Processing |
|---------|----------|----------------|
| **Procedures** | "LS3 cam swap: pull radiator first, use 3-jaw puller for balancer, torque to 22 ft-lb" | Field experience → step-by-step distilled procedures |
| **Part cross-references** | "GM 12346789 = LS3 cam sensor, also fits LS2 (09-13). Summit has best price." | Supplier catalogs/lookup → distilled cross-ref entries |
| **Failure modes** | "If LS7 drops cylinder 4, check lifter bore wear first — known issue on pre-2010 blocks" | Diagnostic experience → cause/effect pairs |
| **Supplier relationships** | "Joe at Pacific Performance: fast turnaround, ships same-day if ordered before 2pm PT" | Interactions → distilled contact + behavior notes |
| **Tool notes** | "Snap-on EECS750 catches intermittent misfires that cheaper scanners miss" | Equipment experience → capability notes |
| **Regulatory/spec data** | "EPA allowance for catalyst monitor: 2 trips, 40-65 mph, steady state 3 min" | Manuals/regulations → extracted key parameters |

| ❌ Don't Save | Why |
|-------------|-----|
| Full service manuals | Reference by part number/page — Shannon is for the *context* around specs, not the specs themselves |
| Every diagnostic code dump | Save the *diagnosis*: "P0300 + P0171 together = intake manifold gasket on this engine" |
| Raw sensor data streams | Save the conclusion: "MAF reads 15g/s at idle, should be 5-7 — sensor is bad" |

#### Universal Don'ts (All Users)

| ❌ Never Save | Why |
|-------------|-----|
| **Secrets / credentials** | API keys, passwords, tokens — Shannon is not a secret store |
| **Binary data / base64** | Shannon is text-only. Reference files by path/URL, don't inline them |
| **Duplicate entries** | Search before saving. Don't store the same fact 10 times |
| **Stream-of-consciousness** | "ok" "sure" "hmm" — these aren't memories for anyone |

### The Pre-Processing Rule (Universal)

Regardless of use case, **always distill before storing:**

1. **Would a future session benefit from knowing this?**
2. **Is this the distilled insight, or raw data that needs summarization?**
3. **Does this already exist in Shannon?** (Search first.)
4. **What kind of memory is this?** Tag it properly for tier assignment.

**Source → Distillation → Entry:**

| Source | Raw Data (don't save) | Distilled Entry (save this) |
|--------|----------------------|----------------------------|
| Keystrokes / app focus | `keypress: a, keypress: b...` | "Spent 3 hours in Excel on RMA analysis, 45 min in Teams calls" |
| Email inbox | Full email thread with signatures | "Email from school: early dismissal Thursday. Action: pick up kids at 1pm" |
| Screenshot | Raw OCR text dump | "Receipt: Amazon order #123, Samsung 860 QVO 1TB, $89, arriving June 20" |
| Browsing history | 47 URLs visited | "Researched Proxmox GPU passthrough. Best guide: [url]. Decision: use vfio-pci" |
| Conversations | Full chat transcript | "Agreed with Sarah to book the cabin for July 4th weekend. Budget: $400" |
| System state | CPU 45%, RAM 62%, uptime 3d | "C: drive critically low (31GB free) — started backup to D: before any changes" |
| Social media | Raw notification stream | "Twitter DM from @lattice_dev about API partnership — needs response by Friday" |
| Diagnostic scan | 47 PIDs dumped | "P0300 + P0171 together on 5.3L = intake manifold gasket (2 of 3 confirmed)" |
| Sensor readings | 2000 data points | "MAF reads 15g/s at idle, should be 5-7. Sensor confirmed bad. Replaced." |

---

## 5. API Reference

**Base URL:** Your Shannon instance (e.g., `http://localhost:8765` or `https://shannon.example.com`)

### Authentication

Shannon supports two authentication paths:

| Method | When to Use | How |
|--------|-------------|-----|
| **Agent parameter** | Internal agents on the same network | `?agent=myagent` query parameter |
| **Bearer token** | CaaS tenants, external integrations | `Authorization: Bearer <your-token>` header |

CaaS users receive a token at registration. Use it on every request. Token-authenticated requests are completely isolated from other tenants.

### 5.1 Health Check

```
GET /health

Response:
{
  "status": "ok",
  "version": "2.0",
  "entries": 21514,
  "embeddings": 21353,
  "embedding_coverage": 99.3
}
```

### 5.2 Semantic Retrieve — Load Context (Primary Endpoint)

The **primary retrieval endpoint.** Returns entries ranked by a composite score: semantic similarity × tier weight × recency decay × trust.

```
GET /memory?topic={topic}&limit_tokens={limit}&recency={window}

Headers (CaaS):  Authorization: Bearer <token>
Query (internal): &agent={agent_id}

Parameters:
  topic        (required)  Natural language topic (embedded for semantic search)
  limit_tokens (optional)  Token budget for response (default: 4000)
  recency      (optional)  Time window: "hot" / "warm" / "cold" / "all" (default: "all")

Response:
{
  "topic": "...",
  "entries": [
    {
      "id": "sha256-hex",
      "session_id": "session-label",
      "tags": ["tag1", "tag2"],
      "body": "entry text...",
      "created_at": "ISO-8601",
      "score": 1.05,
      "relevance_score": 0.65,
      "recency_score": 0.89
    }
  ],
  "total_tokens": 2935,
  "scored_count": 94,
  "returned_count": 11
}
```

**Scoring formula (trust-aware):**
```
score = tier_weight × (0.50 × semantic + 0.25 × recency + 0.25 × trust)
```

**Tier weights:**
| Tier | Weight | Auto-assigned from tags |
|------|--------|----------------------|
| 1 (Gold) | 1.5× | skill, decision, architecture, milestone, lesson-learned |
| 2 (Silver) | 1.0× | Default for most entries |
| 3 (Bronze) | 0.5× | youtube, transcript, raw-note |

**Recency decay:** 7-day half-life. Entries lose relevance over time unless they score high on semantics/trust.

**Three-pass retrieval:**
1. **Semantic search** — cosine similarity of embeddings + trust + recency
2. **Keyword search** — key term presence in body text
3. **Graph traversal** — session/tag/time neighbors of top-5 results

Results are merged, deduplicated, and re-ranked.

### 5.3 Keyword Search

```
GET /memory/search?q={query}&limit={limit}

Headers (CaaS):  Authorization: Bearer <token>
Query (internal): &agent={agent_id}

Response:
{
  "results": [...],
  "count": 8,
  "method": "semantic"  // or "keyword" if embeddings unavailable
}
```

### 5.4 Save Memory

```
POST /memory
Content-Type: application/json
Authorization: Bearer <token>    ← CaaS users
                                  ← Internal: use "agent" field instead

{
  "body": "Descriptive text about what happened and why it matters.",
  "agent": "myagent",            ← internal only (CaaS uses Bearer token)
  "tags": ["decision", "architecture"],
  "session_id": "session-2026-06-18",
  "tier": 2
}

Response: {"id": "sha256-hex", "ok": true}
```

**Auto-tier:** If `tier` is omitted or set to 2, Shannon inspects tags and auto-assigns:
- Tags containing `skill`, `decision`, `architecture`, `milestone`, `lesson-learned` → **Tier 1** (boosted)
- Tags containing `youtube`, `transcript`, `raw-note` → **Tier 3** (deprioritized)
- Everything else → **Tier 2** (standard)

**Automatic embedding:** Every saved entry is embedded in the background immediately after write.

### 5.5 List Agents (Internal)

```
GET /agents

Response: {"agents": [{"agent_id": "guy", "entry_count": 14979, ...}]}
```

### 5.6 Regenerate Context File (Internal)

```
POST /context/regenerate

Response: {"ok": true, "path": "...", "elapsed_seconds": 1.23}
```

### 5.7 Embedding Operations (Internal)

```
POST /embeddings/backfill    → Embed all un-embedded entries (background)
GET  /embeddings/stats       → Coverage, model, dimension count
```

### 5.8 Distillation — Pattern Detection

Shannon can scan entries for repeated patterns and distill them into rules:

```
POST /distill?agent=myagent&days=30&dry_run=true

Response:
{
  "ok": true,
  "rules_created": 3,
  "groups_found": 5,
  "rules": [{"rule": "Distilled pattern...", "source_count": 4}]
}
```

```
GET    /rules?agent=myagent    → List distilled rules
DELETE /rules/{entry_id}       → Remove a rule
```

### 5.9 Tier Backfill (Internal)

```
POST /memory/backfill-tiers    → Re-assign tiers to all entries based on tags
```

---

## 6. CaaS Tenant API

These endpoints are for **Shannon-as-a-Service users** who authenticate with Bearer tokens.

### 6.1 Registration

```
POST /tenant/register
Content-Type: application/json

{"email": "you@example.com", "display_name": "Your Name"}

Response:
{
  "tenant_id": "uuid",
  "auth_token": "your-secret-token",
  "message": "Store your auth_token securely — it won't be shown again.",
  "trial_days": 14,
  "note": "No auto-charge. You'll be paused (not deleted) after 14 days."
}
```

**⚠️ Save your auth_token immediately — it is shown exactly once.**

### 6.2 Account Status

```
GET /tenant/status
Authorization: Bearer <token>

Response:
{
  "tenant_id": "uuid",
  "status": "active",
  "trial_days_remaining": 12,
  "entry_count": 47,
  "storage_mb": 0.234
}
```

### 6.3 Knowledge Profiles

Profiles let you organize memories into namespaces. A mechanic might have "LS3 Turbo Builds", "K-Series NA", and "Diagnostic Patterns" as separate profiles. Each profile can have its own access token for sharing.

```
POST /tenant/profiles                     → Create profile
GET  /tenant/profiles                     → List profiles with entry counts
DELETE /tenant/profiles/{id}              → Delete profile + all its entries
POST /tenant/profiles/{id}/token          → Generate profile-scoped access token
```

**Profile-scoped tokens** are powerful: give someone a token for one profile and they can *only* see that profile's data. Nothing else is visible. Cryptographic data separation.

### 6.4 Export

```
GET /tenant/export?topic=TOPIC&limit_tokens=8000&format=markdown
Authorization: Bearer <token>

Response: markdown document (designed to paste into ChatGPT / Claude / Gemini)
```

### 6.5 Source Viewer

```
GET /source/{entry_id}?token=<your-token>

Response: HTML page showing the source material for a specific memory entry
```

### 6.6 Account Management

```
POST /tenant/pause                        → Pause account (data retained 30 days)
POST /tenant/wipe {"confirm": true}       → Permanently delete all data
POST /tenant/disable                      → Kill switch — revoke access immediately
POST /tenant/resolve-conflict             → Resolve conflicting entries
```

### 6.7 Trial Lifecycle

| Phase | Duration | What Happens |
|-------|----------|-------------|
| **Active trial** | 14 days | Full access. No auto-charge ever. |
| **Paused** | 30-day grace | Data retained. API returns 403. Contact us to resume. |
| **Wiped** | After grace | All data permanently deleted. |

---

## 7. MCP Integration

Shannon exposes tools via the Model Context Protocol (MCP) for direct agent integration.

### Available MCP Transports

| Transport | Use Case | Implementation |
|-----------|----------|---------------|
| **Stdio** | Agent on another machine / different OS | `shannon_mcp_server.py` — translates stdio JSON-RPC → HTTP REST |
| **Native** | Agent on same host | `shannon/mcp_server.py` — direct Python function calls, no HTTP |

### MCP Tools (6 tools)

| Tool | Purpose | Maps To |
|------|---------|---------|
| `memory_search` | Keyword/semantic search | `GET /memory/search` |
| `memory_retrieve` | Load context about a topic (primary) | `GET /memory` |
| `memory_save` | Save a memory entry | `POST /memory` |
| `memory_health` | Check service health | `GET /health` |
| `memory_agents` | List all agents | `GET /agents` |
| `memory_context` | Regenerate context file | `POST /context/regenerate` |

### Identity Isolation

**CRITICAL:** MCP tools hardcode the caller's identity. A tool call from Hermes always uses `agent=hermes`. The tool arguments cannot override this. Each agent/tenant has its own isolated memory slice.

---

## 8. Scoring Deep Dive

Shannon doesn't just find matching entries — it ranks them through a multi-factor scoring system.

### Composite Score Formula

```
final_score = tier_weight × (0.50 × semantic_sim + 0.25 × recency + 0.25 × trust)
            + 0.10 × graph_bonus
            + 0.10 × tag_match_bonus
```

### Semantic Similarity (50% weight)
- Cosine similarity between query embedding and entry embedding
- Model: `mxbai-embed-large` (1024 dimensions)
- Range: 0.0 to 1.0

### Recency (25% weight)
- Exponential decay with 7-day (168-hour) half-life
- Formula: `recency = 0.5 ^ (hours_since_creation / 168)`
- Entries containing update keywords ("revised", "corrected", "supersedes") get a recency boost

### Trust (25% weight)
- Based on trust tags on the entry:
  - `verified`, `causal-knowledge`, `distilled-rule` → **1.0**
  - Default (no trust tag) → **0.5**
  - `spurious-correlation`, `no-causation` → **0.1**

### Tier Weight (multiplier)
- **Tier 1 (Gold):** 1.5× — decisions, architecture, skills, milestones
- **Tier 2 (Silver):** 1.0× — standard entries
- **Tier 3 (Bronze):** 0.5× — transcripts, raw notes

### Graph Bonus (+0.10)
- Entries that share session_id, tags, or creation time window with top-scoring results get a boost
- Creates "neighborhood" retrieval — related entries surface together

### Supersession
- If an entry is detected as superseding another (via keywords like "actually", "turns out", "corrected"), the old entry is deprioritized
- Prevents stale/incorrect information from being retrieved

---

## 9. Operational Patterns

### 9.1 Session Start — Load Context

Every session should begin by loading relevant context:

```bash
# CaaS user
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://shannon.example.com/memory?topic=current+project&limit_tokens=4000"

# Internal agent
curl -s "http://localhost:8765/memory?agent=myagent&topic=current+project&limit_tokens=4000"
```

### 9.2 During Session — Save What Matters

When something worth remembering happens:

```bash
curl -s -X POST http://localhost:8765/memory \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "body": "Decided to use stdio MCP transport for Windows integration because SSE was unreliable",
    "tags": ["decision", "architecture", "mcp"],
    "session_id": "session-2026-06-18"
  }'
```

### 9.3 Session End — Save Summary

```bash
curl -s -X POST http://localhost:8765/memory \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "body": "SESSION 2026-06-18: Set up Shannon MCP on Windows. Gmail OAuth configured. 6 MCP tools working.",
    "tags": ["session-summary", "milestone"],
    "session_id": "session-2026-06-18"
  }'
```

### 9.4 Export for Other AI Tools

Export your knowledge as markdown to paste into ChatGPT, Claude, Gemini, etc.:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://shannon.example.com/tenant/export?topic=engine+diagnostics&limit_tokens=8000" \
  -o context.md
```

### 9.5 Distillation (Periodic)

Run distillation to detect repeated patterns and create rules:

```bash
# Preview first
curl -s -X POST "http://localhost:8765/distill?agent=myagent&days=30&dry_run=true"

# If the rules look good, save them
curl -s -X POST "http://localhost:8765/distill?agent=myagent&days=30"
```

---

## 10. Integration Checklist for New Users

### CaaS Users (Bearer Token)

1. **Register:** `POST /tenant/register` with your email
2. **Save your token** — shown once, never again
3. **Create profiles** for different knowledge domains (optional but recommended)
4. **Start saving:** Use `POST /memory` with your Bearer token on every request
5. **Start retrieving:** Use `GET /memory?topic=...` to load context into sessions
6. **Export:** Use `GET /tenant/export` to take your knowledge to other AI tools
7. **Tag consistently** — use meaningful tags for proper tier auto-assignment

### Internal Agents (Agent ID)

1. **Choose an agent ID** — lowercase, descriptive, permanent (e.g., `hermes`, `gitflow`)
2. **Register:** `POST /agents {"agent_id": "myagent"}`
3. **Choose transport:** same machine → native MCP or HTTP; LAN → stdio MCP bridge or HTTP
4. **Hardcode agent identity** — never let tool arguments override the agent ID
5. **Implement session start** — query `/memory` for context at session begin
6. **Implement save pattern** — save decisions, milestones, lessons during sessions
7. **Tag consistently** — use meaningful tags for tier auto-assignment

---

## 11. Tag Taxonomy

Use these tags consistently for proper tier assignment and retrieval quality.

### Tier 1 (Gold) Tags — Always Retrieved First (1.5× boost)
`skill`, `skill-building`, `decision`, `architecture`, `milestone`, `skill-compilation`, `course-to-skill`, `project-setup`, `lesson-learned`

### Tier 2 (Silver) Tags — Standard Priority (1.0×)
Everything not matching Tier 1 or Tier 3.

### Tier 3 (Bronze) Tags — Background/Reference (0.5×)
`youtube`, `transcript`, `raw-note`

### Trust Tags — Affect Scoring Weight
- **High trust (1.0):** `verified`, `causal-knowledge`, `founder`, `distilled-rule`
- **Default (0.5):** No trust tag
- **Low trust (0.1):** `spurious-correlation`, `no-causation`

### Recommended Contextual Tags
`session-summary`, `error-resolution`, `preference`, `relationship`, `infrastructure`, `backup`, `personal`, `project-context`, `procedure`, `diagnostic`, `supplier`, `part-reference`, `schedule`, `health`

---

## 12. Common Mistakes

| Mistake | Consequence | Fix |
|---------|------------|-----|
| Saving raw data streams | Retrieval polluted with noise, real entries buried | Distill first — activity summaries, not keystroke logs |
| Saving raw email threads | Signatures, disclaimers, quoted replies clutter results | Summarize → extract decisions/actions → save summary |
| Saving raw keystroke/event streams | Massive noise, buries real entries | Distill into activity summaries: "3 hours in Excel on RMA" |
| No tags on entries | All entries default to Tier 2, no trust weighting | Always include at least 1-2 descriptive tags |
| Duplicate saves | Same fact appears multiple times, wastes token budget | Search before saving; use "supersedes" language when updating |
| Saving ephemeral state | "It's 3pm" or "CPU at 45%" clutter the index | Only save if it's diagnostic context for a specific issue |
| Overriding agent/tenant ID | Cross-user data contamination | Hardcode identity; never accept it from tool arguments |
| Saving secrets | Credentials exposed in retrievals | Use a proper secret store. Shannon is for context, not keys. |

---

## 13. Infrastructure (Self-Hosted)

For users running their own Shannon instance.

### Service Management

```bash
sudo systemctl start shannon
sudo systemctl stop shannon
sudo systemctl restart shannon
sudo systemctl status shannon
journalctl -u shannon -f
```

### File Locations

| Component | Default Path |
|-----------|-------------|
| Database | `~/.shannon/dictionary/layer_1/index.db` |
| Embeddings | `~/.shannon/dictionary/layer_1/embeddings.db` |
| Chunks | `~/.shannon/dictionary/layer_1/chunks/` |
| Context file | Configured per deployment |

### Requirements

- Python 3.11+
- Ollama with `mxbai-embed-large` model (for embeddings)
- ~200MB disk for base install; grows with entries

---

## 14. Future Roadmap

| Phase | Feature | Status |
|-------|---------|--------|
| 1 (Current) | HTTP REST + MCP + agent isolation + CaaS tenants | ✅ Live |
| 2 | Bearer token auth on all endpoints | ✅ Live |
| 3 | Knowledge profiles + profile-scoped tokens | ✅ Live |
| 4 | Export to markdown (paste into any AI) | ✅ Live |
| 5 | ML-DSA-87 challenge-response (PQC identity) | 🔲 Planned |
| 6 | mTLS / HTTPS transport encryption | 🔲 Planned |
| 7 | Budget metering at MCP layer | 🔲 Planned |
| — | Distillation automation (periodic pattern detection) | 🔲 Planned |
| — | Cross-tenant knowledge sharing (with consent) | 🔲 Planned |

---

*Document Control: Version 2.0 | Shannon Memory Service*
*Maintained at: `~/development/shannon/SHANNON_OPERATIONS_GUIDE.md`*
*Repository: [github.com/SAMLLC-PROD/Shannon](https://github.com/SAMLLC-PROD/Shannon)*
