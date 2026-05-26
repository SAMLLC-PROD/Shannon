"""Tests for Shannon CaaS API routes (FastAPI TestClient)."""

import time
import pytest
from fastapi.testclient import TestClient

from shannon.api import app
from shannon.store import write, init_store
from shannon.tenants import init_tenant_schema, register_tenant, pause_tenant


client = TestClient(app, raise_server_exceptions=True)


def _email(prefix: str = "api") -> str:
    return f"{prefix}+{int(time.time() * 1000)}@example.com"


@pytest.fixture(autouse=True)
def setup():
    init_store()
    init_tenant_schema()


# ---------------------------------------------------------------------------
# POST /tenant/register
# ---------------------------------------------------------------------------

def test_register_success():
    r = client.post("/tenant/register", json={"email": _email()})
    assert r.status_code == 201
    data = r.json()
    assert "tenant_id" in data
    assert "auth_token" in data
    assert data["trial_days"] == 14


def test_register_invalid_email():
    r = client.post("/tenant/register", json={"email": "notanemail"})
    assert r.status_code == 422


def test_register_duplicate_email():
    email = _email("dup")
    client.post("/tenant/register", json={"email": email})
    r = client.post("/tenant/register", json={"email": email})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# GET /tenant/status
# ---------------------------------------------------------------------------

def test_status_requires_auth():
    r = client.get("/tenant/status")
    assert r.status_code == 401


def test_status_invalid_token():
    r = client.get("/tenant/status", headers={"Authorization": "Bearer badtoken"})
    assert r.status_code == 401


def test_status_valid_token():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    r = client.get("/tenant/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "active"
    assert "entry_count" in data
    assert "trial_days_remaining" in data


def test_status_paused_tenant_returns_403():
    tid, token = register_tenant(_email("paused"))
    pause_tenant(tid)
    r = client.get("/tenant/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert "paused" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /tenant/pause
# ---------------------------------------------------------------------------

def test_pause_endpoint():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    r = client.post("/tenant/pause", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# POST /tenant/wipe
# ---------------------------------------------------------------------------

def test_wipe_requires_confirm_true():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    r = client.post(
        "/tenant/wipe",
        json={"confirm": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_wipe_success():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    r = client.post(
        "/tenant/wipe",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# GET /tenant/export
# ---------------------------------------------------------------------------

def test_export_requires_auth():
    r = client.get("/tenant/export")
    assert r.status_code == 401


def test_export_returns_markdown():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    r = client.get(
        "/tenant/export",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "Shannon Memory Export" in r.text


def test_export_with_content():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    tid = reg.json()["tenant_id"]
    write("Decision: use FastAPI for all new services", tags=["decision"], tenant_id=tid)

    r = client.get(
        "/tenant/export",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "Decision" in r.text


# ---------------------------------------------------------------------------
# GET /source/{entry_id}
# ---------------------------------------------------------------------------

def test_source_viewer_internal_entry_no_auth():
    write("internal note for source viewer", tags=["test"])
    from shannon.store import _connect
    conn = _connect()
    row = conn.execute(
        "SELECT content_hash FROM entries WHERE tags LIKE '%test%' AND tenant_id IS NULL LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        r = client.get(f"/source/{row['content_hash']}")
        assert r.status_code == 200
        assert "Shannon Memory" in r.text


def test_source_viewer_tenant_entry_requires_token():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    tid = reg.json()["tenant_id"]
    write("tenant private note", tags=["private"], tenant_id=tid)

    from shannon.store import _connect
    conn = _connect()
    row = conn.execute(
        "SELECT content_hash FROM entries WHERE tenant_id = ?", (tid,)
    ).fetchone()
    conn.close()

    # No token → should require auth
    r = client.get(f"/source/{row['content_hash']}")
    assert r.status_code == 401

    # With token → success
    r = client.get(f"/source/{row['content_hash']}?token={token}")
    assert r.status_code == 200
    assert "tenant private note" in r.text


def test_source_viewer_wrong_tenant_token():
    reg1 = client.post("/tenant/register", json={"email": _email("sv1")})
    token1 = reg1.json()["auth_token"]
    tid1 = reg1.json()["tenant_id"]

    reg2 = client.post("/tenant/register", json={"email": _email("sv2")})
    token2 = reg2.json()["auth_token"]

    write("tenant1 secret", tags=["private"], tenant_id=tid1)
    from shannon.store import _connect
    conn = _connect()
    row = conn.execute(
        "SELECT content_hash FROM entries WHERE tenant_id = ?", (tid1,)
    ).fetchone()
    conn.close()

    r = client.get(f"/source/{row['content_hash']}?token={token2}")
    assert r.status_code == 403


def test_source_viewer_missing_entry():
    r = client.get("/source/0" * 64)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /memory endpoints with tenant Bearer token
# ---------------------------------------------------------------------------

def test_memory_post_with_tenant_token():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    tid = reg.json()["tenant_id"]

    r = client.post(
        "/memory",
        json={"body": "tenant memory entry", "agent": "irrelevant", "tags": ["test"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Verify stored with correct tenant_id
    from shannon.store import _connect
    conn = _connect()
    entry_id = r.json()["id"]
    row = conn.execute(
        "SELECT tenant_id FROM entries WHERE content_hash = ?", (entry_id,)
    ).fetchone()
    conn.close()
    assert row["tenant_id"] == tid


def test_memory_get_with_tenant_token():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    r = client.get(
        "/memory",
        params={"topic": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "entries" in r.json()


def test_memory_get_internal_still_requires_agent():
    # Without bearer token and without agent param → 422
    r = client.get("/memory")
    assert r.status_code == 422


def test_memory_search_with_tenant_token():
    reg = client.post("/tenant/register", json={"email": _email()})
    token = reg.json()["auth_token"]
    r = client.get(
        "/memory/search",
        params={"q": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "results" in r.json()
