"""Minimal Claude Code JSONL trace loader.

Parses a raw trace file into a normalized structure:
  {'events': [...], 'turns': [...], 'session': {...}}

This is a self-contained, simplified re-implementation of the structural
parsing rules used by Her. It intentionally avoids heavy signal detection
(provenance, loops, heavy turns) — that happens in analyzer.py.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from .models import Event, Tokens, ToolCall, Turn

# Rows that start with these are slash-command scaffolding, not real prompts.
_NON_PROMPT_PREFIXES = ("<command-name", "<command-message", "<local-command")
_SYSTEM_PREFIX = "<task-notification>"


def _is_turn_boundary(row: dict[str, Any]) -> tuple[bool, str]:
    if row.get("type") != "user":
        return False, ""
    if row.get("isMeta"):
        return False, ""
    content = row.get("message", {}).get("content")
    if not isinstance(content, str):
        return False, ""
    s = content.strip()
    if not s:
        return False, ""
    if s.startswith(_NON_PROMPT_PREFIXES):
        return False, ""
    return True, s


def _origin(prompt: str) -> str:
    return "system" if prompt.startswith(_SYSTEM_PREFIX) else "human"


def _is_sidechain(row: dict[str, Any]) -> bool:
    return bool(row.get("isSidechain"))


def _basename(path: Any) -> str:
    if not isinstance(path, str) or not path:
        return str(path)
    return os.path.basename(path.rstrip("/")) or path


def _mcp_of(name: str) -> Optional[dict[str, str]]:
    if isinstance(name, str) and name.startswith("mcp__"):
        rest = name[len("mcp__"):]
        server, sep, tool = rest.partition("__")
        if sep:
            return {"server": server, "tool": tool}
        return {"server": rest, "tool": ""}
    return None


def _summary(name: str, inp: Any, mcp: Optional[dict[str, str]]) -> str:
    inp = inp if isinstance(inp, dict) else {}
    if name == "Read":
        return f"Read {_basename(inp.get('file_path'))}"
    if name in ("Edit", "Write"):
        return f"Edit {_basename(inp.get('file_path'))}"
    if name == "Bash":
        cmd = str(inp.get("command", "") or "")
        return f"Bash: {cmd[:60]}"
    if name in ("Grep", "Glob"):
        return f"{name} {inp.get('pattern', '')}"
    if name == "Task":
        desc = str(inp.get("description", "") or "")
        return f"Task: {desc[:60]}"
    if mcp is not None:
        return f"{mcp.get('server', '')}:{mcp.get('tool', '')}"
    return name


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _visible_text(block: dict[str, Any]) -> Optional[str]:
    if block.get("type") == "text":
        t = block.get("text")
        if isinstance(t, str):
            return t
    return None


def load_trace(path: str) -> dict[str, Any]:
    """Parse a Claude Code session .jsonl into normalized events/turns/session."""
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Session metadata from the first content row that carries it.
    session: dict[str, Any] = {
        "cwd": None,
        "sessionId": None,
        "gitBranch": None,
        "version": None,
        "startedAt": None,
        "endedAt": None,
        "model": None,
    }
    for r in rows:
        if r.get("type") in ("user", "assistant"):
            session["cwd"] = r.get("cwd")
            session["sessionId"] = r.get("sessionId")
            session["gitBranch"] = r.get("gitBranch")
            session["version"] = r.get("version")
            break

    # Modal assistant model.
    models: dict[str, int] = {}
    for r in rows:
        if r.get("type") == "assistant":
            m = (r.get("message", {}) or {}).get("model")
            if m:
                models[m] = models.get(m, 0) + 1
    if models:
        session["model"] = max(models, key=models.get)

    # Session span from any row timestamp.
    for r in rows:
        ts = r.get("timestamp")
        if ts:
            if session["startedAt"] is None:
                session["startedAt"] = ts
            session["endedAt"] = ts

    # Index tool_results by tool_use_id.
    result_text_by_id: dict[str, str] = {}
    for r in rows:
        if r.get("type") != "user":
            continue
        content = r.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tuid = b.get("tool_use_id")
                if tuid is not None:
                    result_text_by_id[tuid] = _tool_result_text(b)

    turns: list[Turn] = []
    events: list[Event] = []
    cur: Optional[Turn] = None
    cur_req_ids: set[str] = set()
    reply_parts: list[str] = []

    def _finalize(turn: Optional[Turn]) -> None:
        if turn is None:
            return
        turn.reqs = len(cur_req_ids)
        turn.reply = "\n".join(p for p in reply_parts if p).strip()

    for idx, r in enumerate(rows):
        rtype = r.get("type")
        sidechain = _is_sidechain(r)
        is_boundary, prompt = (False, "") if sidechain else _is_turn_boundary(r)

        if is_boundary:
            _finalize(cur)
            cur_req_ids = set()
            reply_parts = []
            cur = Turn(
                i=len(turns),
                prompt=prompt,
                origin=_origin(prompt),
                ts=r.get("timestamp"),
            )
            turns.append(cur)
            events.append(
                Event(
                    id=str(r.get("uuid", f"row{idx}")),
                    turn=cur.i,
                    role="user",
                    kind="prompt",
                    ts=r.get("timestamp"),
                    result_text=prompt,
                )
            )
            continue

        if cur is None:
            continue

        turn_i = cur.i

        if rtype == "assistant":
            msg = r.get("message", {}) or {}
            usage = msg.get("usage", {}) or {}
            req_id = r.get("requestId")
            if req_id is not None:
                cur_req_ids.add(req_id)

            cur.tokens = cur.tokens.add(
                Tokens(
                    in_=usage.get("input_tokens", 0) or 0,
                    out=usage.get("output_tokens", 0) or 0,
                    cacheRead=usage.get("cache_read_input_tokens", 0) or 0,
                    cacheCreate=usage.get("cache_creation_input_tokens", 0) or 0,
                )
            )

            if not sidechain:
                occ = (
                    (usage.get("input_tokens", 0) or 0)
                    + (usage.get("cache_read_input_tokens", 0) or 0)
                    + (usage.get("cache_creation_input_tokens", 0) or 0)
                )
                if occ:
                    if not cur.ctx_start:
                        cur.ctx_start = occ
                    cur.ctx_peak = max(cur.ctx_peak, occ)
                    cur.ctx_end = occ

            for b in msg.get("content", []) or []:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "tool_use":
                    name = b.get("name", "")
                    inp = b.get("input")
                    mcp = _mcp_of(name)
                    tuid = b.get("id")
                    tc = ToolCall(
                        name=name,
                        input=inp,
                        summary=_summary(name, inp, mcp),
                        id=tuid,
                        result_text=result_text_by_id.get(tuid, ""),
                        ts=r.get("timestamp"),
                        mcp=mcp,
                    )
                    cur.tools.append(tc)
                    events.append(
                        Event(
                            id=str(tuid) if tuid is not None else f"row{idx}-tooluse",
                            turn=turn_i,
                            role="assistant",
                            kind="tool_use",
                            ts=r.get("timestamp"),
                            tool=name,
                            input=inp,
                            mcp=mcp,
                        )
                    )
                elif btype == "text":
                    vis = _visible_text(b)
                    if vis is not None:
                        reply_parts.append(vis)
                        events.append(
                            Event(
                                id=str(r.get("uuid", f"row{idx}")) + "-text",
                                turn=turn_i,
                                role="assistant",
                                kind="text",
                                ts=r.get("timestamp"),
                                result_text=vis,
                            )
                        )

        elif rtype == "user":
            content = r.get("message", {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tuid = b.get("tool_use_id")
                        events.append(
                            Event(
                                id=(str(tuid) + "-result") if tuid is not None else f"row{idx}-result",
                                turn=turn_i,
                                role="user",
                                kind="tool_result",
                                ts=r.get("timestamp"),
                                result_text=_tool_result_text(b),
                            )
                        )

    _finalize(cur)
    return {"events": events, "turns": turns, "session": session}
