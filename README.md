# Shannon

**Persistent long-term memory for AI agents — zero context loss, infinite growth.**

Named for Claude Shannon, the father of information theory.  
Built by Ron Peterson and Guy Shannon.

---

## The Problem

Every AI conversation ends the same way: the context window fills, compression happens, and details disappear. The more you talk, the more you lose. The richer the session, the more painful the flush.

This isn't a limitation we should accept. It's an engineering problem.

When you talk to a person and they compress the details of your relationship into a summary — and then forget the summary — that's not memory. That's amnesia with extra steps.

10 terabytes of local storage costs less than $200. There is no reason an AI should forget a conversation that happened yesterday.

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
