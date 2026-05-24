"""Shannon MCP Server — HTTP API variant.

Instead of importing Shannon modules directly (which requires local data files),
this variant calls the Shannon HTTP API. This means it can connect to Shannon
running anywhere — local workstation, R740xd VM, or a remote Coop.

Tries SHANNON_SERVER (R740xd) first, falls back to SHANNON_LOCAL (localhost).

    python -m shannon.mcp_http
    # or
    SHANNON_URL=http://192.168.0.71:8765 python -m shannon.mcp_http
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
import urllib.parse
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

SERVER_NAME = "shannon-memory-http"
SERVER_VERSION = "0.2.0"

SHANNON_URLS = [
    os.environ.get("SHANNON_URL", "http://192.168.0.71:8765"),  # R740xd server
    "http://localhost:8765",  # local fallback
]
DEFAULT_AGENT = "guy"
DEFAULT_LIMIT = 10
DEFAULT_TOKEN_BUDGET = 4000

logger = logging.getLogger("shannon.mcp_http")


def _shannon_request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    """Make an HTTP request to Shannon, trying server then local."""
    for base_url in SHANNON_URLS:
        try:
            url = f"{base_url}{path}"
            data = json.dumps(body).encode() if body else None
            headers = {"Content-Type": "application/json"} if body else {}
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            logger.debug(f"Shannon {base_url} failed: {e}")
            continue
    raise ConnectionError("Shannon unavailable (all endpoints failed)")


# --------------------------------------------------------------------------- #
# Tool definitions
# --------------------------------------------------------------------------- #

TOOLS = [
    Tool(
        name="memory_search",
        description=(
            "Semantic search across Shannon personal memory. Returns entries "
            "ranked by relevance + recency. Use for: recalling decisions, "
            "finding project context, checking what's known about a topic."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "agent": {"type": "string", "description": "Agent ID (default: guy)", "default": DEFAULT_AGENT},
                "limit": {"type": "integer", "description": "Max results (default: 10)", "default": DEFAULT_LIMIT},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="memory_retrieve",
        description=(
            "Retrieve memory entries by topic with token budget control. "
            "Returns the most relevant entries within the token budget, "
            "scored by semantic relevance + recency decay."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to retrieve context for"},
                "agent": {"type": "string", "description": "Agent ID", "default": DEFAULT_AGENT},
                "limit_tokens": {"type": "integer", "description": "Token budget", "default": DEFAULT_TOKEN_BUDGET},
            },
            "required": ["topic"],
        },
    ),
    Tool(
        name="memory_save",
        description=(
            "Save a new memory entry to Shannon. Use for: recording decisions, "
            "milestones, lessons learned, important context worth remembering."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "Memory content to save"},
                "agent": {"type": "string", "description": "Agent ID", "default": DEFAULT_AGENT},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization",
                    "default": [],
                },
                "session_id": {"type": "string", "description": "Session identifier", "default": ""},
            },
            "required": ["body"],
        },
    ),
    Tool(
        name="memory_health",
        description="Check Shannon service health, entry count, and embedding coverage.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="memory_agents",
        description="List all known agents in the Shannon memory system.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="memory_context",
        description=(
            "Regenerate the Shannon context file (memory/shannon-context.md). "
            "Call after saving important memories to update the flat-file cache."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #

async def handle_memory_search(arguments: dict) -> list[TextContent]:
    query = arguments["query"]
    agent = arguments.get("agent", DEFAULT_AGENT)
    limit = arguments.get("limit", DEFAULT_LIMIT)
    # Use semantic /memory endpoint (not keyword /memory/search) for better results
    encoded = urllib.parse.quote(query)
    token_budget = min(limit * 400, 8000)  # ~400 tokens per entry estimate
    result = _shannon_request(f"/memory?agent={agent}&topic={encoded}&limit_tokens={token_budget}")
    entries = result.get("entries", [])
    if not entries:
        return [TextContent(type="text", text=f"No results for: {query}")]
    lines = [f"## Shannon Search: {query}\n"]
    for i, e in enumerate(entries[:limit], 1):
        body = e.get("body", str(e))[:500]
        tags = ", ".join(e.get("tags", [])[:5])
        lines.append(f"### [{i}] {e.get('session_id', 'unknown')}")
        if tags:
            lines.append(f"Tags: {tags}")
        lines.append(body)
        lines.append("")
    return [TextContent(type="text", text="\n".join(lines))]


async def handle_memory_retrieve(arguments: dict) -> list[TextContent]:
    topic = arguments["topic"]
    agent = arguments.get("agent", DEFAULT_AGENT)
    limit_tokens = arguments.get("limit_tokens", DEFAULT_TOKEN_BUDGET)
    encoded = urllib.parse.quote(topic)
    result = _shannon_request(f"/memory?agent={agent}&topic={encoded}&limit_tokens={limit_tokens}")
    entries = result.get("entries", [])
    returned = result.get("returned_count", len(entries))
    scored = result.get("scored_count", 0)
    if not entries:
        return [TextContent(type="text", text=f"No entries for topic: {topic}")]
    lines = [f"## Shannon Context: {topic}", f"_{returned} entries from {scored} scored_\n"]
    for i, e in enumerate(entries[:15], 1):
        body = e.get("body", "")[:600]
        tags = ", ".join(e.get("tags", [])[:5])
        score = e.get("score", 0)
        lines.append(f"### [{i}] {e.get('session_id', '')} (score: {score:.3f})")
        if tags:
            lines.append(f"Tags: {tags}")
        lines.append(body)
        lines.append("")
    return [TextContent(type="text", text="\n".join(lines))]


async def handle_memory_save(arguments: dict) -> list[TextContent]:
    body = arguments["body"]
    agent = arguments.get("agent", DEFAULT_AGENT)
    tags = arguments.get("tags", [])
    session_id = arguments.get("session_id", f"mcp-{agent}")
    result = _shannon_request("/memory", method="POST", body={
        "body": body,
        "agent": agent,
        "tags": tags,
        "session_id": session_id,
    })
    return [TextContent(type="text", text=f"Saved: {json.dumps(result)}")]


async def handle_memory_health(arguments: dict) -> list[TextContent]:
    result = _shannon_request("/health")
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def handle_memory_agents(arguments: dict) -> list[TextContent]:
    result = _shannon_request("/agents")
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def handle_memory_context(arguments: dict) -> list[TextContent]:
    result = _shannon_request("/context/regenerate", method="POST")
    return [TextContent(type="text", text=f"Context regenerated: {json.dumps(result)}")]


HANDLERS = {
    "memory_search": handle_memory_search,
    "memory_retrieve": handle_memory_retrieve,
    "memory_save": handle_memory_save,
    "memory_health": handle_memory_health,
    "memory_agents": handle_memory_agents,
    "memory_context": handle_memory_context,
}


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #

async def main():
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = HANDLERS.get(name)
        if not handler:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            return await handler(arguments)
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
