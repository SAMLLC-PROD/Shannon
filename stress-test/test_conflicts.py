"""Tests for conflict detection (Issue #17)."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shannon.retrieval import _detect_conflicts, retrieve
from shannon.store import write, resolve_conflict, init_store, _connect


# ---------------------------------------------------------------------------
# Unit tests for _detect_conflicts
# ---------------------------------------------------------------------------

def _make_entry(id, tags, body):
    return {"id": id, "tags": tags, "body": body}


def test_no_conflicts_no_numbers():
    entries = [
        _make_entry("a1", ["engine", "tuning"], "Engine timing is important for performance"),
        _make_entry("a2", ["engine", "tuning"], "Valve timing affects power output greatly"),
    ]
    conflicts = _detect_conflicts(entries)
    assert conflicts == [], f"Expected no conflicts, got {conflicts}"


def test_no_conflicts_different_tags():
    entries = [
        _make_entry("b1", ["engine"], "Power output is 400 hp at 6000 rpm"),
        _make_entry("b2", ["suspension"], "Power output is 300 hp at 5000 rpm"),
    ]
    conflicts = _detect_conflicts(entries)
    assert conflicts == [], "No shared meaningful tags → no conflict"


def test_detects_numeric_conflict():
    entries = [
        _make_entry("c1", ["engine", "dyno"], "Peak power output is 400 hp at 6000 rpm with boost"),
        _make_entry("c2", ["engine", "dyno"], "Peak power output is 450 hp at 6000 rpm with boost"),
    ]
    conflicts = _detect_conflicts(entries)
    assert len(conflicts) == 1, f"Expected 1 conflict group, got {len(conflicts)}: {conflicts}"
    cg = conflicts[0]
    assert "c1" in cg["entry_ids"]
    assert "c2" in cg["entry_ids"]
    assert "conflict_group_id" in cg
    assert len(cg["conflict_group_id"]) > 0


def test_conflict_group_id_is_string():
    entries = [
        _make_entry("d1", ["timing", "engine"], "Ignition timing set to 15 degrees BTDC at idle"),
        _make_entry("d2", ["timing", "engine"], "Ignition timing set to 18 degrees BTDC at idle"),
    ]
    conflicts = _detect_conflicts(entries)
    if conflicts:
        assert isinstance(conflicts[0]["conflict_group_id"], str)


def test_no_conflict_same_numbers():
    entries = [
        _make_entry("e1", ["fuel", "tuning"], "Air fuel ratio target is 12.5 at full boost"),
        _make_entry("e2", ["fuel", "tuning"], "Air fuel ratio target is 12.5 at full boost"),
    ]
    conflicts = _detect_conflicts(entries)
    assert conflicts == [], "Same numbers → no conflict"


def test_no_conflict_low_text_similarity():
    entries = [
        _make_entry("f1", ["spec"], "Valve spring pressure 150 lbs seat pressure installed"),
        _make_entry("f2", ["spec"], "Completely unrelated text about cooking recipe ingredients 200 grams"),
    ]
    conflicts = _detect_conflicts(entries)
    assert conflicts == [], "Low text similarity → no conflict despite numeric diff"


def test_multiple_conflict_pairs_grouped():
    # Three entries where 1-2 conflict and 2-3 conflict → should form one group
    entries = [
        _make_entry("g1", ["boost", "turbo"], "Boost pressure target 18 psi at 5000 rpm under load"),
        _make_entry("g2", ["boost", "turbo"], "Boost pressure target 22 psi at 5000 rpm under load"),
        _make_entry("g3", ["boost", "turbo"], "Boost pressure target 25 psi at 5000 rpm under load"),
    ]
    conflicts = _detect_conflicts(entries)
    assert len(conflicts) >= 1, "Should detect at least one conflict"
    all_entry_ids = [eid for cg in conflicts for eid in cg["entry_ids"]]
    # All three should be represented
    for eid in ["g1", "g2", "g3"]:
        assert eid in all_entry_ids, f"Entry {eid} should be in a conflict group"


def test_shared_tags_in_conflict():
    entries = [
        _make_entry("h1", ["engine", "spec", "turbo"], "Compression ratio 9.5:1 with forged pistons"),
        _make_entry("h2", ["engine", "spec", "turbo"], "Compression ratio 8.5:1 with forged pistons"),
    ]
    conflicts = _detect_conflicts(entries)
    if conflicts:
        assert len(conflicts[0]["shared_tags"]) > 0


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def test_resolve_conflict_marks_loser():
    """Write two conflicting entries, resolve, verify superseded_by is set."""
    init_store()
    session = f"test-conflict-{int(time.time())}"
    tags = ["test-conflict", "integration"]

    write("Peak torque output 400 ft-lbs at 4500 rpm on dyno run", session_id=session, tags=tags)
    write("Peak torque output 380 ft-lbs at 4500 rpm on dyno run", session_id=session, tags=tags)

    import hashlib
    h1 = hashlib.sha256("Peak torque output 400 ft-lbs at 4500 rpm on dyno run".encode()).hexdigest()
    h2 = hashlib.sha256("Peak torque output 380 ft-lbs at 4500 rpm on dyno run".encode()).hexdigest()

    # Manually assign conflict group
    conn = _connect()
    conn.execute("UPDATE entries SET conflict_group_id = 'test-grp-001' WHERE content_hash = ?", (h1,))
    conn.execute("UPDATE entries SET conflict_group_id = 'test-grp-001' WHERE content_hash = ?", (h2,))
    conn.commit()
    conn.close()

    # Resolve: h1 wins
    updated = resolve_conflict("test-grp-001", h1)
    assert updated == 1, f"Expected 1 entry superseded, got {updated}"

    conn = _connect()
    row = conn.execute("SELECT superseded_by FROM entries WHERE content_hash = ?", (h2,)).fetchone()
    conn.close()
    assert row["superseded_by"] == h1, f"Expected {h1}, got {row['superseded_by']}"


def test_superseded_entries_deprioritized():
    """Entries with superseded_by set should have lower score than normal entries."""
    init_store()
    session = f"test-sup-{int(time.time())}"

    write("Engine knock is caused by detonation at high boost pressure levels",
          session_id=session, tags=["test-sup", "engine", "detonation"])
    write("Engine knock is caused by pre-ignition at high boost pressure levels",
          session_id=session, tags=["test-sup", "engine", "detonation"])

    import hashlib
    h1 = hashlib.sha256("Engine knock is caused by detonation at high boost pressure levels".encode()).hexdigest()
    h2 = hashlib.sha256("Engine knock is caused by pre-ignition at high boost pressure levels".encode()).hexdigest()

    conn = _connect()
    conn.execute("UPDATE entries SET conflict_group_id = 'test-depr-001' WHERE content_hash IN (?, ?)", (h1, h2))
    conn.execute("UPDATE entries SET superseded_by = ? WHERE content_hash = ?", (h1, h2))
    conn.commit()
    conn.close()

    result = retrieve(agent_id="test-sup", topic="engine knock boost", limit_tokens=2000)
    entries = result["entries"]
    h2_entry = next((e for e in entries if e["id"] == h2), None)
    h1_entry = next((e for e in entries if e["id"] == h1), None)

    if h1_entry and h2_entry:
        assert h1_entry["score"] > h2_entry["score"], (
            f"Winner ({h1_entry['score']:.4f}) should outrank superseded ({h2_entry['score']:.4f})"
        )


def test_retrieve_conflicts_in_response():
    result = retrieve(agent_id="guy", topic="timing degrees rpm engine", limit_tokens=2000, multi_pass=True)
    assert "conflicts" in result
    assert isinstance(result["conflicts"], list)
    # Each conflict group should have required fields
    for cg in result["conflicts"]:
        assert "conflict_group_id" in cg
        assert "entry_ids" in cg
        assert len(cg["entry_ids"]) >= 2


if __name__ == "__main__":
    tests = [
        test_no_conflicts_no_numbers,
        test_no_conflicts_different_tags,
        test_detects_numeric_conflict,
        test_conflict_group_id_is_string,
        test_no_conflict_same_numbers,
        test_no_conflict_low_text_similarity,
        test_multiple_conflict_pairs_grouped,
        test_shared_tags_in_conflict,
        test_resolve_conflict_marks_loser,
        test_superseded_entries_deprioritized,
        test_retrieve_conflicts_in_response,
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
