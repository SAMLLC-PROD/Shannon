"""Tests for Shannon CaaS tenant management."""

import time
import pytest
from datetime import datetime, timezone, timedelta

from shannon.tenants import (
    init_tenant_schema,
    register_tenant,
    authenticate,
    pause_tenant,
    wipe_tenant,
    get_tenant_stats,
    _hash_token,
    _parse_dt,
    TRIAL_DAYS,
    GRACE_DAYS,
)
from shannon.store import _connect, write


@pytest.fixture(autouse=True)
def setup_schema():
    init_tenant_schema()


def _unique_email(prefix: str = "test") -> str:
    return f"{prefix}+{int(time.time() * 1000)}@example.com"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_returns_tenant_id_and_token():
    email = _unique_email()
    tid, token = register_tenant(email)
    assert len(tid) == 36  # UUID4
    assert len(token) > 20


def test_register_stores_hashed_token():
    email = _unique_email()
    tid, token = register_tenant(email)
    conn = _connect()
    row = conn.execute("SELECT token_hash FROM tenants WHERE tenant_id = ?", (tid,)).fetchone()
    conn.close()
    assert row["token_hash"] == _hash_token(token)
    assert row["token_hash"] != token  # never stored in plain


def test_register_sets_trial_expiry():
    email = _unique_email()
    tid, _ = register_tenant(email)
    conn = _connect()
    row = conn.execute("SELECT created_at, trial_expires_at FROM tenants WHERE tenant_id = ?", (tid,)).fetchone()
    conn.close()
    created = _parse_dt(row["created_at"])
    expires = _parse_dt(row["trial_expires_at"])
    diff_days = (expires - created).days
    assert diff_days == TRIAL_DAYS


def test_register_duplicate_email_raises():
    email = _unique_email()
    register_tenant(email)
    with pytest.raises(Exception):
        register_tenant(email)


def test_display_name_defaults_to_email_prefix():
    email = _unique_email("alice")
    tid, _ = register_tenant(email)
    stats = get_tenant_stats(tid)
    assert stats["display_name"].startswith("alice")


def test_display_name_custom():
    tid, _ = register_tenant(_unique_email(), display_name="Bob Smith")
    stats = get_tenant_stats(tid)
    assert stats["display_name"] == "Bob Smith"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def test_authenticate_valid_token():
    email = _unique_email()
    tid, token = register_tenant(email)
    tenant = authenticate(token)
    assert tenant is not None
    assert tenant["tenant_id"] == tid
    assert tenant["status"] == "active"


def test_authenticate_invalid_token_returns_none():
    result = authenticate("definitely-not-a-real-token")
    assert result is None


def test_authenticate_wrong_token_returns_none():
    _, _ = register_tenant(_unique_email())
    result = authenticate("wrongtoken12345")
    assert result is None


# ---------------------------------------------------------------------------
# Lifecycle — pause
# ---------------------------------------------------------------------------

def test_pause_tenant():
    email = _unique_email()
    tid, token = register_tenant(email)
    pause_tenant(tid)
    conn = _connect()
    row = conn.execute("SELECT status, wipe_at FROM tenants WHERE tenant_id = ?", (tid,)).fetchone()
    conn.close()
    assert row["status"] == "paused"
    assert row["wipe_at"] is not None
    wipe_dt = _parse_dt(row["wipe_at"])
    now = datetime.now(timezone.utc)
    assert (wipe_dt - now).days >= GRACE_DAYS - 1


def test_authenticate_paused_tenant_returns_paused_status():
    email = _unique_email()
    tid, token = register_tenant(email)
    pause_tenant(tid)
    tenant = authenticate(token)
    assert tenant is not None
    assert tenant["status"] == "paused"


# ---------------------------------------------------------------------------
# Lifecycle — wipe
# ---------------------------------------------------------------------------

def test_wipe_tenant_removes_entries():
    email = _unique_email()
    tid, token = register_tenant(email)
    # Write an entry for this tenant
    write("wipe test data", tags=["test"], tenant_id=tid)

    stats_before = get_tenant_stats(tid)
    assert stats_before["entry_count"] >= 1

    wipe_tenant(tid)

    conn = _connect()
    remaining = conn.execute(
        "SELECT COUNT(*) as c FROM entries WHERE tenant_id = ?", (tid,)
    ).fetchone()["c"]
    status_row = conn.execute(
        "SELECT status FROM tenants WHERE tenant_id = ?", (tid,)
    ).fetchone()
    conn.close()
    assert remaining == 0
    assert status_row["status"] == "wiped"


# ---------------------------------------------------------------------------
# Tenant stats
# ---------------------------------------------------------------------------

def test_stats_entry_count():
    email = _unique_email()
    tid, token = register_tenant(email)
    # Include tenant_id in content to ensure unique content hashes per tenant
    write(f"stats test {tid} entry 1", tags=["test"], tenant_id=tid)
    write(f"stats test {tid} entry 2", tags=["test"], tenant_id=tid)
    stats = get_tenant_stats(tid)
    assert stats["entry_count"] >= 2
    assert stats["storage_bytes"] > 0


def test_stats_trial_days_remaining():
    email = _unique_email()
    tid, _ = register_tenant(email)
    stats = get_tenant_stats(tid)
    # timedelta.days truncates subsecond — allow 13 or 14
    assert stats["trial_days_remaining"] >= TRIAL_DAYS - 1


def test_stats_unknown_tenant_returns_empty():
    result = get_tenant_stats("00000000-0000-0000-0000-000000000000")
    assert result == {}


# ---------------------------------------------------------------------------
# Isolation — tenants cannot see each other's entries
# ---------------------------------------------------------------------------

def test_tenant_isolation():
    tid1, token1 = register_tenant(_unique_email("t1"))
    tid2, token2 = register_tenant(_unique_email("t2"))

    write("secret from tenant 1", tags=["private"], tenant_id=tid1)
    write("secret from tenant 2", tags=["private"], tenant_id=tid2)

    conn = _connect()
    rows1 = conn.execute(
        "SELECT content_hash FROM entries WHERE tenant_id = ?", (tid1,)
    ).fetchall()
    rows2 = conn.execute(
        "SELECT content_hash FROM entries WHERE tenant_id = ?", (tid2,)
    ).fetchall()
    conn.close()

    hashes1 = {r["content_hash"] for r in rows1}
    hashes2 = {r["content_hash"] for r in rows2}
    assert hashes1.isdisjoint(hashes2)
