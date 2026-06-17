#!/usr/bin/env bash
set -euo pipefail

# Locate this skill's root (where SKILL.md lives).
SKILL_ROOT="${WORKFLOW_REVIEW_SKILL_ROOT:-}"
if [[ -z "$SKILL_ROOT" || ! -f "$SKILL_ROOT/SKILL.md" ]]; then
  SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
if [[ ! -f "$SKILL_ROOT/SKILL.md" ]]; then
  # Fallback: search common skill directories.
  SKILL_ROOT="$(find "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.codex/skills" -path "*/workflow-review/SKILL.md" -type f 2>/dev/null | head -1 | xargs dirname 2>/dev/null || true)"
fi

engine_available="no"
if [[ -d "$SKILL_ROOT/scripts/engine" && -f "$SKILL_ROOT/scripts/engine/loader.py" ]]; then
  engine_available="yes"
fi

claude_root="${CLAUDE_DATA_ROOT:-${CLAUDE_CONFIG_DIR:-}}"

if [[ -z "$claude_root" || ! -d "$claude_root" ]]; then
  if [[ -d "$HOME/.claude" ]]; then
    claude_root="$HOME/.claude"
  else
    claude_root="$(find "$HOME" -maxdepth 4 -type d -name .claude 2>/dev/null | head -1 || true)"
  fi
fi

count_files() {
  local dir="$1"
  local pattern="$2"

  if [[ -z "$dir" || ! -d "$dir" ]]; then
    printf '0'
    return
  fi

  find "$dir" -name "$pattern" 2>/dev/null | wc -l | tr -d ' '
}

file_date() {
  local path="$1"

  if [[ -z "$path" || ! -f "$path" ]]; then
    printf ''
    return
  fi

  if stat -f "%Sm" -t "%Y-%m-%d" "$path" >/dev/null 2>&1; then
    stat -f "%Sm" -t "%Y-%m-%d" "$path"
  else
    stat -c "%y" "$path" 2>/dev/null | cut -d' ' -f1
  fi
}

facets_dir=""
session_meta_dir=""
report_path=""
projects_dir=""
transcripts_dir=""

if [[ -n "$claude_root" && -d "$claude_root" ]]; then
  [[ -d "$claude_root/usage-data/facets" ]] && facets_dir="$claude_root/usage-data/facets"
  [[ -d "$claude_root/usage-data/session-meta" ]] && session_meta_dir="$claude_root/usage-data/session-meta"
  [[ -f "$claude_root/usage-data/report.html" ]] && report_path="$claude_root/usage-data/report.html"
  [[ -d "$claude_root/projects" ]] && projects_dir="$claude_root/projects"
  [[ -d "$claude_root/transcripts" ]] && transcripts_dir="$claude_root/transcripts"
fi

report_exists="no"
[[ -n "$report_path" ]] && report_exists="yes"

printf 'SKILL_ROOT=%s\n' "$SKILL_ROOT"
printf 'ENGINE_AVAILABLE=%s\n' "$engine_available"
printf 'CLAUDE_ROOT=%s\n' "$claude_root"
printf 'FACETS_DIR=%s\n' "$facets_dir"
printf 'SESSION_META_DIR=%s\n' "$session_meta_dir"
printf 'REPORT_PATH=%s\n' "$report_path"
printf 'PROJECTS_DIR=%s\n' "$projects_dir"
printf 'TRANSCRIPTS_DIR=%s\n' "$transcripts_dir"
printf 'FACETS_COUNT=%s\n' "$(count_files "$facets_dir" "*.json")"
printf 'SESSION_META_COUNT=%s\n' "$(count_files "$session_meta_dir" "*.json")"
printf 'REPORT_EXISTS=%s\n' "$report_exists"
printf 'REPORT_DATE=%s\n' "$(file_date "$report_path")"
printf 'PROJECTS_COUNT=%s\n' "$(count_files "$projects_dir" "*.jsonl")"
printf 'TRANSCRIPTS_COUNT=%s\n' "$(count_files "$transcripts_dir" "ses_*.jsonl")"
