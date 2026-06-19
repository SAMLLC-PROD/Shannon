"""Mount Shannon MCP tools as a Streamable HTTP endpoint on the FastAPI app.

Adds /mcp endpoint to Shannon's existing FastAPI server so any MCP-compatible
AI agent (Hermes, Claude Desktop, etc.) can connect over HTTP.

Usage:
    # In api.py or server.py:
    from shannon.mcp_mount import mount_mcp
    mount_mcp(app)

    # Unauthenticated (internal only):
    #   Agent connects to: http://<host>:8765/mcp
    #
    # Authenticated (CaaS machine identity):
    #   Agent connects to: http://<host>:8765/mcp/sse?token=<session_jwt>
    #   Set SHANNON_MCP_AUTH_ENFORCE=1 to require auth.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.parse
from typing import Any

logger = logging.getLogger("shannon.mcp_mount")

# Shannon HTTP base URL (talks to itself on localhost)
SHANNON_BASE = os.environ.get("SHANNON_URL", "http://127.0.0.1:8765")
# Default agent for unauthenticated MCP clients (local-only fallback).
DEFAULT_AGENT = "hermes"


def _resolved_agent(requested: str = "") -> str:
    """Return agent_id from JWT if authenticated, else fall back to requested or DEFAULT_AGENT.

    Authenticated clients cannot override their agent identity — the JWT is authoritative.
    This prevents privilege escalation (one tenant impersonating another's agent).
    """
    from .mcp_auth import _current_mcp_jwt
    payload = _current_mcp_jwt.get()
    if payload:
        return payload.get("agent_id") or DEFAULT_AGENT
    return requested or DEFAULT_AGENT


def _shannon_request(path: str, method: str = "GET", body: dict | None = None,
                     params: dict | None = None) -> Any:
    """Call Shannon's own HTTP API, forwarding the session JWT when available."""
    url = f"{SHANNON_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = None
    if body is not None:
        data = json.dumps(body).encode()

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")

    # Forward the session JWT so memory reads/writes are scoped to the tenant
    from .mcp_auth import _current_mcp_token
    token = _current_mcp_token.get()
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error("Shannon request failed: %s %s → %s", method, url, e)
        return {"error": str(e)}


# --------------------------------------------------------------------------- #
# Mount helper — FastMCP initialised here to avoid module-level mcp import
# --------------------------------------------------------------------------- #

def mount_mcp(app):
    """Mount MCP endpoints on a FastAPI/Starlette app with optional JWT auth.

    SSE transport at  /mcp/sse      (GET)  — connect, pass ?token=<jwt> for auth
                      /mcp/messages (POST) — send MCP commands
    Auth is enforced when SHANNON_MCP_AUTH_ENFORCE=1.
    Without a token, unauthenticated pass-through uses DEFAULT_AGENT.
    """
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from .mcp_auth import MCPAuthMiddleware

    mcp = FastMCP(
        "shannon-memory",
        instructions=(
            "Shannon personal semantic memory — search, retrieve, and save memories. "
            "Use memory_retrieve for topic context, memory_search for keyword queries, "
            "memory_save for storing important information."
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "localhost", "localhost:8765",
                "127.0.0.1", "127.0.0.1:8765",
                "192.168.0.68", "192.168.0.68:8765",
                "192.168.0.64", "192.168.0.64:8765",
            ],
        ),
    )

    @mcp.tool()
    def memory_search(query: str, agent: str = "", limit: int = 10) -> str:
        """Search the user's personal semantic memory by keyword or phrase.
        Returns matching entries ranked by relevance and recency."""
        result = _shannon_request("/memory/search", params={
            "q": query, "limit": min(limit, 50),
        })
        if isinstance(result, dict) and "error" in result:
            return f"Error: {result['error']}"
        entries = result if isinstance(result, list) else result.get("results", [])
        if not entries:
            return f"No results for: {query}"
        lines = [f"## Memory Search: {query}", f"_{len(entries)} results_\n"]
        for i, e in enumerate(entries[:limit], 1):
            body = e.get("body", "")[:500]
            tags = ", ".join(e.get("tags", [])[:5])
            lines.append(f"### [{i}] {e.get('session_id', '')} (score: {e.get('score', 0):.3f})")
            if tags:
                lines.append(f"Tags: {tags}")
            lines.append(body)
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    def memory_retrieve(topic: str, agent: str = "", limit_tokens: int = 4000) -> str:
        """Retrieve context about a topic from semantic memory.
        Uses embedding-based semantic search with recency weighting.
        Best for loading context about a project, decision, or concept."""
        resolved = _resolved_agent(agent)
        result = _shannon_request("/memory", params={
            "agent": resolved, "topic": topic, "limit_tokens": limit_tokens,
        })
        if isinstance(result, dict) and "error" in result:
            return f"Error: {result['error']}"
        entries = result.get("entries", [])
        if not entries:
            return f"No entries for topic: {topic}"
        scored = result.get("scored_count", len(entries))
        lines = [f"## Shannon Context: {topic}", f"_{len(entries)} entries from {scored} scored_\n"]
        for i, e in enumerate(entries[:15], 1):
            body = e.get("body", "")[:600]
            tags = ", ".join(e.get("tags", [])[:5])
            score = e.get("score", 0)
            lines.append(f"### [{i}] {e.get('session_id', '')} (score: {score:.3f})")
            if tags:
                lines.append(f"Tags: {tags}")
            lines.append(body)
            lines.append("")
        return "\n".join(lines)

    @mcp.tool()
    def memory_save(body: str, agent: str = "",
                    tags: list[str] | None = None,
                    session_id: str = "") -> str:
        """Save important context to semantic memory.
        Use for decisions, milestones, lessons learned, or anything worth remembering.
        Tags help with future retrieval — use descriptive, lowercase tags."""
        resolved = _resolved_agent(agent)
        result = _shannon_request("/memory", method="POST", body={
            "body": body,
            "agent": resolved,
            "tags": tags or [],
            "session_id": session_id or f"mcp-{resolved}",
        })
        if isinstance(result, dict) and "error" in result:
            return f"Error saving: {result['error']}"
        return f"Saved to Shannon memory (id: {result.get('id', 'unknown')})"

    @mcp.tool()
    def memory_health() -> str:
        """Check Shannon memory service health and stats."""
        result = _shannon_request("/health")
        return json.dumps(result, indent=2)

    @mcp.tool()
    def memory_agents() -> str:
        """List all agents that have stored memories."""
        result = _shannon_request("/agents")
        return json.dumps(result, indent=2)

    @mcp.tool()
    def memory_context(agent: str = "") -> str:
        """Regenerate the Shannon context summary file.
        Rewrites memory/shannon-context.md from recent entries with time-tiered compression."""
        result = _shannon_request("/context/regenerate", method="POST")
        return f"Context regenerated: {json.dumps(result)}"

    sse_app  = mcp.sse_app()
    auth_app = MCPAuthMiddleware(sse_app)
    app.mount("/mcp", auth_app)
    logger.info(
        "Shannon MCP endpoints mounted at /mcp (auth_enforce=%s)",
        "on" if auth_app.enforce else "off",
    )
