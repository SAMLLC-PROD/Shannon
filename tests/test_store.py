"""Tests for Shannon dictionary store."""

import pytest
import hashlib
from shannon.store import write, read_by_hash, read_by_address, read_data, stats, get_session_chunks


SESSION = "test-session-001"


def test_write_returns_address():
    addr = write("hello from Shannon", session_id=SESSION)
    assert addr.startswith("F(")


def test_write_idempotent():
    addr1 = write("same data twice", session_id=SESSION)
    addr2 = write("same data twice", session_id=SESSION)
    assert addr1 == addr2


def test_read_by_hash():
    data = "retrievable chunk"
    write(data, session_id=SESSION)
    content_hash = hashlib.sha256(data.encode()).hexdigest()
    result = read_by_hash(content_hash)
    assert result == data


def test_read_by_address():
    data = "addressable chunk"
    addr = write(data, session_id=SESSION)
    result = read_by_address(addr)
    assert result == data


def test_read_data_content_addressed():
    data = "content addressed lookup"
    write(data, session_id=SESSION)
    result = read_data(data)
    assert result == data


def test_read_missing_returns_none():
    result = read_by_hash("0" * 64)
    assert result is None


def test_write_with_tags():
    addr = write("tagged memory chunk", session_id=SESSION, tags=["important", "lattice"])
    assert addr is not None


def test_stats_structure():
    s = stats()
    assert "total_entries" in s
    assert "total_bytes_raw" in s
    assert "layer" in s
    assert s["layer"] == 1
    assert s["total_entries"] >= 0


def test_session_chunks_retrievable():
    data = "session context chunk"
    write(data, session_id=SESSION)
    chunks = get_session_chunks(SESSION)
    contents = [c["content"] for c in chunks]
    assert data in contents


def test_different_data_different_address():
    addr1 = write("chunk alpha")
    addr2 = write("chunk beta")
    assert addr1 != addr2


def test_init_store_has_all_columns(tmp_path, monkeypatch):
    """Fresh DB from init_store() must have tenant_id and other late columns."""
    import sqlite3
    from shannon import store

    # Origin store resolves paths from module constants, not an env var.
    layer1 = tmp_path / "dictionary" / "layer_1"
    monkeypatch.setattr(store, "CHUNKS_DIR", layer1 / "chunks")
    monkeypatch.setattr(store, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(store, "INDEX_DB", layer1 / "index.db")

    store.init_store()
    conn = sqlite3.connect(layer1 / "index.db")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
    conn.close()
    for col in ("tenant_id", "tier", "profile_id", "conflict_group_id", "superseded_by", "supersedes"):
        assert col in cols, f"Column {col!r} missing after init_store()"

    # Reads that filter on late columns must work before any write.
    assert store.get_superseded_hashes() == set()
    assert store.resolve_conflict("no-such-group", "x", tenant_id="t") == 0
    store.init_store()  # idempotent
