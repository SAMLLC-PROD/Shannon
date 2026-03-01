"""
Shannon dictionary store.
Append-only. Layer 1 of the Tesseract.
"""

import sqlite3
import zstandard as zstd
import hashlib
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from .zeckendorf import data_to_address, address_to_str


SHANNON_HOME = Path.home() / ".shannon"
LAYER1_DB = SHANNON_HOME / "dictionary" / "layer_1" / "index.db"
CHUNKS_DIR = SHANNON_HOME / "dictionary" / "layer_1" / "chunks"


def init_store():
    """Initialize the Shannon store directories and DB."""
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(LAYER1_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            address TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            session_id TEXT,
            tags TEXT
        )
    """)
    conn.commit()
    conn.close()


def write(data: str, session_id: str = None, tags: list = None) -> str:
    """
    Write a context chunk to the Shannon dictionary.
    Returns the address string.
    """
    raw = data.encode("utf-8")
    address = data_to_address(raw)
    addr_str = address_to_str(address)
    content_hash = hashlib.sha256(raw).hexdigest()

    # Compress and store chunk
    cctx = zstd.ZstdCompressor()
    chunk_path = CHUNKS_DIR / f"{content_hash[:16]}.zst"
    chunk_path.write_bytes(cctx.compress(raw))

    # Index entry
    conn = sqlite3.connect(LAYER1_DB)
    conn.execute(
        "INSERT OR IGNORE INTO entries VALUES (?, ?, ?, ?, ?)",
        (addr_str, content_hash, datetime.utcnow().isoformat(),
         session_id, json.dumps(tags or []))
    )
    conn.commit()
    conn.close()

    return addr_str


def read(content_hash: str) -> Optional[str]:
    """Retrieve a chunk by content hash."""
    chunk_path = CHUNKS_DIR / f"{content_hash[:16]}.zst"
    if not chunk_path.exists():
        return None
    dctx = zstd.ZstdDecompressor()
    return dctx.decompress(chunk_path.read_bytes()).decode("utf-8")
