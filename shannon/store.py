"""
Shannon dictionary store — Layer 1 Tesseract.

Append-only. Content-addressed. Collision-free by Zeckendorf theorem.
Nothing is ever deleted. Old context is always retrievable.
"""

import sqlite3
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict

try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

from .zeckendorf import data_to_address, address_to_str
from .qam import data_to_pattern


SHANNON_HOME = Path.home() / ".shannon"
LAYER1_DIR   = SHANNON_HOME / "dictionary" / "layer_1"
CHUNKS_DIR   = LAYER1_DIR / "chunks"
INDEX_DB     = LAYER1_DIR / "index.db"
SESSIONS_DIR = SHANNON_HOME / "sessions"


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_store() -> None:
    """Create directory tree and schema if not already present."""
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entries (
            content_hash  TEXT PRIMARY KEY,
            address       TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            session_id    TEXT,
            tags          TEXT DEFAULT '[]',
            byte_size     INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_session  ON entries(session_id);
        CREATE INDEX IF NOT EXISTS idx_created  ON entries(created_at);
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
            USING fts5(content_hash, address, tags, content='entries');
    """)
    conn.commit()
    conn.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write(data: str, session_id: str = None, tags: List[str] = None) -> str:
    """
    Write a context chunk to the Shannon dictionary.

    Returns the Zeckendorf address string. Idempotent — writing the same
    data twice returns the same address without duplicating storage.
    """
    init_store()
    raw          = data.encode("utf-8")
    content_hash = hashlib.sha256(raw).hexdigest()
    address      = data_to_address(raw)
    addr_str     = address_to_str(address)

    # --- Store compressed chunk ---
    chunk_path = CHUNKS_DIR / f"{content_hash}.zst"
    if not chunk_path.exists():
        if HAS_ZSTD:
            compressed = zstd.ZstdCompressor(level=9).compress(raw)
        else:
            compressed = raw  # fallback: store raw if zstd not installed
        chunk_path.write_bytes(compressed)

    # --- Index entry (idempotent) ---
    conn = _connect()
    conn.execute(
        """INSERT OR IGNORE INTO entries
           (content_hash, address, created_at, session_id, tags, byte_size)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            content_hash,
            addr_str,
            datetime.now(timezone.utc).isoformat(),
            session_id,
            json.dumps(tags or []),
            len(raw),
        ),
    )
    conn.commit()
    conn.close()

    # --- Log address to session index ---
    if session_id:
        _log_session(session_id, content_hash, addr_str)

    return addr_str


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_by_hash(content_hash: str) -> Optional[str]:
    """Retrieve a chunk by its content hash."""
    chunk_path = CHUNKS_DIR / f"{content_hash}.zst"
    if not chunk_path.exists():
        return None
    raw = chunk_path.read_bytes()
    if HAS_ZSTD:
        return zstd.ZstdDecompressor().decompress(raw).decode("utf-8")
    return raw.decode("utf-8")


def read_by_address(addr_str: str) -> Optional[str]:
    """Retrieve a chunk by its Zeckendorf address string."""
    conn = _connect()
    row = conn.execute(
        "SELECT content_hash FROM entries WHERE address = ?", (addr_str,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return read_by_hash(row["content_hash"])


def read_data(data: str) -> Optional[str]:
    """
    Retrieve stored chunk for the given data (content-addressed lookup).
    If we stored it before, this returns it. Otherwise None.
    """
    content_hash = hashlib.sha256(data.encode()).hexdigest()
    return read_by_hash(content_hash)


# ---------------------------------------------------------------------------
# Session context
# ---------------------------------------------------------------------------

def _log_session(session_id: str, content_hash: str, address: str) -> None:
    """Append an address to the session index file."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    session_dir = SESSIONS_DIR / day
    session_dir.mkdir(parents=True, exist_ok=True)
    idx_path = session_dir / f"{session_id}.jsonl"
    with idx_path.open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "hash": content_hash,
            "address": address,
        }) + "\n")


def get_session_chunks(session_id: str, date: str = None) -> List[Dict]:
    """
    Retrieve all chunks written in a session.
    Returns list of dicts with address + content.
    """
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    idx_path = SESSIONS_DIR / day / f"{session_id}.jsonl"
    if not idx_path.exists():
        return []

    results = []
    for line in idx_path.read_text().strip().splitlines():
        entry = json.loads(line)
        content = read_by_hash(entry["hash"])
        if content:
            results.append({
                "address": entry["address"],
                "ts": entry["ts"],
                "content": content,
            })
    return results


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def stats() -> Dict:
    """Return dictionary statistics."""
    init_store()
    conn = _connect()
    row = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(byte_size) as total_bytes,
               MIN(created_at) as oldest,
               MAX(created_at) as newest
        FROM entries
    """).fetchone()
    conn.close()

    total_bytes = row["total_bytes"] or 0
    return {
        "total_entries": row["total"],
        "total_bytes_raw": total_bytes,
        "total_mb_raw": round(total_bytes / 1_048_576, 3),
        "oldest_entry": row["oldest"],
        "newest_entry": row["newest"],
        "layer": 1,
        "capacity": "2^100 positions",
    }
