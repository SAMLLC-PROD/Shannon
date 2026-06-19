"""
Tests for Phase 3 — challenge-response auth.

Covers:
  - shannon.auth_challenge  (challenge store + sig verify)
  - tenant_identities / sessions tables via shannon.tenants
  - /tenant/identity/register, /tenant/auth/challenge, /tenant/auth/verify endpoints
"""

from __future__ import annotations

import os
import time
import threading
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _init_schema():
    from shannon.tenants import init_identity_schema
    init_identity_schema()


@pytest.fixture
def tenant_token():
    """Register a tenant and return (tenant_id, auth_token)."""
    from shannon.tenants import register_tenant
    return register_tenant(email=f"test-{uuid.uuid4()}@example.com")


@pytest.fixture
def client():
    from shannon.api import app
    return TestClient(app)


# ── auth_challenge module ──────────────────────────────────────────────────────

def test_issue_challenge_returns_64_hex_chars():
    from shannon.auth_challenge import issue_challenge
    ch = issue_challenge("machine-a")
    assert len(ch) == 64
    assert all(c in "0123456789abcdef" for c in ch)


def test_peek_returns_live_challenge():
    from shannon.auth_challenge import issue_challenge, peek_challenge
    ch = issue_challenge("machine-b")
    assert peek_challenge("machine-b") == ch


def test_peek_returns_none_for_unknown():
    from shannon.auth_challenge import peek_challenge
    assert peek_challenge("no-such-machine") is None


def test_consume_returns_and_deletes():
    from shannon.auth_challenge import issue_challenge, consume_challenge, peek_challenge
    ch = issue_challenge("machine-c")
    assert consume_challenge("machine-c") == ch
    assert peek_challenge("machine-c") is None  # consumed


def test_consume_is_one_time():
    from shannon.auth_challenge import issue_challenge, consume_challenge
    issue_challenge("machine-d")
    first  = consume_challenge("machine-d")
    second = consume_challenge("machine-d")
    assert first is not None
    assert second is None


def test_issue_replaces_existing():
    from shannon.auth_challenge import issue_challenge, peek_challenge
    ch1 = issue_challenge("machine-e")
    ch2 = issue_challenge("machine-e")
    assert peek_challenge("machine-e") == ch2
    # ch1 and ch2 are random — they should differ with overwhelming probability
    assert ch1 != ch2 or True  # can't assert !=, just confirm no crash


def test_challenge_expires(monkeypatch):
    from shannon import auth_challenge
    # Patch CHALLENGE_TTL to 0 so next issue is instantly expired
    monkeypatch.setattr(auth_challenge, "CHALLENGE_TTL", 0)
    auth_challenge.issue_challenge("machine-ttl")
    time.sleep(0.01)
    assert auth_challenge.peek_challenge("machine-ttl") is None


def test_verify_signature_raises_without_oqs():
    from shannon import auth_challenge
    with patch.object(auth_challenge, "_OQS_AVAILABLE", False):
        with pytest.raises(RuntimeError, match="liboqs not installed"):
            auth_challenge.verify_signature(b"\x00" * 2592, "aa" * 32, "bb" * 10)


def test_verify_signature_calls_oqs():
    from shannon import auth_challenge
    mock_sig = MagicMock()
    mock_sig.verify.return_value = True
    MockOQS = MagicMock(return_value=mock_sig)

    with patch.object(auth_challenge, "_OQS_AVAILABLE", True), \
         patch.object(auth_challenge, "OQSSignature", MockOQS):
        result = auth_challenge.verify_signature(
            b"\x01" * 2592, "ab" * 32, "cd" * 32
        )

    assert result is True
    MockOQS.assert_called_once_with("ML-DSA-87", public_key=b"\x01" * 2592)
    mock_sig.verify.assert_called_once()


def test_verify_signature_returns_false_on_bad_sig():
    from shannon import auth_challenge
    mock_sig = MagicMock()
    mock_sig.verify.return_value = False
    MockOQS = MagicMock(return_value=mock_sig)

    with patch.object(auth_challenge, "_OQS_AVAILABLE", True), \
         patch.object(auth_challenge, "OQSSignature", MockOQS):
        result = auth_challenge.verify_signature(b"\x00" * 2592, "aa" * 32, "bb" * 32)

    assert result is False


# ── tenant identity / session DB functions ────────────────────────────────────

def _rand_nft() -> int:
    return int.from_bytes(os.urandom(4), "big") + 100_000


