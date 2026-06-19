"""
Lattice CaaS (Context as a Service) auth client.

Authenticates a Lattice machine identity (ML-DSA-87 keypair) against
Shannon's challenge-response endpoints and obtains a JWT session token.

Usage:
    client = CaaSClient()                        # reads ~/.lattice/identity/
    token  = client.get_token()                  # authenticate + cache
    url    = client.mcp_url()                    # /mcp/sse?token=<jwt>
    data   = client.request("/memory/search", params={"q": "infra"})

    # Explicit identity dir or Shannon URL:
    client = CaaSClient(
        shannon_url="http://192.168.0.68:8765",
        identity_dir=Path("/etc/lattice/identity"),
    )
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("shannon.caas_client")

_DEFAULT_IDENTITY_DIR = Path.home() / ".lattice" / "identity"
_REFRESH_MARGIN       = 300   # re-auth 5 min before expiry
_DEFAULT_SHANNON_URL  = os.environ.get("SHANNON_URL", "http://127.0.0.1:8765")


class CaaSAuthError(Exception):
    """Raised when CaaS authentication fails and cannot be retried."""


class CaaSClient:
    """
    Manages ML-DSA-87 challenge-response authentication against Shannon CaaS.

    Thread-safety: each instance holds its own token state. Use one instance
    per machine identity. Calling get_token() from multiple threads is safe
    only if you add external locking; for single-threaded agents it is fine.
    """

    def __init__(
        self,
        shannon_url: str = _DEFAULT_SHANNON_URL,
        identity_dir: Optional[Path] = None,
    ):
        self.shannon_url  = shannon_url.rstrip("/")
        self.identity_dir = Path(identity_dir or _DEFAULT_IDENTITY_DIR)
        self._token:      Optional[str] = None
        self._expires_at: float         = 0.0

    # ── Identity loading ─────────────────────────────────────────────────────

    def load_config(self) -> dict:
        """Return parsed identity config.json."""
        cfg_file = self.identity_dir / "config.json"
        if not cfg_file.exists():
            raise CaaSAuthError(
                f"No identity config at {cfg_file}. "
                "Run 'lattice-identity generate --name <machine>' first."
            )
        return json.loads(cfg_file.read_text())

    def load_public_key_hex(self) -> str:
        """Return hex-encoded ML-DSA-87 public key."""
        pub_file = self.identity_dir / "ml_dsa_87.pub"
        if not pub_file.exists():
            raise CaaSAuthError(f"Public key not found: {pub_file}")
        return pub_file.read_text().strip()

    def _sign_challenge(self, challenge_hex: str) -> str:
        """Sign challenge bytes with ML-DSA-87 private key. Requires liboqs."""
        try:
            from oqs import Signature  # type: ignore[import]
        except (ImportError, SystemExit, Exception) as exc:
            raise CaaSAuthError(
                "liboqs not available — cannot sign CaaS challenge. "
                "Install oqs-python (requires liboqs shared library)."
            ) from exc

        key_file = self.identity_dir / "ml_dsa_87.key"
        if not key_file.exists():
            raise CaaSAuthError(f"Private key not found: {key_file}")
        perms = oct(os.stat(key_file).st_mode & 0o777)
        if perms != "0o600":
            raise CaaSAuthError(
                f"Private key {key_file} has insecure permissions {perms}. "
                "Run: chmod 600 " + str(key_file)
            )
        secret_key = bytes.fromhex(key_file.read_text().strip())
        signer     = Signature("ML-DSA-87", secret_key=secret_key)
        signature  = signer.sign(bytes.fromhex(challenge_hex))
        signer.free()
        return signature.hex()

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _http(
        self,
        path: str,
        method: str = "GET",
        body: Optional[dict] = None,
        params: Optional[dict] = None,
        bearer: Optional[str] = None,
    ) -> Any:
        url = self.shannon_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req  = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if bearer:
            req.add_header("Authorization", f"Bearer {bearer}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                detail = json.loads(raw).get("detail", exc.reason)
            except Exception:
                detail = exc.reason
            raise CaaSAuthError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise CaaSAuthError(f"Request failed ({path}): {exc}") from exc

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, tenant_bearer: str) -> dict:
        """
        Register this machine identity with Shannon CaaS.

        Requires the tenant's bearer token (opaque API key).
        Safe to call multiple times — Shannon upserts the row.
        """
        cfg         = self.load_config()
        pub_key_hex = self.load_public_key_hex()
        nft_token_id = cfg.get("nft_token_id")
        if not nft_token_id:
            raise CaaSAuthError(
                "nft_token_id not set in identity config. "
                "Mint the LatticeIdentity NFT first, then update config.json."
            )
        result = self._http(
            "/tenant/identity/register",
            method="POST",
            body={
                "nft_token_id":   int(nft_token_id),
                "machine_name":   cfg["machine_name"],
                "wallet_address": cfg.get("wallet_address") or "0x0",
                "public_key_hex": pub_key_hex,
            },
            bearer=tenant_bearer,
        )
        logger.info(
            "CaaS identity registered: machine=%s nft=%s",
            cfg["machine_name"], nft_token_id,
        )
        return result

    # ── Auth flow ─────────────────────────────────────────────────────────────

    def authenticate(self) -> str:
        """
        Full ML-DSA-87 challenge-response. Returns and caches JWT session token.

        Raises CaaSAuthError on any failure (no identity, no liboqs, bad sig, etc.)
        """
        cfg          = self.load_config()
        machine_id   = cfg["machine_name"]
        nft_token_id = cfg.get("nft_token_id")
        if not nft_token_id:
            raise CaaSAuthError(
                "nft_token_id not set in identity config. "
                "Register this identity with Shannon CaaS first."
            )
        nft_int = int(nft_token_id)

        # 1. Request challenge
        ch_resp   = self._http("/tenant/auth/challenge", method="POST", body={
            "machine_id":   machine_id,
            "nft_token_id": nft_int,
        })
        challenge = ch_resp.get("challenge")
        if not challenge:
            raise CaaSAuthError(f"No challenge in response: {ch_resp}")
        logger.debug("CaaS challenge received: machine=%s nft=%s", machine_id, nft_int)

        # 2. Sign challenge
        sig_hex = self._sign_challenge(challenge)

        # 3. Submit signature → JWT
        sess_resp = self._http("/tenant/auth/verify", method="POST", body={
            "machine_id":    machine_id,
            "nft_token_id":  nft_int,
            "challenge":     challenge,
            "signature_hex": sig_hex,
        })
        token = sess_resp.get("session_token")
        if not token:
            raise CaaSAuthError(f"No session_token in verify response: {sess_resp}")

        expires_in       = int(sess_resp.get("expires_in", 3600))
        self._token      = token
        self._expires_at = time.monotonic() + expires_in

        logger.info(
            "CaaS authenticated: machine=%s nft=%s expires_in=%ds",
            machine_id, nft_int, expires_in,
        )
        return token

    def get_token(self) -> str:
        """Return cached JWT, re-authenticating if within the refresh margin."""
        if self._token and time.monotonic() < self._expires_at - _REFRESH_MARGIN:
            return self._token
        return self.authenticate()

    # ── Convenience ───────────────────────────────────────────────────────────

    def mcp_url(self) -> str:
        """Return the authenticated Shannon MCP SSE endpoint URL."""
        return f"{self.shannon_url}/mcp/sse?token={self.get_token()}"

    def request(
        self,
        path: str,
        method: str = "GET",
        body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """Make an authenticated request to Shannon's REST API."""
        return self._http(
            path, method=method, body=body, params=params,
            bearer=self.get_token(),
        )
