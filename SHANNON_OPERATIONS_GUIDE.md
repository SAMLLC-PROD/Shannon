# Shannon Memory Service — Operations Guide

**Version:** 1.0
**Date:** 2026-06-18
**Authors:** Ron Peterson & Guy Shannon
**Audience:** Agents (Hermes, Guy, Gitflow, etc.), Humans, and Integrators

---

## 1. What Shannon Is

Shannon is a **persistent long-term memory service** for AI agents and humans. It solves one problem: **AI agents forget everything when a session ends.** Shannon writes memories to disk, embeds them for semantic search, and retrieves them based on meaning and recency — so every new session can start with relevant context from every previous session.

Named for Claude Shannon, father of information theory.

### What Shannon Is NOT

- **Not a general database.** Don't store raw data dumps, screenshots, keystroke logs, or unprocessed streams. Shannon is for *distilled knowledge* — decisions, milestones, lessons, and context that helps an agent or person understand what happened and why.
- **Not a vector-only store.** Shannon combines semantic embeddings with recency decay, trust weighting, tier prioritization, and graph traversal. Raw cosine similarity is just one input.
- **Not a replacement for files.** Code, configs, and structured data belong in git repos and filesystems. Shannon stores the *context around* those things — "why we chose this architecture," "what broke when we tried X," "Ron prefers this approach."

---

## 2. The Problem Shannon Solves

Every AI conversation ends the same way: the context window fills, compression happens, and details disappear. The richer the session, the more painful the loss.

Shannon makes memory **persistent, searchable, and agent-scoped:**

| Without Shannon | With Shannon |
|----------------|-------------|
| Agent forgets everything each session | Agent loads relevant context from all prior sessions |
| User repeats themselves constantly | Key decisions and preferences are recalled automatically |
| No institutional memory across agents | Multiple agents share a memory backbone (isolated by agent ID) |
| Context window is the only memory | 21K+ entries, semantically indexed, infinitely expandable |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Agent / Client                                             │
│  (Guy, Hermes, Gitflow, any MCP/HTTP client)                │
│                                                             │
│  MCP (stdio)  ──or──  HTTP REST                             │
└──────┬──────────────────────┬───────────────────────────────┘
       │                      │
       ▼                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Shannon Memory Service  (FastAPI, port 8765)               │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  REST API    │  │  Retrieval   │  │  Embeddings      │   │
│  │  (api.py)    │  │  Engine      │  │  (mxbai-embed)   │   │
│  │              │  │  3-pass:     │  │  via Ollama      │   │
│  │  /health     │  │  semantic +  │  │  1024-dim        │   │
│  │  /memory     │  │  keyword +   │  │                  │   │
│  │  /agents     │  │  graph       │  │                  │   │
│  │  /rules      │  │              │  │                  │   │
│  │  /distill    │  │              │  │                  │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Storage Layer                                       │   │
│  │  SQLite: index.db (entries, agents, sessions)        │   │
│  │  SQLite: embeddings.db (vectors, 1024-dim)           │   │
│  │  Filesystem: chunks/ (raw text, Zeckendorf-addressed)│   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Key internals:**

- **Zeckendorf-Fibonacci Addressing:** Every entry gets a unique address derived from its content hash via Zeckendorf's theorem (unique sum of non-consecutive Fibonacci numbers). Deterministic, collision-free by mathematical proof.
- **QAM Constellation Encoding:** Visual dot patterns for each entry, inspired by RF QAM modulation. Makes the dictionary human-inspectable.
- **Layered Growth:** Layer 1 provides 2^100 positions. Additional layers compound the address space without re-indexing existing entries.
- **Embedding Model:** `mxbai-embed-large` (1024 dimensions) via local Ollama.

---

## 4. What to Store (and What NOT to Store)

This is the most important section. **Data quality determines retrieval quality.** Garbage in = garbage out, and it's worse than nothing because bad data pollutes good retrievals.

### ✅ SAVE These

