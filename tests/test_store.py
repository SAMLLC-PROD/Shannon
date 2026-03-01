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
