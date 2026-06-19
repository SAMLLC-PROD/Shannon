"""Shannon CaaS — FastAPI router for multi-tenant endpoints.

Mounts on the main app. New routes:
  POST /tenant/register
  GET  /tenant/status
  POST /tenant/pause
  POST /tenant/wipe
  GET  /tenant/export
  GET  /tenant/audit
  GET  /source/{entry_id}

The existing /memory endpoints gain optional bearer-token auth:
  - Bearer token present → tenant-scoped read/write
  - No bearer token     → existing internal agent behavior unchanged
"""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr

from .tenants import (
    authenticate,
    get_tenant_stats,
    log_trial_request,
    pause_tenant,
    register_tenant,
    wipe_tenant,
    init_tenant_schema,
    register_machine_identity,
    get_machine_identity,
    update_identity_last_auth,
    create_session,
    authenticate_session,
    SESSION_TTL_SECONDS,
)
from .export import export_tenant_memory
from .source_viewer import auth_error_page, render_source_page
from .audit_log import log_auth_event
from . import audit_log as _audit

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def _get_tenant(authorization: Annotated[Optional[str], Header()] = None) -> Optional[dict]:
    """
    Extract and validate bearer token from Authorization header.
    Supports both tenant-wide tokens and profile-scoped tokens.
    Profile tokens restrict ALL queries to that single profile's data.
    """
    if not authorization:
        return None

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization must be Bearer token")

    token = authorization.removeprefix("Bearer ").strip()

    # 1. Try JWT session token (challenge-response auth — Phase 4+)
    from .jwt_tokens import decode_session_jwt
    jwt_payload = decode_session_jwt(token)
    if jwt_payload is not None:
        session = authenticate_session(token)
        if not session:
            raise HTTPException(status_code=401, detail="Session token expired or revoked.")
        from .store import _connect as _sc
        conn = _sc()
        row = conn.execute(
            "SELECT * FROM tenants WHERE tenant_id = ?",
            (jwt_payload["tenant_id"],),
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=401, detail="Tenant not found.")
        tenant_row = dict(row)
        if tenant_row["status"] in ("paused", "wiped", "disabled"):
            raise HTTPException(status_code=403, detail="Tenant account is not active.")
        tenant_row["scope"]      = "session"
        tenant_row["agent_id"]   = jwt_payload.get("agent_id")
        tenant_row["machine_id"] = jwt_payload.get("machine_id")
        return tenant_row

    # 2. Try profile-scoped token
    from .tenants import authenticate_profile
    profile_auth = authenticate_profile(token)
    if profile_auth:
        return profile_auth  # has scope="profile", profile_id set

    # 3. Fall back to static tenant-wide Bearer token
    tenant = authenticate(token)

    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    status = tenant["status"]
    if status == "paused":
        raise HTTPException(
            status_code=403,
            detail=(
                "Your trial has ended and your account is paused. "
                "Contact us at shannon@latticeproxy.io to continue. "
                "Your data is retained for 30 days."
            ),
        )
    if status == "wiped":
        raise HTTPException(status_code=403, detail="Account data has been wiped.")

    return tenant


RequiredTenant = Annotated[dict, Depends(_get_tenant)]


async def _require_tenant(authorization: Annotated[Optional[str], Header()] = None) -> dict:
    """Like _get_tenant but raises 401 if no token provided."""
    tenant = await _get_tenant(authorization)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Bearer token required for this endpoint")
    return tenant


AuthRequired = Annotated[dict, Depends(_require_tenant)]


# ---------------------------------------------------------------------------
# POST /tenant/register
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    display_name: Optional[str] = None


@router.post("/tenant/register", status_code=201)
def register(payload: RegisterRequest):
    """
    Register a new tenant. Returns tenant_id and auth_token.
    The auth_token is shown once — store it securely.
    Trial: 14 days free, then paused (not deleted). No auto-charge.
    """
    init_tenant_schema()
    # Basic email sanity check
    if "@" not in payload.email or len(payload.email) < 5:
        raise HTTPException(status_code=422, detail="Valid email required")

    try:
        tenant_id, token = register_tenant(
            email=payload.email,
            display_name=payload.display_name or "",
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="Email already registered")
        log.exception("Registration failed")
        raise HTTPException(status_code=500, detail="Registration failed")

    return {
        "tenant_id": tenant_id,
        "auth_token": token,
        "message": (
            "Store your auth_token securely — it won't be shown again. "
            "Use it as a Bearer token on all /memory requests."
        ),
        "trial_days": 14,
        "note": "No auto-charge. You'll be paused (not deleted) after 14 days.",
    }


