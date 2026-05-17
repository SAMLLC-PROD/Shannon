"""Shannon MCP Server — entry point.

This module exists so the server can be launched in two equivalent ways:

    # As a module (used by Claude Desktop's config):
    python -m shannon.mcp_main

    # As a console script (after `pip install -e .` with a [project.scripts]
    # entry of `shannon-mcp = "shannon.mcp_main:run"`):
    shannon-mcp
"""
from __future__ import annotations

import asyncio

from shannon.mcp_server import main


def run() -> None:
    """Synchronous wrapper around the async server entry point.

    Used by the `shannon-mcp` console_scripts entry in pyproject.toml.
    """
    asyncio.run(main())


if __name__ == "__main__":
    run()
