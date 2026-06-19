"""
Tests for Phase 4 — JWT session tokens.

Covers:
  - shannon.jwt_tokens  (create / verify / decode)
  - create_session() now returns a JWT
  - authenticate_session() validates JWT + revocation check
  - _get_tenant() accepts JWT Bearer tokens
"""

from __future__ import annotations

import time
import uuid
import os
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _init_schema():
    from shannon.tenants import init_identity_schema
    init_identity_schema()


@pytest.fixture
def tenant_token():
    from shannon.tenants import register_tenant
    return register_tenant(email=f"jwt-{uuid.uuid4()}@example.com")


@pytest.fixture
def client():
    from shannon.api import app
    return TestClient(app)


def _rand_nft() -> int:
    return int.from_bytes(os.urandom(4), "big") + 200_000


# ── jwt_tokens module ──────────────────────────────────────────────────────────

def test_create_jwt_returns_string():
    from shannon.jwt_tokens import create_session_jwt
    token = create_session_jwt("tenant-1", "hermes", 5)
    assert isinstance(token, str)
    assert len(token) > 40  # proper JWT


def test_verify_jwt_round_trip():
    from shannon.jwt_tokens import create_session_jwt, verify_session_jwt
    token = create_session_jwt("tenant-1", "hermes", 5, agent_id="hermes-win")
    payload = verify_session_jwt(token)
    assert payload["tenant_id"]    == "tenant-1"
    assert payload["machine_id"]   == "hermes"
    assert payload["nft_token_id"] == 5
    assert payload["agent_id"]     == "hermes-win"
    assert payload["iss"]          == "shannon-caas"


def test_verify_jwt_defaults_agent_id_to_machine_id():
    from shannon.jwt_tokens import create_session_jwt, verify_session_jwt
    token   = create_session_jwt("t1", "my-machine", 1)
    payload = verify_session_jwt(token)
    assert payload["agent_id"] == "my-machine"


def test_decode_jwt_returns_none_for_garbage():
    from shannon.jwt_tokens import decode_session_jwt
    assert decode_session_jwt("not.a.token") is None


def test_decode_jwt_returns_none_for_expired(tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "jwt_secret.key")
    token = jt.create_session_jwt("t1", "m1", 1, expires_in=-1)
    assert jt.decode_session_jwt(token) is None


def test_decode_jwt_returns_none_for_wrong_secret(tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "jwt_secret.key")
    token = jt.create_session_jwt("t1", "m1", 1)

    # Now change the secret so verification fails
    (tmp_path / "jwt_secret.key").write_text("different_secret_value_here_x" * 2)
    assert jt.decode_session_jwt(token) is None


def test_secret_file_created_with_0600(tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt
    secret_path = tmp_path / "sub" / "jwt_secret.key"
    monkeypatch.setattr(jt, "_SECRET_PATH", secret_path)
    jt._load_secret()
    assert secret_path.exists()
    import stat
    mode = stat.S_IMODE(os.stat(secret_path).st_mode)
    assert mode == 0o600


def test_secret_file_reused_across_calls(tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "jwt_secret.key")
    s1 = jt._load_secret()
    s2 = jt._load_secret()
    assert s1 == s2


# ── create_session / authenticate_session ─────────────────────────────────────

def test_create_session_returns_jwt(tenant_token):
    from shannon.tenants import create_session
    from shannon.jwt_tokens import verify_session_jwt
    tenant_id, _ = tenant_token
    token = create_session(tenant_id, "hermes", 42)
    payload = verify_session_jwt(token)
    assert payload["tenant_id"]    == tenant_id
    assert payload["machine_id"]   == "hermes"
    assert payload["nft_token_id"] == 42


def test_authenticate_session_valid(tenant_token):
    from shannon.tenants import create_session, authenticate_session
    tenant_id, _ = tenant_token
    token   = create_session(tenant_id, "m1", 1)
    session = authenticate_session(token)
    assert session is not None
    assert session["tenant_id"]  == tenant_id
    assert session["machine_id"] == "m1"


def test_authenticate_session_expired(tenant_token, tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt, tenants as tm
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "s.key")
    tenant_id, _ = tenant_token
    token = jt.create_session_jwt(tenant_id, "m", 2, expires_in=-1)
    assert tm.authenticate_session(token) is None


def test_authenticate_session_unknown_token(tenant_token):
    from shannon.tenants import authenticate_session
    from shannon.jwt_tokens import create_session_jwt
    tenant_id, _ = tenant_token
    # Valid JWT but never recorded in sessions table
    token = create_session_jwt(tenant_id, "ghost", 9999)
    assert authenticate_session(token) is None


# ── _get_tenant accepts JWT Bearer ────────────────────────────────────────────

def _register_and_get_session(client, tenant_id, token):
    """Register an NFT, do challenge-response with mocked sig, return session JWT."""
    from shannon import auth_challenge as ac
    from unittest.mock import MagicMock, patch

    nft_id = _rand_nft()
    pk_hex = os.urandom(2592).hex()

    client.post(
        "/tenant/identity/register",
        json={"nft_token_id": nft_id, "machine_name": "hermes",
              "wallet_address": "0x0", "public_key_hex": pk_hex},
        headers={"Authorization": f"Bearer {token}"},
    )
    ch_resp = client.post(
        "/tenant/auth/challenge",
        json={"machine_id": "hermes", "nft_token_id": nft_id},
    )
    challenge = ch_resp.json()["challenge"]

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
    return resp.json()["session_token"]


def test_jwt_bearer_accepted_for_tenant_status(client, tenant_token):
    """A JWT session token should pass the /tenant/status auth gate."""
    tenant_id, bearer = tenant_token
    session_jwt = _register_and_get_session(client, tenant_id, bearer)

    resp = client.get(
        "/tenant/status",
        headers={"Authorization": f"Bearer {session_jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == tenant_id


def test_jwt_bearer_carries_agent_id(client, tenant_token):
    """Session JWT should populate agent_id in the resolved tenant dict."""
    tenant_id, bearer = tenant_token
    session_jwt = _register_and_get_session(client, tenant_id, bearer)

    # /tenant/status itself doesn't expose agent_id, but we can inspect via
    # the decoded payload directly
    from shannon.jwt_tokens import verify_session_jwt
    payload = verify_session_jwt(session_jwt)
    assert payload["agent_id"] == "hermes"


def test_revoked_session_rejected(client, tenant_token):
    """A JWT removed from the sessions table (revoked) should be rejected."""
    tenant_id, bearer = tenant_token
    session_jwt = _register_and_get_session(client, tenant_id, bearer)

    # Simulate revocation by deleting the sessions row directly
    from shannon.store import _connect
    conn = _connect()
    conn.execute("DELETE FROM sessions WHERE session_token = ?", (session_jwt,))
    conn.commit()
    conn.close()

    resp = client.get(
        "/tenant/status",
        headers={"Authorization": f"Bearer {session_jwt}"},
    )
    assert resp.status_code == 401


def test_expired_jwt_rejected_at_api(client, tenant_token, tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "s.key")
    tenant_id, _ = tenant_token
    expired_jwt = jt.create_session_jwt(tenant_id, "m", 1, expires_in=-1)
    resp = client.get(
        "/tenant/status",
        headers={"Authorization": f"Bearer {expired_jwt}"},
    )
    # JWT is invalid so falls through to static token check → 401
    assert resp.status_code == 401
