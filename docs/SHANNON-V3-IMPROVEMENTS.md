# Shannon V3 — improvement backlog

Started 2026-10-21. Source: CE fsb-foundation-r2, trade study TS-06 (metadata and vector store).

## Why this exists

TS-06 asked which store should hold the family scrapbook's photo metadata, grants and search vectors.
The package listed "Shannon's own index, if WP-00 shows it fits" as one candidate. The study screened it
out before scoring because the condition was not met (conflict C-3, ISSU-FSB-P4-TS06-CONFLICTS).
Ron confirmed the removal on 2026-10-21 and asked that the gaps become V3 targets.
The pick was PostgreSQL + pgvector (DEC-FSB-FOUNDATION-R2-TS06-SELECTION).

The four gaps below are what Shannon would need to be a real candidate next time.

## Gaps → V3 targets

### 1. Vector query with in-query filtering
- **Gap:** ICD-SHN lists no vector query API and no filter that runs inside the search.
- **Why it matters:** an access check applied after the search leaks counts and ranking, and wastes results.
  The scrapbook needs "similar to this, and this person may see it" in one query (C4-010 at C4-009 speed).
- **V3:** a documented vector query endpoint with a predicate (tenant, zone, grant set, tags) applied during
  the nearest-neighbour search, not after it.

### 2. Media zones
- **Gap:** media zones (Q13) are not built. Shannon stores text memories, not media assets and their embeddings.
- **V3:** a zone type for media: asset reference, embedding(s) per model, metadata, no raw bytes required in Shannon.

### 3. Remote client authentication
- **Gap:** remote client sign-in is still open (Q2). Shannon is localhost-first.
- **V3:** an authenticated remote client path (per-client tokens or device enroll; tie to CLIENT_ADMISSION P0 /
  ML-DSA when that lands). Must work from another host on the LAN without exposing the guy lane.

### 4. Vault isolation
- **Gap:** isolation of a family vault from other Shannon data is not confirmed (Q8).
  Tenants exist (Games Clean Room) but no proof that a vault tenant cannot read or be read by other lanes.
- **V3:** a tested isolation guarantee per tenant/vault, with a proof (tests that try cross-tenant reads and fail),
  and no permission data stored in the vector index itself (architecture rule from the FSB package).

## Not in scope here
- Re-opening TS-06. The pgvector pick stands.
- The Memory Gate (credential pre-save check) is tracked separately in MEMORY.md "Shannon Planned Builds".
