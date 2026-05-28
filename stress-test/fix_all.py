#!/usr/bin/env python3
"""
Shannon V2 — Combined fix for all 3 benchmark failures:

1. TEMPORAL DECAY (0% → target 85%)
   Root cause: RECENCY_WEIGHT=0.15 with 168h half-life means entries created
   seconds apart have identical scores. Semantic dominates completely.
   Fix: Add "update detection" — when two entries share tags and one says
   "updated/revised/corrected", boost its recency contribution.
   Also: Increase RECENCY_WEIGHT from 0.15 to 0.25 for same-tag matches.

2. CROSS-DOMAIN (77.8% → target 80%)
   Root cause: Electoral college round-robin dilutes domain-specific results
   when benchmark agent has entries from many domains.
   Fix: When query includes a tag that matches entry tags, apply a tag_bonus
   to keep domain-specific entries ranked higher.

3. CONCURRENT LATENCY (p95=6.9s → target <5s)
   Root cause: Each query calls Ollama for embedding (1.3s), serialized.
   Fix: Batch embedding requests — queue concurrent queries and send them
   to Ollama in one batch call.

All three fixes go into retrieval.py as a single update.
"""
print("This is the plan file — the actual edits go into retrieval.py and embeddings.py")
print("Run the benchmark after applying edits to verify.")