# ---------------------------------------------------------------------------
# GET /tenant/status
# ---------------------------------------------------------------------------

@router.get("/tenant/status")
def tenant_status(tenant: AuthRequired):
    """Return trial status, entry count, and storage used for the calling tenant."""
    stats = get_tenant_stats(tenant["tenant_id"])
    return stats


# ---------------------------------------------------------------------------
# POST /tenant/pause
# ---------------------------------------------------------------------------

@router.post("/tenant/pause")
def tenant_pause(tenant: AuthRequired):
    """Manually pause service. Data is retained; 30-day grace period starts now."""
    tid = tenant["tenant_id"]
    pause_tenant(tid)
    return {
        "ok": True,
        "tenant_id": tid,
        "message": "Account paused. Data retained for 30 days. Contact us to resume.",
    }


# ---------------------------------------------------------------------------
# POST /tenant/wipe
# ---------------------------------------------------------------------------

class WipeRequest(BaseModel):
    confirm: bool  # must be True


@router.post("/tenant/wipe")
def tenant_wipe(payload: WipeRequest, tenant: AuthRequired):
    """Permanently delete all tenant data. Irreversible."""
    if not payload.confirm:
        raise HTTPException(
            status_code=422,
            detail='Set {"confirm": true} to confirm permanent data deletion.',
        )
    tid = tenant["tenant_id"]
    wipe_tenant(tid)
    return {
        "ok": True,
        "tenant_id": tid,
        "message": "All data permanently deleted.",
    }


# ---------------------------------------------------------------------------
# GET /tenant/export
# ---------------------------------------------------------------------------

@router.get("/tenant/export")
def tenant_export(
    tenant: AuthRequired,
    topic: Optional[str] = Query(None, description="Semantic topic filter"),
    format: str = Query("markdown", description="Output format (markdown only for now)"),
    limit_tokens: int = Query(8000, ge=500, le=32000, description="Max token budget"),
):
    """
    Export tenant's knowledge as a structured markdown document.
    Designed to paste into ChatGPT / Claude / Gemini as context.
    YouTube references include timestamps; file references include paths.
    """
    tid = tenant["tenant_id"]
    log_trial_request(tid, "GET", "/tenant/export")

    markdown = export_tenant_memory(
        tenant_id=tid,
        topic=topic,
        limit_tokens=limit_tokens,
        format=format,
    )
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="shannon-export.md"'},
    )


# ---------------------------------------------------------------------------
# GET /tenant/audit  — CaaS auth event log
# ---------------------------------------------------------------------------

@router.get("/tenant/audit")
def tenant_audit(
    tenant: AuthRequired,
    limit: int = Query(50, ge=1, le=200, description="Max events to return"),
):
    """
    Return recent CaaS auth events for this tenant, newest first.

    Events: identity_registered, challenge_issued, auth_success,
            auth_failure, access_disabled, access_enabled.
    """
    from .audit_log import get_audit_log
    entries = get_audit_log(tenant["tenant_id"], limit=limit)
    return {
        "tenant_id": tenant["tenant_id"],
        "count":     len(entries),
        "entries":   entries,
    }


# ---------------------------------------------------------------------------
# GET /source/{entry_id}  — HTML source viewer
# ---------------------------------------------------------------------------

