"""
CaaS auth audit log.

Every significant auth event (identity registration, challenge, success/failure,
access toggle) is written as a structured entry to Shannon's own store, scoped
to the tenant.  Failures are non-fatal — a broken audit write never blocks auth.

Entry format (JSON body):
    {
        "event":        "<event_name>",
        "tenant_id":    "<uuid>",
        "machine_id":   "<name>",
        "nft_token_id": <int | null>,
        "detail":       "<human-readable context>",
        "ip_address":   "<source IP or ''>",
        "timestamp":    "<ISO-8601 UTC>"
    }

Tags always include "audit" and "caas" plus the event name, enabling
retrieval with GET /tenant/audit or memory_search("audit caas").
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from .store import write as _store_write

logger = logging.getLogger("shannon.audit")


# ── Event constants ───────────────────────────────────────────────────────────

IDENTITY_REGISTERED = "identity_registered"
CHALLENGE_ISSUED    = "challenge_issued"
AUTH_SUCCESS        = "auth_success"
AUTH_FAILURE        = "auth_failure"
ACCESS_DISABLED     = "access_disabled"
ACCESS_ENABLED      = "access_enabled"


# ── Core writer ───────────────────────────────────────────────────────────────

def log_auth_event(
    event: str,
    tenant_id: str,
    *,
    machine_id: str = "",
    nft_token_id: Optional[int] = None,
    detail: str = "",
    ip_address: str = "",
) -> None:
    """Write one audit entry to Shannon's store. Never raises."""
    try:
        now  = datetime.now(timezone.utc)
        body = json.dumps({
            "event":        event,
            "tenant_id":    tenant_id,
            "machine_id":   machine_id,
            "nft_token_id": nft_token_id,
            "detail":       detail,
            "ip_address":   ip_address,
            "timestamp":    now.isoformat(),
        }, separators=(",", ":"))

        tags       = ["audit", "caas", event]
        session_id = f"audit-{now.strftime('%Y-%m-%d')}"

        _store_write(body, session_id=session_id, tags=tags, tier=2, tenant_id=tenant_id)
        logger.debug(
            "audit: event=%s tenant=%.8s machine=%s",
            event, tenant_id, machine_id,
        )
    except Exception as exc:
        logger.warning("audit write failed (event=%s tenant=%.8s): %s", event, tenant_id, exc)


# ── Query ─────────────────────────────────────────────────────────────────────

def get_audit_log(tenant_id: str, limit: int = 50) -> list[dict]:
    """
    Return the most recent audit entries for a tenant, newest first.

    Reads directly from the entries table filtered by tenant_id + "audit" tag.
    Returns a list of parsed event dicts (not Shannon store rows).
    """
    from .store import _connect, init_store, CHUNKS_DIR, HAS_ZSTD
    if HAS_ZSTD:
        import zstandard as _zstd

    init_store()
    conn  = _connect()
    rows  = conn.execute(
        """SELECT content_hash, created_at
           FROM   entries
           WHERE  tenant_id = ?
             AND  tags LIKE '%"audit"%'
           ORDER  BY created_at DESC
           LIMIT  ?""",
        (tenant_id, limit),
    ).fetchall()
    conn.close()

    results = []
    for row in rows:
        chunk_path = CHUNKS_DIR / f"{row['content_hash']}.zst"
        if not chunk_path.exists():
            continue
        raw = chunk_path.read_bytes()
        try:
            if HAS_ZSTD:
                text = _zstd.ZstdDecompressor().decompress(raw).decode()
            else:
                text = raw.decode()
            entry = json.loads(text)
            entry["_stored_at"] = row["created_at"]
            results.append(entry)
        except Exception:
            continue

    return results
