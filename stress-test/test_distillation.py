"""Tests for memory distillation (Issue #18)."""

import sys
import os
import time
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shannon.distillation import (
    _jaccard_words,
    distill_rule,
    save_rule,
    list_rules,
    delete_rule,
    scan_for_patterns,
)
from shannon.retrieval import retrieve
from shannon.store import write, init_store
from shannon.embeddings import embed_and_store


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

def test_jaccard_identical():
    j = _jaccard_words("engine knock causes damage", "engine knock causes damage")
    assert j == 1.0


def test_jaccard_no_overlap():
    j = _jaccard_words("apple banana cherry", "delta echo foxtrot")
    assert j == 0.0


def test_jaccard_partial():
    j = _jaccard_words("engine knock boost turbo", "engine boost intercooler")
    # words: engine(4+), knock(5+), boost(5+), turbo(5+), intercooler → short words excluded
    # 4+ char: engine, knock, boost, turbo vs engine, boost, intercooler
    # intersection: engine, boost = 2; union = engine, knock, boost, turbo, intercooler = 5
    assert 0.0 < j < 1.0


def test_jaccard_short_words_excluded():
    j = _jaccard_words("the cat sat on mat", "cat sat mat")
    # Only words 4+ chars: none here (all < 4 chars)
    assert j == 0.0


def test_distill_rule_single_entry():
    text = "Use forged pistons for high boost applications. They handle pressure better."
    rule = distill_rule([text])
    assert len(rule) > 0
    assert len(rule) <= len(text) + 5


def test_distill_rule_multiple_entries():
    entries = [
        "Always use forged pistons for high boost turbo applications to handle cylinder pressure.",
        "Forged pistons are required for high boost turbo builds due to cylinder pressure loads.",
        "High boost turbo engines need forged pistons because cylinder pressure exceeds stock limits.",
    ]
    rule = distill_rule(entries)
    assert len(rule) > 0
    # The rule should reference common concepts
    rule_lower = rule.lower()
    assert any(word in rule_lower for word in ["forged", "boost", "turbo", "piston", "pressure", "cylinder"])


def test_distill_rule_empty():
    rule = distill_rule([])
    assert rule == ""


def test_distill_rule_no_short_sentences():
    entries = ["Hi.", "Yes.", "Ok."]
    rule = distill_rule(entries)
    # Should not crash even with no long sentences


def test_save_and_list_rule():
    init_store()
    agent = f"rule-test-{int(time.time())}"
    rule_text = "Always use forged pistons for builds exceeding 400 horsepower boost."
    sources = ["fake-hash-001", "fake-hash-002", "fake-hash-003"]

    rule_id = save_rule(agent, rule_text, sources)
    assert isinstance(rule_id, str)
    assert len(rule_id) == 64  # sha256 hex

    rules = list_rules(agent)
    assert len(rules) > 0
    rule_bodies = [r["body"] for r in rules]
    assert any(rule_text in body for body in rule_bodies), f"Rule not found in {rule_bodies[:3]}"


def test_list_rules_filtered_by_agent():
    init_store()
    agent_a = f"agent-a-{int(time.time())}"
    agent_b = f"agent-b-{int(time.time())}"

    save_rule(agent_a, "Agent A rule: use synthetic oil every 3000 miles interval minimum", ["h1"])
    save_rule(agent_b, "Agent B rule: check tire pressure monthly for optimal fuel economy", ["h2"])

    rules_a = list_rules(agent_a)
    rules_b = list_rules(agent_b)

    assert len(rules_a) >= 1
    assert len(rules_b) >= 1

    for r in rules_a:
        assert agent_a in r["tags"], f"Expected {agent_a} in tags, got {r['tags']}"
    for r in rules_b:
        assert agent_b in r["tags"], f"Expected {agent_b} in tags, got {r['tags']}"


def test_delete_rule_soft():
    init_store()
    agent = f"del-test-{int(time.time())}"
    rule_text = "Delete test rule: intercooler efficiency affects charge temperature inlet."
    rule_id = save_rule(agent, rule_text, ["h1", "h2"])

    rules_before = list_rules(agent)
    assert any(r["id"] == rule_id for r in rules_before), "Rule should exist before delete"

    ok = delete_rule(rule_id)
    assert ok is True

    rules_after = list_rules(agent)
    assert not any(r["id"] == rule_id for r in rules_after), "Rule should not appear after soft-delete"


