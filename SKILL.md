---
name: workflow-review
description: |
  工作流复盘与重复模式发现 Skill。当用户询问最近有哪些重复工作流、哪些事情可以打包成 skill/command/hook/script、哪些手动流程值得自动化、或想从 Claude Code 使用历史里发现模式时使用。

  触发后默认回顾最近 30 天；如果可用历史少于 30 天，就回顾全部可用历史。第一产出物必须是候选清单，而不是直接创建资产。候选清单至少包含：重复工作流、支持证据和日期、频率/置信度、评分、推荐落点（skill / command / subagent-workflow / hook / script / external-automation / extend-existing / skip）和一句话理由。

  只有用户确认候选清单后，才创建或扩展 1-2 个高置信度、范围窄、可验证的最小资产。禁止读取完整会话记录；使用本 skill 的 scripts/ 目录提取统计摘要。官方 usage-data/facets 优先；没有 facets 时，优先使用本 skill 内置的 trace 引擎直接分析本地 `~/.claude/projects/**/*.jsonl`，只有用户明确选择更弱降级时才使用 projects / 工作区 git / 可选 legacy transcripts / 用户口述。
metadata:
  short-description: 分析工作历史，发现重复模式，打包成最小有用技能
---

# Workflow Review

回答一个问题：最近哪些重复手动流程值得被打包，以及应该打包成什么形式。

## 硬性边界

- 先交付候选清单；没有用户明确确认，不创建、不改写、不安装任何资产。
- 禁止读取完整 JSONL/transcript 到上下文；只使用摘要、统计、截断首条用户提示和聚合字段。
- 外部对话源只能作为旁证，不能替代 Claude Code 本地数据链路。
- 每次最多创建或扩展 1-2 个 narrow、practical、可验证的最小资产。
- 如果候选与用户画像或实际工作明显不匹配，直接标注 `skip`，不要为了产出而产出。
- 不做全 `$HOME` 内容扫描。降级时只扫 Claude projects 数据、当前工作区 git，或用户明确授权的路径。

## 推荐落点

- `skill`：有明确触发场景、判断规则和可复用步骤。
- `command`：用户手动触发、单次执行、主要靠提示词编排。
- `subagent-workflow`：适合拆成搜索、比较、汇总等子任务。
- `hook`：适合 Claude Code 固定事件点自动触发。
- `script`：输入输出稳定、主要靠 shell/python/node 确定性完成。
- `external-automation`：定时、轮询、跨系统编排，或必须脱离 Claude Code 会话独立运行。
- `extend-existing`：已有资产覆盖 70% 以上，只需要扩展。
- `skip`：证据不足、复发概率低、投入产出比差，或不适合固化。

`automation` 不是最终落点名。必须继续分清是 `hook`、`script` 还是 `external-automation`。

## 数据收集

将 `SKILL_ROOT` 设为包含本 `SKILL.md` 的目录。若不确定，先在常见安装目录中定位 `workflow-review/scripts/detect-claude-data.sh`。

```bash
SKILL_ROOT="${WORKFLOW_REVIEW_SKILL_ROOT:-$HOME/.claude/skills/workflow-review}"
if [ ! -x "$SKILL_ROOT/scripts/detect-claude-data.sh" ]; then
  SKILL_ROOT="$(find "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.codex/skills" -path "*/workflow-review/scripts/detect-claude-data.sh" -type f 2>/dev/null | head -1 | sed 's#/scripts/detect-claude-data.sh##')"
fi
```

### 1. 探测数据源

```bash
"$SKILL_ROOT/scripts/detect-claude-data.sh"
```

记录输出里的 `FACETS_DIR`、`SESSION_META_DIR`、`REPORT_PATH`、`PROJECTS_DIR`、`TRANSCRIPTS_DIR` 及各自 count。不要写死 `$HOME/.claude`。如果后续 Bash 调用不保留变量，就把命令里的 `$FACETS_DIR` 等直接替换成探测输出的实际路径。

同时确认内置 trace 引擎可用：

```bash
test -d "$SKILL_ROOT/scripts/engine" && echo "engine_ok"
```

### 2. 主流程：facets 存在

当 `FACETS_COUNT > 0`：

```bash
python3 "$SKILL_ROOT/scripts/summarize-facets.py" "$FACETS_DIR" --days 30
python3 "$SKILL_ROOT/scripts/summarize-session-meta.py" "$SESSION_META_DIR" --days 30
```

若 `report.html` 存在且 7 天内更新，可读取其文字段落辅助验证；忽略 `<style>` 和 `<script>`。若超过 7 天，标注已过期但可以继续使用 facets/session-meta。

### 3. 无 facets：三条路径

当 `FACETS_COUNT = 0`，必须先暂停并询问：

