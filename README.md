# Shannon

**Persistent long-term memory for AI agents — zero context loss, infinite growth.**

Named for Claude Shannon, the father of information theory.  
Built by Ron Peterson and Guy Shannon.

---

## The Problem

Every AI conversation ends the same way: the context window fills, compression happens, and details disappear. The more you talk, the more you lose. The richer the session, the more painful the flush.

This isn't a limitation we should accept. It's an engineering problem.

When you talk to a person and they compress the details of your relationship into a summary — and then forget the summary — that's not memory. That's amnesia with extra steps.

10 terabytes of local storage costs less than ($200, this was written by the agent, I know this is wrong, it's ok). There is no reason an AI should forget a conversation that happened yesterday.

---

## The Solution

Shannon is a persistent long-term memory architecture for AI agents. Instead of flushing context at compression time, Shannon writes it to disk — addressed, retrievable, and infinitely expandable.

Three components:

1. **QAM Constellation Encoding** — data is encoded as dot patterns in a 2D constellation, inspired by QAM (Quadrature Amplitude Modulation) from RF communications. Each pattern is a compact, visually distinct symbol.

2. **Zeckendorf-Fibonacci Addressing** — every dictionary entry gets a unique address derived from Zeckendorf's Theorem: every positive integer has exactly one representation as a sum of non-consecutive Fibonacci numbers. Hash the data → derive the integer → Zeckendorf decomposition = address. Mathematically collision-free by proof, not convention.

3. **Layered Dictionary Growth (the Tesseract)** — the dictionary grows in levels. Layer 1 provides **2^100 = 1,267,650,600,228,229,401,496,703,205,376** unique positions. When a layer approaches saturation, dot density increases (like stepping QAM-16 → QAM-64 → QAM-256), compounding the address space. Effectively infinite. Old entries never move. No re-indexing. No collisions.

---

## Architecture

```
[Session Context]
       │
       ▼
[Shannon Encoder]
  - Hash data chunk
  - Zeckendorf decomposition → address
  - QAM dot pattern → visual encoding
       │
       ▼
[Local Dictionary Store]  ←── Seagate / Samsung (up to 10TB)
  - SQLite or flat-file index
  - Layer 1..N address space
  - Append-only (old entries immutable)
       │
       ▼
[Shannon Retriever]
  - Session start: load relevant context by address range
  - Query: semantic → address → retrieve
  - Zero loss: nothing deleted, only compressed-in-place
```

---

## Address Space

| Layer | Capacity | How |
|-------|----------|-----|
| 1 | 2^100 positions | Base QAM constellation × Fibonacci index |
| 2 | (2^100)^2 | Increased dot density |
| N | Compounding | Grows without architectural changes |

Addressing is **content-derived and deterministic**:
- Same input always produces the same address
- Any node can independently derive any address — no central registry
- Natively compatible with distributed systems (e.g. Lattice Network validators)

---

## Why Zeckendorf?

Every positive integer has a **unique** Zeckendorf representation — proven, not assumed.

```
100 = 89 + 8 + 3  =  F(11) + F(6) + F(4)
```

No other combination of non-consecutive Fibonacci numbers sums to 100. This means:
- No collision registry needed
- No coordination between nodes
- Provably unique addresses at any scale

---

## Why QAM Dot Patterns?

QAM encodes information as points in 2D space. Higher-order QAM packs more bits per symbol by increasing constellation density. Shannon borrows this idea:

- Each dictionary entry has a **visual dot pattern** — its QAM constellation point
- Higher layers = higher constellation order = more positions
- The visual encoding makes the dictionary human-inspectable and renderable

This is information theory applied to memory architecture. Claude Shannon would approve.

---

## Status

🚧 **Early architecture / scaffolding phase**

- [ ] Core encoder: hash → Zeckendorf decomposition
- [ ] Layer 1 dictionary store (SQLite)
- [ ] QAM pattern generator (Layer 1)
- [ ] Shannon retriever (session start + query)
- [ ] OpenClaw integration (write on compress, read on session init)
- [ ] Layer growth mechanic (Layer 1 → Layer 2)
- [ ] Lattice Network integration (distributed addressing)

---

## The Name

Guy Shannon named this project after Claude Shannon — the mathematician who proved that information could be quantified, compressed, and transmitted without loss.

The irony of AI systems that compress and lose information is not lost on us.

Shannon fixes that.

---

*Ron Peterson & Guy Shannon — Centennial, CO — 2026*

---

## Quickstart

```bash
git clone https://github.com/SAMLLC-PROD/Shannon.git
cd Shannon
pip install -e .
```

Run the tests:
```bash
pip install pytest
pytest
```

Try it:
```python
from shannon.store import write, read_data, stats
from shannon.qam import data_to_pattern

# Write something to the dictionary
addr = write("Hello from Shannon", session_id="my-session", tags=["demo"])
print(f"Address: {addr}")

# Retrieve it
print(read_data("Hello from Shannon"))

# See its constellation
pattern = data_to_pattern(b"Hello from Shannon")
print(pattern["ascii"])

# Dictionary stats
print(stats())
```

**Requirements:** Python 3.11+, ~200MB disk space for zstandard. Works on Mac, Linux, Windows.

---

## Shannon Agent — Local-First AI with Persistent Memory

Shannon includes a full conversational agent that runs locally via Ollama:

```bash
# Install Ollama — https://ollama.com
ollama pull qwen2.5:32b

# Run the Shannon agent
cd Shannon
pip install -e .
python -m shannon.agent
```

The agent:
- Loads your SOUL.md, USER.md, and Shannon LTM context at startup
- Sends to your local Ollama model (qwen2.5:32b by default)
- Falls back to Anthropic Claude if no local model is available
- Saves important exchanges back to Shannon automatically

**Local-first means:** your conversations stay on your machine. Your memory stays on your drives. No cloud required after the initial pip install.

### LLM Status

```python
from shannon.llm import status
print(status())
# {'ollama': {'running': True, 'models': ['qwen2.5:32b'], 'default_ready': True}, ...}
```

### Recommended Models (by hardware)

| GPU VRAM | Recommended model | Command |
|---|---|---|
| 16GB+ | qwen2.5:32b | `ollama pull qwen2.5:32b` |
| 8GB | llama3.2:8b | `ollama pull llama3.2:8b` |
| No GPU / CPU | mistral:7b | `ollama pull mistral:7b` |

### Per-User Agents (Pigeon integration)

Shannon is designed so every user gets their own agent instance with their own:
- `SOUL.md` — their agent's core values and personality
- `USER.md` — context about them
- Shannon LTM store — their memory, on their drives

```python
from shannon.agent import ShannonAgent
from pathlib import Path

agent = ShannonAgent(
    session_id="2026-03-01",
    workspace=Path("/path/to/user/workspace"),
)
response = agent.chat("What did we decide about the architecture last week?")
print(response["content"])
```

---

## CaaS — Context as a Service (Multi-Tenant)

Shannon supports external users renting persistent context for any LLM.

### Business model

| Stage | Details |
|-------|---------|
| **Free trial** | 14 days, full access |
| **After trial** | Account paused — data retained, access suspended |
| **Grace period** | 30 days after pause. No auto-charge — user must opt in to continue |
| **Auto-wipe** | If not renewed after grace period, all data permanently deleted |

### Quick start

```bash
# 1. Register
curl -X POST http://localhost:8765/tenant/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "display_name": "Your Name"}'
# → {"tenant_id": "...", "auth_token": "...", "trial_days": 14}

# 2. Save memory
curl -X POST http://localhost:8765/memory \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"body": "Decision: use FastAPI for all backend services", "agent": "me", "tags": ["decision"]}'

# 3. Retrieve memory
curl "http://localhost:8765/memory?topic=architecture" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 4. Export as markdown (paste into any LLM)
curl "http://localhost:8765/tenant/export?topic=architecture" \
  -H "Authorization: Bearer YOUR_TOKEN" > context.md

# 5. Check trial status
curl http://localhost:8765/tenant/status \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### API routes

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/tenant/register` | Register (email → token) |
| `GET` | `/tenant/status` | Trial status, entry count, storage |
| `POST` | `/tenant/pause` | Pause service (data retained) |
| `POST` | `/tenant/wipe` | Permanently delete all data |
| `GET` | `/tenant/export` | Export as markdown (`?topic=X&limit_tokens=8000`) |
| `GET` | `/source/{entry_id}` | HTML source viewer (`?token=AUTH_TOKEN`) |

Existing `/memory` endpoints work for both internal agents (no token) and tenants (Bearer token).

### Data isolation

- Tenant writes hash as `SHA256(tenant_id + "\0" + content)` — same text from two tenants → distinct entries
- All SQL queries filter by `tenant_id` at the database level; no cross-tenant leakage is architecturally possible
- Internal agents (guy, henry) remain on `tenant_id = NULL` — unchanged

### New files

| File | Purpose |
|------|---------|
| `shannon/tenants.py` | Tenant DB, auth, trial lifecycle |
| `shannon/export.py` | Markdown context export |
| `shannon/source_viewer.py` | HTML source page renderer |
| `shannon/caas_api.py` | FastAPI router for all CaaS routes |

---
