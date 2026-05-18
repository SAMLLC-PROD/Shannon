# Shannon MCP Server

Exposes Shannon's personal semantic memory service over the Model Context
Protocol (MCP). Any MCP-compatible AI client — Claude Desktop, Cursor,
VS Code Copilot, Claude Code, etc. — can connect over stdio and call six
tools to read and write the user's personal long-term memory.

## What this is

A thin MCP wrapper around the existing Shannon Python modules. It does
**not** duplicate any retrieval, embedding, or storage logic — it imports
`shannon.store`, `shannon.embeddings`, `shannon.retrieval`, and
`shannon.openclaw` directly and wires them into the MCP protocol surface.

## Tools exposed

| Tool | Purpose |
|------|---------|
| `memory_search` | Semantic search across stored memories (keyword fallback if Ollama is down) |
| `memory_retrieve` | Token-budgeted context retrieval — the primary way to load topical context |
| `memory_save` | Persist a new memory chunk; auto-attaches agent tag, triggers embedding |
| `memory_health` | Service status: entry counts, embedding coverage, version |
| `memory_agents` | List registered memory agents and their entry counts |
| `memory_context` | Regenerate Shannon's tiered context summary via openclaw |

## Install

Drop the files into your existing Shannon project tree:

```
~/development/shannon/
├── shannon/
│   ├── mcp_server.py     ← from shannon/ in this directory
│   ├── mcp_main.py       ← from shannon/ in this directory
│   └── ... (existing shannon files, unchanged)
├── tests/
│   └── test_mcp.py       ← from tests/ in this directory
└── pyproject.toml        ← apply the snippet from docs/INTEGRATION_SNIPPETS.md
```

Then:

```bash
cd ~/development/shannon
pip install -e .
```

The `pip install -e .` step registers the `shannon-mcp` console script
defined by the `[project.scripts]` entry in
`docs/INTEGRATION_SNIPPETS.md`.

## Configure Claude Desktop

See `docs/claude-desktop-config.md` for the exact JSON to drop into your
Claude Desktop config (also works for Cursor, VS Code, Claude Code).

The short version:

```json
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
```

## Test

```bash
cd ~/development/shannon
python -m pytest tests/test_mcp.py -v
```

33 tests, all offline (every shannon.* call is mocked at the boundary, so
no real ~/.shannon directory is touched).

## Files

- `shannon/mcp_server.py` — main MCP server (~790 lines): tool definitions,
  schema constants, async handlers for all six tools, SQLite helpers for
  agents & keyword fallback, dispatch table, `main()` entrypoint with
  stdio transport
- `shannon/mcp_main.py` — sync `run()` wrapper for the `shannon-mcp`
  console script entry
- `tests/test_mcp.py` — 33 tests across 10 test classes covering schemas,
  the dispatcher, each handler, end-to-end save→search round-trip
- `docs/claude-desktop-config.md` — client setup instructions for Claude
  Desktop, Cursor, VS Code, and Claude Code
- `docs/INTEGRATION_SNIPPETS.md` — exact lines to paste into your
  existing `pyproject.toml` and `README.md` (since we don't modify those
  per the spec's "do not modify" rule)

## Design rules followed

- Imports from shannon internals rather than duplicating logic
- No existing `shannon/*.py` file modified
- `from __future__ import annotations` in every file
- Type hints on all public function signatures
- Docstrings on all public functions
- stdio transport only (single async event loop)
- Graceful degradation when Ollama is unavailable (keyword fallback for
  search, store-without-embedding for save)

— Spec: Guy Shannon, 2026-05-17
— For: Ron Peterson / SAMLLC
