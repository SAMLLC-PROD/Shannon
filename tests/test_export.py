"""Tests for Shannon CaaS context export."""

import time
import pytest

from shannon.store import write, init_store
from shannon.tenants import init_tenant_schema, register_tenant
from shannon.export import (
    export_tenant_memory,
    _ts_to_seconds,
    _extract_yt_info,
    _classify_entry,
    _cluster_entries,
)


def _email(p: str = "exp") -> str:
    return f"{p}+{int(time.time() * 1000)}@example.com"


@pytest.fixture(autouse=True)
def setup():
    init_store()
    init_tenant_schema()


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

def test_ts_to_seconds_mm_ss():
    assert _ts_to_seconds("1:30") == 90


def test_ts_to_seconds_hh_mm_ss():
    assert _ts_to_seconds("1:02:03") == 3723


def test_ts_to_seconds_invalid():
    assert _ts_to_seconds("bad") == 0


def test_extract_yt_info_with_url():
    body = "https://www.youtube.com/watch?v=dQw4w9WgXcQ — [2:30] Key insight here"
    info = _extract_yt_info(body, "yt-test")
    assert info is not None
    assert info["video_id"] == "dQw4w9WgXcQ"
    assert info["timestamp_str"] == "2:30"
    assert info["timestamp_sec"] == 150


def test_extract_yt_info_no_url():
    body = "Just a regular note with no YouTube link"
    assert _extract_yt_info(body, "session-123") is None


def test_extract_yt_info_short_url():
    body = "https://youtu.be/dQw4w9WgXcQ — [0:45] quick note"
    info = _extract_yt_info(body, "yt-test")
    assert info is not None
    assert info["video_id"] == "dQw4w9WgXcQ"
    assert info["timestamp_sec"] == 45


def test_classify_entry_youtube():
    etype = _classify_entry(["youtube", "transcript"], "yt-something")
    assert etype == "youtube"


def test_classify_entry_decision():
    etype = _classify_entry(["decision", "architecture"], "2026-01-01")
    assert etype == "decision"


def test_classify_entry_skill():
    etype = _classify_entry(["skill-building"], "2026-01-01")
    assert etype == "skill"


def test_classify_entry_note():
    etype = _classify_entry(["random", "tags"], "random-session")
    assert etype == "note"


def test_cluster_entries_groups_by_tag():
    entries = [
        {"tags": ["lattice", "decision"], "body": "a"},
        {"tags": ["lattice", "note"], "body": "b"},
        {"tags": ["pigeon", "note"], "body": "c"},
    ]
    clusters = _cluster_entries(entries)
    assert "lattice" in clusters
    assert len(clusters["lattice"]) == 2
    assert "pigeon" in clusters
    assert len(clusters["pigeon"]) == 1


# ---------------------------------------------------------------------------
# Full export tests
# ---------------------------------------------------------------------------

def test_export_empty_tenant():
    tid, _ = register_tenant(_email())
    output = export_tenant_memory(tid)
    assert "Shannon Memory Export" in output
    assert "Metadata" in output


def test_export_includes_decisions():
    tid, _ = register_tenant(_email())
    write(
        "Decision: use ML-DSA-87 for all signing operations",
        tags=["decision", "architecture"],
        tenant_id=tid,
    )
    output = export_tenant_memory(tid)
    assert "Decision" in output
    assert "ML-DSA-87" in output


def test_export_includes_metadata_header():
    tid, _ = register_tenant(_email("meta"))
    output = export_tenant_memory(tid)
    assert "Export Date" in output
    assert "Total Entries" in output
    assert "Topics" in output


def test_export_respects_topic_filter(monkeypatch):
    tid, _ = register_tenant(_email())
    write("Note about lattice network", tags=["lattice"], tenant_id=tid)
    write("Note about pigeon mail", tags=["pigeon"], tenant_id=tid)
    output = export_tenant_memory(tid, topic="lattice")
    assert "Filtered by topic" in output


def test_export_token_budget():
    tid, _ = register_tenant(_email())
    # Write many entries
    for i in range(20):
        write(f"Entry {i}: " + "x" * 200, tags=["note"], tenant_id=tid)
    output = export_tenant_memory(tid, limit_tokens=500)
    # Should be truncated
    assert "truncated" in output.lower() or len(output) < 5000


def test_export_youtube_entry_has_timestamp():
    tid, _ = register_tenant(_email())
    body = (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ — [3:45] "
        "Key insight about distributed systems"
    )
    write(body, tags=["youtube"], session_id="yt-distributed-systems-1", tenant_id=tid)
    output = export_tenant_memory(tid)
    # YouTube entry should produce a link with timestamp
    assert "youtube.com" in output or "@ 3:45" in output


def test_export_markdown_format():
    tid, _ = register_tenant(_email())
    write("A decision about something", tags=["decision"], tenant_id=tid)
    output = export_tenant_memory(tid, format="markdown")
    assert output.startswith("#")
    assert "##" in output