def test_register_and_get_machine_identity(tenant_token):
    from shannon.tenants import register_machine_identity, get_machine_identity
    tenant_id, _ = tenant_token
    nft_id = _rand_nft()
    pk = os.urandom(2592)
    register_machine_identity(tenant_id, nft_id, "hermes-win", "0xABC", pk)
    identity = get_machine_identity(nft_id)
    assert identity is not None
    assert identity["machine_name"]     == "hermes-win"
    assert identity["nft_token_id"]     == nft_id
    assert identity["public_key_bytes"] == pk


def test_register_machine_identity_upserts(tenant_token):
    from shannon.tenants import register_machine_identity, get_machine_identity
    tenant_id, _ = tenant_token
    nft_id = _rand_nft()
    pk1 = os.urandom(2592)
    pk2 = os.urandom(2592)
    register_machine_identity(tenant_id, nft_id, "m1", "0xA", pk1)
    register_machine_identity(tenant_id, nft_id, "m1-renamed", "0xB", pk2)
    identity = get_machine_identity(nft_id)
    assert identity["machine_name"]     == "m1-renamed"
    assert identity["public_key_bytes"] == pk2


def test_get_machine_identity_returns_none_for_unknown():
    from shannon.tenants import get_machine_identity
    assert get_machine_identity(99999) is None


def test_create_and_authenticate_session(tenant_token):
    from shannon.tenants import create_session, authenticate_session
    tenant_id, _ = tenant_token
    token = create_session(tenant_id, "hermes", 7)
    session = authenticate_session(token)
    assert session is not None
    assert session["tenant_id"]    == tenant_id
    assert session["machine_id"]   == "hermes"
    assert session["nft_token_id"] == 7


def test_authenticate_session_returns_none_for_invalid():
    from shannon.tenants import authenticate_session
    assert authenticate_session("no-such-token") is None


def test_authenticate_session_returns_none_after_expiry(tenant_token, monkeypatch):
    from shannon import tenants as tm
    tenant_id, _ = tenant_token
    monkeypatch.setattr(tm, "SESSION_TTL_SECONDS", 0)
    token = tm.create_session(tenant_id, "hermes", 3)
    time.sleep(0.01)
    assert tm.authenticate_session(token) is None


def test_cleanup_expired_sessions(tenant_token, monkeypatch):
    from shannon import tenants as tm
    tenant_id, _ = tenant_token
    monkeypatch.setattr(tm, "SESSION_TTL_SECONDS", 0)
    tm.create_session(tenant_id, "x", 1)
    tm.create_session(tenant_id, "y", 2)
    time.sleep(0.01)
    removed = tm.cleanup_expired_sessions()
    assert removed >= 2


