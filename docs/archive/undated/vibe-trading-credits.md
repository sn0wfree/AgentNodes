# vibe-trading 复制来源归属

> 本文档记录从外部项目复制到本仓库的模块来源，**不**对复制文件本身添加任何 header 注释。

## 复制清单（PR4 commit 1, 2026-07-22）

以下 6 个模块来自 [vibe-trading-ai 0.1.11](https://github.com/HKUDS/Vibe-Trading)（HKUDS, MIT License）。

| 目标文件 | 行数 | 原始来源 | 适配说明 |
|---|---|---|---|
| `src/strategy_research/core/agent/tools.py` | 94 | `src/agent/tools.py` | 无内部依赖，原样复制 |
| `src/strategy_research/core/agent/frontmatter.py` | 40 | `src/agent/frontmatter.py` | 无内部依赖，原样复制 |
| `src/strategy_research/core/agent/progress.py` | 184 | `src/agent/progress.py` | 无内部依赖，原样复制 |
| `src/strategy_research/core/agent/trace.py` | 284 | `src/agent/trace.py` | env var 名 `VIBE_TRADING_*` → `STRATEGY_RESEARCH_*` |
| `src/strategy_research/core/agent/redaction.py` | 209 | `src/tools/redaction.py` | 路径注释更新（parents[2] 自动指向 strategy_research 包根）|
| `src/strategy_research/core/memory/persistent.py` | 368 | `src/memory/persistent.py` | `MEMORY_BASE` 改 `~/.quantnodes-research/memory/`；import `from src.agent.frontmatter` → `from ..agent.frontmatter` |
| **合计** | **1179** | | |

## 适配原则

1. **不修改源代码**：复制文件保持原内容（除上述表格所列必要的环境适配）
2. **不加文件头**：复制文件顶部**不**添加 `# Adapted from ...` 注释
3. **归属集中**：所有来源信息集中记录于此文档及 `docs/archive/undated/enhancement.md` 执行日志

## 适配详细

### env var 重命名（`trace.py`）

| 原名 | 新名 |
|---|---|
| `VIBE_TRADING_TRACE_TOOL_RESULT_INLINE_LIMIT` | `STRATEGY_RESEARCH_TRACE_TOOL_RESULT_INLINE_LIMIT` |
| `VIBE_TRADING_TRACE_TEXT_INLINE_LIMIT` | `STRATEGY_RESEARCH_TRACE_TEXT_INLINE_LIMIT` |
| `VIBE_TRADING_TRACE_PREVIEW_CHARS` | `STRATEGY_RESEARCH_TRACE_PREVIEW_CHARS` |

### 存储路径（`persistent.py`）

| 原路径 | 新路径 |
|---|---|
| `~/.vibe-trading/memory/` | `~/.quantnodes-research/memory/` |

### 模块导入（`persistent.py`）

| 原导入 | 新导入 |
|---|---|
| `from src.agent.frontmatter import parse_frontmatter` | `from ..agent.frontmatter import parse_frontmatter` |

## 原项目 LICENSE

```
MIT License

Copyright (c) HKUDS
```

完整 LICENSE 见上游仓库：https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE

## 参考链接

- 上游仓库：https://github.com/HKUDS/Vibe-Trading
- 调研报告：`docs/archive/undated/vibe-trading-survey.md`（1805 行完整功能清单）
- 路线图：`docs/archive/undated/enhancement.md`（P0-P3 借鉴计划）