"""
Shannon dictionary store — Layer 1 Tesseract.

Append-only. Content-addressed. Collision-free by Zeckendorf theorem.
Nothing is ever deleted. Old context is always retrievable.

Supersession: entries can mark other entries as superseded via the
`supersedes` column. Superseded entries are excluded from retrieval
but remain in storage (append-only — never deleted).
"""

import sqlite3
import json
import hashlib
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

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
            byte_size     INTEGER DEFAULT 0,
            supersedes    TEXT DEFAULT '[]'
        );

        CREATE INDEX IF NOT EXISTS idx_session  ON entries(session_id);
        CREATE INDEX IF NOT EXISTS idx_created  ON entries(created_at);
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
            USING fts5(content_hash, address, tags, content='entries');
    """)
    conn.commit()
    # Add tier column — ALTER TABLE doesn't support IF NOT EXISTS in SQLite
    try:
        conn.execute("ALTER TABLE entries ADD COLUMN tier INTEGER DEFAULT 2")
        conn.commit()
    except Exception:
        pass  # column already exists
    conn.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write(
    data: str,
    session_id: str = None,
    tags: List[str] = None,
    tier: int = 2,
    tenant_id: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> str:
    """
    Write a context chunk to the Shannon dictionary.

    Returns the Zeckendorf address string. Idempotent — writing the same
    data twice returns the same address without duplicating storage.
    tenant_id=None means internal (guy/henry/etc); provide a UUID for CaaS tenants.
    """
    init_store()
    raw = data.encode("utf-8")
    # Tenant entries get a per-tenant hash so the same text from two different
    # tenants produces distinct entries (full namespace isolation).
    # Internal writes (tenant_id=None) use the plain content hash as before.
    if tenant_id is not None:
        content_hash = hashlib.sha256(f"{tenant_id}\0".encode() + raw).hexdigest()
    else:
        content_hash = hashlib.sha256(raw).hexdigest()
    address  = data_to_address(raw)
    addr_str = address_to_str(address)

    # --- Store compressed chunk ---
    chunk_path = CHUNKS_DIR / f"{content_hash}.zst"
    if not chunk_path.exists():
        if HAS_ZSTD:
            compressed = zstd.ZstdCompressor(level=9).compress(raw)
        else:
            compressed = raw  # fallback: store raw if zstd not installed
        chunk_path.write_bytes(compressed)

    # --- Index entry (idempotent on content_hash) ---
    conn = _connect()
    # Ensure tenant_id column exists (migration guard)
    try:
        conn.execute("ALTER TABLE entries ADD COLUMN tenant_id TEXT")
        conn.commit()
    except Exception:
        pass
    # Ensure profile_id column exists
    try:
        conn.execute("ALTER TABLE entries ADD COLUMN profile_id TEXT")
        conn.commit()
    except Exception:
        pass
    conn.execute(
        """INSERT OR IGNORE INTO entries
           (content_hash, address, created_at, session_id, tags, byte_size, tier, tenant_id, profile_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            content_hash,
            addr_str,
            datetime.now(timezone.utc).isoformat(),
            session_id,
            json.dumps(tags or []),
            len(raw),
            tier,
            tenant_id,
            profile_id,
        ),
    )
    conn.commit()
    conn.close()

    # --- Supersession detection ---
    # If the content contains retraction patterns, search for contradicted
    # memories and attach supersedes pointers.
    superseded = _detect_supersession(data, tags or [])
    if superseded:
        conn = _connect()
        conn.execute(
            "UPDATE entries SET supersedes = ? WHERE content_hash = ?",
            (json.dumps(superseded), content_hash),
        )
        conn.commit()
        conn.close()

    # --- Log address to session index ---
    if session_id:
        _log_session(session_id, content_hash, addr_str)

    return addr_str


# ---------------------------------------------------------------------------
# Supersession detection
# ---------------------------------------------------------------------------

