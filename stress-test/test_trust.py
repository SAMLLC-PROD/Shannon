"""Tests for expert trust scoring (Issue #16)."""

import sys
import os
import time
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shannon.retrieval import _trust_weight, retrieve, TRUST_HIGH_TAGS, TRUST_LOW_TAGS
from shannon.store import write, init_store, _connect
from shannon.embeddings import embed_and_store


# ---------------------------------------------------------------------------
# Unit tests for _trust_weight
# ---------------------------------------------------------------------------

def test_verified_tag_returns_1():
    assert _trust_weight(["verified", "engine"]) == 1.0


def test_causal_knowledge_returns_1():
    assert _trust_weight(["causal-knowledge", "science"]) == 1.0


def test_founder_tag_returns_1():
    assert _trust_weight(["founder", "decisions"]) == 1.0


def test_distilled_rule_returns_1():
    assert _trust_weight(["distilled-rule", "guy"]) == 1.0


def test_spurious_correlation_returns_01():
    assert _trust_weight(["spurious-correlation"]) == 0.1


def test_no_causation_returns_01():
    assert _trust_weight(["no-causation", "stats"]) == 0.1


def test_default_tags_return_05():
    assert _trust_weight(["engine", "tuning", "guy"]) == 0.5


def test_empty_tags_return_05():
    assert _trust_weight([]) == 0.5


def test_high_tags_override_low():
    # If somehow both high and low tags are present, high wins (checked first)
    assert _trust_weight(["verified", "spurious-correlation"]) == 1.0


def test_trust_high_tags_constant():
    assert "verified" in TRUST_HIGH_TAGS
    assert "causal-knowledge" in TRUST_HIGH_TAGS
    assert "distilled-rule" in TRUST_HIGH_TAGS
    assert "founder" in TRUST_HIGH_TAGS


def test_trust_low_tags_constant():
    assert "spurious-correlation" in TRUST_LOW_TAGS
    assert "no-causation" in TRUST_LOW_TAGS


# ---------------------------------------------------------------------------
# Integration: verified entry must outrank spurious at same semantic distance
# ---------------------------------------------------------------------------

def _write_and_embed(body, tags, session):
    write(body, session_id=session, tags=tags, tier=2)
    ch = hashlib.sha256(body.encode()).hexdigest()
    embed_and_store(ch, body)
    return ch


def test_verified_outranks_spurious():
    """
    Two entries with identical semantic content.
    verified → trust 1.0, spurious-correlation → trust 0.1.
    The verified entry must appear first in retrieval.
    """
    init_store()
    session = f"trust-test-{int(time.time())}"
    agent = f"trust-test-agent-{int(time.time())}"

    # Use very similar bodies so semantic scores are nearly equal.
    # Agent is included in the body to guarantee unique content hashes per run.
    body_verified = (
        f"High octane fuel prevents engine knock by resisting premature "
        f"detonation during compression stroke under boost conditions. [{agent}]"
    )
    body_spurious = (
        f"High octane fuel prevents engine knock by resisting premature "
        f"detonation during compression stroke under boost conditions. [{agent}]"
        f" (note: coincidental correlation in small sample)"
    )
    ch_verified = _write_and_embed(body_verified, ["verified", "engine", "fuel", agent], session)
    ch_spurious = _write_and_embed(body_spurious, ["spurious-correlation", "engine", "fuel", agent], session)

    result = retrieve(agent_id=agent, topic="engine knock fuel octane", limit_tokens=4000, multi_pass=False)
    entries = result["entries"]

    e_verified = next((e for e in entries if e["id"] == ch_verified), None)
    e_spurious = next((e for e in entries if e["id"] == ch_spurious), None)

    assert e_verified is not None, "Verified entry not found in results"
    assert e_spurious is not None, "Spurious entry not found in results"
    assert e_verified["score"] > e_spurious["score"], (
        f"verified score ({e_verified['score']:.4f}) must exceed "
        f"spurious score ({e_spurious['score']:.4f})"
    )


