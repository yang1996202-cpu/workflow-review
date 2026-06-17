"""Lightweight analyzer over normalized Claude Code turns.

Adds deterministic signals without any model:
  - session token/cost rollups
  - heavy / over-budget turn detection
  - simple provenance (value-flow) detection
  - per-turn and per-session tool/file statistics
  - entity extraction (skills, sub-agents, mcp servers)
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Optional

from .models import ToolCall, Turn

# Cost floor for over-budget marking.
_OVER_BUDGET_FLOOR = 500_000

# A workflow script declares spawned agents with label: "..." and the run with
# name: "...".  Parsing these from the inline script is deterministic.
_WF_LABEL_RE = re.compile(r"""label:\s*[`'"]([^`'"]+)[`'"]""")
_WF_NAME_RE = re.compile(r"""name:\s*['"]([^'"]+)['"]""")


def _tool_name(tc: dict[str, Any]) -> str:
    return str(tc.get("name", "") or "")


def _workflow_agents(inp: dict[str, Any]) -> tuple[str, list[str]]:
    script = str(inp.get("script") or "")
    if not script:
        return "", []
    nm = _WF_NAME_RE.search(script)
    name = nm.group(1).strip() if nm else ""
    labels: list[str] = []
    for lab in _WF_LABEL_RE.findall(script):
        lab = lab.strip()
        if lab and lab not in labels:
            labels.append(lab)
    return name, labels[:64]


def _mcp_parts(name: str) -> tuple[str, str]:
    rest = name[len("mcp__"):]
    server, sep, tool = rest.partition("__")
    return (server, tool if sep else "")


def _flow_value(tool_input: Any, prior_text: str, min_len: int = 8) -> Optional[str]:
    """Return the longest substring of prior_text that appears in tool_input."""
    if not isinstance(tool_input, dict) or not prior_text:
        return None
    text = json.dumps(tool_input)
    best = ""
    # Greedy: test decreasing chunk lengths of prior_text.
    prior = prior_text
    for length in range(min(len(prior), 120), min_len - 1, -1):
        for start in range(0, len(prior) - length + 1):
            chunk = prior[start:start + length]
            if chunk in text and len(chunk) > len(best):
                best = chunk
                if len(best) >= 80:
                    return best[:120]
        if best:
            return best[:120]
    return best[:120] if best else None


def _mark_provenance(turns: list[Turn]) -> None:
    """Simple provenance: if a tool input contains text from the previous tool
    result, mark it as indirect.  This is intentionally lightweight; it does not
    try to match across multiple turns or large result blocks."""

    for turn in turns:
        prev_result = ""
        direct = 0
        indirect = 0
        for tc in turn.tools:
            flow = _flow_value(tc.input, prev_result)
            if flow:
                tc.provenance = "indirect"
                tc.source_tool = turn.tools[turn.tools.index(tc) - 1].name if turn.tools.index(tc) > 0 else None
                tc.flow_value = flow
                indirect += 1
            else:
                tc.provenance = "direct"
                direct += 1
            prev_result = tc.result_text or ""
        turn.direct = direct
        turn.indirect = indirect


