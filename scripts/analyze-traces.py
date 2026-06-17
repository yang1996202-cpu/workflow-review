#!/usr/bin/env python3
"""Analyze Claude Code trace files using the built-in workflow-review engine.

Outputs a structured summary per session without loading full transcripts
into the LLM context.  Designed as a fallback when official /insights data
is unavailable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

# Ensure the skill's engine is importable regardless of cwd.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from engine.analyzer import summarize_session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Claude Code traces via built-in engine.")
    parser.add_argument("path", type=Path, help="Path to a trace .jsonl or a projects directory")
    parser.add_argument("--days", type=int, default=30, help="Only include traces modified within N days")
    parser.add_argument("--limit", type=int, default=50, help="Max sessions to summarize")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of human-readable text")
    return parser.parse_args()


def _recent_traces(projects_dir: Path, days: int, limit: int) -> list[Path]:
    cutoff = dt.datetime.now().timestamp() - days * 86400
    paths = [p for p in projects_dir.glob("*/*.jsonl") if p.stat().st_mtime >= cutoff]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[:limit]


def main() -> int:
    args = parse_args()
    path = args.path

    if not path.exists():
        print(f"path_missing: {path}", file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []

    if path.is_file():
        if path.suffix != ".jsonl":
            print(f"not_a_jsonl: {path}", file=sys.stderr)
            return 1
        results.append(summarize_session(str(path)))
    else:
        for trace_path in _recent_traces(path, args.days, args.limit):
            try:
                results.append(summarize_session(str(trace_path)))
            except Exception as e:
                print(f"error: {trace_path}: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    print(f"trace_sessions_last_{args.days}_days: {len(results)}")
    print()

    for r in results:
        date = "?"
        if r.get("started_at"):
            date = r["started_at"][:10]
        print(f"session: {r.get('session_id') or 'unknown'} | {date} | {r.get('cwd')}")
        print(f"  turns: {r.get('turns')} (human {r.get('human_turns')}, system {r.get('system_turns')})")
        print(f"  tools: {r.get('tools')} | cost: {r.get('cost'):,} | cache_read: {r.get('cache_read'):,}")
        if r.get("heavy_turns"):
            print(f"  heavy_turns: {r['heavy_turns']}")
        if r.get("tool_counts"):
            print("  tool_counts:", dict(list(r["tool_counts"].items())[:8]))
        if r.get("hot_files"):
            print("  hot_files:", dict(list(r["hot_files"].items())[:6]))
        if r.get("human_prompts"):
            print("  prompts:")
            for p in r["human_prompts"][:3]:
                print(f"    - {p[:100]}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
