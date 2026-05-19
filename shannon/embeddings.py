"""
shannon/embeddings.py — Embedding computation, storage, and similarity search.

Uses Ollama's nomic-embed-text model (768 dimensions) for local embeddings.
Falls back to keyword search if Ollama is unavailable.
"""

import hashlib
import json
import logging
import sqlite3
import struct
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import httpx

from .store import _connect, init_store, read_by_hash, LAYER1_DIR

log = logging.getLogger(__name__)

EMBED_MODEL = "mxbai-embed-large"
EMBED_DIM = 1024
OLLAMA_URL = "http://localhost:11434"

EMBEDDINGS_DB = LAYER1_DIR / "embeddings.db"


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def _embed_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(EMBEDDINGS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_embeddings() -> None:
    """Create embeddings table if not present."""
    LAYER1_DIR.mkdir(parents=True, exist_ok=True)
    conn = _embed_connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            content_hash TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            embedding BLOB NOT NULL,
            dim INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Embedding computation
# ---------------------------------------------------------------------------

def _pack_embedding(vec: list[float]) -> bytes:
    """Pack float list to compact binary (4 bytes per float)."""
    return struct.pack(f"!{len(vec)}f", *vec)


def _unpack_embedding(blob: bytes, dim: int = EMBED_DIM) -> list[float]:
    """Unpack binary embedding to float list."""
    return list(struct.unpack(f"!{dim}f", blob))


# ---------------------------------------------------------------------------
# Query embedding cache — avoids 2-second Ollama round-trip for repeated queries
# ---------------------------------------------------------------------------

_CACHE_MAX = 256  # max cached query embeddings
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(text: str) -> str:
    """Deterministic cache key for a query string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _cache_get(text: str) -> Optional[list[float]]:
    """Check the in-memory embedding cache."""
    key = _cache_key(text)
    with _cache_lock:
        if key in _embed_cache:
            _embed_cache.move_to_end(key)  # refresh LRU position
            return _embed_cache[key]
    return None


def _cache_put(text: str, vec: list[float]) -> None:
    """Store an embedding in the in-memory cache."""
    key = _cache_key(text)
    with _cache_lock:
        _embed_cache[key] = vec
        _embed_cache.move_to_end(key)
        while len(_embed_cache) > _CACHE_MAX:
            _embed_cache.popitem(last=False)  # evict oldest


def compute_embedding(text: str) -> Optional[list[float]]:
    """Compute embedding via Ollama with LRU cache.
    
    Returns cached result if available (instant). Otherwise calls Ollama
    (~2s round-trip) and caches the result for future calls.
    """
    # Check cache first
    cached = _cache_get(text)
    if cached is not None:
        return cached
    
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/embed",
            json={
                "model": EMBED_MODEL,
                "input": text,
                # Embed model is small (0.6GB) — CPU is fast enough
                # and avoids CUDA contention with inference models.
                "options": {"num_gpu": 0},
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ollama returns {"embeddings": [[...]]}
        embeddings = data.get("embeddings", [])
        if embeddings and len(embeddings) > 0:
            vec = embeddings[0]
            _cache_put(text, vec)
            return vec
        return None
    except Exception as e:
        log.warning("Embedding computation failed: %s", e)
        return None


def store_embedding(content_hash: str, embedding: list[float]) -> None:
    """Store a computed embedding."""
    init_embeddings()
    conn = _embed_connect()
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (content_hash, model, embedding, dim) VALUES (?, ?, ?, ?)",
        (content_hash, EMBED_MODEL, _pack_embedding(embedding), len(embedding)),
    )
    conn.commit()
    conn.close()


def get_embedding(content_hash: str) -> Optional[list[float]]:
    """Retrieve stored embedding for a content hash."""
    init_embeddings()
    conn = _embed_connect()
    row = conn.execute(
        "SELECT embedding, dim FROM embeddings WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    conn.close()
    if row:
        return _unpack_embedding(row["embedding"], row["dim"])
    return None


def embed_and_store(content_hash: str, text: str) -> Optional[list[float]]:
    """Compute embedding for text and store it. Returns the embedding or None."""
    # Check if already embedded
    existing = get_embedding(content_hash)
    if existing:
        return existing

    # mxbai-embed-large has a 512 token context window (~1500 chars).
    # Truncate to fit — the opening of a chunk is usually the most
    # informative part (title + source + first paragraph).
    if len(text) > 1500:
        text = text[:1500]

    vec = compute_embedding(text)
    if vec:
        store_embedding(content_hash, vec)
    return vec


# ---------------------------------------------------------------------------
# Similarity search
# ---------------------------------------------------------------------------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def semantic_search(
    query: str,
    content_hashes: list[str],
    top_k: int = 20,
) -> list[tuple[str, float]]:
    """
    Search for most similar entries to query.
    
    Args:
        query: search query text
        content_hashes: list of content hashes to search within
        top_k: number of results to return
    
    Returns:
        List of (content_hash, similarity_score) tuples, sorted by score desc.
    """
    query_vec = compute_embedding(query)
    if query_vec is None:
        return []  # Ollama unavailable — caller should fall back to keyword

    init_embeddings()
    conn = _embed_connect()
    
    # Batch fetch all embeddings
    results = []
    for ch in content_hashes:
        row = conn.execute(
            "SELECT embedding, dim FROM embeddings WHERE content_hash = ?",
            (ch,),
        ).fetchone()
        if row:
            vec = _unpack_embedding(row["embedding"], row["dim"])
            sim = _cosine_similarity(query_vec, vec)
            results.append((ch, sim))
    
    conn.close()
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def backfill_all(batch_size: int = 50) -> dict:
    """
    Embed all entries that don't have embeddings yet.
    
    Returns stats dict with counts.
    """
    init_store()
    init_embeddings()
    
    # Get all content hashes from main store
    conn = _connect()
    all_hashes = [
        row["content_hash"]
        for row in conn.execute("SELECT content_hash FROM entries").fetchall()
    ]
    conn.close()
    
    # Get already-embedded hashes
    econn = _embed_connect()
    embedded = set(
        row["content_hash"]
        for row in econn.execute("SELECT content_hash FROM embeddings").fetchall()
    )
    econn.close()
    
    missing = [h for h in all_hashes if h not in embedded]
    
    log.info("Backfill: %d total entries, %d already embedded, %d to process",
             len(all_hashes), len(embedded), len(missing))
    
    succeeded = 0
    failed = 0
    
    for i, ch in enumerate(missing):
        text = read_by_hash(ch)
        if not text:
            failed += 1
            continue
        
        vec = embed_and_store(ch, text)
        if vec:
            succeeded += 1
        else:
            failed += 1
        
        if (i + 1) % batch_size == 0:
            log.info("Backfill progress: %d/%d", i + 1, len(missing))
    
    return {
        "total": len(all_hashes),
        "already_embedded": len(embedded),
        "newly_embedded": succeeded,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def embedding_stats() -> dict:
    """Return embedding store statistics."""
    init_embeddings()
    conn = _embed_connect()
    row = conn.execute("SELECT COUNT(*) as count FROM embeddings").fetchone()
    conn.close()
    
    main_conn = _connect()
    total = main_conn.execute("SELECT COUNT(*) as count FROM entries").fetchone()["count"]
    main_conn.close()
    
    embedded = row["count"]
    return {
        "total_entries": total,
        "embedded": embedded,
        "coverage": round(embedded / total * 100, 1) if total > 0 else 0,
        "model": EMBED_MODEL,
        "dimensions": EMBED_DIM,
    }