def analyze_turns(turns: list[Turn], session: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic analysis over loaded turns."""
    _mark_provenance(turns)

    total_tokens = sum((t.tokens.cost() for t in turns), 0)
    max_cost = max((t.tokens.cost() for t in turns), default=1)

    # Mark heavy turns: top 3 by cost, but only if they actually have cost.
    sorted_by_cost = sorted(
        [t for t in turns if t.tokens.cost() > 0],
        key=lambda t: t.tokens.cost(),
        reverse=True,
    )
    heavy_set = {t.i for t in sorted_by_cost[:3]}
    over_budget_set = {t.i for t in turns if t.tokens.cost() >= _OVER_BUDGET_FLOOR}

    for t in turns:
        t.heavy = t.i in heavy_set
        t.over_budget = t.i in over_budget_set

    total_tools = sum(len(t.tools) for t in turns)
    total_direct = sum(t.direct for t in turns)
    total_indirect = sum(t.indirect for t in turns)

    # Session-level token rollup.
    sess_tokens = {
        "in": sum(t.tokens.in_ for t in turns),
        "out": sum(t.tokens.out for t in turns),
        "cacheRead": sum(t.tokens.cacheRead for t in turns),
        "cacheCreate": sum(t.tokens.cacheCreate for t in turns),
        "cost": total_tokens,
    }

    session_out = dict(session)
    session_out.update(
        {
            "turns": len(turns),
            "humanTurns": sum(1 for t in turns if t.origin == "human"),
            "systemTurns": sum(1 for t in turns if t.origin == "system"),
            "tools": total_tools,
            "direct": total_direct,
            "indirect": total_indirect,
            "tokens": sess_tokens,
            "heavyTurns": sorted(heavy_set),
            "overBudgetTurns": sorted(over_budget_set),
            "cacheReadOverOut": round(sess_tokens["cacheRead"] / max(1, sess_tokens["out"])),
        }
    )

    return {"session": session_out, "turns": turns}


def analyze_trace_path(path: str) -> dict[str, Any]:
    from .loader import load_trace

    loaded = load_trace(path)
    return analyze_turns(loaded["turns"], loaded["session"])


def extract_entities(turns: list[Turn]) -> dict[str, list[dict[str, Any]]]:
    """Return {skills, subAgents, mcpServers} with turn-level traceback."""
    skills: dict[str, dict[str, Any]] = {}
    subagents: dict[str, dict[str, Any]] = {}
    mcp: dict[str, dict[str, Any]] = {}

    def bump(table: dict, key: str, ti: int) -> dict:
        row = table.setdefault(key, {"name": key, "count": 0, "turns": set()})
        row["count"] += 1
        row["turns"].add(ti)
        return row

    for t in turns:
        ti = t.i
        for tc in t.tools:
            name = tc.name
            inp = tc.input if isinstance(tc.input, dict) else {}

            if name == "Skill":
                sk = str(inp.get("skill") or inp.get("command") or "skill").strip() or "skill"
                bump(skills, sk, ti)

            elif name in ("Agent", "Task"):
                st = str(
                    inp.get("subagent_type") or ("general-purpose" if name == "Agent" else "task")
                ).strip() or "agent"
                row = bump(subagents, st, ti)
                row["via"] = name.lower()
                desc = (inp.get("description") or "").strip()
                samples = row.setdefault("samples", [])
                if desc and desc not in samples and len(samples) < 4:
                    samples.append(desc)

            elif name == "Workflow":
                wf_name, agent_labels = _workflow_agents(inp)
                for lab in agent_labels:
                    row = bump(subagents, lab, ti)
                    row["via"] = "workflow"
                    if wf_name:
                        row["workflow"] = wf_name
                if not agent_labels:
                    row = bump(subagents, wf_name or "workflow", ti)
                    row["via"] = "workflow"

            elif name.startswith("mcp__"):
                server, tool = _mcp_parts(name)
                server = server or "mcp"
                row = mcp.setdefault(server, {"name": server, "count": 0, "turns": set(), "tools": set()})
                row["count"] += 1
                row["turns"].add(ti)
                if tool:
                    row["tools"].add(tool)

    def finalize(table: dict, set_keys: tuple = ("turns",)) -> list[dict]:
        out = []
        for row in table.values():
            r = dict(row)
            for k in set_keys:
                if isinstance(r.get(k), set):
                    r[k] = sorted(r[k])
            out.append(r)
        out.sort(key=lambda x: (-x["count"], x["name"]))
        return out

    return {
        "skills": finalize(skills),
        "subAgents": finalize(subagents),
        "mcpServers": finalize(mcp, set_keys=("turns", "tools")),
    }


def extract_tool_stats(turns: list[Turn]) -> dict[str, Any]:
    """Per-session tool usage statistics."""
    tool_counts = Counter()
    file_counts: Counter[str] = Counter()
    bash_commands: list[str] = []

    for t in turns:
        for tc in t.tools:
            tool_counts[tc.name] += 1
            if tc.name in ("Read", "Edit", "Write"):
                inp = tc.input if isinstance(tc.input, dict) else {}
                fp = inp.get("file_path") or inp.get("path")
                if fp:
                    file_counts[str(fp)] += 1
            if tc.name == "Bash":
                inp = tc.input if isinstance(tc.input, dict) else {}
                cmd = str(inp.get("command", "") or "")
                if cmd:
                    bash_commands.append(cmd[:120])

    return {
        "tool_counts": dict(tool_counts.most_common(20)),
        "hot_files": dict(file_counts.most_common(20)),
        "bash_commands": bash_commands[:30],
    }


_NOISE_PREFIXES = (
    "this session is being continued",
    "caveat: the messages below were generated",
)
_NOISE_SUBSTRINGS = ("thought for", "⏺", "successfully loaded")


def _is_noise(prompt: str) -> bool:
    low = prompt.lower()
    if len(prompt.strip()) < 10:
        return True
    if low.startswith(_NOISE_PREFIXES):
        return True
    for sub in _NOISE_SUBSTRINGS:
        if sub in low:
            return True
    return False


def extract_human_prompts(turns: list[Turn], limit: int = 20) -> list[str]:
    """Real human prompts, skipping system/task notifications and noise."""
    out = []
    for t in turns:
        if t.origin != "human":
            continue
        p = t.prompt.strip()
        if p.startswith("<"):
            continue
        if _is_noise(p):
            continue
        out.append(p[:200])
        if len(out) >= limit:
            break
    return out


def summarize_session(path: str) -> dict[str, Any]:
    """High-level summary of one session, suitable for workflow-review."""
    result = analyze_trace_path(path)
    turns = result["turns"]
    session = result["session"]
    entities = extract_entities(turns)
    stats = extract_tool_stats(turns)

    return {
        "session_id": session.get("sessionId"),
        "cwd": session.get("cwd"),
        "path": path,
        "model": session.get("model"),
        "started_at": session.get("startedAt"),
        "ended_at": session.get("endedAt"),
        "turns": session.get("turns"),
        "human_turns": session.get("humanTurns"),
        "system_turns": session.get("systemTurns"),
        "tools": session.get("tools"),
        "cost": session.get("tokens", {}).get("cost"),
        "cache_read": session.get("tokens", {}).get("cacheRead"),
        "output_tokens": session.get("tokens", {}).get("out"),
        "heavy_turns": session.get("heavyTurns"),
        "over_budget_turns": session.get("overBudgetTurns"),
        "indirect_tools": session.get("indirect"),
        "direct_tools": session.get("direct"),
        "tool_counts": stats["tool_counts"],
        "hot_files": stats["hot_files"],
        "bash_commands": stats["bash_commands"],
        "entities": entities,
        "human_prompts": extract_human_prompts(turns),
    }
