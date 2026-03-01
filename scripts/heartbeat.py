#!/usr/bin/env python3
"""
Shannon heartbeat script.
Run periodically to persist context and keep LTM current.

Usage:
    python scripts/heartbeat.py
    python scripts/heartbeat.py --save "some important context" --tags decision,lattice
    python scripts/heartbeat.py --days 14  (regenerate from last 14 days)
"""

import sys
import argparse
import datetime
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from shannon.openclaw import save, generate_context_file
from shannon.store import stats


def main():
    parser = argparse.ArgumentParser(description="Shannon LTM heartbeat")
    parser.add_argument("--save", "-s", type=str, help="Context chunk to save")
    parser.add_argument("--tags", "-t", type=str, default="", help="Comma-separated tags")
    parser.add_argument("--days", "-d", type=int, default=7, help="Days back for context file (default 7)")
    parser.add_argument("--session", type=str, default=None, help="Session ID (default: today's date)")
    args = parser.parse_args()

    session_id = args.session or datetime.date.today().isoformat()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    # Save a chunk if provided
    if args.save:
        addr = save(args.save, session_id=session_id, tags=tags + ["heartbeat"])
        print(f"✓ Saved chunk")
        print(f"  Session: {session_id}")
        print(f"  Tags:    {tags + ['heartbeat']}")
        print(f"  Address: {addr[:80]}...")

    # Always regenerate context file
    path = generate_context_file(days_back=args.days)
    s = stats()
    print(f"✓ Context file regenerated: {path}")
    print(f"  Dictionary: {s['total_entries']} entries · {s['total_mb_raw']} MB · {s['capacity']}")


if __name__ == "__main__":
    main()
