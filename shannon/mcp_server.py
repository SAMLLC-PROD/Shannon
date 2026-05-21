"""Shannon MCP Server.

Exposes the Shannon personal semantic memory service as a Model Context
Protocol (MCP) server over stdio. Any MCP-compatible AI client (Claude
Desktop, Cursor, VS Code Copilot, etc.) can connect and call the six tools
defined below to read from and write to the user's personal memory.

Architecture:

    AI Client  --(JSON-RPC over stdio)-->  this module  --(direct calls)-->
        shannon.store / shannon.embeddings / shannon.retrieval / shannon.openclaw
        -->  ~/.shannon/dictionary/layer_1/

Design rules (from SHANNON-MCP-SPEC.md):
  * This file MUST NOT duplicate logic that already lives in shannon.*. It
    only wires the existing functions into the MCP protocol surface.
  * No existing shannon/*.py file is modified.
  * stdio transport only (single async event loop, requests serialized).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Shannon internal modules. We call these directly rather than going over HTTP
# so that the MCP server works even when the systemd FastAPI service is down.
from shannon import store, embeddings, retrieval, openclaw
from shannon.api import _ensure_agent, _init_agents_table

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SERVER_NAME = "shannon-memory"
SERVER_VERSION = "0.1.0"

DEFAULT_AGENT = "default"
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
DEFAULT_TOKEN_BUDGET = 4000
VALID_RECENCY = ("hot", "warm", "cold", "all")

logger = logging.getLogger("shannon.mcp")

# --------------------------------------------------------------------------- #
# Path helpers (mirrors shannon's own resolution of SHANNON_HOME)
# --------------------------------------------------------------------------- #

def _shannon_home() -> Path:
    """Return the Shannon data directory (respects SHANNON_HOME env var)."""
    return Path(os.environ.get("SHANNON_HOME", str(Path.home() / ".shannon")))


def _index_db_path() -> Path:
    """Path to layer_1/index.db where agents and entry metadata live."""
    return _shannon_home() / "dictionary" / "layer_1" / "index.db"


# --------------------------------------------------------------------------- #
# Tool schemas
# --------------------------------------------------------------------------- #
#
# JSON Schema definitions for each tool's inputSchema. Kept as a module-level
# constant so tests can validate them without instantiating the server.

TOOL_DEFINITIONS: list[Tool] = [
    Tool(
        name="memory_search",
        description=(
            "Search the user's personal semantic memory. Returns the most "
            "relevant memories scored by semantic similarity and recency. "
            "Use this to recall prior conversations, decisions, project "
            "context, or anything the user has stored."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "agent": {
                    "type": "string",
                    "description": (
                        "Agent ID to filter memories by (optional, "
                        "searches all if omitted)"
                    ),
                    "default": DEFAULT_AGENT,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (1-50)",
                    "default": DEFAULT_SEARCH_LIMIT,
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="memory_retrieve",
        description=(
            "Retrieve relevant memories for a topic, scored by semantic "
            "similarity and recency, within a token budget. This is the "
            "primary way to load context about a subject. Returns the "
            "highest-scoring memories that fit within the token limit."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic to retrieve context for (natural language)",
                },
                "agent": {
                    "type": "string",
                    "description": "Agent ID to filter by",
                    "default": DEFAULT_AGENT,
                },
                "limit_tokens": {
                    "type": "integer",
                    "description": "Maximum tokens to return (default 4000)",
                    "default": DEFAULT_TOKEN_BUDGET,
                    "minimum": 1,
                },
                "recency": {
                    "type": "string",
                    "enum": list(VALID_RECENCY),
                    "description": (
                        "Time window: hot (0-48h), warm (48h-7d), "
                        "cold (7d-30d), all"
                    ),
                    "default": "all",
                },
            },
            "required": ["topic"],
        },
    ),
    Tool(
        name="memory_save",
        description=(
            "Save a new memory to the user's personal long-term memory. Use "
            "this to store important context: decisions, milestones, "
            "insights, lessons learned, or anything worth remembering "
            "across sessions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The memory content to store",
                },
                "agent": {
                    "type": "string",
                    "description": "Agent ID storing this memory",
                    "default": DEFAULT_AGENT,
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Tags for categorization (e.g. "
                        "['project-x', 'decision', 'architecture'])"
                    ),
                    "default": [],
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session identifier for grouping related memories "
                        "(e.g. '2026-05-17-planning')"
                    ),
                },
            },
            "required": ["content"],
        },
    ),
    Tool(
        name="memory_health",
        description=(
            "Check the status of the Shannon memory service. Returns entry "
            "count, embedding coverage, and service version."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="memory_agents",
        description=(
            "List all registered memory agents and their entry counts. "
            "Agents are isolated memory namespaces — each agent only sees "
            "memories tagged with their ID."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="memory_context",
        description=(
            "Regenerate the Shannon context summary file from recent "
            "memories. This creates a time-tiered summary (hot/warm/cold) "
            "of the most important memories. Returns the generated context "
            "text."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]

TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOL_DEFINITIONS}


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #
# Each handler is async and returns a plain string. The top-level dispatcher
# wraps the result in a TextContent block. Handlers translate exceptions into
# human-readable error strings so the AI client gets actionable feedback.

async def _handle_memory_search(args: dict[str, Any]) -> str:
    """Semantic search across stored memories with keyword fallback."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: 'query' parameter is required and cannot be empty."

    agent = str(args.get("agent") or DEFAULT_AGENT)
    raw_limit = args.get("limit", DEFAULT_SEARCH_LIMIT)
    try:
        limit = max(1, min(int(raw_limit), MAX_SEARCH_LIMIT))
    except (TypeError, ValueError):
        return f"Error: 'limit' must be an integer (got {raw_limit!r})."

    _ensure_agent(agent)

    # Mirror the HTTP API approach: fetch recent entries, filter by agent,
    # extract content_hashes, then pass to semantic_search.
    method = "semantic"
    results: list[dict[str, Any]]
    try:
        db_path = _index_db_path()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT content_hash, created_at, session_id, tags "
            "FROM entries ORDER BY created_at DESC LIMIT 2000"
        ).fetchall()
        conn.close()

        # Filter by agent tag
        filtered = [
            r for r in rows
            if agent in json.loads(r["tags"] or "[]")
        ]

        content_hashes = [r["content_hash"] for r in filtered]
        if not content_hashes:
            return f"No memories found for agent {agent!r}."

        hash_to_row = {r["content_hash"]: dict(r) for r in filtered}

        sem_results = embeddings.semantic_search(query, content_hashes, top_k=limit)

        if sem_results:
            results = []
            for ch, score in sem_results:
                row = hash_to_row.get(ch, {})
                body = _safe_read_by_hash(ch)
                results.append({
                    "content_hash": ch,
                    "body": body,
                    "score": score,
                    "tags": json.loads(row.get("tags") or "[]"),
                    "created_at": row.get("created_at", ""),
                    "session_id": row.get("session_id", ""),
                })
        else:
            # semantic_search returned empty (Ollama down?) — keyword fallback
            logger.warning("semantic_search returned no results; keyword fallback")
            results = _keyword_search(query, limit, agent)
            method = "keyword"
    except Exception as exc:
        logger.warning("semantic search failed (%s); using keyword fallback", exc)
        results = _keyword_search(query, limit, agent)
        method = "keyword"

    if not results:
        return f"No memories found for query: {query!r} (method={method})."

    return _format_search_results(query, method, results)