@router.get("/source/{entry_id}", response_class=HTMLResponse)
def source_viewer(
    entry_id: str,
    request: Request,
    token: Optional[str] = Query(None, description="Auth token"),
):
    """
    Clean HTML page showing the source material for a memory entry.
    Auth: token query param OR Authorization Bearer header.
    Internal admin access (no token) is allowed for null-tenant entries.
    """
    # Try token from query param first, then Authorization header
    auth_header = request.headers.get("authorization", "")
    bearer = None
    if token:
        bearer = token
    elif auth_header.startswith("Bearer "):
        bearer = auth_header.removeprefix("Bearer ").strip()

    if bearer:
        tenant = authenticate(bearer)
        if tenant is None:
            return HTMLResponse(content=auth_error_page(), status_code=401)
        if tenant["status"] in ("paused", "wiped"):
            return HTMLResponse(content=auth_error_page(), status_code=403)

        # Verify this tenant owns the entry
        from .store import _connect
        conn = _connect()
        row = conn.execute(
            "SELECT tenant_id FROM entries WHERE content_hash = ?", (entry_id,)
        ).fetchone()
        conn.close()

        if row is None:
            return HTMLResponse(
                content=render_source_page(entry_id, tenant_id=None),
                status_code=404,
            )

        entry_tenant = row["tenant_id"]
        if entry_tenant is not None and entry_tenant != tenant["tenant_id"]:
            return HTMLResponse(content=auth_error_page(), status_code=403)

        html_content = render_source_page(entry_id, tenant_id=tenant["tenant_id"])
        return HTMLResponse(content=html_content)

    # No token provided — allow access only for internal (null tenant_id) entries
    from .store import _connect
    conn = _connect()
    row = conn.execute(
        "SELECT tenant_id FROM entries WHERE content_hash = ?", (entry_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return HTMLResponse(content=render_source_page(entry_id), status_code=404)

    if row["tenant_id"] is not None:
        # Tenant-owned entry requires auth
        return HTMLResponse(content=auth_error_page(), status_code=401)

    return HTMLResponse(content=render_source_page(entry_id))


# ---------------------------------------------------------------------------
# Knowledge Profile endpoints
# ---------------------------------------------------------------------------

from .tenants import create_profile, list_profiles, delete_profile, get_profile


class CreateProfileRequest(BaseModel):
    name: str
    description: Optional[str] = ""


@router.post("/tenant/profiles", status_code=201)
def create_profile_endpoint(payload: CreateProfileRequest, tenant: AuthRequired):
    """Create a new knowledge profile (e.g., 'LS3 Turbo Builds', 'K-Series NA')."""
    profile_id = create_profile(
        tenant_id=tenant["tenant_id"],
        name=payload.name,
        description=payload.description or "",
    )
    return {"profile_id": profile_id, "name": payload.name}


@router.get("/tenant/profiles")
def list_profiles_endpoint(tenant: AuthRequired):
    """List all knowledge profiles with entry counts."""
    profiles = list_profiles(tenant["tenant_id"])
    return {"profiles": profiles}


class DeleteProfileRequest(BaseModel):
    confirm: bool


@router.delete("/tenant/profiles/{profile_id}")
def delete_profile_endpoint(
    profile_id: str, payload: DeleteProfileRequest, tenant: AuthRequired
):
    """Delete a knowledge profile and all its entries."""
    if not payload.confirm:
        raise HTTPException(status_code=422, detail='Set {"confirm": true}')
    deleted = delete_profile(tenant["tenant_id"], profile_id)
    if deleted == 0 and not get_profile(tenant["tenant_id"], profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"ok": True, "entries_deleted": deleted}


# ---------------------------------------------------------------------------
# Token toggle — disable/enable Shannon access
# ---------------------------------------------------------------------------

from .tenants import disable_token, enable_token

@router.post("/tenant/disable")
def disable_access(tenant: AuthRequired):
    """Kill switch — immediately revoke all access. Data retained."""
    tid = tenant["tenant_id"]
    disable_token(tid)
    log_auth_event(_audit.ACCESS_DISABLED, tid, detail="Tenant access disabled via kill switch")
    return {
        "ok": True,
        "tenant_id": tid,
        "message": "Access disabled. All API calls will fail. Data is retained. Call /tenant/enable to restore.",
    }


# ---------------------------------------------------------------------------
# Profile-scoped token auth — cryptographic data separation
# ---------------------------------------------------------------------------

from .tenants import authenticate_profile, generate_profile_token


# ---------------------------------------------------------------------------
# POST /tenant/resolve-conflict (Issue #17)
# ---------------------------------------------------------------------------

class ResolveConflictRequest(BaseModel):
    conflict_group_id: str
    winning_entry_id: str


@router.post("/tenant/resolve-conflict")
def resolve_conflict_endpoint(payload: ResolveConflictRequest, tenant: AuthRequired):
    """
    Resolve a detected conflict by declaring a winning entry.
    The losing entries in the group are deprioritized (0.3x score multiplier)
    but not deleted.
    """
    from .store import resolve_conflict
    tid = tenant["tenant_id"]
    updated = resolve_conflict(
        conflict_group_id=payload.conflict_group_id,
        winning_entry_id=payload.winning_entry_id,
        tenant_id=tid,
    )
    if updated == 0:
        raise HTTPException(
            status_code=404,
            detail="Conflict group not found or no entries to supersede",
        )
    return {
        "ok": True,
        "conflict_group_id": payload.conflict_group_id,
        "winning_entry_id": payload.winning_entry_id,
        "entries_superseded": updated,
    }


# ---------------------------------------------------------------------------
# POST /tenant/identity/register  — link NFT to tenant
# ---------------------------------------------------------------------------

class RegisterIdentityRequest(BaseModel):
    nft_token_id:   int
    machine_name:   str
    wallet_address: str
    public_key_hex: str  # ML-DSA-87 public key, 2592 bytes → 5184 hex chars


@router.post("/tenant/identity/register", status_code=201)
def register_identity(payload: RegisterIdentityRequest, tenant: AuthRequired):
    """
    Bind a LatticeIdentity NFT to the calling tenant.

    The machine's ML-DSA-87 public key is cached here so subsequent
    challenge-response verifications don't require an on-chain RPC call.
    The client is responsible for providing the correct public key; the
    operator can verify it against Polygon at any time.
    """
    try:
        pk_bytes = bytes.fromhex(payload.public_key_hex)
    except ValueError:
        raise HTTPException(status_code=422, detail="public_key_hex is not valid hex")

    if len(pk_bytes) != 2592:
        raise HTTPException(
            status_code=422,
            detail=f"ML-DSA-87 public key must be 2592 bytes, got {len(pk_bytes)}",
        )

    register_machine_identity(
        tenant_id=tenant["tenant_id"],
        nft_token_id=payload.nft_token_id,
        machine_name=payload.machine_name,
        wallet_address=payload.wallet_address,
        public_key_bytes=pk_bytes,
    )
    log_auth_event(
        _audit.IDENTITY_REGISTERED,
        tenant["tenant_id"],
        machine_id=payload.machine_name,
        nft_token_id=payload.nft_token_id,
        detail=f"ML-DSA-87 identity registered for NFT #{payload.nft_token_id}",
    )
    return {
        "ok": True,
        "tenant_id": tenant["tenant_id"],
        "nft_token_id": payload.nft_token_id,
        "machine_name": payload.machine_name,
        "message": (
            "Identity registered. Use POST /tenant/auth/challenge to begin "
            "a challenge-response session."
        ),
    }


# ---------------------------------------------------------------------------
# POST /tenant/auth/challenge  — issue a challenge
# ---------------------------------------------------------------------------

class ChallengeRequest(BaseModel):
    machine_id:   str
    nft_token_id: int


@router.post("/tenant/auth/challenge")
def auth_challenge(payload: ChallengeRequest):
    """
    Issue a 32-byte (64 hex char) challenge for the named machine.

    The machine must sign the challenge bytes with its ML-DSA-87 private key
    and submit within 60 seconds to POST /tenant/auth/verify.
    Challenges are one-time-use; a new call replaces any pending challenge.
    """
    from .auth_challenge import issue_challenge

    # Verify the NFT is registered with some tenant before issuing a challenge
    identity = get_machine_identity(payload.nft_token_id)
    if not identity:
        raise HTTPException(
            status_code=404,
            detail=f"NFT token_id {payload.nft_token_id} not registered. "
                   "Call POST /tenant/identity/register first.",
        )

    challenge = issue_challenge(payload.machine_id)
    log_auth_event(
        _audit.CHALLENGE_ISSUED,
        identity["tenant_id"],
        machine_id=payload.machine_id,
        nft_token_id=payload.nft_token_id,
        detail="Challenge issued (60s TTL)",
    )
    return {
        "challenge":   challenge,
        "machine_id":  payload.machine_id,
        "expires_in":  60,
        "instruction": (
            "Sign the challenge bytes (bytes.fromhex(challenge)) with your "
            "ML-DSA-87 private key and POST the hex signature to /tenant/auth/verify."
        ),
    }


# ---------------------------------------------------------------------------
# POST /tenant/auth/verify  — verify signature, issue session token
# ---------------------------------------------------------------------------

class VerifyRequest(BaseModel):
    machine_id:    str
    nft_token_id:  int
    challenge:     str  # must match the issued challenge (replay guard)
    signature_hex: str  # ML-DSA-87 signature over bytes.fromhex(challenge)


@router.post("/tenant/auth/verify")
def auth_verify(payload: VerifyRequest):
    """
    Verify an ML-DSA-87 challenge-response and issue a short-lived session token.

    Steps:
      1. Retrieve the stored challenge (must exist and not be expired)
      2. Verify the challenge matches what was issued
      3. Load the cached ML-DSA-87 public key for the NFT
      4. Verify the signature
      5. Issue a session token (valid for 1 hour)
      6. Consume the challenge (one-time use)
    """
    from .auth_challenge import consume_challenge, verify_signature, peek_challenge

    # 1. Check the challenge exists
    live_challenge = peek_challenge(payload.machine_id)
    if not live_challenge:
        identity = get_machine_identity(payload.nft_token_id)
        if identity:
            log_auth_event(
                _audit.AUTH_FAILURE,
                identity["tenant_id"],
                machine_id=payload.machine_id,
                nft_token_id=payload.nft_token_id,
                detail="No active challenge — expired or never issued",
            )
        raise HTTPException(
            status_code=401,
            detail="No active challenge for this machine_id — it may have expired (60s TTL). "
                   "Request a new one via POST /tenant/auth/challenge.",
        )

    # 2. Verify it matches what was issued (prevents substitution attacks)
    if live_challenge != payload.challenge:
        identity = get_machine_identity(payload.nft_token_id)
        if identity:
            log_auth_event(
                _audit.AUTH_FAILURE,
                identity["tenant_id"],
                machine_id=payload.machine_id,
                nft_token_id=payload.nft_token_id,
                detail="Challenge mismatch — possible replay or substitution attempt",
            )
        raise HTTPException(status_code=401, detail="Challenge mismatch.")

    # 3. Load public key
    identity = get_machine_identity(payload.nft_token_id)
    if not identity:
        raise HTTPException(
            status_code=404,
            detail=f"NFT token_id {payload.nft_token_id} not registered.",
        )

    # 4. Verify signature
    try:
        valid = verify_signature(
            identity["public_key_bytes"],
            payload.challenge,
            payload.signature_hex,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Signature verification unavailable: {exc}",
        )

    if not valid:
        log_auth_event(
            _audit.AUTH_FAILURE,
            identity["tenant_id"],
            machine_id=payload.machine_id,
            nft_token_id=payload.nft_token_id,
            detail="ML-DSA-87 signature verification failed",
        )
        raise HTTPException(status_code=401, detail="Signature verification failed.")

    # 5 & 6. Consume challenge (one-time use) and issue session token
    consume_challenge(payload.machine_id)
    update_identity_last_auth(identity["tenant_id"], payload.nft_token_id)
    session_token = create_session(
        tenant_id=identity["tenant_id"],
        machine_id=payload.machine_id,
        nft_token_id=payload.nft_token_id,
    )

    log_auth_event(
        _audit.AUTH_SUCCESS,
        identity["tenant_id"],
        machine_id=payload.machine_id,
        nft_token_id=payload.nft_token_id,
        detail=f"Session issued, expires_in={SESSION_TTL_SECONDS}s",
    )
    return {
        "session_token": session_token,
        "tenant_id":     identity["tenant_id"],
        "machine_id":    payload.machine_id,
        "nft_token_id":  payload.nft_token_id,
        "machine_name":  identity["machine_name"],
        "expires_in":    SESSION_TTL_SECONDS,
    }


@router.post("/tenant/profiles/{profile_id}/token")
def generate_token_for_profile(profile_id: str, tenant: AuthRequired):
    """Generate an access token scoped to a single profile.
    This token can ONLY read/write data in this one profile.
    Give this token to someone and they can only see this profile's data."""
    try:
        token = generate_profile_token(tenant["tenant_id"], profile_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "profile_id": profile_id,
        "profile_token": token,
        "message": "This token only has access to this single profile. No other data is visible.",
    }
