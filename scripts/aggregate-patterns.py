#!/usr/bin/env python3
"""Aggregate trace summaries across sessions to surface recurring patterns.

This is the cross-session layer that turns per-session summaries from
analyze-traces.py into workflow-review candidates.  It runs entirely
locally and needs no model.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from engine.analyzer import summarize_session


# Stopwords for simple prompt clustering.
_STOP = {
    "the", "and", "you", "are", "this", "that", "for", "with", "have", "has",
    "can", "could", "would", "should", "will", "what", "how", "why", "where",
    "when", "who", "which", "there", "here", "then", "than", "they", "them",
    "their", "them", "into", "from", "about", "over", "under", "just", "only",
    "also", "some", "any", "all", "not", "but", "or", "if", "so", "do", "does",
    "did", "done", "a", "an", "is", "it", "its", "to", "of", "in", "on", "at",
    "我", "你", "他", "她", "它", "的", "了", "在", "是", "和", "就", "都",
    "要", "会", "能", "可以", "怎么", "什么", "为什么", "哪里", "这个", "那个",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate trace patterns across sessions.")
    parser.add_argument("projects_dir", type=Path, help="Path to ~/.claude/projects")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=100, help="Max sessions to load")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top-n", type=int, default=10, help="Top N patterns to emit")
    return parser.parse_args()


def _recent_traces(projects_dir: Path, days: int, limit: int) -> list[Path]:
    cutoff = dt.datetime.now().timestamp() - days * 86400
    paths = [p for p in projects_dir.glob("*/*.jsonl") if p.stat().st_mtime >= cutoff]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[:limit]


def _tokenize(text: str) -> list[str]:
    """Extract alphanumeric tokens, dropping stopwords and short tokens."""
    tokens = re.findall(r"[a-zA-Z0-9一-鿿]+", text.lower())
    return [t for t in tokens if len(t) >= 2 and t not in _STOP]


def _prompt_signature(prompt: str) -> frozenset[str]:
    return frozenset(_tokenize(prompt))


def _cluster_prompts(prompts: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """Group similar human prompts by normalized token overlap.

    prompts: list of (project, session_id, prompt_text)
    Returns clusters sorted by size.

    Two prompts cluster only if their shared keywords cover at least
    SIMILARITY_THRESHOLD of the shorter prompt's keywords. This prevents
    long prompts from being grouped just because they share a few common
    words (e.g. "skill", "项目").
    """
    SIMILARITY_THRESHOLD = 0.30
    clusters: list[dict[str, Any]] = []

    for project, sid, prompt in prompts:
        sig = _prompt_signature(prompt)
        if not sig:
            continue
        best = None
        best_score = 0.0
        for c in clusters:
            denom = min(len(sig), len(c["sig"]))
            if denom == 0:
                continue
            score = len(sig & c["sig"]) / denom
            if score > best_score:
                best = c
                best_score = score
        if best and best_score >= SIMILARITY_THRESHOLD:
            best["prompts"].append((project, sid, prompt))
            best["projects"].add(project)
            # Do NOT merge signatures: keep the cluster representative fixed
            # to avoid a snowball effect where clusters grow indefinitely.
        else:
            clusters.append({
                "sig": sig,
                "prompts": [(project, sid, prompt)],
                "projects": {project},
            })

    for c in clusters:
        c["size"] = len(c["prompts"])
        c["projects"] = sorted(c["projects"])

    return sorted(clusters, key=lambda c: -c["size"])


def main() -> int:
    args = parse_args()

    if not args.projects_dir.is_dir():
        print(f"projects_dir_missing: {args.projects_dir}", file=sys.stderr)
        return 1

    trace_paths = _recent_traces(args.projects_dir, args.days, args.limit)
    summaries: list[dict[str, Any]] = []
    for path in trace_paths:
        try:
            summaries.append(summarize_session(str(path)))
        except Exception as e:
            print(f"error: {path}: {e}", file=sys.stderr)

    # Per-project aggregation.
    by_project: dict[str, dict[str, Any]] = {}
    all_prompts: list[tuple[str, str, str]] = []
    all_tool_counts: Counter[str] = Counter()
    all_file_counts: Counter[str] = Counter()
    all_entity_skills: Counter[str] = Counter()
    all_entity_agents: Counter[str] = Counter()
    all_entity_mcp: Counter[str] = Counter()

    for s in summaries:
        cwd = s.get("cwd") or s.get("path", "").split("/")[-2]
        if cwd not in by_project:
            by_project[cwd] = {
                "sessions": 0,
                "tool_counts": Counter(),
                "file_counts": Counter(),
                "cost": 0,
                "tools": 0,
            }
        by_project[cwd]["sessions"] += 1
        by_project[cwd]["tool_counts"].update(s.get("tool_counts", {}))
        by_project[cwd]["file_counts"].update(s.get("hot_files", {}))
        by_project[cwd]["cost"] += s.get("cost", 0) or 0
        by_project[cwd]["tools"] += s.get("tools", 0) or 0

        all_tool_counts.update(s.get("tool_counts", {}))
        all_file_counts.update(s.get("hot_files", {}))

        for e in s.get("entities", {}).get("skills", []):
            all_entity_skills[e["name"]] += e.get("count", 0)
        for e in s.get("entities", {}).get("subAgents", []):
            all_entity_agents[e["name"]] += e.get("count", 0)
        for e in s.get("entities", {}).get("mcpServers", []):
            all_entity_mcp[e["name"]] += e.get("count", 0)

        for p in s.get("human_prompts", []):
            all_prompts.append((cwd, s.get("session_id") or "?", p))

    prompt_clusters = _cluster_prompts(all_prompts)

    result = {
        "total_sessions": len(summaries),
        "total_projects": len(by_project),
        "global_tool_counts": dict(all_tool_counts.most_common(args.top_n)),
        "global_file_counts": dict(all_file_counts.most_common(args.top_n)),
        "global_entities": {
            "skills": dict(all_entity_skills.most_common(args.top_n)),
            "subAgents": dict(all_entity_agents.most_common(args.top_n)),
            "mcpServers": dict(all_entity_mcp.most_common(args.top_n)),
        },
        "projects": {
            cwd: {
                "sessions": d["sessions"],
                "cost": d["cost"],
                "tools": d["tools"],
                "top_tools": dict(d["tool_counts"].most_common(8)),
                "top_files": dict(d["file_counts"].most_common(8)),
            }
            for cwd, d in sorted(by_project.items(), key=lambda x: -x[1]["cost"])
        },
        "prompt_clusters": [
            {
                "size": c["size"],
                "projects": c["projects"],
                "sample_prompts": [p[2] for p in c["prompts"][:3]],
                "session_ids": list({p[1] for p in c["prompts"]}),
            }
            for c in prompt_clusters[: args.top_n]
        ],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"aggregate_trace_sessions_last_{args.days}_days: {result['total_sessions']}")
    print(f"projects: {result['total_projects']}")
    print()

    print("global_tool_counts:")
    for name, count in all_tool_counts.most_common(args.top_n):
        print(f"- {count} {name}")

    print("\nglobal_hot_files:")
    for name, count in all_file_counts.most_common(args.top_n):
        print(f"- {count} {name}")

    print("\nproject_activity:")
    for cwd, d in sorted(by_project.items(), key=lambda x: -x[1]["cost"])[: args.top_n]:
        print(f"- {cwd}: {d['sessions']} sessions, cost {d['cost']:,}, tools {d['tools']}")

    print("\nprompt_clusters:")
    for c in prompt_clusters[: args.top_n]:
        print(f"- size {c['size']} | projects: {', '.join(c['projects'][:3])}")
        for p in c["prompts"][:2]:
            print(f"    · {p[2][:100]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
