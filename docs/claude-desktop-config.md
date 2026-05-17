# Using Shannon with MCP-Compatible AI Clients

Shannon ships an MCP (Model Context Protocol) server that exposes your
personal semantic memory as a set of tools any MCP-compatible AI client
can call: Claude Desktop, Claude Code, Cursor, VS Code Copilot, Zed,
ChatGPT desktop, and others.

The server speaks JSON-RPC 2.0 over stdio. There is no network exposure,
no auth surface, and no extra daemon — the client spawns the MCP server
as a subprocess on demand.

---

## What You Get

Once configured, your AI assistant can call these tools:

| Tool              | Purpose                                              |
| ----------------- | ---------------------------------------------------- |
| `memory_search`   | Semantic search across your stored memories          |
| `memory_retrieve` | Token-budgeted context loading for a topic           |
| `memory_save`     | Persist a new memory (decisions, insights, milestones) |
| `memory_health`   | Service status, entry counts, embedding coverage     |
| `memory_agents`   | List registered memory agents and entry counts      |
| `memory_context`  | Regenerate the tiered context summary file           |

Your data stays on your machine. The cloud model only sees the
retrieved results for each individual tool call.

---

## Install

From the Shannon repo root:

```bash
pip install -e .
```

This installs both `shannon` and the `shannon-mcp` console script.

The MCP server reads from the same SQLite store the HTTP service uses
(`~/.shannon/dictionary/layer_1/`). You can override the location with
`SHANNON_HOME`.

---

## Claude Desktop

Edit your Claude Desktop config file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

Add a `shannon` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "shannon": {
      "command": "/home/you/development/shannon/.venv/bin/python",
      "args": ["-m", "shannon.mcp_main"],
      "env": {
        "SHANNON_HOME": "/home/you/.shannon"
      }
    }
  }
}
```

Restart Claude Desktop. The six `memory_*` tools will appear in the
tool list.

If you used `pip install -e .` and have `shannon-mcp` on your `$PATH`,
the simpler form also works:

```json
{
  "mcpServers": {
    "shannon": {
      "command": "/path/to/.venv/bin/shannon-mcp",
      "env": { "SHANNON_HOME": "/home/you/.shannon" }
    }
  }
}
```

---

## Cursor

Cursor reads MCP config from `~/.cursor/mcp.json` (or per-project at
`.cursor/mcp.json`). Same shape as Claude Desktop:

```json
{
  "mcpServers": {
    "shannon": {
      "command": "/home/you/development/shannon/.venv/bin/python",
      "args": ["-m", "shannon.mcp_main"]
    }
  }
}
```

---

## VS Code (with the MCP / Copilot extension)

Add to your user `settings.json`:

```json
{
  "mcp.servers": {
    "shannon": {
      "command": "/home/you/development/shannon/.venv/bin/python",
      "args": ["-m", "shannon.mcp_main"],
      "env": { "SHANNON_HOME": "/home/you/.shannon" }
    }
  }
}
```

---

## Claude Code (CLI)

```bash
claude mcp add shannon \
    /home/you/development/shannon/.venv/bin/python \
    -- -m shannon.mcp_main
```

---

## Environment Variables

| Variable                | Default                     | Purpose                                  |
| ----------------------- | --------------------------- | ---------------------------------------- |
| `SHANNON_HOME`          | `~/.shannon`                | Data directory (overrides default)       |
| `OLLAMA_URL`            | `http://localhost:11434`    | Ollama endpoint for embeddings           |
| `SHANNON_EMBED_MODEL`   | `nomic-embed-text`          | Embedding model                          |
| `SHANNON_MCP_LOG_LEVEL` | `INFO`                      | Logger level for the MCP server          |

---

## Sample Conversations

Once configured, ask your assistant things like:

> "What decisions did we make about authentication?"
> → triggers `memory_retrieve(topic="authentication decisions")`

> "Search my memory for anything about Project X."
> → triggers `memory_search(query="Project X")`

> "Remember this: we chose Postgres over DynamoDB because of relational
> joins in the billing reports. Tag it `infra` and `decision`."
> → triggers `memory_save(content=..., tags=["infra", "decision"])`

> "How many memories do I have stored?"
> → triggers `memory_health()`

> "Regenerate my context summary."
> → triggers `memory_context()`

---

## Troubleshooting

**The server starts but no tools appear.** Make sure the `command` path
points at a Python interpreter that has `shannon` installed (the venv's
Python, not the system Python). Check the client's MCP log; for Claude
Desktop on macOS it's at
`~/Library/Logs/Claude/mcp-server-shannon.log`.

**`memory_save` works but `memory_search` returns "method=keyword".**
This is the graceful fallback when Ollama is unreachable. The save
still succeeds; the embedding will be backfilled when Ollama comes back
(call `POST /embeddings/backfill` on the HTTP service, or just let the
next `memory_save` retry).

**`memory_agents` says "does not exist".** The on-disk store hasn't
been initialized yet. Call `memory_save` once to bootstrap.

**Permission errors on `SHANNON_HOME`.** Confirm the user the client
launches the subprocess as can write to the directory. On macOS this is
your login user; on systemd-managed setups, double-check.

---

## Data Privacy

- The MCP server runs as a subprocess of your AI client, on your machine.
- It reads and writes only to `SHANNON_HOME` (default `~/.shannon`).
- It makes no outbound network calls except to your local Ollama instance
  for embeddings (if available).
- The AI client sees only the JSON-RPC responses for each tool call.
- Nothing is shipped to Anthropic / OpenAI / Cursor / Microsoft other
  than the specific retrieved text the model asks for.
