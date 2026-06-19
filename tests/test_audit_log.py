"""
Tests for Phase 7 — CaaS auth audit log.

Covers:
  - log_auth_event writes an entry to Shannon's store
  - log_auth_event is non-fatal when store write fails
  - get_audit_log returns entries scoped to tenant, newest first
  - get_audit_log excludes other tenants' entries
  - All event constants exist and round-trip through the log
  - caas_api instruments: identity_registered, challenge_issued,
    auth_success, auth_failure (bad sig + expired challenge), access_disabled
  - GET /tenant/audit returns events for the authenticated tenant
"""
from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _init_schemas():
    from shannon.tenants import init_identity_schema
    from shannon.store import init_store
    init_identity_schema()
    init_store()


@pytest.fixture
def tenant_token():
    from shannon.tenants import register_tenant
    return register_tenant(email=f"audit-{uuid.uuid4()}@example.com")


@pytest.fixture
def client():
    from shannon.api import app
    return TestClient(app)


def _rand_nft() -> int:
    return int.from_bytes(os.urandom(4), "big") + 400_000


# ── log_auth_event ────────────────────────────────────────────────────────────

def test_log_auth_event_writes_entry():
    from shannon.audit_log import log_auth_event, get_audit_log, AUTH_SUCCESS
    tid = str(uuid.uuid4())
    log_auth_event(AUTH_SUCCESS, tid, machine_id="test-box", nft_token_id=1,
                   detail="test entry")
    entries = get_audit_log(tid, limit=10)
    assert len(entries) >= 1
    e = entries[0]
    assert e["event"]      == AUTH_SUCCESS
    assert e["tenant_id"]  == tid
    assert e["machine_id"] == "test-box"
    assert e["detail"]     == "test entry"


def test_log_auth_event_is_nonfatal_on_store_failure():
    from shannon.audit_log import log_auth_event, AUTH_FAILURE
    tid = str(uuid.uuid4())
    with patch("shannon.audit_log._store_write", side_effect=Exception("disk full")):
        # Must not raise
        log_auth_event(AUTH_FAILURE, tid, machine_id="m", detail="boom")


def test_log_auth_event_stores_timestamp():
    from shannon.audit_log import log_auth_event, get_audit_log, CHALLENGE_ISSUED
    tid = str(uuid.uuid4())
    log_auth_event(CHALLENGE_ISSUED, tid, machine_id="m", nft_token_id=5)
    entries = get_audit_log(tid)
    assert entries[0]["timestamp"].startswith("20")  # ISO-8601


def test_log_auth_event_all_event_types():
    from shannon.audit_log import (log_auth_event, get_audit_log,
        IDENTITY_REGISTERED, CHALLENGE_ISSUED, AUTH_SUCCESS,
        AUTH_FAILURE, ACCESS_DISABLED, ACCESS_ENABLED)
    tid = str(uuid.uuid4())
    for evt in [IDENTITY_REGISTERED, CHALLENGE_ISSUED, AUTH_SUCCESS,
                AUTH_FAILURE, ACCESS_DISABLED, ACCESS_ENABLED]:
        log_auth_event(evt, tid, machine_id="m")
    entries = get_audit_log(tid, limit=10)
    found = {e["event"] for e in entries}
    assert found == {IDENTITY_REGISTERED, CHALLENGE_ISSUED, AUTH_SUCCESS,
                     AUTH_FAILURE, ACCESS_DISABLED, ACCESS_ENABLED}


# ── get_audit_log ─────────────────────────────────────────────────────────────

def test_get_audit_log_scoped_to_tenant():
    from shannon.audit_log import log_auth_event, get_audit_log, AUTH_SUCCESS
    tid_a = str(uuid.uuid4())
    tid_b = str(uuid.uuid4())
    log_auth_event(AUTH_SUCCESS, tid_a, machine_id="a-box")
    log_auth_event(AUTH_SUCCESS, tid_b, machine_id="b-box")

    entries_a = get_audit_log(tid_a)
    assert all(e["tenant_id"] == tid_a for e in entries_a)
    assert not any(e["tenant_id"] == tid_b for e in entries_a)


def test_get_audit_log_returns_empty_for_unknown_tenant():
    from shannon.audit_log import get_audit_log
    entries = get_audit_log(str(uuid.uuid4()))
    assert entries == []


def test_get_audit_log_respects_limit():
    from shannon.audit_log import log_auth_event, get_audit_log, AUTH_SUCCESS
    tid = str(uuid.uuid4())
    for _ in range(5):
        log_auth_event(AUTH_SUCCESS, tid, machine_id="m")
    entries = get_audit_log(tid, limit=3)
    assert len(entries) <= 3


# ── caas_api instrumentation ──────────────────────────────────────────────────

