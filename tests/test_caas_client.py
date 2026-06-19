"""
Tests for shannon.caas_client — CaaS auth client.

Covers:
  - load_config / load_public_key_hex
  - _sign_challenge errors (no oqs, missing key, bad permissions)
  - _http error handling (HTTPError, generic exception)
  - authenticate() happy path and error paths
  - get_token() caching and refresh
  - register() sends correct payload
  - mcp_url() includes token
  - request() forwards Bearer token
"""
from __future__ import annotations

import json
import os
import stat
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shannon.caas_client import CaaSAuthError, CaaSClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _write_identity(tmp_path: Path, nft_id: int | None = 42) -> Path:
    """Write a minimal identity directory to tmp_path."""
    pub_hex = "ab" * 2592
    key_hex = "cd" * 4896
    cfg = {
        "machine_name":   "test-machine",
        "algorithm":      "ML-DSA-87",
        "nft_token_id":   nft_id,
        "wallet_address": "0xDEAD",
        "public_key_hex": pub_hex,
        "fingerprint":    "test",
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    pub_file = tmp_path / "ml_dsa_87.pub"
    pub_file.write_text(pub_hex)

    key_file = tmp_path / "ml_dsa_87.key"
    key_file.touch(mode=0o600, exist_ok=True)
    key_file.write_text(key_hex)
    os.chmod(key_file, 0o600)

    return tmp_path


def _client(tmp_path: Path) -> CaaSClient:
    return CaaSClient(shannon_url="http://localhost:9999", identity_dir=tmp_path)


# ── load_config ───────────────────────────────────────────────────────────────

def test_load_config_returns_dict(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)
    cfg = c.load_config()
    assert cfg["machine_name"] == "test-machine"
    assert cfg["nft_token_id"] == 42


def test_load_config_raises_when_missing(tmp_path):
    c = _client(tmp_path)
    with pytest.raises(CaaSAuthError, match="No identity config"):
        c.load_config()


def test_load_public_key_hex(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)
    assert c.load_public_key_hex() == "ab" * 2592


def test_load_public_key_hex_raises_when_missing(tmp_path):
    c = _client(tmp_path)
    with pytest.raises(CaaSAuthError, match="Public key not found"):
        c.load_public_key_hex()


# ── _sign_challenge ───────────────────────────────────────────────────────────

def test_sign_challenge_raises_when_no_oqs(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)
    with patch.dict("sys.modules", {"oqs": None}):
        with pytest.raises(CaaSAuthError, match="liboqs not available"):
            c._sign_challenge("deadbeef" * 8)


def test_sign_challenge_raises_when_key_missing(tmp_path):
    _write_identity(tmp_path)
    (tmp_path / "ml_dsa_87.key").unlink()
    c = _client(tmp_path)

    mock_oqs = MagicMock()
    with patch.dict("sys.modules", {"oqs": mock_oqs}):
        with pytest.raises(CaaSAuthError, match="Private key not found"):
            c._sign_challenge("deadbeef" * 8)


def test_sign_challenge_raises_when_bad_permissions(tmp_path):
    _write_identity(tmp_path)
    key_file = tmp_path / "ml_dsa_87.key"
    os.chmod(key_file, 0o644)   # too permissive
    c = _client(tmp_path)

    mock_oqs = MagicMock()
    with patch.dict("sys.modules", {"oqs": mock_oqs}):
        with pytest.raises(CaaSAuthError, match="insecure permissions"):
            c._sign_challenge("deadbeef" * 8)


def test_sign_challenge_calls_oqs_signature(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)

    mock_sig = MagicMock()
    mock_sig.sign.return_value = bytes.fromhex("ff" * 32)
    MockOQS = MagicMock(return_value=mock_sig)

    with patch.dict("sys.modules", {"oqs": MagicMock(Signature=MockOQS)}):
        result = c._sign_challenge("deadbeef" * 8)

    assert result == "ff" * 32
    mock_sig.sign.assert_called_once()
    mock_sig.free.assert_called_once()


# ── _http error handling ──────────────────────────────────────────────────────

def _make_urlopen_ok(payload: dict):
    class FakeResp:
        def read(self): return json.dumps(payload).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    return lambda req, timeout=30: FakeResp()


def test_http_raises_caas_auth_error_on_http_error(tmp_path):
    import urllib.error
    _write_identity(tmp_path)
    c = _client(tmp_path)

    err = urllib.error.HTTPError(
        url="http://x", code=404, msg="Not Found",
        hdrs=MagicMock(), fp=MagicMock(read=lambda: b'{"detail":"nope"}'),
    )
    with patch("shannon.caas_client.urllib.request.urlopen", side_effect=err):
        with pytest.raises(CaaSAuthError, match="HTTP 404"):
            c._http("/test")


def test_http_raises_caas_auth_error_on_connection_error(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)

    with patch("shannon.caas_client.urllib.request.urlopen",
               side_effect=Exception("connection refused")):
        with pytest.raises(CaaSAuthError, match="Request failed"):
            c._http("/test")


# ── authenticate ─────────────────────────────────────────────────────────────

def _mock_auth_urlopen(challenge="aabbcc" * 10, token="jwt.token.here"):
    """Return a urlopen side_effect that handles challenge + verify calls."""
    calls = []

    def _urlopen(req, timeout=30):
        calls.append(req.get_full_url())
        if "/challenge" in req.get_full_url():
            payload = {"challenge": challenge, "expires_in": 60}
        else:
            payload = {"session_token": token, "expires_in": 3600,
                       "machine_id": "test-machine", "tenant_id": "t1"}

        class FakeResp:
            def read(self): return json.dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        return FakeResp()

    return _urlopen, calls


def test_authenticate_happy_path(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)

    urlopen, calls = _mock_auth_urlopen(token="my.jwt.token")

    mock_sig = MagicMock()
    mock_sig.sign.return_value = bytes.fromhex("aa" * 32)
    MockOQS = MagicMock(return_value=mock_sig)

    with patch("shannon.caas_client.urllib.request.urlopen", side_effect=urlopen), \
         patch.dict("sys.modules", {"oqs": MagicMock(Signature=MockOQS)}):
        token = c.authenticate()

    assert token == "my.jwt.token"
    assert c._token == "my.jwt.token"
    assert c._expires_at > time.monotonic()
    assert any("/challenge" in u for u in calls)
    assert any("/verify" in u for u in calls)


def test_authenticate_raises_when_no_nft_token_id(tmp_path):
    _write_identity(tmp_path, nft_id=None)
    c = _client(tmp_path)
    with pytest.raises(CaaSAuthError, match="nft_token_id not set"):
        c.authenticate()


def test_authenticate_raises_when_challenge_missing(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)

    def _urlopen(req, timeout=30):
        class R:
            def read(self): return b'{"other": "field"}'
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return R()

    with patch("shannon.caas_client.urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(CaaSAuthError, match="No challenge in response"):
            c.authenticate()


def test_authenticate_raises_when_no_session_token(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)

    call_count = [0]

    def _urlopen(req, timeout=30):
        call_count[0] += 1
        if call_count[0] == 1:
            payload = {"challenge": "deadbeef" * 8}
        else:
            payload = {"oops": "no token here"}

        class R:
            def read(self): return json.dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return R()

    mock_sig = MagicMock()
    mock_sig.sign.return_value = bytes.fromhex("aa" * 32)
    MockOQS = MagicMock(return_value=mock_sig)

    with patch("shannon.caas_client.urllib.request.urlopen", side_effect=_urlopen), \
         patch.dict("sys.modules", {"oqs": MagicMock(Signature=MockOQS)}):
        with pytest.raises(CaaSAuthError, match="No session_token"):
            c.authenticate()


# ── get_token caching ─────────────────────────────────────────────────────────

def test_get_token_returns_cached_when_fresh(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)
    c._token      = "cached.token"
    c._expires_at = time.monotonic() + 3600

    with patch.object(c, "authenticate") as mock_auth:
        result = c.get_token()

    assert result == "cached.token"
    mock_auth.assert_not_called()


def test_get_token_reauthenticates_when_expired(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)
    c._token      = "old.token"
    c._expires_at = time.monotonic() - 1  # expired

    with patch.object(c, "authenticate", return_value="new.token") as mock_auth:
        result = c.get_token()

    assert result == "new.token"
    mock_auth.assert_called_once()


def test_get_token_reauthenticates_within_refresh_margin(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)
    c._token      = "expiring.token"
    c._expires_at = time.monotonic() + 60  # within 300s margin

    with patch.object(c, "authenticate", return_value="fresh.token") as mock_auth:
        result = c.get_token()

    assert result == "fresh.token"
    mock_auth.assert_called_once()


# ── mcp_url ───────────────────────────────────────────────────────────────────

def test_mcp_url_includes_token(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)
    with patch.object(c, "get_token", return_value="tok123"):
        url = c.mcp_url()
    assert url == "http://localhost:9999/mcp/sse?token=tok123"


# ── register ──────────────────────────────────────────────────────────────────

def test_register_sends_correct_payload(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)

    captured = []

    def _urlopen(req, timeout=30):
        import json as _json
        captured.append({
            "url":    req.get_full_url(),
            "body":   _json.loads(req.data.decode()),
            "bearer": req.get_header("Authorization"),
        })
        class R:
            def read(self): return b'{"ok": true}'
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return R()

    with patch("shannon.caas_client.urllib.request.urlopen", side_effect=_urlopen):
        c.register("tenant-api-key")

    assert captured
    req = captured[0]
    assert "/tenant/identity/register" in req["url"]
    assert req["body"]["machine_name"]   == "test-machine"
    assert req["body"]["nft_token_id"]   == 42
    assert req["bearer"]                 == "Bearer tenant-api-key"


def test_register_raises_when_no_nft_token_id(tmp_path):
    _write_identity(tmp_path, nft_id=None)
    c = _client(tmp_path)
    with pytest.raises(CaaSAuthError, match="nft_token_id not set"):
        c.register("some-bearer")


# ── request ───────────────────────────────────────────────────────────────────

def test_request_includes_bearer_token(tmp_path):
    _write_identity(tmp_path)
    c = _client(tmp_path)

    captured = []

    def _urlopen(req, timeout=30):
        captured.append(req.get_header("Authorization"))
        class R:
            def read(self): return b'{"result": 1}'
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return R()

    with patch.object(c, "get_token", return_value="bearer.jwt"), \
         patch("shannon.caas_client.urllib.request.urlopen", side_effect=_urlopen):
        result = c.request("/memory/search", params={"q": "infra"})

    assert captured[0] == "Bearer bearer.jwt"
    assert result == {"result": 1}
