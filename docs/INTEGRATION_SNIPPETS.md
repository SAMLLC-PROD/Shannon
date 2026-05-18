# Snippets to paste into existing files

The MCP build instructed me not to modify any pre-existing files. Here
are the two small additions you (the maintainer) should paste in by
hand to wire everything up.

---

## 1. `pyproject.toml`

Add to `[project] dependencies` (or wherever your runtime deps live):

```toml
dependencies = [
    # ...your existing deps...
    "mcp>=1.0",
]
```

Add a console-scripts entry so `shannon-mcp` is on the path after
`pip install -e .`:

```toml
[project.scripts]
shannon-mcp = "shannon.mcp_main:run"
```

After editing, reinstall:

```bash
pip install -e .
```

---

## 2. `README.md`

Add this section anywhere appropriate (suggest right after the HTTP API
section):

```markdown
## MCP Server

Shannon exposes its memory as a Model Context Protocol (MCP) server so
any MCP-compatible AI client — Claude Desktop, Claude Code, Cursor, VS
Code Copilot, Zed — can read from and write to your personal semantic
memory.

### Install

```bash
pip install -e .
```

### Configure your client

See [`docs/claude-desktop-config.md`](docs/claude-desktop-config.md) for
the per-client config snippets. The short version (Claude Desktop):

```json
{
  "mcpServers": {
    "shannon": {
      "command": "/path/to/shannon/.venv/bin/python",
      "args": ["-m", "shannon.mcp_main"]
    }
  }
}
```

### Available tools

| Tool              | Purpose                                              |
| ----------------- | ---------------------------------------------------- |
| `memory_search`   | Semantic search across your stored memories          |
| `memory_retrieve` | Token-budgeted context loading for a topic           |
| `memory_save`     | Persist a new memory                                 |
| `memory_health`   | Service status, entry counts, embedding coverage     |
| `memory_agents`   | List registered memory agents                        |
| `memory_context`  | Regenerate the tiered context summary file           |

Your data stays on your machine. The cloud model sees only the retrieved
results per query.
```

---

## 3. Run the tests

```bash
python -m pytest tests/test_mcp.py -v
```

Expect 33 tests, all passing.
