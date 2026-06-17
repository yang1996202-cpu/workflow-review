"""Self-contained trace analysis engine for workflow-review.

Mirrors a small subset of the structural parsing and deterministic analysis
used by Her, but with no external dependencies beyond the Python stdlib.
"""
from .analyzer import analyze_trace_path, summarize_session
from .loader import load_trace

__all__ = ["load_trace", "analyze_trace_path", "summarize_session"]
