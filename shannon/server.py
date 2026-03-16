import os
import uvicorn
from shannon.api import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("SHANNON_API_PORT", "8765")))
