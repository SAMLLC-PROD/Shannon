"""Tests for OpenClaw integration."""

import pytest
from pathlib import Path
from shannon.openclaw import save, compress_session, generate_context_file

SESSION = "test-openclaw-session"


def test_save_returns_address():
    addr = save("Important decision: use Zeckendorf addressing", SESSION, tags=["architecture"])
    assert addr.startswith("F(")


def test_compress_session_multiple_chunks():
    chunks = [
        "Ron built Lattice Network across 3 continents",
        "7 validators, 5-of-7 Byzantine consensus",
        "Post-quantum crypto: ML-KEM-768 + ML-DSA-87",
    ]
    addresses = compress_session(chunks, SESSION, tags=["lattice"])
    assert len(addresses) == 3
    assert all(a.startswith("F(") for a in addresses)


def test_compress_session_skips_empty():
    chunks = ["real chunk", "", "   ", "another real chunk"]
    addresses = compress_session(chunks, SESSION)
    assert len(addresses) == 2


def test_generate_context_file_creates_file():
    save("Shannon context file test", SESSION)
    path = generate_context_file(days_back=1)
    assert path.exists()
    assert path.suffix == ".md"


def test_context_file_contains_saved_content():
    save("Unique marker: xq7z9shannon", SESSION, tags=["test"])
    generate_context_file(days_back=1)
    content = Path(generate_context_file(days_back=1)).read_text()
    assert "xq7z9shannon" in content


def test_context_file_has_shannon_header():
    path = generate_context_file(days_back=1)
    content = path.read_text()
    assert "Shannon Context" in content
    assert "Layer 1 Tesseract" in content
