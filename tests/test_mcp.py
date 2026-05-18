"""Tests for shannon.mcp_server.

These tests exercise the MCP-layer logic directly: they call the async
tool handlers without going through the stdio transport, and mock out
the shannon.* internals so the suite never touches the real ~/.shannon
directory. One round-trip test sets SHANNON_HOME to a tmp dir and uses
real (but isolated) shannon modules.

Run with:

    python -m pytest tests/test_mcp.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Skip the entire file if the shannon package or the mcp SDK is missing.
shannon = pytest.importorskip("shannon")
pytest.importorskip("mcp")

from shannon import mcp_server  # noqa: E402
from mcp.types import Tool  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def run_async(coro):
    """Run an async coroutine synchronously inside a sync test."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1. Tool schema validation
# --------------------------------------------------------------------------- #

class TestToolSchemas:
    """Every tool must have a valid name, description, and JSON Schema input."""

    def test_six_tools_exposed(self):
        names = {t.name for t in mcp_server.TOOL_DEFINITIONS}
        assert names == {
            "memory_search",
            "memory_retrieve",
            "memory_save",
            "memory_health",
            "memory_agents",
            "memory_context",
        }

    def test_each_tool_is_well_formed(self):
        for tool in mcp_server.TOOL_DEFINITIONS:
            assert isinstance(tool, Tool)
            assert isinstance(tool.name, str) and tool.name
            assert isinstance(tool.description, str) and len(tool.description) > 20
            schema = tool.inputSchema
            assert isinstance(schema, dict)
            assert schema.get("type") == "object"
            assert "properties" in schema and isinstance(schema["properties"], dict)

    def test_required_fields_present(self):
        by_name = {t.name: t for t in mcp_server.TOOL_DEFINITIONS}
        assert by_name["memory_search"].inputSchema["required"] == ["query"]
        assert by_name["memory_retrieve"].inputSchema["required"] == ["topic"]
        assert by_name["memory_save"].inputSchema["required"] == ["content"]
        # No-arg tools should not declare any required keys.
        for name in ("memory_health", "memory_agents", "memory_context"):
            assert "required" not in by_name[name].inputSchema or \
                   by_name[name].inputSchema.get("required") == []

    def test_recency_enum(self):
        retrieve = next(
            t for t in mcp_server.TOOL_DEFINITIONS if t.name == "memory_retrieve"
        )
        recency = retrieve.inputSchema["properties"]["recency"]
        assert recency["enum"] == ["hot", "warm", "cold", "all"]

    def test_search_limit_bounds(self):
        search = next(
            t for t in mcp_server.TOOL_DEFINITIONS if t.name == "memory_search"
        )
        limit = search.inputSchema["properties"]["limit"]
        assert limit["minimum"] == 1
        assert limit["maximum"] == 50

    def test_handlers_match_schemas(self):
        # Every schema-defined tool must have a registered handler.
        schema_names = {t.name for t in mcp_server.TOOL_DEFINITIONS}
        handler_names = set(mcp_server.TOOL_HANDLERS.keys())
        assert schema_names == handler_names


# --------------------------------------------------------------------------- #
# 2. Dispatcher behavior
# --------------------------------------------------------------------------- #

class TestDispatcher:
    """The call_tool wrapper turns handler output into TextContent and
    converts exceptions into actionable error strings."""

    def test_unknown_tool_returns_text_error(self):
        result = run_async(mcp_server.call_tool("does_not_exist", {}))
        assert len(result) == 1
        assert result[0].type == "text"
        assert "unknown tool" in result[0].text.lower()

    def test_handler_exception_is_caught(self):
        async def boom(_args):
            raise RuntimeError("synthetic failure")

        with patch.dict(mcp_server.TOOL_HANDLERS, {"memory_search": boom}):
            result = run_async(mcp_server.call_tool("memory_search", {"query": "x"}))
        assert len(result) == 1
        assert "RuntimeError" in result[0].text
        assert "synthetic failure" in result[0].text

    def test_none_arguments_treated_as_empty(self):
        async def echo(args):
            return f"got {len(args)} keys"

        with patch.dict(mcp_server.TOOL_HANDLERS, {"memory_health": echo}):
            result = run_async(mcp_server.call_tool("memory_health", None))
        assert result[0].text == "got 0 keys"


# --------------------------------------------------------------------------- #
# 3. memory_save
# --------------------------------------------------------------------------- #