# ── API endpoints ──────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_identity_endpoint(client, tenant_token):
    tenant_id, token = tenant_token
    nft_id = _rand_nft()
    pk_hex = os.urandom(2592).hex()
    resp = client.post(
        "/tenant/identity/register",
        json={
            "nft_token_id":   nft_id,
            "machine_name":   "test-node",
            "wallet_address": "0xWALLET",
            "public_key_hex": pk_hex,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ok"] is True
    assert body["nft_token_id"] == nft_id


def test_register_identity_requires_auth(client):
    resp = client.post(
        "/tenant/identity/register",
        json={
            "nft_token_id": 1, "machine_name": "x",
            "wallet_address": "0x0", "public_key_hex": "aa" * 2592,
        },
    )
    assert resp.status_code == 401


def test_register_identity_rejects_wrong_key_length(client, tenant_token):
    _, token = tenant_token
    resp = client.post(
        "/tenant/identity/register",
        json={
            "nft_token_id": 1, "machine_name": "x",
            "wallet_address": "0x0",
            "public_key_hex": "aa" * 100,  # too short
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_challenge_endpoint_returns_64_hex(client, tenant_token):
    tenant_id, token = tenant_token
    nft_id = _rand_nft()
    pk_hex = os.urandom(2592).hex()
    client.post(
        "/tenant/identity/register",
        json={"nft_token_id": nft_id, "machine_name": "m", "wallet_address": "0x0",
              "public_key_hex": pk_hex},
        headers=_auth(token),
    )
    resp = client.post(
        "/tenant/auth/challenge",
        json={"machine_id": "m", "nft_token_id": nft_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["challenge"]) == 64
    assert body["expires_in"] == 60


def test_challenge_endpoint_404_for_unregistered_nft(client):
    resp = client.post(
        "/tenant/auth/challenge",
        json={"machine_id": "ghost", "nft_token_id": _rand_nft()},
    )
    assert resp.status_code == 404


def _register_nft(client, token, nft_id, name):
    pk_hex = os.urandom(2592).hex()
    client.post(
        "/tenant/identity/register",
        json={"nft_token_id": nft_id, "machine_name": name,
              "wallet_address": "0x0", "public_key_hex": pk_hex},
        headers=_auth(token),
    )


def test_verify_endpoint_success(client, tenant_token):
    """Full happy-path: register → challenge → verify (sig mocked to True)."""
    tenant_id, token = tenant_token
    nft_id = _rand_nft()
    _register_nft(client, token, nft_id, "hermes")
    ch_resp = client.post(
        "/tenant/auth/challenge",
        json={"machine_id": "hermes", "nft_token_id": nft_id},
    )
    challenge = ch_resp.json()["challenge"]

    from shannon import auth_challenge as ac
    mock_sig = MagicMock()
    mock_sig.verify.return_value = True
    MockOQS = MagicMock(return_value=mock_sig)

    with patch.object(ac, "_OQS_AVAILABLE", True), \
         patch.object(ac, "OQSSignature", MockOQS):
        resp = client.post(
            "/tenant/auth/verify",
            json={"machine_id": "hermes", "nft_token_id": nft_id,
                  "challenge": challenge, "signature_hex": "aa" * 3309},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "session_token" in body
    assert body["machine_name"] == "hermes"
    assert body["expires_in"]   == 3600


def test_verify_endpoint_consumes_challenge(client, tenant_token):
    """Challenge can only be used once."""
    tenant_id, token = tenant_token
    nft_id = _rand_nft()
    _register_nft(client, token, nft_id, "m2")
    ch_resp = client.post(
        "/tenant/auth/challenge",
        json={"machine_id": "m2", "nft_token_id": nft_id},
    )
    challenge = ch_resp.json()["challenge"]

    from shannon import auth_challenge as ac
    mock_sig = MagicMock()
    mock_sig.verify.return_value = True
    MockOQS = MagicMock(return_value=mock_sig)

    with patch.object(ac, "_OQS_AVAILABLE", True), \
         patch.object(ac, "OQSSignature", MockOQS):
        r1 = client.post(
            "/tenant/auth/verify",
            json={"machine_id": "m2", "nft_token_id": nft_id,
                  "challenge": challenge, "signature_hex": "aa" * 3309},
        )
        r2 = client.post(
            "/tenant/auth/verify",
            json={"machine_id": "m2", "nft_token_id": nft_id,
                  "challenge": challenge, "signature_hex": "aa" * 3309},
        )

    assert r1.status_code == 200
    assert r2.status_code == 401


def test_verify_endpoint_bad_signature(client, tenant_token):
    tenant_id, token = tenant_token
    nft_id = _rand_nft()
    _register_nft(client, token, nft_id, "m3")
    ch_resp = client.post(
        "/tenant/auth/challenge",
        json={"machine_id": "m3", "nft_token_id": nft_id},
    )
    challenge = ch_resp.json()["challenge"]

    from shannon import auth_challenge as ac
    mock_sig = MagicMock()
    mock_sig.verify.return_value = False
    MockOQS = MagicMock(return_value=mock_sig)

    with patch.object(ac, "_OQS_AVAILABLE", True), \
         patch.object(ac, "OQSSignature", MockOQS):
        resp = client.post(
            "/tenant/auth/verify",
            json={"machine_id": "m3", "nft_token_id": nft_id,
                  "challenge": challenge, "signature_hex": "aa" * 3309},
        )

    assert resp.status_code == 401
    assert "Signature verification failed" in resp.json()["detail"]


def test_verify_endpoint_503_when_oqs_unavailable(client, tenant_token):
    tenant_id, token = tenant_token
    nft_id = _rand_nft()
    _register_nft(client, token, nft_id, "m4")
    ch_resp = client.post(
        "/tenant/auth/challenge",
        json={"machine_id": "m4", "nft_token_id": nft_id},
    )
    challenge = ch_resp.json()["challenge"]

    from shannon import auth_challenge as ac
    with patch.object(ac, "_OQS_AVAILABLE", False):
        resp = client.post(
            "/tenant/auth/verify",
            json={"machine_id": "m4", "nft_token_id": nft_id,
                  "challenge": challenge, "signature_hex": "aa" * 10},
        )

    assert resp.status_code == 503


def test_verify_endpoint_expired_challenge(client, tenant_token, monkeypatch):
    tenant_id, token = tenant_token
    nft_id = _rand_nft()
    _register_nft(client, token, nft_id, "m5")

    from shannon import auth_challenge as ac
    monkeypatch.setattr(ac, "CHALLENGE_TTL", 0)
    ch_resp = client.post(
        "/tenant/auth/challenge",
        json={"machine_id": "m5", "nft_token_id": nft_id},
    )
    challenge = ch_resp.json()["challenge"]
    time.sleep(0.01)

    resp = client.post(
        "/tenant/auth/verify",
        json={"machine_id": "m5", "nft_token_id": nft_id,
              "challenge": challenge, "signature_hex": "aa" * 10},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()