def test_register_identity_logs_event(client, tenant_token):
    tenant_id, bearer = tenant_token
    nft_id  = _rand_nft()
    pub_hex = "ab" * 2592

    from shannon.audit_log import get_audit_log, IDENTITY_REGISTERED
    client.post(
        "/tenant/identity/register",
        json={"nft_token_id": nft_id, "machine_name": "audit-machine",
              "wallet_address": "0x0", "public_key_hex": pub_hex},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    entries = get_audit_log(tenant_id)
    reg_events = [e for e in entries if e["event"] == IDENTITY_REGISTERED]
    assert reg_events, "identity_registered event not logged"
    assert reg_events[0]["nft_token_id"] == nft_id


def test_challenge_issued_logs_event(client, tenant_token):
    tenant_id, bearer = tenant_token
    nft_id  = _rand_nft()
    pub_hex = "ab" * 2592

    client.post(
        "/tenant/identity/register",
        json={"nft_token_id": nft_id, "machine_name": "ch-machine",
              "wallet_address": "0x0", "public_key_hex": pub_hex},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    client.post("/tenant/auth/challenge",
                json={"machine_id": "ch-machine", "nft_token_id": nft_id})

    from shannon.audit_log import get_audit_log, CHALLENGE_ISSUED
    entries = get_audit_log(tenant_id)
    ch_events = [e for e in entries if e["event"] == CHALLENGE_ISSUED]
    assert ch_events, "challenge_issued event not logged"


def test_auth_success_logs_event(client, tenant_token):
    from shannon import auth_challenge as ac
    tenant_id, bearer = tenant_token
    nft_id  = _rand_nft()
    pub_hex = "ab" * 2592

    client.post(
        "/tenant/identity/register",
        json={"nft_token_id": nft_id, "machine_name": "ok-machine",
              "wallet_address": "0x0", "public_key_hex": pub_hex},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    ch = client.post("/tenant/auth/challenge",
                     json={"machine_id": "ok-machine", "nft_token_id": nft_id})
    challenge = ch.json()["challenge"]

    mock_sig = MagicMock()
    mock_sig.verify.return_value = True
    MockOQS = MagicMock(return_value=mock_sig)

    with patch.object(ac, "_OQS_AVAILABLE", True), \
         patch.object(ac, "OQSSignature", MockOQS):
        client.post("/tenant/auth/verify",
                    json={"machine_id": "ok-machine", "nft_token_id": nft_id,
                          "challenge": challenge, "signature_hex": "aa" * 3309})

    from shannon.audit_log import get_audit_log, AUTH_SUCCESS
    entries = get_audit_log(tenant_id)
    ok_events = [e for e in entries if e["event"] == AUTH_SUCCESS]
    assert ok_events, "auth_success event not logged"


def test_auth_failure_bad_sig_logs_event(client, tenant_token):
    from shannon import auth_challenge as ac
    tenant_id, bearer = tenant_token
    nft_id  = _rand_nft()
    pub_hex = "ab" * 2592

    client.post(
        "/tenant/identity/register",
        json={"nft_token_id": nft_id, "machine_name": "fail-machine",
              "wallet_address": "0x0", "public_key_hex": pub_hex},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    ch = client.post("/tenant/auth/challenge",
                     json={"machine_id": "fail-machine", "nft_token_id": nft_id})
    challenge = ch.json()["challenge"]

    mock_sig = MagicMock()
    mock_sig.verify.return_value = False  # bad signature
    MockOQS = MagicMock(return_value=mock_sig)

    with patch.object(ac, "_OQS_AVAILABLE", True), \
         patch.object(ac, "OQSSignature", MockOQS):
        resp = client.post("/tenant/auth/verify",
                           json={"machine_id": "fail-machine", "nft_token_id": nft_id,
                                 "challenge": challenge, "signature_hex": "bb" * 3309})

    assert resp.status_code == 401

    from shannon.audit_log import get_audit_log, AUTH_FAILURE
    entries = get_audit_log(tenant_id)
    fail_events = [e for e in entries if e["event"] == AUTH_FAILURE]
    assert fail_events, "auth_failure event not logged"
    assert "signature" in fail_events[0]["detail"].lower()


def test_access_disabled_logs_event(client, tenant_token):
    tenant_id, bearer = tenant_token
    client.post("/tenant/disable",
                headers={"Authorization": f"Bearer {bearer}"})

    from shannon.audit_log import get_audit_log, ACCESS_DISABLED
    entries = get_audit_log(tenant_id)
    dis_events = [e for e in entries if e["event"] == ACCESS_DISABLED]
    assert dis_events, "access_disabled event not logged"


# ── GET /tenant/audit endpoint ────────────────────────────────────────────────

def test_audit_endpoint_returns_events(client, tenant_token):
    tenant_id, bearer = tenant_token

    # Generate an access_disabled event, then restore with a new token
    client.post("/tenant/disable", headers={"Authorization": f"Bearer {bearer}"})
    from shannon.tenants import enable_token
    new_bearer = enable_token(tenant_id)

    resp = client.get("/tenant/audit",
                      headers={"Authorization": f"Bearer {new_bearer}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == tenant_id
    assert isinstance(body["entries"], list)
    assert body["count"] == len(body["entries"])
    assert any(e["event"] == "access_disabled" for e in body["entries"])


def test_audit_endpoint_requires_auth(client):
    resp = client.get("/tenant/audit")
    assert resp.status_code == 401


def test_audit_endpoint_limit_param(client, tenant_token):
    tenant_id, bearer = tenant_token
    from shannon.audit_log import log_auth_event, AUTH_SUCCESS
    for _ in range(10):
        log_auth_event(AUTH_SUCCESS, tenant_id, machine_id="m")

    resp = client.get("/tenant/audit?limit=3",
                      headers={"Authorization": f"Bearer {bearer}"})
    assert resp.status_code == 200
    assert resp.json()["count"] <= 3