| Category | Examples | Why It Matters |
|----------|----------|---------------|
| **Decisions** | "Chose FastAPI over Flask because of async support and auto-docs" | Future sessions understand *why* the codebase looks the way it does |
| **Milestones** | "Deployed 7 validators across 3 continents on 2026-02-24" | Timeline reconstruction, progress tracking |
| **Lessons Learned** | "ANTHROPIC_BASE_URL in bashrc poisoned all API calls — now use proxy-on/proxy-off" | Prevents repeating mistakes |
| **Architecture** | "Shannon uses 3-pass retrieval: semantic → keyword → graph" | Any agent can understand system design |
| **Preferences** | "Ron prefers explanations of WHY, not just the fix" | Consistent interaction style |
| **Key Relationships** | "Ron's email: spaceautomationmachinesLLC@gmail.com (work)" | Practical operational context |
| **Project Context** | "Lattice Network = quantum-safe Byzantine consensus for secure AI internet" | Any agent can orient itself |
| **Error Resolutions** | "VPN split-tunnel issue: NordVPN blocks LAN traffic to 192.168.0.0/24 unless excluded" | Operational troubleshooting knowledge |
| **Meeting Notes** (summarized) | "2026-06-15 standup: agreed to prioritize Pigeon Mail over Search" | Decision trail |

### ❌ DO NOT SAVE These

| Anti-pattern | Why It's Harmful |
|-------------|-----------------|
| **Raw keystroke logs** | Massive volume, zero signal. Drowns meaningful entries in noise. Destroys retrieval precision. |
| **Unprocessed screenshots** | Shannon stores text, not images. OCR dumps without summarization are noise. |
| **Every email verbatim** | Save the *decision* or *action item* from an email, not the full thread with signatures and disclaimers. |
| **Stream-of-consciousness chat** | "ok" "sure" "hmm" "let me think" — these are not memories. |
| **Duplicate entries** | Shannon has basic dedup, but don't save the same fact 10 times. Check first. |
| **Highly ephemeral state** | "CPU is at 45% right now" — unless it's diagnostic context for a problem you're solving. |
| **Secrets / credentials** | API keys, passwords, tokens. Shannon is not a secret store. |
| **Binary data / base64** | Shannon is text-only. Reference files by path, don't inline them. |

### The Pre-Processing Rule

**Before saving anything to Shannon, ask:**
1. Would a future session benefit from knowing this?
2. Is this the *distilled insight*, or raw data that needs summarization first?
3. Does this already exist in Shannon? (Search first.)
4. Is this a *decision*, *lesson*, *milestone*, or *relationship* — or is it noise?

If you're ingesting external sources (emails, documents, social media), **summarize and extract** before saving:
- Emails → extract decisions, action items, key facts
- Documents → extract architecture, key points, relationships
- Screenshots → describe what's relevant in text
- Conversations → save the conclusions, not the back-and-forth

---

## 5. API Reference

**Base URL:** `http://localhost:8765` (local) or `http://192.168.0.68:8765` (LAN)

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

### 5.2 Semantic Retrieve — Load Context

The **primary retrieval endpoint.** Returns entries ranked by a composite score: semantic similarity × tier weight × recency decay × trust.

```
GET /memory?agent={agent}&topic={topic}&limit_tokens={limit}&recency={window}

Parameters:
  agent        (required)  Agent ID — scopes retrieval to entries tagged with this agent
  topic        (required)  Natural language topic (embedded for semantic search)
  limit_tokens (optional)  Token budget for response (default: 4000)
  recency      (optional)  Time window: "hot" / "warm" / "cold" / "all" (default: "all")

Response:
{
  "agent": "hermes",
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

**Recency decay:** 7-day half-life (entries lose relevance over time unless they score high on semantics/trust).

**Three-pass retrieval:**
1. **Semantic search** — cosine similarity of embeddings + trust + recency
2. **Keyword search** — key term presence in body text
3. **Graph traversal** — session/tag/time neighbors of top-5 results

Results are merged, deduplicated, and re-ranked.

### 5.3 Keyword Search

```
GET /memory/search?q={query}&agent={agent}&limit={limit}

Parameters:
  q      (required)  Search query
  agent  (optional)  Filter to specific agent
  limit  (optional)  Max results (default 10, max 50)

Response:
{
  "results": [...],
  "count": 8,
  "method": "semantic"  // or "keyword" if embeddings unavailable
}
```

Uses semantic search with tier-weighted scoring when embeddings are available; falls back to keyword matching.

### 5.4 Save Memory

```
POST /memory
Content-Type: application/json