async def _handle_memory_retrieve(args: dict[str, Any]) -> str:
    """Token-budgeted retrieval — the primary way to load topical context."""
    topic = str(args.get("topic", "")).strip()
    if not topic:
        return "Error: 'topic' parameter is required and cannot be empty."

    agent = str(args.get("agent") or DEFAULT_AGENT)
    try:
        limit_tokens = int(args.get("limit_tokens", DEFAULT_TOKEN_BUDGET))
        if limit_tokens < 1:
            raise ValueError
    except (TypeError, ValueError):
        return (
            f"Error: 'limit_tokens' must be a positive integer "
            f"(got {args.get('limit_tokens')!r})."
        )

    recency = str(args.get("recency") or "all").lower()
    if recency not in VALID_RECENCY:
        return (
            f"Error: 'recency' must be one of {'/'.join(VALID_RECENCY)} "
            f"(got {recency!r})."
        )

    _ensure_agent(agent)

    result = retrieval.retrieve(
        agent_id=agent,
        topic=topic,
        limit_tokens=limit_tokens,
        recency=recency,
        relevance_weight=0.7,
        recency_weight=0.3,
    )

    entries = result.get("entries", []) if isinstance(result, dict) else []
    if not entries:
        return (
            f"No memories found for topic: {topic!r} "
            f"(agent={agent}, recency={recency})."
        )

    return _format_retrieval(topic, agent, recency, result)


