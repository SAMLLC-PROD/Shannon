# Shannon MCP Server — Build Spec

**Goal:** Turn Shannon Memory Service into an MCP (Model Context Protocol) server so any MCP-compatible AI client (Claude Desktop, Cursor, VS Code Copilot, ChatGPT plugins, etc.) can access the user's personal semantic memory.

**Why:** "Semantic memory as a service" — give any AI persistent, personal memory in 5 minutes. Data stays on the user's machine. The cloud model only sees retrieved context per-request.

---

## What Exists (DO NOT MODIFY)

Shannon is a running systemd service on port 8765 with these files:

```
~/development/shannon/
├── shannon/
│   ├── __init__.py
│   ├── api.py           # FastAPI HTTP API (323 lines) — DO NOT MODIFY
│   ├── embeddings.py    # Ollama nomic-embed-text embeddings (267 lines) — DO NOT MODIFY
│   ├── retrieval.py     # Token-budgeted semantic+recency scoring (201 lines) — DO NOT MODIFY
│   ├── store.py         # Zeckendorf-addressed SQLite store (221 lines) — DO NOT MODIFY
│   ├── openclaw.py      # Context file generation (243 lines) — DO NOT MODIFY
│   ├── server.py        # uvicorn launcher
│   ├── agent.py         # Agent profiles
│   ├── llm.py           # LLM integration
│   ├── qam.py           # QAM modulation patterns
│   ├── tools.py         # Tool definitions
│   └── zeckendorf.py    # Fibonacci addressing
├── tests/
├── .venv/               # Python 3.12 venv
├── pyproject.toml
└── shannon.service      # systemd unit
```

### Key Internal APIs You'll Call

From `store.py`:
- `init_store()` — ensure DB tables exist
- `write(data: str, session_id: str, tags: list[str]) -> str` — write memory chunk, returns Zeckendorf address
- `read_by_hash(content_hash: str) -> Optional[str]` — read chunk by hash
- `stats() -> dict` — entry count, byte totals

From `embeddings.py`:
- `embed_and_store(content_hash: str, text: str)` — compute + store embedding (background ok)
- `embedding_stats() -> dict` — coverage stats

From `retrieval.py`:
- `retrieve(agent_id, topic, limit_tokens, recency, relevance_weight, recency_weight) -> dict` — the main retrieval function. Returns `{entries: [...], total_tokens, truncated, scored_count, returned_count}`

Each entry in the `entries` list has: `id, session_id, tags, body, created_at, score, recency_score, relevance_score`

From `api.py` (for agent management):
- `_ensure_agent(agent_id)` — auto-register agent if not exists
- `_init_agents_table()` — create agents table

### Existing HTTP API (for reference — MCP wraps the same functionality)

```
GET  /health                    → {status, version, entries, embeddings, embedding_coverage}
GET  /memory?agent=X&topic=Y&limit_tokens=N&recency=hot|warm|cold|all → {entries: [...]}
POST /memory                    → {body, agent, tags[], session_id} → {id, ok}
GET  /memory/search?q=X&limit=N → {results: [...], method: semantic|keyword}
GET  /agents                    → {agents: [...]}
POST /agents                    → {agent_id, display_name, tag_profile[]}
POST /context/regenerate        → regenerate context file
GET  /embeddings/stats          → {total_entries, embedded, coverage, model, dimensions}
POST /embeddings/backfill       → start embedding backfill
```

---

## What To Build

### 1. MCP Server (`shannon/mcp_server.py`)

Create a new file `shannon/mcp_server.py` that implements the MCP protocol.

**MCP Protocol:** JSON-RPC 2.0 over stdio (stdin/stdout). The server reads JSON-RPC requests from stdin and writes responses to stdout. Each message is a single JSON line.