def test_causal_knowledge_outranks_no_causation():
    """causal-knowledge (trust=1.0) must beat no-causation (trust=0.1)."""
    init_store()
    session = f"causal-test-{int(time.time())}"
    agent = f"causal-test-agent-{int(time.time())}"

    body_causal = (
        f"Smoking cigarettes directly causes lung cancer through carcinogens "
        f"that damage DNA in bronchial epithelial cells leading to malignant transformation. [{agent}]"
    )
    body_nocause = (
        f"Smoking cigarettes directly causes lung cancer through carcinogens "
        f"that damage DNA in bronchial epithelial cells — but this is debated. [{agent}]"
    )

    ch_causal = _write_and_embed(body_causal, ["causal-knowledge", "health", "smoking", agent], session)
    ch_nocause = _write_and_embed(body_nocause, ["no-causation", "health", "smoking", agent], session)

    result = retrieve(agent_id=agent, topic="smoking lung cancer causation", limit_tokens=4000, multi_pass=False)
    entries = result["entries"]

    e_causal = next((e for e in entries if e["id"] == ch_causal), None)
    e_nocause = next((e for e in entries if e["id"] == ch_nocause), None)

    assert e_causal is not None, "Causal entry not found"
    assert e_nocause is not None, "No-causation entry not found"
    assert e_causal["score"] > e_nocause["score"], (
        f"causal-knowledge ({e_causal['score']:.4f}) must beat "
        f"no-causation ({e_nocause['score']:.4f})"
    )


def test_trust_weight_in_entry_response():
    """Retrieved entries should include trust_weight field."""
    result = retrieve(agent_id="guy", topic="architecture decisions", limit_tokens=1000)
    for entry in result.get("entries", []):
        assert "trust_weight" in entry, f"trust_weight missing from entry {entry['id'][:8]}"
        tw = entry["trust_weight"]
        assert 0.0 <= tw <= 1.0, f"trust_weight {tw} out of range [0, 1]"


def test_trust_weight_matches_tags():
    """Entries tagged 'verified' should report trust_weight 1.0."""
    init_store()
    session = f"tw-check-{int(time.time())}"
    agent = f"tw-agent-{int(time.time())}"
    body = "Verified fact: water boils at 100 degrees Celsius at sea level atmospheric pressure."
    ch = _write_and_embed(body, ["verified", "science", agent], session)

    result = retrieve(agent_id=agent, topic="water boiling point temperature", limit_tokens=4000, multi_pass=False)
    entry = next((e for e in result["entries"] if e["id"] == ch), None)
    assert entry is not None, "Entry not found"
    assert entry["trust_weight"] == 1.0, f"Expected 1.0, got {entry['trust_weight']}"


def test_spurious_trust_weight_is_01():
    """Entries tagged 'spurious-correlation' should report trust_weight 0.1."""
    init_store()
    session = f"sp-check-{int(time.time())}"
    agent = f"sp-agent-{int(time.time())}"
    body = f"Spurious: Ice cream sales correlate with drowning deaths in summer months. [{agent}]"
    ch = _write_and_embed(body, ["spurious-correlation", "stats", agent], session)

    result = retrieve(agent_id=agent, topic="ice cream drowning correlation summer", limit_tokens=4000, multi_pass=False)
    entry = next((e for e in result["entries"] if e["id"] == ch), None)
    assert entry is not None, "Spurious entry not found"
    assert entry["trust_weight"] == 0.1, f"Expected 0.1, got {entry['trust_weight']}"


if __name__ == "__main__":
    tests = [
        test_verified_tag_returns_1,
        test_causal_knowledge_returns_1,
        test_founder_tag_returns_1,
        test_distilled_rule_returns_1,
        test_spurious_correlation_returns_01,
        test_no_causation_returns_01,
        test_default_tags_return_05,
        test_empty_tags_return_05,
        test_high_tags_override_low,
        test_trust_high_tags_constant,
        test_trust_low_tags_constant,
        test_verified_outranks_spurious,
        test_causal_knowledge_outranks_no_causation,
        test_trust_weight_in_entry_response,
        test_trust_weight_matches_tags,
        test_spurious_trust_weight_is_01,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