async def _handle_memory_save(args: dict[str, Any]) -> str:
    """Persist a new memory chunk and trigger embedding."""
    content = str(args.get("content", "")).strip()
    if not content:
        return "Error: 'content' parameter is required and cannot be empty."

    agent = str(args.get("agent") or DEFAULT_AGENT)

    raw_tags = args.get("tags") or []
    if not isinstance(raw_tags, list) or not all(isinstance(t, str) for t in raw_tags):
        return "Error: 'tags' must be an array of strings."
    tags: list[str] = list(raw_tags)

    # Ensure the agent ID is present in tags so per-agent retrieval works.
    if agent and agent not in tags:
        tags = [agent, *tags]

    session_id = str(
        args.get("session_id")
        or f"mcp-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    )

    _ensure_agent(agent)

    content_hash = store.write(content, session_id=session_id, tags=tags)

    embed_status = "embedded"
    try:
        embeddings.embed_and_store(content_hash, content)
    except Exception as exc:
        # Per spec: degrade gracefully if Ollama is down. Backfill later.
        logger.warning("Embedding failed for %s: %s", content_hash, exc)
        embed_status = f"stored without embedding ({type(exc).__name__})"

    # Return compact hash (not Zeckendorf address) — the full address
    # is massive and confuses local models that receive MCP responses.
    short_hash = content_hash[:12] if len(content_hash) > 12 else content_hash
    return (
        f"Memory saved successfully.\n"
        f"- ID: `{short_hash}`\n"
        f"- Session: {session_id}\n"
        f"- Agent: {agent}\n"
        f"- Tags: {', '.join(tags) if tags else '(none)'}\n"
        f"- Embedding: {embed_status}\n"
        f"- Length: {len(content)} chars"
    )


async def _handle_memory_health(args: dict[str, Any]) -> str:
    """Service health: entry counts, embedding coverage, version."""
    store_stats: dict[str, Any]
    try:
        store_stats = store.stats() or {}
    except Exception as exc:
        store_stats = {"error": f"{type(exc).__name__}: {exc}"}

    embed_stats: dict[str, Any]
    try:
        embed_stats = embeddings.embedding_stats() or {}
    except Exception as exc:
        embed_stats = {"error": f"{type(exc).__name__}: {exc}"}

    lines = [
        "# Shannon Memory Service Health",
        "",
        f"- Server: {SERVER_NAME} v{SERVER_VERSION}",
        f"- SHANNON_HOME: `{_shannon_home()}`",
        "",
        "## Store",
    ]
    for key in sorted(store_stats):
        lines.append(f"- {key}: {store_stats[key]}")

    lines.append("")
    lines.append("## Embeddings")
    for key in sorted(embed_stats):
        lines.append(f"- {key}: {embed_stats[key]}")

    return "\n".join(lines)


async def _handle_memory_agents(args: dict[str, Any]) -> str:
    """List registered agents and their entry counts."""
    db_path = _index_db_path()
    if not db_path.exists():
        return (
            "No agents registered yet. Shannon's index database does not "
            f"exist at `{db_path}`. Save a memory first to initialize."
        )

    try:
        agents = _query_agents(db_path)
    except sqlite3.OperationalError as exc:
        return (
            f"Error: agents table is not initialized yet ({exc}). Try "
            "calling memory_save once to bootstrap the schema."
        )

    if not agents:
        return "No agents registered."

    lines = ["# Registered Memory Agents", ""]
    for agent in agents:
        title = agent.get("display_name") or agent["agent_id"]
        lines.append(f"## {title}")
        lines.append(f"- ID: `{agent['agent_id']}`")
        if agent.get("tag_profile"):
            lines.append(f"- Tag profile: {agent['tag_profile']}")
        if agent.get("entry_count") is not None:
            lines.append(f"- Entries: {agent['entry_count']}")
        if agent.get("created_at"):
            lines.append(f"- Created: {agent['created_at']}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def _handle_memory_context(args: dict[str, Any]) -> str:
    """Regenerate the tiered context summary file via openclaw."""
    # Probe for the regeneration entrypoint — exact name isn't pinned in the
    # spec, so we try the most likely candidates in order.
    candidate_names = (
        "regenerate_context",
        "generate_context",
        "build_context",
        "write_context",
        "make_context",
        "regenerate",
    )
    func = None
    for name in candidate_names:
        if hasattr(openclaw, name):
            func = getattr(openclaw, name)
            break

    if func is None:
        return (
            "Error: could not find a context-regeneration function in "
            f"shannon.openclaw. Tried: {', '.join(candidate_names)}."
        )

    try:
        result: Any = func()
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:
        return f"Error generating context: {type(exc).__name__}: {exc}"

    return _coerce_context_result(result)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def _format_search_results(
    query: str,
    method: str,
    results: list[dict[str, Any]],
) -> str:
    """Render semantic-search results as readable Markdown."""
    lines = [
        f"# Memory Search Results",
        f"_Query: {query!r} | Method: {method} | {len(results)} result(s)_",
        "",
    ]
    for i, row in enumerate(results, start=1):
        body = row.get("body")
        if not body:
            body = _safe_read_by_hash(
                row.get("content_hash") or row.get("hash") or row.get("id")
            )
        if not body:
            continue

        score = row.get("score") or row.get("similarity") or 0.0
        tags = row.get("tags") or []
        if isinstance(tags, str):
            # Some stores keep tags as a JSON string column.
            try:
                tags = json.loads(tags)
            except Exception:
                tags = [tags]

        created = row.get("created_at") or row.get("created") or ""

        meta_parts: list[str] = []
        try:
            meta_parts.append(f"Score: {float(score):.3f}")
        except (TypeError, ValueError):
            pass
        if tags:
            meta_parts.append(f"Tags: {', '.join(map(str, tags))}")
        if created:
            meta_parts.append(f"Created: {created}")

        lines.append(f"## Result {i}")
        if meta_parts:
            lines.append(" | ".join(meta_parts))
        lines.append("")
        lines.append(str(body).strip())
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_retrieval(
    topic: str,
    agent: str,
    recency: str,
    result: dict[str, Any],
) -> str:
    """Render retrieve() output as readable Markdown."""
    entries = result.get("entries", [])
    total_tokens = result.get("total_tokens", 0)
    returned = result.get("returned_count", len(entries))
    scored = result.get("scored_count", "?")
    truncated = " (truncated)" if result.get("truncated") else ""

    lines = [
        f"# Memory Retrieval: {topic!r}",
        f"_Agent: {agent} | Recency: {recency} | "
        f"Returned {returned} of {scored} scored | "
        f"~{total_tokens} tokens{truncated}_",
        "",
    ]
    for entry in entries:
        session = entry.get("session_id") or "?"
        created = entry.get("created_at") or ""
        tags = entry.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = [tags]

        score = entry.get("score", 0.0)
        rel = entry.get("relevance_score")
        rec = entry.get("recency_score")
        meta = f"Score: {float(score):.3f}"
        if rel is not None and rec is not None:
            try:
                meta += f" (relevance {float(rel):.2f}, recency {float(rec):.2f})"
            except (TypeError, ValueError):
                pass
        if tags:
            meta += f" | Tags: {', '.join(map(str, tags))}"

        header = f"## Memory: session {session}"
        if created:
            header += f" ({created})"
        lines.append(header)
        lines.append(meta)
        lines.append("")
        lines.append(str(entry.get("body", "")).strip())
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip()


def _safe_read_by_hash(content_hash: Any) -> str | None:
    """Read a chunk body by hash, swallowing errors."""
    if not content_hash:
        return None
    try:
        return store.read_by_hash(str(content_hash))
    except Exception as exc:
        logger.debug("read_by_hash(%s) failed: %s", content_hash, exc)
        return None


def _coerce_context_result(result: Any) -> str:
    """Turn whatever openclaw returns into a string of generated context."""
    if result is None:
        return "Context regeneration completed (no content returned)."

    if isinstance(result, bytes):
        return result.decode("utf-8", errors="replace")

    if isinstance(result, str):
        # A short, path-like string that points at an existing file? Read it.
        candidate = Path(result)
        try:
            if (
                "\n" not in result
                and len(result) < 1024
                and candidate.exists()
                and candidate.is_file()
            ):
                return candidate.read_text(encoding="utf-8")
        except OSError:
            pass
        return result

    if isinstance(result, dict):
        # Common shapes from FastAPI-style handlers.
        for key in ("content", "context", "text", "body", "output"):
            if key in result and isinstance(result[key], str):
                return result[key]
        for key in ("path", "file", "context_path", "output_path"):
            value = result.get(key)
            if isinstance(value, (str, Path)):
                p = Path(value)
                if p.exists() and p.is_file():
                    try:
                        return p.read_text(encoding="utf-8")
                    except OSError:
                        return f"Context written to: {p}"
        return json.dumps(result, indent=2, default=str)

    if isinstance(result, Path):
        if result.exists() and result.is_file():
            return result.read_text(encoding="utf-8")
        return f"Context written to: {result}"

    return str(result)


# --------------------------------------------------------------------------- #
# SQLite helpers (used by memory_agents and the keyword fallback)
# --------------------------------------------------------------------------- #

def _query_agents(db_path: Path) -> list[dict[str, Any]]:
    """Return a list of {agent_id, display_name, tag_profile, created_at,
    entry_count} dicts by querying layer_1/index.db directly.

    Mirrors the same SQL the GET /agents HTTP endpoint runs.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT agent_id, display_name, tag_profile, created_at "
            "FROM agents"
        )
        agents = [dict(row) for row in cur.fetchall()]

        for agent in agents:
            agent_id = agent["agent_id"]
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM entries WHERE tags LIKE ?",
                    (f'%"{agent_id}"%',),
                )
                row = cur.fetchone()
                agent["entry_count"] = row[0] if row else 0
            except sqlite3.OperationalError:
                agent["entry_count"] = None
        return agents
    finally:
        conn.close()


def _keyword_search(
    query: str,
    limit: int,
    agent: str,
) -> list[dict[str, Any]]:
    """LIKE-based fallback when semantic_search is unavailable.

    Intentionally permissive about schema. Returns at most `limit` rows shaped
    like the dicts that semantic_search would produce (content_hash, body,
    tags, created_at, score).
    """
    db_path = _index_db_path()
    if not db_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        like = f"%{query}%"
        try:
            cur.execute(
                "SELECT content_hash, body, tags, created_at "
                "FROM entries "
                "WHERE body LIKE ? AND tags LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (like, f'%"{agent}"%', limit),
            )
            for row in cur.fetchall():
                d = dict(row)
                d["score"] = 0.0
                rows.append(d)
        except sqlite3.OperationalError:
            # Schema isn't what we expected — give up quietly.
            return []
    finally:
        conn.close()
    return rows


# --------------------------------------------------------------------------- #
# MCP server wiring
# --------------------------------------------------------------------------- #

# Dispatch table mapping tool names to async handlers. Tests reach for this
# directly to invoke handlers without going through the stdio transport.
TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[str]]] = {
    "memory_search": _handle_memory_search,
    "memory_retrieve": _handle_memory_retrieve,
    "memory_save": _handle_memory_save,
    "memory_health": _handle_memory_health,
    "memory_agents": _handle_memory_agents,
    "memory_context": _handle_memory_context,
}

server: Server = Server(SERVER_NAME)


@server.list_resources()  # type: ignore[misc]
async def list_resources():
    """Return empty resources list — Shannon exposes tools, not resources."""
    return []


@server.list_prompts()  # type: ignore[misc]
async def list_prompts():
    """Return empty prompts list — Shannon exposes tools, not prompts."""
    return []


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return the full set of memory tools the MCP client can call."""
    return TOOL_DEFINITIONS


@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> list[TextContent]:
    """Top-level dispatch. Catches every handler exception and returns it as
    a TextContent block so a single bad call never tears down the server.
    """
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return [
            TextContent(
                type="text",
                text=(
                    f"Error: unknown tool '{name}'. "
                    f"Available tools: {', '.join(sorted(TOOL_HANDLERS))}."
                ),
            )
        ]
    try:
        text = await handler(arguments or {})
    except Exception as exc:
        logger.exception("Tool %s raised an unhandled exception", name)
        text = f"Error: {type(exc).__name__}: {exc}"
    return [TextContent(type="text", text=text)]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

async def main() -> None:
    """Run the Shannon MCP server over stdio.

    Ensures the on-disk store and agents table exist, then hands control to
    the MCP SDK's stdio transport. Returns when the client closes stdin.
    """
    logging.basicConfig(
        level=os.environ.get("SHANNON_MCP_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Bootstrap on-disk state. init_store() is idempotent; _init_agents_table
    # may raise if it's already there, which is fine.
    try:
        store.init_store()
    except Exception as exc:
        logger.warning("init_store() failed: %s", exc)
    try:
        _init_agents_table()
    except Exception as exc:
        logger.debug("_init_agents_table() said: %s", exc)

    logger.info("Shannon MCP server starting (SHANNON_HOME=%s)", _shannon_home())

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
