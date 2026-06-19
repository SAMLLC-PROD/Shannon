"""
JWT session tokens for Shannon CaaS — Phase 4.

Tokens are HS256-signed, 1-hour TTL, scoped to a single machine identity.
The signing secret lives at ~/.shannon/jwt_secret.key (hex, 32 bytes).
Created on first use; never committed.

JWT payload:
  {
    "iss":          "shannon-caas",
    "iat":          <unix timestamp>,
    "exp":          <iat + 3600>,
    "tenant_id":    "...",
    "machine_id":   "hermes-windows",
    "nft_token_id": 2,
    "agent_id":     "hermes-windows"
  }
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

import jwt

_ISSUER    = "shannon-caas"
_ALGORITHM = "HS256"
SESSION_TTL_SECONDS = 3600

_SECRET_PATH = Path.home() / ".shannon" / "jwt_secret.key"


def _load_secret() -> str:
    """Return the HS256 signing secret, creating it if absent."""
    if _SECRET_PATH.exists():
        return _SECRET_PATH.read_text().strip()
    _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    _SECRET_PATH.write_text(secret)
    _SECRET_PATH.chmod(0o600)
    return secret


def create_session_jwt(
    tenant_id:    str,
    machine_id:   str,
    nft_token_id: int,
    agent_id:     Optional[str] = None,
    expires_in:   int = SESSION_TTL_SECONDS,
) -> str:
    """Issue a signed JWT session token."""
    import time
    now    = int(time.time())
    secret = _load_secret()
    payload = {
        "iss":          _ISSUER,
        "iat":          now,
        "exp":          now + expires_in,
        "tenant_id":    tenant_id,
        "machine_id":   machine_id,
        "nft_token_id": nft_token_id,
        "agent_id":     agent_id or machine_id,
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_session_jwt(token: str) -> dict:
    """
    Decode and verify a session JWT.

    Raises jwt.ExpiredSignatureError if expired.
    Raises jwt.InvalidTokenError (or subclass) for any other problem.
    Returns the decoded payload dict on success.
    """
    secret = _load_secret()
    return jwt.decode(
        token,
        secret,
        algorithms=[_ALGORITHM],
        issuer=_ISSUER,
        options={"require": ["exp", "iat", "iss", "tenant_id", "machine_id"]},
    )


def decode_session_jwt(token: str) -> Optional[dict]:
    """Safe wrapper — returns None instead of raising on invalid/expired tokens."""
    try:
        return verify_session_jwt(token)
    except jwt.PyJWTError:
        return None