def test_delete_nonexistent_rule():
    ok = delete_rule("deadbeef" * 8)
    assert ok is False


def test_delete_non_rule_entry():
    # Write a normal (non-rule) entry and try to delete it as a rule
    init_store()
    body = "Normal entry, not a rule"
    write(body, session_id="test-del", tags=["test"])
    ch = hashlib.sha256(body.encode()).hexdigest()
    ok = delete_rule(ch)
    assert ok is False, "Should not delete non-rule entries"


def test_spurious_entries_excluded_from_scan():
    """Entries tagged no-causation should not appear in scan_for_patterns results."""
    init_store()
    agent = f"spurious-scan-{int(time.time())}"
    session = f"spurious-session-{int(time.time())}"

    # Write 3 similar spurious entries
    for i in range(3):
        body = f"Spurious pattern {i}: ice cream consumption correlates with sunburn incidents in summer."
        write(body, session_id=session, tags=["no-causation", "spurious-correlation", "stats", agent])

    groups = scan_for_patterns(agent, days=1)
    # Spurious entries should be excluded
    for group in groups:
        # Load the actual entries and check their tags
        from shannon.store import _connect, read_by_hash
        import json
        conn = _connect()
        for entry_id in group["entry_ids"]:
            row = conn.execute("SELECT tags FROM entries WHERE content_hash = ?", (entry_id,)).fetchone()
            if row:
                tags = json.loads(row["tags"] or "[]")
                assert "no-causation" not in tags, f"Spurious entry {entry_id} included in scan!"
                assert "spurious-correlation" not in tags, f"Spurious entry {entry_id} included in scan!"
        conn.close()


def test_distilled_rules_top_in_retrieval():
    """Distilled rules should appear at the top of retrieval results."""
    init_store()
    agent = f"top-rule-{int(time.time())}"
    session = f"top-rule-session-{int(time.time())}"

    # Write a regular entry
    regular_body = "Regular entry about engine tuning and boost pressure adjustments for performance."
    write(regular_body, session_id=session, tags=[agent, "engine", "tuning"], tier=2)
    ch_regular = hashlib.sha256(regular_body.encode()).hexdigest()
    embed_and_store(ch_regular, regular_body)

    # Write a distilled rule
    rule_text = "Distilled rule test: forged pistons required for high boost applications."
    rule_id = save_rule(agent, rule_text, ["src1", "src2", "src3"])
    embed_and_store(rule_id, f"[DISTILLED RULE] {rule_text}")

    result = retrieve(agent_id=agent, topic="engine boost pistons turbo", limit_tokens=4000)
    entries = result["entries"]

    rule_entry = next((e for e in entries if e["id"] == rule_id), None)
    regular_entry = next((e for e in entries if e["id"] == ch_regular), None)

    if rule_entry and regular_entry:
        # Rule should appear before regular entry (higher score or earlier in list)
        rule_idx = entries.index(rule_entry)
        regular_idx = entries.index(regular_entry)
        assert rule_idx <= regular_idx, (
            f"Rule (idx {rule_idx}) should appear before regular entry (idx {regular_idx})"
        )


def test_scan_returns_groups_of_3_plus():
    """scan_for_patterns should only return groups with 3+ entries."""
    init_store()
    agent = f"scan-size-{int(time.time())}"
    session = f"scan-session-{int(time.time())}"

    for i in range(5):
        body = (
            f"Forged pistons are required for high boost turbo applications version {i}. "
            "They handle cylinder pressure loads that exceed stock piston limits significantly."
        )
        write(body, session_id=session, tags=[agent, "engine", "pistons", "turbo", "forged"])

    groups = scan_for_patterns(agent, days=1)
    for group in groups:
        assert group["count"] >= 3, f"Group has only {group['count']} entries: {group}"


if __name__ == "__main__":
    tests = [
        test_jaccard_identical,
        test_jaccard_no_overlap,
        test_jaccard_partial,
        test_jaccard_short_words_excluded,
        test_distill_rule_single_entry,
        test_distill_rule_multiple_entries,
        test_distill_rule_empty,
        test_distill_rule_no_short_sentences,
        test_save_and_list_rule,
        test_list_rules_filtered_by_agent,
        test_delete_rule_soft,
        test_delete_nonexistent_rule,
        test_delete_non_rule_entry,
        test_spurious_entries_excluded_from_scan,
        test_distilled_rules_top_in_retrieval,
        test_scan_returns_groups_of_3_plus,
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