{
  "body": "Descriptive text about what happened and why it matters.",
  "agent": "hermes",
  "tags": ["decision", "architecture"],
  "session_id": "hermes-2026-06-18",
  "tier": 2
}

Response: {"id": "sha256-hex", "ok": true}
```

**Auto-tier:** If `tier` is omitted or set to 2, Shannon inspects tags and auto-assigns:
- Tags containing `skill`, `decision`, `architecture`, `milestone`, `lesson-learned` → **Tier 1** (boosted in retrieval)
- Tags containing `youtube`, `transcript`, `raw-note` → **Tier 3** (deprioritized)
- Everything else → **Tier 2** (standard)

**Agent isolation:** The `agent` field is appended to tags automatically. Entries are scoped to their agent — Hermes cannot read Guy's entries via the agent API path.

**Automatic embedding:** Every saved entry is embedded in the background immediately after write.

### 5.5 List Agents

```
GET /agents

Response:
{
  "agents": [
    {"agent_id": "guy", "display_name": "guy", "entry_count": 14979, ...},
    {"agent_id": "hermes", "display_name": "hermes", "entry_count": 8, ...}
  ]
}
```

### 5.6 Register Agent

```
POST /agents
Content-Type: application/json

{"agent_id": "hermes", "display_name": "Hermes", "tag_profile": ["backup", "personal"]}
```

### 5.7 Regenerate Context File

```
POST /context/regenerate

Response: {"ok": true, "path": "...", "elapsed_seconds": 1.23}
```

Rewrites `memory/shannon-context.md` from recent entries using time-tiered compression. Useful as a flat-file fallback when Shannon is down.

### 5.8 Embedding Operations

```
POST /embeddings/backfill    → Embed all un-embedded entries (background task)
GET  /embeddings/stats       → {"total_entries": 21514, "embedded": 21353, "coverage": 99.3, "model": "mxbai-embed-large", "dimensions": 1024}
```

### 5.9 Distillation (Pattern Detection)

Shannon can scan an agent's entries for repeated patterns and distill them into rules:

```
POST /distill?agent=hermes&days=30&dry_run=true

Response:
{
  "ok": true,
  "rules_created": 3,
  "groups_found": 5,
  "rules": [
    {"rule": "Distilled pattern text...", "source_count": 4, "dry_run": true}
  ]
}
```

```
GET  /rules?agent=hermes       → List distilled rules
DELETE /rules/{entry_id}       → Remove a rule
```

**Trust tags for rules:**
- `verified`, `causal-knowledge`, `distilled-rule` → **trust weight 1.0** (always surfaced)
- `spurious-correlation`, `no-causation` → **trust weight 0.1** (suppressed)
- Default → **trust weight 0.5**

### 5.10 Tier Backfill

```
POST /memory/backfill-tiers    → Re-assign tiers to all entries based on tags
```

---

## 6. MCP Integration

Shannon exposes tools via the Model Context Protocol (MCP) for direct agent integration.

### Available MCP Transports

| Transport | Use Case | Implementation |
|-----------|----------|---------------|
| **Stdio** | Local agent on same machine or LAN (Hermes on Windows) | `shannon_mcp_server.py` — translates stdio JSON-RPC → HTTP REST |
| **Native** | Agent on same Linux host (OpenClaw/Guy) | `shannon/mcp_server.py` — direct Python function calls, no HTTP |

### MCP Tools (6 tools)

| Tool | Purpose | Maps To |
|------|---------|---------|
| `memory_search` | Keyword/semantic search | `GET /memory/search` |
| `memory_retrieve` | Load context about a topic (primary) | `GET /memory` |
| `memory_save` | Save a memory entry | `POST /memory` |
| `memory_health` | Check service health | `GET /health` |
| `memory_agents` | List all agents | `GET /agents` |
| `memory_context` | Regenerate context file | `POST /context/regenerate` |

### Agent Identity Isolation

**CRITICAL:** All MCP tools hardcode the agent identity. A tool call from Hermes always uses `agent=hermes`. The tool arguments cannot override this. Each agent has its own isolated memory slice.

---

## 7. Multi-Tenant Support

Shannon supports external tenants via Bearer token authentication:

- `Authorization: Bearer <token>` in HTTP headers
- Tenant data is fully isolated from internal agent data
- Profile-scoped tokens restrict reads/writes to a specific profile within a tenant
- Trial lifecycle: 7-day trial → pause → wipe after 30 days
- Tenant operations: register, authenticate, pause, wipe, revoke token

This is for future SaaS use. Internal agents (Guy, Hermes, Gitflow) use the agent parameter path, not Bearer tokens.

---

## 8. Operational Patterns

### 8.1 Session Start (Agent Bootstrap)

Every agent session should begin by loading context:

```bash
# 1. Check health
curl -s http://localhost:8765/health

