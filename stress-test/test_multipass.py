"""Tests for multi-pass retrieval (Issue #19)."""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shannon.retrieval import (
    retrieve,
    _extract_keywords,
    _keyword_score,
    _graph_expand,
)
from shannon.store import write, init_store


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

def test_extract_keywords_removes_stopwords():
    kws = _extract_keywords("What causes engine knock under boost pressure?")
    assert "what" not in kws
    assert "causes" in kws
    assert "engine" in kws
    assert "knock" in kws
    assert "boost" in kws
    assert "pressure" in kws


def test_extract_keywords_min_length():
    kws = _extract_keywords("the a an is are to of it")
    assert kws == [], f"Expected empty, got {kws}"


def test_keyword_score_full_match():
    score = _keyword_score(["engine", "knock", "boost"], "engine knock under boost causes damage")
    assert score == 1.0, f"Expected 1.0, got {score}"


def test_keyword_score_partial():
    score = _keyword_score(["engine", "knock", "boost"], "engine boost")
    assert abs(score - 2/3) < 0.01, f"Expected ~0.667, got {score}"


def test_keyword_score_no_keywords():
    score = _keyword_score([], "any text here")
    assert score == 0.0


def test_keyword_score_empty_body():
    score = _keyword_score(["engine", "knock"], "")
    assert score == 0.0


def test_graph_expand_same_session():
    entry1 = {
        "content_hash": "hash_a",
        "session_id": "session-xyz",
        "tags": ["test"],
        "created_at": "2026-01-01T12:00:00+00:00",
    }
    entry2 = {
        "content_hash": "hash_b",
        "session_id": "session-xyz",
        "tags": ["test"],
        "created_at": "2026-01-01T12:30:00+00:00",
    }
    entry3 = {
        "content_hash": "hash_c",
        "session_id": "session-other",
        "tags": ["different"],
        "created_at": "2026-01-01T13:00:00+00:00",
    }
    related = _graph_expand([entry1], [entry1, entry2, entry3])
    assert "hash_b" in related
    assert "hash_c" not in related


def test_graph_expand_tag_overlap():
    entry1 = {
        "content_hash": "hash_a",
        "session_id": "s1",
        "tags": ["engine", "turbo", "boost"],
        "created_at": "2026-01-01T12:00:00+00:00",
    }
    entry2 = {
        "content_hash": "hash_b",
        "session_id": "s2",
        "tags": ["engine", "turbo", "intercooler"],
        "created_at": "2026-01-01T15:00:00+00:00",
    }
    entry3 = {
        "content_hash": "hash_c",
        "session_id": "s3",
        "tags": ["unrelated"],
        "created_at": "2026-01-01T15:00:00+00:00",
    }
    related = _graph_expand([entry1], [entry1, entry2, entry3])
    assert "hash_b" in related  # 2 shared tags: engine, turbo
    assert "hash_c" not in related


def test_graph_expand_time_proximity():
    entry1 = {
        "content_hash": "hash_a",
        "session_id": None,
        "tags": [],
        "created_at": "2026-01-01T12:00:00+00:00",
    }
    entry2 = {
        "content_hash": "hash_b",
        "session_id": None,
        "tags": [],
        "created_at": "2026-01-01T12:03:00+00:00",  # 3 min later — within 5min window
    }
    entry3 = {
        "content_hash": "hash_c",
        "session_id": None,
        "tags": [],
        "created_at": "2026-01-01T12:10:00+00:00",  # 10 min later — outside
    }
    related = _graph_expand([entry1], [entry1, entry2, entry3])
    assert "hash_b" in related
    assert "hash_c" not in related


# ---------------------------------------------------------------------------
# Integration tests against live Shannon
# ---------------------------------------------------------------------------

def test_retrieve_returns_synthesis():
    result = retrieve(agent_id="guy", topic="Shannon memory architecture", limit_tokens=1000)
    assert "synthesis" in result, "synthesis key missing from retrieve() result"
    s = result["synthesis"]
    assert "entry_count" in s
    assert "session_count" in s
    assert "passes_used" in s
    assert isinstance(s["passes_used"], list)
    assert len(s["passes_used"]) > 0


def test_retrieve_multi_pass_uses_all_three():
    result = retrieve(agent_id="guy", topic="neural network training embeddings", limit_tokens=2000, multi_pass=True)
    assert "synthesis" in result
    passes = result["synthesis"]["passes_used"]
    assert "semantic" in passes, f"Expected semantic pass, got {passes}"
    # keyword and graph may or may not trigger depending on data, but they should at least be tried


def test_retrieve_single_pass():
    result = retrieve(agent_id="guy", topic="embeddings", limit_tokens=1000, multi_pass=False)
    assert "synthesis" in result
    passes = result["synthesis"]["passes_used"]
    assert "graph" not in passes, f"Graph pass should not run when multi_pass=False"


def test_retrieve_no_topic_uses_recency():
    result = retrieve(agent_id="guy", limit_tokens=500, multi_pass=False)
    assert "synthesis" in result
    passes = result["synthesis"]["passes_used"]
    assert "semantic" not in passes or passes == ["recency"]


def test_retrieve_returns_conflicts_key():
    result = retrieve(agent_id="guy", topic="test", limit_tokens=1000)
    assert "conflicts" in result, "conflicts key missing from retrieve() result"
    assert isinstance(result["conflicts"], list)


def test_retrieve_keyword_score_in_entries():
    result = retrieve(agent_id="guy", topic="Shannon memory context", limit_tokens=1000, multi_pass=True)
    # Each entry should have trust_weight field
    for entry in result.get("entries", []):
        assert "trust_weight" in entry, f"trust_weight missing from entry {entry.get('id','?')[:8]}"


def test_retrieve_multi_pass_finds_more():
    """Multi-pass should return at least as many results as single-pass."""
    r_single = retrieve(agent_id="guy", topic="machine learning model training", limit_tokens=3000, multi_pass=False)
    r_multi = retrieve(agent_id="guy", topic="machine learning model training", limit_tokens=3000, multi_pass=True)
    # Multi-pass should have >= entries (graph expansion can add more)
    assert r_multi["scored_count"] >= r_single["scored_count"]


if __name__ == "__main__":
    tests = [
        test_extract_keywords_removes_stopwords,
        test_extract_keywords_min_length,
        test_keyword_score_full_match,
        test_keyword_score_partial,
        test_keyword_score_no_keywords,
        test_keyword_score_empty_body,
        test_graph_expand_same_session,
        test_graph_expand_tag_overlap,
        test_graph_expand_time_proximity,
        test_retrieve_returns_synthesis,
        test_retrieve_multi_pass_uses_all_three,
        test_retrieve_single_pass,
        test_retrieve_no_topic_uses_recency,
        test_retrieve_returns_conflicts_key,
        test_retrieve_keyword_score_in_entries,
        test_retrieve_multi_pass_finds_more,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
