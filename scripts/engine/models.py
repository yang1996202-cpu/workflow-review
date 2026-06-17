"""Lightweight data models for Claude Code trace analysis.

Self-contained; does not depend on any external project.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Tokens:
    in_: int = 0
    out: int = 0
    cacheRead: int = 0
    cacheCreate: int = 0

    def add(self, other: "Tokens") -> "Tokens":
        return Tokens(
            in_=self.in_ + other.in_,
            out=self.out + other.out,
            cacheRead=self.cacheRead + other.cacheRead,
            cacheCreate=self.cacheCreate + other.cacheCreate,
        )

    def cost(self) -> int:
        """Anthropic-style cost-weighted token count.

        cache read ~= 1x, fresh input ~= 3x, output ~= 10x, cache write ~= 3x
        This is only for ranking/heavy detection; not a dollar estimate.
        """
        return self.cacheRead + 3 * self.in_ + 10 * self.out + 3 * self.cacheCreate

    def to_dict(self) -> dict[str, int]:
        return {
            "in": self.in_,
            "out": self.out,
            "cacheRead": self.cacheRead,
            "cacheCreate": self.cacheCreate,
            "cost": self.cost(),
        }


@dataclass
class ToolCall:
    name: str = ""
    input: Any = None
    summary: str = ""
    id: Optional[str] = None
    result_text: str = ""
    ts: Optional[str] = None
    mcp: Optional[dict[str, str]] = None
    provenance: str = "direct"  # 'direct' | 'indirect'
    source_tool: Optional[str] = None
    flow_value: Optional[str] = None
    errored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input": self.input,
            "summary": self.summary,
            "id": self.id,
            "resultText": self.result_text,
            "ts": self.ts,
            "mcp": self.mcp,
            "provenance": self.provenance,
            "sourceTool": self.source_tool,
            "flowValue": self.flow_value,
            "errored": self.errored,
        }


@dataclass
class Turn:
    i: int = 0
    prompt: str = ""
    origin: str = "human"  # 'human' | 'system'
    ts: Optional[str] = None
    tools: list[ToolCall] = field(default_factory=list)
    tokens: Tokens = field(default_factory=Tokens)
    reqs: int = 0
    reply: str = ""
    ctx_peak: int = 0
    ctx_start: int = 0
    ctx_end: int = 0
    heavy: bool = False
    over_budget: bool = False
    guide: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "i": self.i,
            "prompt": self.prompt,
            "origin": self.origin,
            "ts": self.ts,
            "tools": [t.to_dict() for t in self.tools],
            "tokens": self.tokens.to_dict(),
            "reqs": self.reqs,
            "reply": self.reply,
            "ctxPeak": self.ctx_peak,
            "ctxStart": self.ctx_start,
            "ctxEnd": self.ctx_end,
            "heavy": self.heavy,
            "overBudget": self.over_budget,
            "guide": self.guide,
        }


@dataclass
class Event:
    id: str = ""
    turn: int = 0
    role: str = ""  # 'user' | 'assistant'
    kind: str = ""  # 'prompt' | 'tool_use' | 'tool_result' | 'text'
    ts: Optional[str] = None
    tool: Optional[str] = None
    input: Any = None
    mcp: Optional[dict[str, str]] = None
    result_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "turn": self.turn,
            "role": self.role,
            "kind": self.kind,
            "ts": self.ts,
            "tool": self.tool,
            "input": self.input,
            "mcp": self.mcp,
            "resultText": self.result_text,
        }