# 2. Load relevant context (replace TOPIC with current work)
curl -s "http://localhost:8765/memory?agent=hermes&topic=current+project+context&limit_tokens=4000"
```

### 8.2 During Session (Save Decisions)

When something worth remembering happens:

```bash
curl -s -X POST http://localhost:8765/memory \
  -H "Content-Type: application/json" \
  -d '{
    "body": "Decided to use stdio MCP transport for Windows integration because SSE was unreliable in Hermes environment",
    "agent": "hermes",
    "tags": ["decision", "architecture", "mcp"],
    "session_id": "hermes-2026-06-18"
  }'
```

### 8.3 Session End (Save Summary)

```bash
curl -s -X POST http://localhost:8765/memory \
  -H "Content-Type: application/json" \
  -d '{
    "body": "SESSION 2026-06-18: Set up Shannon MCP stdio server on Windows. Gmail OAuth configured. 6 MCP tools working. Backup to D: drive started (57GB of 114GB copied).",
    "agent": "hermes",
    "tags": ["session-summary", "milestone"],
    "session_id": "hermes-2026-06-18"
  }'
```

### 8.4 Context Regeneration

```bash
curl -s -X POST http://localhost:8765/context/regenerate
```

This creates a flat-file summary at `memory/shannon-context.md` for use when Shannon is down.

### 8.5 Distillation (Periodic)

Run distillation to detect repeated patterns and create rules:

```bash
# Preview first
curl -s -X POST "http://localhost:8765/distill?agent=hermes&days=30&dry_run=true"

# If the rules look good, save them
curl -s -X POST "http://localhost:8765/distill?agent=hermes&days=30"
```

---

## 9. Scoring Deep Dive

Shannon doesn't just find matching entries — it ranks them through a multi-factor scoring system:

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
- An entry from 7 days ago gets 0.5; from 14 days ago gets 0.25
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
- If an entry is detected as superseding another (via keywords like "actually", "turns out", "corrected"), the old entry is marked superseded and deprioritized
- Prevents stale/incorrect information from being retrieved

---

## 10. Infrastructure

### Current Deployment

| Component | Location | Details |
|-----------|----------|---------|
| Shannon Service | `192.168.0.68:8765` (Linux workstation) | systemd: `shannon.service`, user: `ron` |
| Database | `~/.shannon/dictionary/layer_1/index.db` | SQLite, ~21K entries |
| Embeddings | `~/.shannon/dictionary/layer_1/embeddings.db` | SQLite, 1024-dim vectors |
| Chunks | `~/.shannon/dictionary/layer_1/chunks/` | Raw text files, Zeckendorf-addressed |
| Embedding Model | Ollama on port 11434 | `mxbai-embed-large` (1024 dimensions) |
| Context File | `~/.openclaw/workspace/memory/shannon-context.md` | Flat-file fallback |

### Service Management

```bash
# Start/stop/restart
sudo systemctl start shannon
sudo systemctl stop shannon
sudo systemctl restart shannon
sudo systemctl status shannon