class TestMemorySave:

    def test_save_calls_write_and_embed(self):
        with patch.object(mcp_server.store, "write", return_value="HASH-ABC") as w, \
             patch.object(mcp_server.embeddings, "embed_and_store") as e, \
             patch.object(mcp_server, "_ensure_agent"):
            result = run_async(mcp_server._handle_memory_save({
                "content": "we picked Postgres over DynamoDB",
                "agent": "ron",
                "tags": ["decision", "infra"],
                "session_id": "2026-05-17-planning",
            }))

        assert "HASH-ABC" in result
        assert "ron" in result
        assert "decision" in result
        w.assert_called_once()
        kwargs = w.call_args.kwargs
        # The agent tag should be auto-prepended.
        assert "ron" in kwargs["tags"]
        assert "decision" in kwargs["tags"]
        assert kwargs["session_id"] == "2026-05-17-planning"
        e.assert_called_once_with("HASH-ABC", "we picked Postgres over DynamoDB")

    def test_save_without_session_generates_one(self):
        with patch.object(mcp_server.store, "write", return_value="H1"), \
             patch.object(mcp_server.embeddings, "embed_and_store"), \
             patch.object(mcp_server, "_ensure_agent"):
            result = run_async(mcp_server._handle_memory_save({
                "content": "hello",
            }))
        assert "Session: mcp-" in result

    def test_save_rejects_empty_content(self):
        result = run_async(mcp_server._handle_memory_save({"content": "   "}))
        assert result.lower().startswith("error")
        assert "content" in result.lower()

    def test_save_rejects_bad_tags(self):
        result = run_async(mcp_server._handle_memory_save({
            "content": "x",
            "tags": [1, 2, 3],
        }))
        assert result.lower().startswith("error")
        assert "tags" in result.lower()

    def test_save_degrades_when_embedding_fails(self):
        with patch.object(mcp_server.store, "write", return_value="H2"), \
             patch.object(
                 mcp_server.embeddings,
                 "embed_and_store",
                 side_effect=ConnectionError("ollama down"),
             ), \
             patch.object(mcp_server, "_ensure_agent"):
            result = run_async(mcp_server._handle_memory_save({"content": "x"}))
        assert "without embedding" in result
        assert "ConnectionError" in result


# --------------------------------------------------------------------------- #
# 4. memory_search
# --------------------------------------------------------------------------- #

class TestMemorySearch:

    def test_search_rejects_empty_query(self):
        result = run_async(mcp_server._handle_memory_search({"query": "   "}))
        assert result.lower().startswith("error")

    def test_search_returns_formatted_results(self):
        fake_results = [
            {
                "content_hash": "AAA",
                "body": "Postgres beats DynamoDB for relational workloads.",
                "tags": ["decision", "infra"],
                "created_at": "2026-05-17 12:00",
                "score": 0.91,
            },
            {
                "content_hash": "BBB",
                "body": "DynamoDB is fine for write-heavy KV.",
                "tags": ["counterpoint"],
                "created_at": "2026-05-10 09:00",
                "score": 0.74,
            },
        ]
        with patch.object(
            mcp_server.embeddings, "semantic_search", return_value=fake_results
        ), patch.object(mcp_server, "_ensure_agent"):
            result = run_async(mcp_server._handle_memory_search({
                "query": "database choice",
                "limit": 5,
            }))

        assert "Memory Search Results" in result
        assert "Postgres beats DynamoDB" in result
        assert "Score: 0.910" in result
        assert "Tags: decision, infra" in result
        assert "Method: semantic" in result

    def test_search_clamps_limit(self):
        captured = {}

        def fake(q, **kwargs):
            captured["q"] = q
            captured["limit"] = kwargs.get("limit")
            return []

        with patch.object(mcp_server.embeddings, "semantic_search", side_effect=fake), \
             patch.object(mcp_server, "_ensure_agent"):
            run_async(mcp_server._handle_memory_search({"query": "x", "limit": 9999}))
        assert captured["limit"] == 50  # MAX_SEARCH_LIMIT

    def test_search_falls_back_to_keyword(self):
        with patch.object(
            mcp_server.embeddings,
            "semantic_search",
            side_effect=ConnectionError("ollama down"),
        ), patch.object(
            mcp_server, "_keyword_search", return_value=[]
        ) as kw, patch.object(mcp_server, "_ensure_agent"):
            result = run_async(mcp_server._handle_memory_search({"query": "x"}))
        kw.assert_called_once()
        assert "method=keyword" in result

    def test_search_hydrates_body_via_read_by_hash(self):
        # semantic_search returned a row without a body — we should
        # fetch it from store.read_by_hash.
        with patch.object(
            mcp_server.embeddings,
            "semantic_search",
            return_value=[{"content_hash": "ZZZ", "score": 0.5}],
        ), patch.object(
            mcp_server.store, "read_by_hash", return_value="hydrated body"
        ) as r, patch.object(mcp_server, "_ensure_agent"):
            result = run_async(mcp_server._handle_memory_search({"query": "x"}))
        r.assert_called_once_with("ZZZ")
        assert "hydrated body" in result


# --------------------------------------------------------------------------- #
# 5. memory_retrieve
# --------------------------------------------------------------------------- #

