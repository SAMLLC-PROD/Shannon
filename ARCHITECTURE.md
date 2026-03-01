# Shannon — Architecture

## Core Concepts

### 1. The Dictionary

Shannon maintains a persistent dictionary on local storage (Seagate/Samsung drives).
Each entry maps a semantic chunk of context to a unique, content-derived address.

The dictionary is **append-only**. Nothing is ever deleted or moved.
Old context is always retrievable. The address space never runs out.

### 2. Zeckendorf-Fibonacci Addressing

**Zeckendorf's Theorem**: Every positive integer N has a unique representation
as a sum of non-consecutive Fibonacci numbers.

Shannon uses this to derive a collision-free address from any data:

```
data → SHA-256 hash → large integer N → Zeckendorf(N) → address
```

The Zeckendorf representation serves as a canonical, distributed address.
No central registry. No coordination. Any node derives the same address independently.

### 3. QAM Dot Pattern Encoding

Each dictionary address has a corresponding visual representation as a
dot pattern in 2D constellation space — inspired by QAM modulation.

Layer 1: Base constellation (2^100 positions)
Layer N: Increased dot density, compounding address space

The visual encoding is compact, human-inspectable, and renderable.
It allows the dictionary to be visualized as a growing 2D (or higher-D) map.

### 4. The Tesseract (Layered Growth)

The dictionary grows in layers without architectural change:

- Layer 1: 2^100 unique addresses
- Layer 2: (2^100)^2 — triggered when Layer 1 approaches saturation
- Layer N: Compounds indefinitely

"Tessearct" because the growth is dimensional — each layer adds a dimension
to the address space rather than simply extending a flat list.

---

## Data Flow

### Write Path (on context compression)
```
Session context chunk
  → Semantic extraction (strip noise, keep signal)
  → SHA-256 hash
  → Zeckendorf decomposition → address
  → QAM pattern assignment
  → Write to local dictionary store
  → Log address to session index
```

### Read Path (on session start or query)
```
Session init or query term
  → Load session index (recent address ranges)
  → Retrieve entries from local dictionary
  → Inject into context window (ranked by relevance/recency)
```

---

## Storage Layout

```
~/.shannon/
  dictionary/
    layer_1/
      index.db          # SQLite: address → metadata
      chunks/           # Raw compressed context chunks
        {address}.zst   # Zstandard compressed
    layer_2/            # Created when layer_1 saturates
  sessions/
    {date}/
      {session_id}.idx  # Addresses written this session
  config.json
```

---

## Integration Points

### OpenClaw (primary)
- Hook into context compression event → write to Shannon
- Hook into session init → load relevant Shannon context
- Query interface: `shannon.search(query)` → ranked chunks

### Lattice Network (future)
- Zeckendorf addresses are deterministic → distributed dictionary validation
- Byzantine consensus on dictionary entries (5-of-7)
- Shared dictionary across validator nodes

---

## Open Questions

1. **Semantic chunking**: What's the right granularity? Sentence? Paragraph? Topic?
2. **Relevance ranking**: How to decide which chunks to inject at session start?
3. **QAM layer threshold**: At what fill % do we step up to Layer 2?
4. **Compression**: zstd for chunks? Or encode semantics directly in the Fibonacci address?
