# W1-1 审计报告 — agent 22k 包分类（2026-11-03）

审计类 · 不改代码。数据源：**graphify AST 全量图**（45,877 节点 / 88,873 边，
`tmp/graphify/graphify-out/graph.json`），脚本 `tmp/w11_graphify.py`
按出向 `imports` 边统计每个 agent 源文件对研究域（L2）与平台（L3/L4）的依赖。

## 0. 方法

不使用主观臆断：每个文件的类别由「它实际 import 了什么」决定。
研究域依赖 = 指向 `core/backtest|alpha_zoo|goal|hypothesis|study|compute_factor|
config_runner|db|data_readiness|data_source|strategy_acceptance|validation|
portfolio|tools/` 的边。

## 1. 分类结果

### R 类 — 研究特化语义（10 个文件）

| 文件 | 研究依赖数 | 具体目标 |
|---|---:|---|
| `builtin_tools/data_tools.py` | 6 | config_runner, data_readiness, data_source×3, db |
| `builtin_tools/backtest_tools.py` | 3 | backtest, config_runner, data_readiness |
| `builtin_tools/factor_tools.py` | 3 | compute_factor, db, tools |
| `builtin_tools/goal_tools.py` | 3 | goal×3 |
| `builtin_tools/data_clean_tools.py` | 2 | db, tools |
| `context_injector.py` | 2 | goal, hypothesis |
| `exec_registry.py` | 2 | backtest, strategy_acceptance |
| `loop.py` | 2 | goal, hypothesis |
| `builtin_tools/backtest_analysis_tools.py` | 1 | db |
| `dag_config.py` | 1 | study |

### P 类 — 平台通用 tool-loop / 基础设施（其余 50+ 文件）

`builtin_tools/` 下的 bg/display/file/help/options/shell/subagent/todo/web/workspace
（零研究依赖，纯 I/O 与工具协议）、`loop_*` 全家（lifecycle/progress/telemetry/
tools/assembly/compaction）、`compact` / `compaction_runtime`、`event_store`、
`executor`、`gateway`、`combo`、`chat_loop`、`circuit_breaker`、`memory_manager`、
`role_factory`、`registry`、`runtime/*`、`sandbox/*`、`strategy/*`（含 9 个
protocol/steps 与 5 个策略实现）。

## 2. 判定门（实施计划 3.1 规定的阈值）

按**文件数**：研究特化 10 / 总 60 ≈ **17%** —— 远低于 50% 阈值。
按**代码量**：R 类 10 个文件合计约 5.6k 行（data_tools 758 + factor_tools 1150 +
backtest_tools 510 + goal_tools 574 + data_clean_tools ~200 + backtest_analysis 448 +
context_injector 408 + exec_registry ~150 + loop 的 goal/hypothesis 部分 +
dag_config）／agent 总 22.0k ≈ **25%**。

**结论：不触发「kernel 能力面重新划界」。** 维持现状（agent 整体留 L3），
但 R 类 10 个文件是「kernel 包内的研究适配器」，与既有叙事一致（接缝契约
已把 `builtin_tools/` 记为「能力适配器，合法包装域工具」）。

## 3. 与 W1-3 的交叉验证

W1-3 发现的 46 条 L3→L2 边，其中 **41 条来自本表 R 类的 6 个 builtin_tools 文件**
（data/factor/backtest/goal/data_clean/backtest_analysis）。两份审计互相印证：
**L3→L2 的实质耦合 = agent 里的研究工具适配器**，不是散落全 kernel。

## 4. 建议

1. **不做重划界**（阈值未达）。重开条件：R 类文件数 > 30 个 或 R 类 LOC > 8k。
2. R 类 10 文件不搬位置（接缝契约已合法化）；若要减少门禁噪声，可行方案是
   **把 `builtin_tools/` 显式标记为「能力适配器子层」**，门禁对它的 L3→L2 计数
   单列（与 W1-3 建议的 lazy-import 归类合并处理，同属 W4-1 门禁规则工作项）。
3. graphify 枢纽数据（见下）为 W2-1 划界决策提供第二组证据。

## 5. graphify 架构枢纽（god-nodes·附加发现）

| # | 节点 | 边数 | 源文件 | 层 |
|---:|---|---:|---|---|
| 1 | `ToolContext` | 458 | `core/agent/tools.py` | L3 |
| 2 | `CompactConfig` | 258 | `core/agent/compact.py` | L3 |
| 3 | `GoalStore` | 257 | `core/goal/store.py` | L2 |
| 4 | `LLMConfig` | 256 | `core/llm/config.py` | L4 |
| 5 | `AgentLoop` | 247 | `core/agent/loop.py` | L3 |
| 6 | `build_default_registry()` | 234 | `core/agent/builtin_tools/__init__.py` | L3 |
| 7 | `EventV2` | 234 | `core/events/event_v2.py` | L3 |
| 8 | `StudyStore` | 217 | `core/study/store.py` | L2 |

前 8 个枢纽：**L3×5 · L2×2 · L4×1**（`LLMConfig` 的源文件按 `core/llm/config.py` 计，
bridge 未返回精确行号）——与脊-翼布局的「平台脊承载机制、研究域承载账本」判断一致。
`ToolContext`（458 边）是**全库第一枢纽**，且被 tests 大量引用（64+55+29 条），
印证 W1-3 的结论：它是 L3 的通用协议件，`core/tools`（L2）只有 5 条边指向它。