class TestMemoryRetrieve:

    def test_retrieve_rejects_empty_topic(self):
        result = run_async(mcp_server._handle_memory_retrieve({"topic": ""}))
        assert result.lower().startswith("error")

    def test_retrieve_rejects_bad_recency(self):
        result = run_async(mcp_server._handle_memory_retrieve({
            "topic": "auth", "recency": "lukewarm",
        }))
        assert result.lower().startswith("error")
        assert "recency" in result.lower()

    def test_retrieve_passes_token_budget_through(self):
        captured: dict[str, Any] = {}

        def fake_retrieve(**kwargs):
            captured.update(kwargs)
            return {
                "entries": [],
                "total_tokens": 0,
                "scored_count": 0,
                "returned_count": 0,
                "truncated": False,
            }

        with patch.object(mcp_server.retrieval, "retrieve", side_effect=fake_retrieve), \
             patch.object(mcp_server, "_ensure_agent"):
            run_async(mcp_server._handle_memory_retrieve({
                "topic": "auth architecture",
                "agent": "ron",
                "limit_tokens": 1500,
                "recency": "warm",
            }))

        assert captured["topic"] == "auth architecture"
        assert captured["agent_id"] == "ron"
        assert captured["limit_tokens"] == 1500
        assert captured["recency"] == "warm"

    def test_retrieve_formats_entries(self):
        fake = {
            "entries": [
                {
                    "id": 1,
                    "session_id": "2026-05-15",
                    "tags": ["architecture", "auth"],
                    "body": "We chose ML-DSA-87 over JWT-only.",
                    "created_at": "2026-05-15 10:30",
                    "score": 0.87,
                    "relevance_score": 0.92,
                    "recency_score": 0.75,
                },
            ],
            "total_tokens": 412,
            "scored_count": 27,
            "returned_count": 1,
            "truncated": False,
        }
        with patch.object(mcp_server.retrieval, "retrieve", return_value=fake), \
             patch.object(mcp_server, "_ensure_agent"):
            result = run_async(mcp_server._handle_memory_retrieve({
                "topic": "auth architecture",
            }))

        assert "ML-DSA-87" in result
        assert "Score: 0.870" in result
        assert "relevance 0.92" in result
        assert "recency 0.75" in result
        assert "Tags: architecture, auth" in result
        assert "412 tokens" in result

    def test_retrieve_handles_empty(self):
        with patch.object(
            mcp_server.retrieval, "retrieve",
            return_value={"entries": []},
        ), patch.object(mcp_server, "_ensure_agent"):
            result = run_async(mcp_server._handle_memory_retrieve({
                "topic": "obscure topic",
            }))
        assert "No memories found" in result


# --------------------------------------------------------------------------- #
# 6. memory_health
# --------------------------------------------------------------------------- #

class TestMemoryHealth:

    def test_health_includes_store_and_embedding_sections(self):
        with patch.object(
            mcp_server.store, "stats",
            return_value={"entries": 1234, "bytes": 5_678_900},
        ), patch.object(
            mcp_server.embeddings, "embedding_stats",
            return_value={
                "total_entries": 1234,
                "embedded": 1200,
                "coverage": 0.972,
                "model": "nomic-embed-text",
                "dimensions": 768,
            },
        ):
            result = run_async(mcp_server._handle_memory_health({}))

        assert "Shannon Memory Service Health" in result
        assert "## Store" in result
        assert "## Embeddings" in result
        assert "entries: 1234" in result
        assert "nomic-embed-text" in result
        assert "768" in result

    def test_health_survives_failing_subcalls(self):
        with patch.object(
            mcp_server.store, "stats", side_effect=RuntimeError("db locked"),
        ), patch.object(
            mcp_server.embeddings, "embedding_stats",
            side_effect=ConnectionError("ollama"),
        ):
            result = run_async(mcp_server._handle_memory_health({}))

        # Both subsystems failed but the tool should still produce output.
        assert "Shannon Memory Service Health" in result
        assert "db locked" in result
        assert "ConnectionError" in result or "ollama" in result


# --------------------------------------------------------------------------- #
# 7. memory_agents
# --------------------------------------------------------------------------- #