> 未检测到官方 `/insights` 结构化数据。workflow-review 的高质量复盘优先依赖 usage-data/facets、session-meta 和 report.html。
>
> 当前数据状态：
> - facets: {FACETS_COUNT}
> - session-meta: {SESSION_META_COUNT}
> - report.html: {REPORT_EXISTS} ({REPORT_DATE})
> - projects: {PROJECTS_COUNT}
> - transcripts: {TRANSCRIPTS_COUNT}
> - 内置 trace 引擎: 可用（基于 `~/.claude/projects/**/*.jsonl`）
>
> 你有三个选项：
> a. 推荐：先在 Claude Code 里运行 `/insights`，完成后重新调用 workflow-review。语义评估最完整。
> b. 本地引擎：回复“用本地引擎”，我直接分析你的原始 trace 文件，不依赖官方功能。客观执行模式强，但缺少满意度/目标等语义判断。
> c. 弱降级：回复“继续降级审计”，我改用 projects 轻量扫描 + 当前工作区 git + 可选 legacy transcripts，结果最弱。

如果用户没有明确选择，不要继续。

### 4. 本地 trace 引擎流程

只有用户明确回复“用本地引擎”或等价确认后，才运行：

```bash
# 单会话钻取（可选）
python3 "$SKILL_ROOT/scripts/analyze-traces.py" "$PROJECTS_DIR" --days 30 --limit 50

# 跨会话聚合，生成重复模式候选
python3 "$SKILL_ROOT/scripts/aggregate-patterns.py" "$PROJECTS_DIR" --days 30 --top-n 10
```

内置引擎输出包含：

- 每个会话的回合数、工具数、cost、缓存重读、heavy turns
- 高频工具（Bash / Read / Edit / MCP 等）
- 高频文件（被反复 Read/Edit 的文件）
- 常用 Skill / Sub-agent / MCP server
- 真实 human prompt 的聚类（重复请求模式）
- 按项目聚合的活跃度

使用这些输出生成候选清单时，必须明确标注数据源为 `trace-engine`，不要把它包装成 insights 数据。

### 5. 降级流程：用户确认后

只有用户明确回复“继续降级审计”或等价确认后，才运行：

```bash
python3 "$SKILL_ROOT/scripts/summarize-projects.py" "$PROJECTS_DIR" --days 30
```

然后只在当前工作区或用户授权的仓库中补充 git 证据：

```bash
git log --oneline --since="30 days ago" 2>/dev/null | head -30
```

legacy transcripts 只在存在且用户接受弱证据时使用，且必须标注为 `legacy-transcripts` 旁证。

## 评分矩阵

对每个候选给 0-3 分，并附一句判断依据：

| 维度 | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| 频率 | 一次性 | 可能复发 | 至少 2 次 | 高频或持续出现 |
| 摩擦 | 几乎无成本 | 有轻微重复 | 明显耗时/易错 | 高耗时、高风险或上下文重 |
| 可重复性 | 输入输出不稳定 | 部分稳定 | 多数步骤稳定 | 触发、步骤、停止条件清楚 |
| 价值 | 价值不明 | 小幅省时 | 明显提速/提质 | 能稳定提升速度、质量或可靠性 |
| 覆盖缺口 | 已被覆盖 | 只需提醒 | 需扩展已有资产 | 需要新资产 |

推荐动作：
- 总分 11-15：优先候选，可创建或扩展。
- 总分 7-10：保留候选，但通常先补证据或轻量化处理。
- 总分 0-6：默认 `skip`。
- 任一候选若没有稳定输入/输出，即使总分高也不要创建资产。

## 候选清单格式

第一产出物必须是候选清单。推荐用表格：

| 候选 | 证据和日期 | 数据源 | 频率/置信度 | 评分 | 推荐落点 | 一句话理由 |
| --- | --- | --- | --- | --- | --- | --- |

候选清单后必须暂停，并问：

> 以上是候选清单。你觉得哪些需要补充、删减，或优先级不对？确认后我再创建或扩展 1-2 个最小资产。

## 创建阶段

用户确认后才进入创建阶段：

1. 先检查是否已有相邻 skill、command、hook、script 或模板覆盖 70% 以上。
2. 如果已有覆盖，优先 `extend-existing`，不要新建重复资产。
3. 新资产必须有窄触发、窄输入、明确输出和验证方式。
4. 创建后汇报：创建/扩展了什么、验证方式、刻意跳过了什么、哪些证据仍不足。

## 正常工作标志

- 能正确检测 Claude Code 数据目录和各数据源状态。
- facets 存在时走主流程；facets 缺失时提供三条清晰路径（`/insights` / 本地 trace 引擎 / 弱降级），不擅自替用户选择。
- 本地 trace 引擎输出必须标注数据源为 `trace-engine`，不把弱证据包装成 insights 结论。
- 输出候选清单时标注数据源、日期、评分、置信度和推荐落点。
- 清单后暂停，不自动创建任何东西。
- 降级结果明确标注证据更弱，不把弱证据包装成确定结论。
