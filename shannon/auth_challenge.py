"""
Challenge-response infrastructure for ML-DSA-87 machine authentication.

Challenges are stored in-memory with a 60-second TTL and are one-time-use.
Signature verification requires liboqs; without it, verify_signature() raises
RuntimeError so callers can return a meaningful 503 rather than silently passing.
"""

from __future__ import annotations

import os
import time
import threading
from typing import Optional

try:
    from oqs import Signature as OQSSignature
    _OQS_AVAILABLE = True
except (ImportError, SystemExit, Exception):
    _OQS_AVAILABLE = False
    OQSSignature = None  # type: ignore[assignment,misc]

CHALLENGE_TTL = 60  # seconds

# machine_id → (hex_challenge, monotonic_expires_at)
_store: dict[str, tuple[str, float]] = {}
_lock  = threading.Lock()


def issue_challenge(machine_id: str) -> str:
    """Generate a 32-byte (64 hex char) challenge for machine_id.

    Replaces any existing live challenge for that machine.
    """
    challenge = os.urandom(32).hex()
    with _lock:
        _store[machine_id] = (challenge, time.monotonic() + CHALLENGE_TTL)
    return challenge


def peek_challenge(machine_id: str) -> Optional[str]:
    """Return the live challenge without consuming it. Returns None if absent/expired."""
    with _lock:
        entry = _store.get(machine_id)
        if not entry:
            return None
        challenge, expires_at = entry
        if time.monotonic() > expires_at:
            del _store[machine_id]
            return None
        return challenge


def consume_challenge(machine_id: str) -> Optional[str]:
    """Return and delete the challenge (one-time use). Returns None if absent/expired."""
    with _lock:
        entry = _store.pop(machine_id, None)
    if not entry:
        return None
    challenge, expires_at = entry
    if time.monotonic() > expires_at:
        return None
    return challenge


def verify_signature(public_key_bytes: bytes, challenge_hex: str, signature_hex: str) -> bool:
    """
    Verify an ML-DSA-87 signature.

    The signer should have called:
        sig = Signature("ML-DSA-87", secret_key=sk).sign(bytes.fromhex(challenge_hex))

    Raises RuntimeError if liboqs is not available on this host.
    """
    if not _OQS_AVAILABLE:
        raise RuntimeError(
            "liboqs not installed on this host — cannot verify ML-DSA-87 signatures. "
            "Install liboqs to enable PQC challenge-response auth."
        )
    verifier = OQSSignature("ML-DSA-87", public_key=public_key_bytes)
    return verifier.verify(
        bytes.fromhex(challenge_hex),
        bytes.fromhex(signature_hex),
    )
