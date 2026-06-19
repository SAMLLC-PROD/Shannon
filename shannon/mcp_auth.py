"""
MCP auth middleware — Phase 5.

Validates JWT session tokens on the MCP SSE endpoint so only authenticated
machines (holding a valid Lattice CaaS session JWT) can connect.

Flow:
  1. Client GETs /mcp/sse?token=<jwt>  (or Authorization: Bearer <jwt>)
  2. Middleware validates JWT + revocation check
  3. Captures the session_id from the first SSE "endpoint" event
  4. Stores session_id → {payload, token} in _mcp_sessions
  5. Client POSTs to /mcp/messages/?session_id=<uuid_hex>
  6. Middleware looks up session by session_id, injects ContextVars
  7. MCP tool calls read ContextVars to scope memory operations

Set SHANNON_MCP_AUTH_ENFORCE=1 to require auth (default: not enforced,
so internal Claude Code connections keep working without a token).
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import time
import urllib.parse
from typing import Any, Optional

log = logging.getLogger("shannon.mcp_auth")

# ── ContextVars injected per-request ──────────────────────────────────────────

# Decoded JWT payload dict, or None when unauthenticated
_current_mcp_jwt:   contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "mcp_jwt", default=None
)
# Raw JWT string (to forward as Bearer token to Shannon's own HTTP API)
_current_mcp_token: contextvars.ContextVar[Optional[str]]  = contextvars.ContextVar(
    "mcp_token", default=None
)

# ── In-memory SSE session map ─────────────────────────────────────────────────
# session_id (uuid hex, no dashes) → {"payload": dict, "token": str, "expires_at": float}
_mcp_sessions: dict[str, dict] = {}

_SESSION_GRACE = 7200  # keep sessions 2× JWT TTL for late-arriving POSTs

ENFORCE: bool = os.environ.get("SHANNON_MCP_AUTH_ENFORCE", "").lower() in ("1", "true", "yes")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_token(scope: dict) -> Optional[str]:
    """Pull JWT from ?token= query param, then Authorization: Bearer header."""
    qs = scope.get("query_string", b"").decode("utf-8", errors="ignore")
    params = dict(urllib.parse.parse_qsl(qs))
    if params.get("token"):
        return params["token"]
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            v = value.decode("utf-8", errors="ignore")
            if v.startswith("Bearer "):
                return v.removeprefix("Bearer ").strip()
    return None


def _validate_token(token: str) -> Optional[dict]:
    """Return JWT payload dict if valid + not revoked, else None."""
    from .jwt_tokens import decode_session_jwt
    from .tenants import authenticate_session
    payload = decode_session_jwt(token)
    if not payload:
        return None
    if not authenticate_session(token):
        return None
    return payload


def _purge_expired_sessions() -> None:
    now = time.monotonic()
    expired = [sid for sid, s in _mcp_sessions.items() if s["expires_at"] < now]
    for sid in expired:
        del _mcp_sessions[sid]


async def _send_401(scope, receive, send) -> None:
    body = b'{"detail":"Authentication required. Pass ?token=<session_jwt> on MCP SSE connect."}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type",   b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body, "more_body": False})


# ── Middleware ────────────────────────────────────────────────────────────────

class MCPAuthMiddleware:
    """
    ASGI middleware that enforces JWT auth on /mcp SSE and message endpoints.

    Mounted as a wrapper around the FastMCP sse_app Starlette sub-application.
    """

    def __init__(self, app, enforce: Optional[bool] = None):
        self.app    = app
        self.enforce = enforce if enforce is not None else ENFORCE

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path.endswith("/sse"):
            await self._handle_sse(scope, receive, send)
        elif "/messages" in path:
            await self._handle_messages(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    # ── SSE connect ────────────────────────────────────────────────────────────

    async def _handle_sse(self, scope, receive, send) -> None:
        token = _extract_token(scope)

        if token:
            payload = _validate_token(token)
            if payload:
                log.info(
                    "MCP SSE connect: tenant=%s machine=%s",
                    payload.get("tenant_id", "?"),
                    payload.get("machine_id", "?"),
                )
                await self._run_with_session_capture(scope, receive, send, token, payload)
                return
            else:
                log.warning("MCP SSE connect: invalid/expired token rejected")
                await _send_401(scope, receive, send)
                return

        if self.enforce:
            log.warning("MCP SSE connect: no token, enforcing auth → 401")
            await _send_401(scope, receive, send)
        else:
            log.debug("MCP SSE connect: no token, unauthenticated pass-through")
            await self.app(scope, receive, send)

    async def _run_with_session_capture(self, scope, receive, send, token, payload) -> None:
        """Run the SSE app while capturing the generated session_id from the endpoint event."""
        _purge_expired_sessions()
        expires_at = time.monotonic() + _SESSION_GRACE
        captured    = []  # holds the session_id once found

        async def capturing_send(message: dict) -> None:
            # The first http.response.body chunk contains the "endpoint" SSE event
            # which carries the session_id the client will use for POSTs.
            if not captured and message.get("type") == "http.response.body":
                raw = message.get("body", b"")
                text = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else raw
                m = re.search(r"session_id=([0-9a-f]+)", text)
                if m:
                    sid = m.group(1)
                    _mcp_sessions[sid] = {
                        "payload":    payload,
                        "token":      token,
                        "expires_at": expires_at,
                    }
                    captured.append(sid)
                    log.debug("MCP session registered: session_id=%s machine=%s",
                              sid, payload.get("machine_id", "?"))
            await send(message)

        jwt_var   = _current_mcp_jwt.set(payload)
        token_var = _current_mcp_token.set(token)
        try:
            await self.app(scope, receive, capturing_send)
        finally:
            _current_mcp_jwt.reset(jwt_var)
            _current_mcp_token.reset(token_var)

    # ── Message POST ───────────────────────────────────────────────────────────

    async def _handle_messages(self, scope, receive, send) -> None:
        qs     = scope.get("query_string", b"").decode("utf-8", errors="ignore")
        params = dict(urllib.parse.parse_qsl(qs))
        sid    = params.get("session_id")

        session = _mcp_sessions.get(sid) if sid else None

        if session:
            if time.monotonic() > session["expires_at"]:
                del _mcp_sessions[sid]
                session = None

        # Also accept a token passed directly on the POST (for stateless clients)
        if not session:
            token = _extract_token(scope)
            if token:
                payload = _validate_token(token)
                if payload:
                    session = {"payload": payload, "token": token,
                               "expires_at": time.monotonic() + _SESSION_GRACE}

        if session:
            jwt_var   = _current_mcp_jwt.set(session["payload"])
            token_var = _current_mcp_token.set(session["token"])
            try:
                await self.app(scope, receive, send)
            finally:
                _current_mcp_jwt.reset(jwt_var)
                _current_mcp_token.reset(token_var)
            return

        if self.enforce:
            await _send_401(scope, receive, send)
        else:
            await self.app(scope, receive, send)
