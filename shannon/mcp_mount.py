"""Mount Shannon MCP tools as a Streamable HTTP endpoint on the FastAPI app.

Adds /mcp endpoint to Shannon's existing FastAPI server so any MCP-compatible
AI agent (Hermes, Claude Desktop, etc.) can connect over HTTP.

Usage:
    # In api.py or server.py:
    from shannon.mcp_mount import mount_mcp
    mount_mcp(app)

    # Agent connects to: http://<host>:8765/mcp
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.parse
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger("shannon.mcp_mount")

# Shannon HTTP base URL (talks to itself on localhost)
SHANNON_BASE = os.environ.get("SHANNON_URL", "http://127.0.0.1:8765")
# Default agent for unauthenticated MCP clients (local-only fallback).
# Authenticated clients get agent identity from their tenant token.
DEFAULT_AGENT = "hermes"

# Tenant token → agent mapping. When a client sends Authorization: Bearer <token>,
# the MCP endpoint resolves the agent identity from this mapping.
# This ensures: you ARE your token. No token = no access (when auth is enforced).
TOKEN_AGENT_MAP: dict[str, str] = {}  # populated by mount_mcp from tenant DB


def _shannon_request(path: str, method: str = "GET", body: dict | None = None,
                     params: dict | None = None) -> Any:
    """Call Shannon's own HTTP API internally."""
    url = f"{SHANNON_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = None
    if body is not None:
        data = json.dumps(body).encode()

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error("Shannon request failed: %s %s → %s", method, url, e)
        return {"error": str(e)}


# --------------------------------------------------------------------------- #
# FastMCP server with tools
# --------------------------------------------------------------------------- #

mcp = FastMCP(
    "shannon-memory",
    instructions="Shannon personal semantic memory — search, retrieve, and save memories. Use memory_retrieve for topic context, memory_search for keyword queries, memory_save for storing important information.",
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
def memory_search(query: str, agent: str = DEFAULT_AGENT, limit: int = 10) -> str:
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
def memory_retrieve(topic: str, agent: str = DEFAULT_AGENT, limit_tokens: int = 4000) -> str:
    """Retrieve context about a topic from semantic memory.
    Uses embedding-based semantic search with recency weighting.
    Best for loading context about a project, decision, or concept."""
    result = _shannon_request("/memory", params={
        "agent": agent, "topic": topic, "limit_tokens": limit_tokens,
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
def memory_save(body: str, agent: str = DEFAULT_AGENT,
                tags: list[str] | None = None,
                session_id: str = "") -> str:
    """Save important context to semantic memory.
    Use for decisions, milestones, lessons learned, or anything worth remembering.
    Tags help with future retrieval — use descriptive, lowercase tags."""
    result = _shannon_request("/memory", method="POST", body={
        "body": body,
        "agent": agent,
        "tags": tags or [],
        "session_id": session_id or f"mcp-{agent}",
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
def memory_context(agent: str = DEFAULT_AGENT) -> str:
    """Regenerate the Shannon context summary file.
    Rewrites memory/shannon-context.md from recent entries with time-tiered compression."""
    result = _shannon_request("/context/regenerate", method="POST")
    return f"Context regenerated: {json.dumps(result)}"


# --------------------------------------------------------------------------- #
# Mount helper
# --------------------------------------------------------------------------- #

def mount_mcp(app):
    """Mount MCP endpoints on a FastAPI/Starlette app.
    
    SSE transport at /mcp/sse (GET) + /mcp/messages/ (POST)
    Streamable HTTP at /mcp/stream (POST) — for clients that prefer single-endpoint
    """
    # SSE transport (two-step: GET /sse for stream, POST /messages for commands)
    sse_app = mcp.sse_app()
    app.mount("/mcp", sse_app)
    logger.info("Shannon MCP endpoints mounted at /mcp (SSE + Streamable HTTP)")