**Use the official Python MCP SDK:** `pip install mcp` (package: `mcp`). This handles the JSON-RPC transport layer.

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
```

The SDK provides:
- `Server(name)` — create server instance
- `@server.list_tools()` — register tool listing handler
- `@server.call_tool()` — register tool call handler
- `stdio_server()` — context manager for stdio transport
- Tool schemas are defined with JSON Schema for parameters

### 2. MCP Tools to Expose

Define these 6 tools:

#### `memory_search`
Semantic search across the user's long-term memory.

```json
{
  "name": "memory_search",
  "description": "Search the user's personal semantic memory. Returns the most relevant memories scored by semantic similarity and recency. Use this to recall prior conversations, decisions, project context, or anything the user has stored.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language search query"
      },
      "agent": {
        "type": "string",
        "description": "Agent ID to filter memories by (optional, searches all if omitted)",
        "default": "default"
      },
      "limit": {
        "type": "integer",
        "description": "Maximum number of results (1-50)",
        "default": 10
      }
    },
    "required": ["query"]
  }
}
```

**Implementation:** Call `semantic_search()` from embeddings.py for the query, then fetch bodies with `read_by_hash()`. Return results as formatted text.

#### `memory_retrieve`
Token-budgeted context retrieval — the main way to load relevant context for a topic.

```json
{
  "name": "memory_retrieve",
  "description": "Retrieve relevant memories for a topic, scored by semantic similarity and recency, within a token budget. This is the primary way to load context about a subject. Returns the highest-scoring memories that fit within the token limit.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "topic": {
        "type": "string",
        "description": "Topic to retrieve context for (natural language)"
      },
      "agent": {
        "type": "string",
        "description": "Agent ID to filter by",
        "default": "default"
      },
      "limit_tokens": {
        "type": "integer",
        "description": "Maximum tokens to return (default 4000)",
        "default": 4000
      },
      "recency": {
        "type": "string",
        "enum": ["hot", "warm", "cold", "all"],
        "description": "Time window: hot (0-48h), warm (48h-7d), cold (7d-30d), all",
        "default": "all"
      }
    },
    "required": ["topic"]
  }
}
```

**Implementation:** Call `retrieve()` from retrieval.py directly. Format the entries as readable text with metadata.

#### `memory_save`
Store a new memory.

```json
{
  "name": "memory_save",
  "description": "Save a new memory to the user's personal long-term memory. Use this to store important context: decisions, milestones, insights, lessons learned, or anything worth remembering across sessions.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "content": {
        "type": "string",
        "description": "The memory content to store"
      },
      "agent": {
        "type": "string",
        "description": "Agent ID storing this memory",
        "default": "default"
      },
      "tags": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tags for categorization (e.g. ['project-x', 'decision', 'architecture'])",
        "default": []
      },
      "session_id": {
        "type": "string",
        "description": "Session identifier for grouping related memories (e.g. '2026-05-17-planning')"
      }
    },
    "required": ["content"]
  }
}
```

**Implementation:** Call `write()` from store.py, then `embed_and_store()` from embeddings.py. Return confirmation with the content hash.

#### `memory_health`
Check Shannon service status.

```json
{
  "name": "memory_health",
  "description": "Check the status of the Shannon memory service. Returns entry count, embedding coverage, and service version.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

**Implementation:** Call `stats()` from store.py and `embedding_stats()` from embeddings.py.

#### `memory_agents`
List registered memory agents.

```json
{
  "name": "memory_agents",
  "description": "List all registered memory agents and their entry counts. Agents are isolated memory namespaces — each agent only sees memories tagged with their ID.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

**Implementation:** Query the agents table and count entries per agent (same as the `/agents` HTTP endpoint logic).

#### `memory_context`
Generate a context summary file from recent memories.

```json
{
  "name": "memory_context",
  "description": "Regenerate the Shannon context summary file from recent memories. This creates a time-tiered summary (hot/warm/cold) of the most important memories. Returns the generated context text.",
  "inputSchema": {
    "type": "object",
    "properties": {}
  }
}
```

**Implementation:** Call the context regeneration logic from openclaw.py. Return the generated content.

### 3. Entry Point (`shannon/mcp_main.py`)

Create a simple entry point:

```python
"""Shannon MCP Server — entry point."""
import asyncio
from shannon.mcp_server import main

if __name__ == "__main__":
    asyncio.run(main())
```

Also add a console_scripts entry to pyproject.toml:

```toml
[project.scripts]
shannon-mcp = "shannon.mcp_main:run"
```

Where `run()` is a sync wrapper around `asyncio.run(main())`.

### 4. Claude Desktop Configuration Example

Create `docs/claude-desktop-config.md`:

```markdown
# Using Shannon with Claude Desktop

Add this to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

\```json
{
  "mcpServers": {
    "shannon": {
      "command": "/path/to/shannon/.venv/bin/python",
      "args": ["-m", "shannon.mcp_main"],
      "env": {
        "SHANNON_HOME": "/home/user/.shannon"
      }
    }
  }
}
\```

## What This Gives You

Claude will now have access to your personal semantic memory:
- **memory_search** — "What do I know about Project X?"
- **memory_retrieve** — "Load context about the authentication architecture"
- **memory_save** — "Remember this decision: we chose Postgres over DynamoDB because..."
- **memory_health** — "How many memories do I have stored?"
- **memory_agents** — "What agents are registered?"
- **memory_context** — "Generate a fresh context summary"

Your data stays on your machine. Claude sees only the retrieved results per-query.
```

### 5. Tests (`tests/test_mcp.py`)

Write tests that:
1. Test each tool handler directly (unit tests, no MCP transport)
2. Verify tool schemas are valid JSON Schema
3. Test memory_save → memory_search round-trip
4. Test memory_retrieve with token budget
5. Test memory_health returns expected fields
6. Test memory_agents lists registered agents

Use a temporary SQLite database (in-memory or tmp dir) — do NOT hit the real Shannon DB.

To isolate from the real DB, override `LAYER1_DIR` and `EMBEDDINGS_DB` in tests using monkeypatch or by setting `SHANNON_HOME` env var to a temp directory.

### 6. Dependencies

Add to pyproject.toml dependencies:

```toml
"mcp>=1.0",
```

The `mcp` package pulls in the MCP SDK which handles JSON-RPC, stdio transport, and tool schema validation.

### 7. README section

Add an "MCP Server" section to README.md explaining:
- What MCP is (one sentence)
- How to install (`pip install -e .` from the shannon dir)
- How to configure Claude Desktop / Cursor / VS Code
- The 6 available tools with brief descriptions
- That data stays local

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────┐
│ AI Client (Claude Desktop / Cursor / ChatGPT)   │
│                                                  │
│   "What decisions did we make about auth?"       │
│       ↓ MCP tool call: memory_retrieve           │
└───────────────┬──────────────────────────────────┘
                │ JSON-RPC over stdio
┌───────────────▼──────────────────────────────────┐
│ Shannon MCP Server (shannon/mcp_server.py)       │
│                                                  │
│   Receives: {topic: "auth decisions"}            │
│   Calls: retrieve(topic="auth decisions",        │
│                    limit_tokens=4000)             │
│   Returns: formatted memory entries              │
└───────────────┬──────────────────────────────────┘
                │ direct Python imports
┌───────────────▼──────────────────────────────────┐
│ Shannon Core (existing, unchanged)               │
│                                                  │
│   store.py → SQLite + Zeckendorf addressing      │
│   embeddings.py → nomic-embed-text (768d)        │
│   retrieval.py → semantic + recency scoring      │
│   openclaw.py → context file generation          │
└───────────────┬──────────────────────────────────┘
                │ file I/O
┌───────────────▼──────────────────────────────────┐
│ ~/.shannon/dictionary/layer_1/                   │
│   index.db        — entry metadata               │
│   embeddings.db   — vector store                 │
│   chunks/         — zstd-compressed content       │
└──────────────────────────────────────────────────┘
```

---

## Implementation Notes

### Response Formatting

When returning memory entries from tools, format them as readable text, not raw JSON. Example:

```
## Memory: auth architecture decision (2026-05-15)
Score: 0.87 | Tags: architecture, auth, decision

We decided to use ML-DSA-87 challenge-response for authentication instead of
traditional JWT-only. The SIM-NFT provides hardware-bound identity, and OAuth 2.1
sits in the middle as a session layer for compatibility with external services.

---

## Memory: OAuth 2.1 session layer spec (2026-03-27)
Score: 0.72 | Tags: m33, oauth, spec

Add OAuth 2.1 as session layer between ML-DSA-87 identity proof and agent action
execution. Flow: ML-DSA-87 challenge-response → OAuth 2.1 token (scoped JWT) →
agent tool calls → ML-DSA-87 signed action record.
```

### Error Handling

- If Ollama is down (embeddings unavailable), fall back gracefully — `memory_search` uses keyword search, `memory_save` stores without embedding (backfill later)
- If Shannon DB doesn't exist, `init_store()` creates it automatically
- Return clear error messages in MCP error responses

### Environment Variables

- `SHANNON_HOME` — override `~/.shannon` (default)
- `OLLAMA_URL` — override `http://localhost:11434` (default)
- `SHANNON_EMBED_MODEL` — override `nomic-embed-text` (default)

### Thread Safety

Shannon's SQLite store uses file-level locking. The MCP server runs in a single async event loop, so concurrent requests are serialized naturally. No additional locking needed for stdio transport.

---

## Coding Rules

- Use `from __future__ import annotations` in every new file
- Type hints on all function signatures
- Docstrings on all public functions
- Import from shannon internals (store, embeddings, retrieval) — do NOT duplicate logic
- Do NOT modify any existing shannon/*.py files
- All new code goes in: `shannon/mcp_server.py`, `shannon/mcp_main.py`, `tests/test_mcp.py`, `docs/claude-desktop-config.md`
- Run `python -m pytest tests/test_mcp.py -v` to verify

---

## Estimated Size

- `mcp_server.py`: ~200-250 lines
- `mcp_main.py`: ~15 lines
- `tests/test_mcp.py`: ~150-200 lines
- `docs/claude-desktop-config.md`: ~60 lines
- Total new code: ~450 lines

---

_Spec: Guy Shannon, 2026-05-17_
_For: Ron Peterson / SAMLLC_
