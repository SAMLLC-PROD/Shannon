import os
import uvicorn
from shannon.api import app
from shannon.mcp_mount import mount_mcp

# Mount MCP Streamable HTTP endpoint at /mcp
mount_mcp(app)

if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("SHANNON_BIND_HOST", "127.0.0.1"), port=int(os.getenv("SHANNON_API_PORT", "8765")))