# Logs
journalctl -u shannon -f
```

### Networking

- **LAN access:** Any device on `192.168.0.0/24` can reach Shannon at port 8765
- **No TLS (Phase 1):** HTTP only. Auth tokens are for tenant isolation, not transport security.
- **VPN note:** NordVPN may block LAN traffic — ensure split-tunnel allows `192.168.0.0/24`

---

## 11. Connected Agents

| Agent | Entry Count | Purpose |
|-------|-------------|---------|
| `guy` | ~15,000 | Primary agent (Ron's main AI partner). Architecture, decisions, milestones. |
| `hermes` | 8 (growing) | Windows agent. Personal context, backup ops, daily life management. |
| `gitflow` | ~200 | OODA loop substrate development context. |
| `benchmark` | ~1,100 | Benchmark test entries from stress testing. |

---

## 12. Integration Checklist for New Agents

When connecting a new agent to Shannon:

1. **Choose an agent ID** — lowercase, descriptive, permanent (e.g., `hermes`, `gitflow`)
2. **Register the agent:** `POST /agents {"agent_id": "myagent"}`
3. **Choose transport:**
   - Same machine → native MCP (`mcp_server.py`) or direct HTTP
   - LAN → HTTP REST or stdio MCP bridge
4. **Hardcode agent identity** — never let tool arguments override the agent ID
5. **Implement session start** — query `/memory` for context at session begin
6. **Implement save pattern** — save decisions, milestones, lessons during sessions
7. **Tag consistently** — use meaningful tags (see Section 4) for tier auto-assignment
8. **Test the cycle:** save → retrieve → verify the entry surfaces correctly

---

## 13. Hermes-Specific: Windows Setup (Quick Reference)

For the complete step-by-step, see `HERMES_SHANNON_MCP_DUPLICATE_WORKFLOW.md`.

**Summary:**
1. Gmail OAuth setup (for email-based spec delivery)
2. Build `shannon_mcp_server.py` (stdio, stdlib-only, Hermes venv Python)
3. Configure `config.yaml` with MCP server entry
4. Verify: `hermes mcp test shannon-memory`

**Config entry:**
```yaml
mcp_servers:
  shannon-memory:
    command: "C:\\Users\\ronpe\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\python.exe"
    args: ["-u", "C:\\Users\\ronpe\\shannon-mcp\\shannon_mcp_server.py"]
    env:
      SHANNON_URL: "http://192.168.0.68:8765"
      SHANNON_AGENT: "hermes"
    enabled: true
```

---

## 14. Future Roadmap

| Phase | Feature | Status |
|-------|---------|--------|
| 1 (Current) | HTTP REST + MCP stdio + agent isolation | ✅ Live |
| 2 | Bearer token authentication for all endpoints | 🔲 Planned |
| 3 | ML-DSA-87 challenge-response (PQC identity) | 🔲 Planned |
| 4 | mTLS / HTTPS transport encryption | 🔲 Planned |
| 5 | Budget metering at MCP layer | 🔲 Planned |
| — | Distillation automation (periodic pattern detection) | 🔲 Planned |
| — | Cross-agent knowledge sharing (with consent) | 🔲 Planned |
| — | Lattice Network integration (distributed addressing) | 🔲 Planned |

---

## Appendix A: Tag Taxonomy

Use these tags consistently for proper tier assignment and retrieval quality:

### Tier 1 (Gold) Tags — Always Retrieved First
`skill`, `skill-building`, `decision`, `architecture`, `milestone`, `skill-compilation`, `course-to-skill`, `claude-drop`, `project-setup`, `lesson-learned`

### Tier 3 (Bronze) Tags — Deprioritized
`youtube`, `transcript`, `raw-note`

### Trust Tags — Affect Scoring Weight
- **High trust:** `verified`, `causal-knowledge`, `founder`, `distilled-rule`
- **Low trust:** `spurious-correlation`, `no-causation`

### Recommended Contextual Tags
`session-summary`, `error-resolution`, `preference`, `relationship`, `infrastructure`, `backup`, `personal`, `project-context`

---

## Appendix B: Common Mistakes

| Mistake | Consequence | Fix |
|---------|------------|-----|
| Saving raw email threads | Retrieval polluted with signatures, disclaimers, quoted replies | Summarize → extract decisions/actions → save summary |
| Saving keystroke logs | Massive noise, buries real entries | Never. Not even summarized. |
| No tags on entries | All entries default to Tier 2, no trust weighting | Always include at least agent tag + 1-2 descriptive tags |
| Duplicate saves | Same fact appears multiple times, wastes token budget on retrieval | Search before saving; if updating, use "supersedes" language |
| Saving ephemeral state | "It's 3pm" or "CPU at 45%" clutter the index | Only save if it's diagnostic context for a problem resolution |
| Overriding agent ID | Cross-agent contamination | Hardcode agent ID in MCP server; never accept it from tool args |

---

*Document Control: Version 1.0 | Classification: SAMLLC Internal*
*Maintained at: `~/development/shannon/SHANNON_OPERATIONS_GUIDE.md`*
