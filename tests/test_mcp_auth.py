"""
Tests for Phase 5 — MCP auth middleware.

Tests cover:
  - Token extraction from query param and Authorization header
  - JWT validation + revocation check in middleware
  - SSE endpoint auth enforcement (enforce=True) and pass-through (enforce=False)
  - Session_id capture from SSE endpoint event
  - Message POST session lookup
  - _resolved_agent() respects JWT identity (prevents escalation)
  - _shannon_request() forwards Bearer token when JWT is set
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio

import pytest


def run(coro):
    """Run an async coroutine synchronously — avoids pytest-asyncio dependency."""
    return asyncio.run(coro)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_scope(path: str, qs: str = "", headers: list | None = None) -> dict:
    return {
        "type":         "http",
        "path":         path,
        "query_string": qs.encode(),
        "headers":      [(k.encode(), v.encode()) for k, v in (headers or [])],
    }


async def _noop_receive():
    return {}


def _rand_nft() -> int:
    return int.from_bytes(os.urandom(4), "big") + 300_000


# ── _extract_token ─────────────────────────────────────────────────────────────

def test_extract_token_from_query_param():
    from shannon.mcp_auth import _extract_token
    scope = _make_scope("/mcp/sse", qs="token=abc123")
    assert _extract_token(scope) == "abc123"


def test_extract_token_from_auth_header():
    from shannon.mcp_auth import _extract_token
    scope = _make_scope("/mcp/sse", headers=[("authorization", "Bearer mytoken")])
    assert _extract_token(scope) == "mytoken"


def test_extract_token_query_takes_precedence():
    from shannon.mcp_auth import _extract_token
    scope = _make_scope("/mcp/sse", qs="token=qparam",
                        headers=[("authorization", "Bearer header")])
    assert _extract_token(scope) == "qparam"


def test_extract_token_returns_none_when_absent():
    from shannon.mcp_auth import _extract_token
    assert _extract_token(_make_scope("/mcp/sse")) is None


# ── _validate_token ───────────────────────────────────────────────────────────

def test_validate_token_returns_none_for_garbage():
    from shannon.mcp_auth import _validate_token
    assert _validate_token("not.a.jwt") is None


def test_validate_token_returns_payload_for_valid_session(tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt, tenants as tm
    from shannon.mcp_auth import _validate_token
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "s.key")
    tm.init_identity_schema()
    tenant_id, _ = tm.register_tenant(email=f"mcp-{uuid.uuid4()}@example.com")
    token = tm.create_session(tenant_id, "hermes", 1)
    payload = _validate_token(token)
    assert payload is not None
    assert payload["tenant_id"] == tenant_id


# ── MCPAuthMiddleware — SSE path ───────────────────────────────────────────────

def test_middleware_sse_401_when_enforce_and_no_token():
    from shannon.mcp_auth import MCPAuthMiddleware

    async def _test():
        inner = AsyncMock()
        mw    = MCPAuthMiddleware(inner, enforce=True)
        responses = []

        async def collect(m):
            responses.append(m)

        await mw(_make_scope("/mcp/sse"), _noop_receive, collect)
        starts = [r for r in responses if r.get("type") == "http.response.start"]
        assert starts[0]["status"] == 401

    run(_test())


def test_middleware_sse_passthrough_when_not_enforced():
    from shannon.mcp_auth import MCPAuthMiddleware

    async def _test():
        inner = AsyncMock()
        mw    = MCPAuthMiddleware(inner, enforce=False)
        await mw(_make_scope("/mcp/sse"), _noop_receive, AsyncMock())
        inner.assert_called_once()

    run(_test())


def test_middleware_sse_401_for_invalid_token():
    from shannon.mcp_auth import MCPAuthMiddleware

    async def _test():
        inner     = AsyncMock()
        mw        = MCPAuthMiddleware(inner, enforce=True)
        responses = []

        async def collect(m):
            responses.append(m)

        await mw(_make_scope("/mcp/sse", qs="token=badtoken"), _noop_receive, collect)
        starts = [r for r in responses if r.get("type") == "http.response.start"]
        assert starts[0]["status"] == 401
        inner.assert_not_called()

    run(_test())


def test_middleware_sse_valid_token_passes_through(tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt, tenants as tm
    from shannon.mcp_auth import MCPAuthMiddleware, _mcp_sessions
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "s.key")
    tm.init_identity_schema()
    tenant_id, _ = tm.register_tenant(email=f"mcp2-{uuid.uuid4()}@example.com")
    token = tm.create_session(tenant_id, "hermes", _rand_nft())

    async def _test():
        inner_called = []

        async def inner(scope, receive, send):
            inner_called.append(True)
            await send({
                "type": "http.response.body",
                "body": b"data: /mcp/messages/?session_id=abc123def456\n\n",
                "more_body": True,
            })

        mw = MCPAuthMiddleware(inner, enforce=True)
        await mw(_make_scope("/mcp/sse", qs=f"token={token}"), _noop_receive, AsyncMock())
        assert inner_called
        assert "abc123def456" in _mcp_sessions

    run(_test())


def test_middleware_sse_captures_session_id(tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt, tenants as tm
    from shannon.mcp_auth import MCPAuthMiddleware, _mcp_sessions
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "s.key")
    tm.init_identity_schema()
    tenant_id, _ = tm.register_tenant(email=f"mcp3-{uuid.uuid4()}@example.com")
    token = tm.create_session(tenant_id, "hermes", _rand_nft())

    async def _test():
        async def inner(scope, receive, send):
            await send({
                "type": "http.response.body",
                "body": b"data: /mcp/messages/?session_id=deadbeef1234\n\n",
                "more_body": True,
            })

        mw = MCPAuthMiddleware(inner, enforce=True)
        await mw(_make_scope("/mcp/sse", qs=f"token={token}"), _noop_receive, AsyncMock())
        assert "deadbeef1234" in _mcp_sessions
        assert _mcp_sessions["deadbeef1234"]["token"] == token

    run(_test())


# ── MCPAuthMiddleware — messages path ─────────────────────────────────────────

def test_middleware_messages_uses_stored_session(tmp_path, monkeypatch):
    from shannon import jwt_tokens as jt, tenants as tm
    from shannon.mcp_auth import MCPAuthMiddleware, _mcp_sessions, _current_mcp_jwt
    monkeypatch.setattr(jt, "_SECRET_PATH", tmp_path / "s.key")
    tm.init_identity_schema()
    tenant_id, _ = tm.register_tenant(email=f"mcp4-{uuid.uuid4()}@example.com")
    token = tm.create_session(tenant_id, "hermes", _rand_nft())

    from shannon.jwt_tokens import decode_session_jwt
    payload = decode_session_jwt(token)
    _mcp_sessions["testsession"] = {
        "payload":    payload,
        "token":      token,
        "expires_at": time.monotonic() + 3600,
    }

    async def _test():
        captured = []

        async def inner(scope, receive, send):
            captured.append(_current_mcp_jwt.get())

        mw = MCPAuthMiddleware(inner, enforce=True)
        await mw(_make_scope("/mcp/messages/", qs="session_id=testsession"),
                 _noop_receive, AsyncMock())
        assert captured[0] is not None
        assert captured[0]["tenant_id"] == tenant_id

    run(_test())


def test_middleware_messages_401_when_enforce_and_no_session():
    from shannon.mcp_auth import MCPAuthMiddleware

    async def _test():
        inner     = AsyncMock()
        mw        = MCPAuthMiddleware(inner, enforce=True)
        responses = []

        async def collect(m):
            responses.append(m)

        await mw(_make_scope("/mcp/messages/", qs="session_id=unknownsession"),
                 _noop_receive, collect)
        starts = [r for r in responses if r.get("type") == "http.response.start"]
        assert starts[0]["status"] == 401
        inner.assert_not_called()

    run(_test())


def test_middleware_messages_passthrough_when_not_enforced():
    from shannon.mcp_auth import MCPAuthMiddleware

    async def _test():
        inner = AsyncMock()
        mw    = MCPAuthMiddleware(inner, enforce=False)
        await mw(_make_scope("/mcp/messages/", qs="session_id=unknownsession"),
                 _noop_receive, AsyncMock())
        inner.assert_called_once()

    run(_test())


# ── _resolved_agent ───────────────────────────────────────────────────────────

def test_resolved_agent_returns_jwt_agent_id_when_authenticated():
    from shannon.mcp_auth import _current_mcp_jwt
    from shannon.mcp_mount import _resolved_agent
    token = _current_mcp_jwt.set({"agent_id": "hermes-win", "tenant_id": "t1"})
    try:
        assert _resolved_agent("other-agent") == "hermes-win"
    finally:
        _current_mcp_jwt.reset(token)


def test_resolved_agent_falls_back_to_requested_when_unauthenticated():
    from shannon.mcp_mount import _resolved_agent, DEFAULT_AGENT
    assert _resolved_agent("custom-agent") == "custom-agent"


def test_resolved_agent_falls_back_to_default_when_neither():
    from shannon.mcp_mount import _resolved_agent, DEFAULT_AGENT
    assert _resolved_agent("") == DEFAULT_AGENT


# ── _shannon_request forwards Bearer token ────────────────────────────────────

def test_shannon_request_forwards_bearer_token():
    """When a JWT is set in ContextVar, it should be added as Authorization header."""
    from shannon.mcp_auth import _current_mcp_token
    from shannon import mcp_mount as mm

    captured_headers = []

    class FakeResponse:
        def read(self): return b'{"ok": true}'
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=30):
        captured_headers.append(dict(req.headers))
        return FakeResponse()

    token_var = _current_mcp_token.set("my.jwt.token")
    try:
        with patch("shannon.mcp_mount.urllib.request.urlopen", side_effect=fake_urlopen):
            mm._shannon_request("/health")
    finally:
        _current_mcp_token.reset(token_var)

    assert captured_headers
    auth = captured_headers[0].get("Authorization", "")
    assert auth == "Bearer my.jwt.token"


def test_shannon_request_no_auth_header_when_unauthenticated():
    from shannon import mcp_mount as mm

    captured_headers = []

    class FakeResponse:
        def read(self): return b'{"ok": true}'
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=30):
        captured_headers.append(dict(req.headers))
        return FakeResponse()

    with patch("shannon.mcp_mount.urllib.request.urlopen", side_effect=fake_urlopen):
        mm._shannon_request("/health")

    assert "Authorization" not in captured_headers[0]