# Patterns that indicate the author is retracting or replacing prior context.
_RETRACTION_PATTERNS = [
    re.compile(r"\b(no longer|not anymore|stopped? using|switched (?:from|away)|changed (?:from|my mind))", re.I),
    re.compile(r"\b(replaced|deprecated|superseded|obsolete|retired|decommissioned)", re.I),
    re.compile(r"\b(used to|previously|formerly|was using|were using)", re.I),
    re.compile(r"\b(moved (?:from|to)|migrated (?:from|to)|transitioned (?:from|to))", re.I),
    re.compile(r"\b(reverted|rolled back|undid|cancelled|abandoned)", re.I),
]


def _detect_supersession(content: str, tags: List[str]) -> List[str]:
    """Detect if new content supersedes existing entries.
    
    If the content contains retraction-pattern language, search for
    entries with overlapping tags that might be contradicted.
    Returns a list of content_hashes that this entry supersedes.
    
    Conservative: only marks supersession when there's both a retraction
    pattern AND a tag-overlapping prior entry. False negatives are safe;
    false positives lose retrieval signal.
    """
    # Only trigger for entries with meaningful tags (skip generic)
    meaningful_tags = [t for t in tags if t not in ("guy", "heartbeat", "default", "test")]
    if not meaningful_tags:
        return []
    
    # Check if content contains retraction language
    has_retraction = any(p.search(content) for p in _RETRACTION_PATTERNS)
    if not has_retraction:
        return []
    
    # Search for prior entries with overlapping tags
    # that might be contradicted by this new entry
    try:
        from .embeddings import compute_embedding, get_embedding, _cosine_similarity
        query_vec = compute_embedding(content)
    except Exception:
        query_vec = None
    
    conn = _connect()
    # Find entries with at least one overlapping meaningful tag
    candidates = []
    for tag in meaningful_tags[:5]:  # limit tag search breadth
        rows = conn.execute(
            "SELECT content_hash, tags, created_at FROM entries "
            "WHERE tags LIKE ? ORDER BY created_at DESC LIMIT 20",
            (f'%"{tag}"%',),
        ).fetchall()
        for row in rows:
            candidates.append(row)
    conn.close()
    
    # Deduplicate candidates
    seen = set()
    unique_candidates = []
    for c in candidates:
        ch = c["content_hash"]
        if ch not in seen:
            seen.add(ch)
            unique_candidates.append(c)
    
    # Score candidates by semantic similarity to the new content
    superseded = []
    for candidate in unique_candidates:
        ch = candidate["content_hash"]
        
        if query_vec:
            entry_vec = get_embedding(ch)
            if entry_vec:
                sim = _cosine_similarity(query_vec, entry_vec)
                # High similarity + retraction language = likely supersession
                if sim > 0.65:
                    superseded.append(ch)
                    if len(superseded) >= 5:  # cap to avoid runaway
                        break
    
    if superseded:
        import logging
        logging.getLogger(__name__).info(
            "Supersession detected: new entry supersedes %d prior entries",
            len(superseded),
        )
    
    return superseded


def get_superseded_hashes() -> Set[str]:
    """Return the set of all content_hashes that have been superseded.
    
    Used by retrieval to filter out stale entries.
    """
    init_store()
    conn = _connect()
    
    # Migrate: add supersedes column if missing (for existing DBs)
    try:
        conn.execute("SELECT supersedes FROM entries LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE entries ADD COLUMN supersedes TEXT DEFAULT '[]'")
        conn.commit()
    
    rows = conn.execute(
        "SELECT supersedes FROM entries WHERE supersedes != '[]' AND supersedes IS NOT NULL"
    ).fetchall()
    conn.close()
    
    result: Set[str] = set()
    for row in rows:
        try:
            hashes = json.loads(row["supersedes"])
            result.update(hashes)
        except (json.JSONDecodeError, TypeError):
            pass
    return result


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
        # Try zstd decompression first; fall back to raw text if the
        # file isn't actually compressed (e.g., YouTube transcript chunks
        # stored as plain text with .zst extension).
        try:
            return zstd.ZstdDecompressor().decompress(raw).decode("utf-8")
        except zstd.ZstdError:
            return raw.decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


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