class TestMemoryAgents:

    def test_agents_missing_db_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHANNON_HOME", str(tmp_path / "no-such-shannon"))
        result = run_async(mcp_server._handle_memory_agents({}))
        assert "does not exist" in result or "No agents" in result

    def test_agents_lists_registered(self, tmp_path, monkeypatch):
        home = tmp_path / "shannon"
        layer1 = home / "dictionary" / "layer_1"
        layer1.mkdir(parents=True)
        db_path = layer1 / "index.db"

        # Set up a minimal compatible schema.
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE agents ("
                " agent_id TEXT PRIMARY KEY,"
                " display_name TEXT,"
                " tag_profile TEXT,"
                " created_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE entries ("
                " content_hash TEXT PRIMARY KEY,"
                " body TEXT,"
                " tags TEXT,"
                " created_at TEXT)"
            )
            conn.execute(
                "INSERT INTO agents VALUES (?, ?, ?, ?)",
                ("ron", "Ron Peterson", "infra,decisions", "2026-05-01"),
            )
            conn.execute(
                "INSERT INTO agents VALUES (?, ?, ?, ?)",
                ("guy", "Guy Shannon", "specs", "2026-05-10"),
            )
            conn.execute(
                "INSERT INTO entries VALUES (?, ?, ?, ?)",
                ("h1", "b1", json.dumps(["ron", "decision"]), "2026-05-15"),
            )
            conn.execute(
                "INSERT INTO entries VALUES (?, ?, ?, ?)",
                ("h2", "b2", json.dumps(["ron"]), "2026-05-16"),
            )
            conn.execute(
                "INSERT INTO entries VALUES (?, ?, ?, ?)",
                ("h3", "b3", json.dumps(["guy", "spec"]), "2026-05-17"),
            )
            conn.commit()
        finally:
            conn.close()

        monkeypatch.setenv("SHANNON_HOME", str(home))
        result = run_async(mcp_server._handle_memory_agents({}))

        assert "Ron Peterson" in result
        assert "Guy Shannon" in result
        assert "`ron`" in result
        assert "`guy`" in result
        # ron should have 2 entries, guy should have 1.
        # Look for the counts somewhere in the relevant blocks.
        ron_block = result.split("## Ron Peterson")[1].split("##")[0]
        guy_block = result.split("## Guy Shannon")[1]
        assert "Entries: 2" in ron_block
        assert "Entries: 1" in guy_block


# --------------------------------------------------------------------------- #
# 8. memory_context
# --------------------------------------------------------------------------- #

class TestMemoryContext:

    def test_context_calls_openclaw_function(self):
        with patch.object(
            mcp_server.openclaw,
            "regenerate_context",
            create=True,
            return_value="# Context\n\nHot tier: ...",
        ):
            result = run_async(mcp_server._handle_memory_context({}))
        assert "Context" in result
        assert "Hot tier" in result

    def test_context_reads_returned_path(self, tmp_path):
        context_file = tmp_path / "ctx.md"
        context_file.write_text("# Generated context\n\nHello.", encoding="utf-8")

        # Simulate openclaw returning a path object.
        with patch.object(
            mcp_server.openclaw,
            "regenerate_context",
            create=True,
            return_value=context_file,
        ):
            result = run_async(mcp_server._handle_memory_context({}))
        assert "Generated context" in result

    def test_context_reports_when_no_function_found(self):
        # Replace the openclaw module reference with an empty namespace so
        # none of the candidate function names exist on it.
        class _Empty:
            pass

        with patch.object(mcp_server, "openclaw", _Empty()):
            result = run_async(mcp_server._handle_memory_context({}))
        assert "could not find" in result.lower()


# --------------------------------------------------------------------------- #
# 9. End-to-end via call_tool
# --------------------------------------------------------------------------- #

class TestEndToEnd:

    def test_save_then_search_roundtrip(self):
        """memory_save -> memory_search through the public call_tool surface
        (still mocked at the shannon.* boundary)."""
        stored: dict[str, str] = {}

        def fake_write(content, *, session_id, tags):
            h = f"HASH-{len(stored)}"
            stored[h] = content
            return h

        def fake_semantic_search(q, **_):
            return [
                {
                    "content_hash": h,
                    "body": body,
                    "tags": ["default"],
                    "created_at": "2026-05-17",
                    "score": 1.0 if q.lower() in body.lower() else 0.2,
                }
                for h, body in stored.items()
            ]

        with patch.object(mcp_server.store, "write", side_effect=fake_write), \
             patch.object(mcp_server.embeddings, "embed_and_store"), \
             patch.object(mcp_server.embeddings, "semantic_search",
                          side_effect=fake_semantic_search), \
             patch.object(mcp_server, "_ensure_agent"):

            save_result = run_async(mcp_server.call_tool("memory_save", {
                "content": "MCP server uses stdio for transport.",
            }))
            assert "HASH-0" in save_result[0].text

            search_result = run_async(mcp_server.call_tool("memory_search", {
                "query": "stdio",
            }))
            assert "stdio for transport" in search_result[0].text


# --------------------------------------------------------------------------- #
# 10. list_tools
# --------------------------------------------------------------------------- #

def test_list_tools_returns_all_six():
    tools = run_async(mcp_server.list_tools())
    assert len(tools) == 6
    assert {t.name for t in tools} == set(mcp_server.TOOL_HANDLERS.keys())
