"""Shannon CaaS — tenant management: registration, auth, trial lifecycle.

Business rules:
- Free trial: 14 days from registration, then auto-pause (data retained)
- Grace period: 30 days after pause, then auto-wipe
- No auto-charge ever; user must explicitly opt in to continue
- During trial: log request patterns (anonymized), never log content
"""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from .store import _connect, init_store, CHUNKS_DIR

log = logging.getLogger(__name__)

TRIAL_DAYS = 14
GRACE_DAYS = 30


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

def init_tenant_schema() -> None:
    """Create tenants table and add tenant_id column to entries if absent."""
    init_store()
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id        TEXT PRIMARY KEY,
            display_name     TEXT,
            email            TEXT UNIQUE NOT NULL,
            token_hash       TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            trial_expires_at TEXT NOT NULL,
            paused_at        TEXT,
            wipe_at          TEXT,
            status           TEXT NOT NULL DEFAULT 'active'
        )
    """)
    conn.commit()
    try:
        conn.execute("ALTER TABLE entries ADD COLUMN tenant_id TEXT")
        conn.commit()
    except Exception:
        pass  # column already exists
    conn.close()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_tenant(email: str, display_name: str = "") -> tuple[str, str]:
    """
    Register a new tenant. Returns (tenant_id, auth_token).
    The auth_token is returned once and never stored — only its SHA-256 hash is kept.
    """
    init_tenant_schema()
    tenant_id = str(uuid.uuid4())
    token = _generate_token()
    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)
    trial_expires = now + timedelta(days=TRIAL_DAYS)

    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO tenants
               (tenant_id, display_name, email, token_hash, created_at, trial_expires_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            (
                tenant_id,
                display_name or email.split("@")[0],
                email,
                token_hash,
                now.isoformat(),
                trial_expires.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    log.info("Registered tenant: %s (%s)", tenant_id, email)
    return tenant_id, token


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate(token: str) -> Optional[dict]:
    """
    Resolve bearer token to tenant. Returns tenant dict or None.
    Enforces trial expiry and grace-period wipe on every call.
    """
    init_tenant_schema()
    token_hash = _hash_token(token)

    conn = _connect()
    row = conn.execute(
        "SELECT * FROM tenants WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    tenant = dict(row)
    _enforce_lifecycle(tenant)

    # Re-fetch after any status change
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM tenants WHERE tenant_id = ?", (tenant["tenant_id"],)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _enforce_lifecycle(tenant: dict) -> None:
    now = datetime.now(timezone.utc)
    status = tenant["status"]
    tid = tenant["tenant_id"]

    if status == "active":
        if now > _parse_dt(tenant["trial_expires_at"]):
            _pause_tenant(tid)
            log.info("Auto-paused tenant %s: trial expired", tid)
    elif status == "paused":
        wipe_at = tenant.get("wipe_at")
        if wipe_at and now > _parse_dt(wipe_at):
            _do_wipe(tid)
            log.info("Auto-wiped tenant %s: grace period expired", tid)


# ---------------------------------------------------------------------------
# Lifecycle operations
# ---------------------------------------------------------------------------

def _pause_tenant(tenant_id: str) -> None:
    now = datetime.now(timezone.utc)
    wipe_at = now + timedelta(days=GRACE_DAYS)
    conn = _connect()
    conn.execute(
        "UPDATE tenants SET status='paused', paused_at=?, wipe_at=? WHERE tenant_id=?",
        (now.isoformat(), wipe_at.isoformat(), tenant_id),
    )
    conn.commit()
    conn.close()


def pause_tenant(tenant_id: str) -> None:
    """Manually pause a tenant (data retained, 30-day grace period starts)."""
    _pause_tenant(tenant_id)


def _do_wipe(tenant_id: str) -> None:
    """Delete all entries for a tenant and mark status wiped."""
    init_tenant_schema()
    conn = _connect()
    # Collect hashes to check for orphaned chunks
    rows = conn.execute(
        "SELECT content_hash FROM entries WHERE tenant_id = ?", (tenant_id,)
    ).fetchall()
    tenant_hashes = {r["content_hash"] for r in rows}

    # Find hashes referenced by OTHER tenants or internal entries
    shared = set()
    if tenant_hashes:
        placeholders = ",".join("?" * len(tenant_hashes))
        shared_rows = conn.execute(
            f"SELECT DISTINCT content_hash FROM entries WHERE content_hash IN ({placeholders}) AND (tenant_id != ? OR tenant_id IS NULL)",
            (*tenant_hashes, tenant_id),
        ).fetchall()
        shared = {r["content_hash"] for r in shared_rows}

    # Remove chunk files that are exclusively this tenant's
    for ch in tenant_hashes - shared:
        chunk = CHUNKS_DIR / f"{ch}.zst"
        if chunk.exists():
            chunk.unlink(missing_ok=True)

    conn.execute("DELETE FROM entries WHERE tenant_id = ?", (tenant_id,))
    conn.execute("UPDATE tenants SET status='wiped' WHERE tenant_id=?", (tenant_id,))
    conn.commit()
    conn.close()
    log.info("Wiped tenant %s: %d entries removed", tenant_id, len(tenant_hashes))


def wipe_tenant(tenant_id: str) -> None:
    """Permanently delete all tenant data (user-initiated or admin)."""
    _do_wipe(tenant_id)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_tenant_stats(tenant_id: str) -> dict:
    init_tenant_schema()
    conn = _connect()
    row = conn.execute(
        "SELECT COUNT(*) as count, SUM(byte_size) as total_bytes FROM entries WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    tenant = conn.execute(
        "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    conn.close()

    if not tenant:
        return {}

    now = datetime.now(timezone.utc)
    trial_expires = _parse_dt(tenant["trial_expires_at"])
    days_remaining = max(0, (trial_expires - now).days)

    return {
        "tenant_id": tenant["tenant_id"],
        "display_name": tenant["display_name"],
        "email": tenant["email"],
        "status": tenant["status"],
        "created_at": tenant["created_at"],
        "trial_expires_at": tenant["trial_expires_at"],
        "trial_days_remaining": days_remaining,
        "paused_at": tenant["paused_at"],
        "wipe_at": tenant["wipe_at"],
        "entry_count": row["count"] or 0,
        "storage_bytes": row["total_bytes"] or 0,
        "storage_mb": round((row["total_bytes"] or 0) / 1_048_576, 3),
    }


# ---------------------------------------------------------------------------
# Anonymized trial usage logging (patterns only, no content)
# ---------------------------------------------------------------------------

_trial_log = logging.getLogger("shannon.trial_usage")


def log_trial_request(tenant_id: str, method: str, path: str) -> None:
    """Log anonymized request pattern during trial (no body/content logged)."""
    _trial_log.info(
        '{"tenant": "%s", "method": "%s", "path": "%s", "ts": "%s"}',
        tenant_id,
        method,
        path,
        datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Knowledge Profiles — sub-namespaces within a tenant
# ---------------------------------------------------------------------------

def init_profiles_schema() -> None:
    """Create profiles table for sub-namespaces within a tenant."""
    init_tenant_schema()
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            profile_id   TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL,
            name         TEXT NOT NULL,
            description  TEXT DEFAULT '',
            created_at   TEXT NOT NULL,
            FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
        )
    """)
    # Add profile_id column to entries if absent
    try:
        conn.execute("ALTER TABLE entries ADD COLUMN profile_id TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()


def create_profile(tenant_id: str, name: str, description: str = "") -> str:
    """Create a knowledge profile under a tenant. Returns profile_id."""
    init_profiles_schema()
    profile_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn = _connect()
    conn.execute(
        "INSERT INTO profiles (profile_id, tenant_id, name, description, created_at) VALUES (?, ?, ?, ?, ?)",
        (profile_id, tenant_id, name, description, now.isoformat()),
    )
    conn.commit()
    conn.close()
    log.info("Created profile '%s' (%s) for tenant %s", name, profile_id, tenant_id)
    return profile_id


def list_profiles(tenant_id: str) -> list[dict]:
    """List all knowledge profiles for a tenant."""
    init_profiles_schema()
    conn = _connect()
    rows = conn.execute(
        "SELECT p.profile_id, p.tenant_id, p.name, p.description, p.created_at, "
        "COUNT(e.content_hash) as entry_count "
        "FROM profiles p LEFT JOIN entries e ON e.profile_id = p.profile_id "
        "WHERE p.tenant_id = ? GROUP BY p.profile_id ORDER BY p.name",
        (tenant_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_profile(tenant_id: str, profile_id: str) -> int:
    """Delete a profile and all its entries. Returns count of deleted entries."""
    conn = _connect()
    # Verify ownership
    row = conn.execute(
        "SELECT 1 FROM profiles WHERE profile_id = ? AND tenant_id = ?",
        (profile_id, tenant_id),
    ).fetchone()
    if not row:
        conn.close()
        return 0
    # Delete entries
    cursor = conn.execute(
        "DELETE FROM entries WHERE profile_id = ?", (profile_id,)
    )
    deleted = cursor.rowcount
    conn.execute("DELETE FROM profiles WHERE profile_id = ?", (profile_id,))
    conn.commit()
    conn.close()
    log.info("Deleted profile %s: %d entries removed", profile_id, deleted)
    return deleted


def get_profile(tenant_id: str, profile_id: str) -> Optional[dict]:
    """Get a single profile if owned by tenant."""
    init_profiles_schema()
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM profiles WHERE profile_id = ? AND tenant_id = ?",
        (profile_id, tenant_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Founder/permanent accounts
# ---------------------------------------------------------------------------

def set_founder(tenant_id: str) -> None:
    """Mark a tenant as founder — no trial expiry, permanent access."""
    conn = _connect()
    conn.execute(
        "UPDATE tenants SET status='founder', trial_expires_at='9999-12-31T23:59:59+00:00' WHERE tenant_id=?",
        (tenant_id,),
    )
    conn.commit()
    conn.close()
    log.info("Tenant %s marked as founder (permanent access)", tenant_id)


def revoke_token(tenant_id: str) -> str:
    """Revoke current token and issue a new one. Old token immediately invalid."""
    new_token = _generate_token()
    new_hash = _hash_token(new_token)
    conn = _connect()
    conn.execute(
        "UPDATE tenants SET token_hash=? WHERE tenant_id=?",
        (new_hash, tenant_id),
    )
    conn.commit()
    conn.close()
    log.info("Token revoked and regenerated for tenant %s", tenant_id)
    return new_token


def disable_token(tenant_id: str) -> None:
    """Disable access without deleting data. Set token to impossible hash."""
    conn = _connect()
    conn.execute(
        "UPDATE tenants SET token_hash='DISABLED', status='disabled' WHERE tenant_id=?",
        (tenant_id,),
    )
    conn.commit()
    conn.close()
    log.info("Token disabled for tenant %s", tenant_id)


def enable_token(tenant_id: str) -> str:
    """Re-enable access and generate a new token."""
    new_token = _generate_token()
    new_hash = _hash_token(new_token)
    conn = _connect()
    conn.execute(
        "UPDATE tenants SET token_hash=?, status='founder' WHERE tenant_id=?",
        (new_hash, tenant_id),
    )
    conn.commit()
    conn.close()
    log.info("Token re-enabled for tenant %s", tenant_id)
    return new_token


# ---------------------------------------------------------------------------
# Per-Profile Access Tokens — cryptographic data separation
# ---------------------------------------------------------------------------

def init_profile_tokens_schema() -> None:
    """Add token_hash column to profiles table."""
    init_profiles_schema()
    conn = _connect()
    try:
        conn.execute("ALTER TABLE profiles ADD COLUMN token_hash TEXT")
        conn.commit()
    except Exception:
        pass  # already exists
    conn.close()


def generate_profile_token(tenant_id: str, profile_id: str) -> str:
    """Generate an access token scoped to a single profile."""
    init_profile_tokens_schema()
    # Verify ownership
    profile = get_profile(tenant_id, profile_id)
    if not profile:
        raise ValueError(f"Profile {profile_id} not found for tenant {tenant_id}")
    
    token = _generate_token()
    token_hash = _hash_token(token)
    conn = _connect()
    conn.execute(
        "UPDATE profiles SET token_hash=? WHERE profile_id=?",
        (token_hash, profile_id),
    )
    conn.commit()
    conn.close()
    log.info("Profile token generated for %s/%s", tenant_id, profile_id)
    return token


def authenticate_profile(token: str) -> Optional[dict]:
    """
    Resolve a token to a specific profile. Returns dict with tenant_id + profile_id.
    Profile tokens ONLY give access to that one profile's data — nothing else.
    """
    init_profile_tokens_schema()
    token_hash = _hash_token(token)
    
    conn = _connect()
    # Check profile tokens first
    row = conn.execute(
        "SELECT p.profile_id, p.tenant_id, p.name, t.status "
        "FROM profiles p JOIN tenants t ON p.tenant_id = t.tenant_id "
        "WHERE p.token_hash = ?",
        (token_hash,),
    ).fetchone()
    conn.close()
    
    if not row:
        return None
    
    if row["status"] in ("paused", "wiped", "disabled"):
        return None
    
    return {
        "tenant_id": row["tenant_id"],
        "profile_id": row["profile_id"],
        "profile_name": row["name"],
        "scope": "profile",  # vs "tenant" for full-access tokens
    }
