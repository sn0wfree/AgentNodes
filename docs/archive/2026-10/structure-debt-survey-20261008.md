# 结构债盘点 — 2026-10-08

> 目的：把「梳理项目代码」的现状扫描固化成清单，标注 ROI，供下一步挑选。
> 基线：commit `3bdde1d`（ruff 88→71，F821 全清）。

## 1. 体量与巨文件

| 指标 | 值 |
|---|---|
| 总行数（不含 alpha_zoo） | 107,117 |
| 文件数 | 466 |
| median | 136.5 行 |
| p90 | 492 行 |
| max | 1,775 行（`core/goal/store.py`） |
| >3000 行的文件 | **0** |

**Top 12 巨文件**（按行数）：

| 行数 | 文件 | 状态 |
|---|---|---|
| 1775 | `core/goal/store.py` | W4 抽过 `store_utils.py` |
| 1618 | `core/study/store.py` | — |
| 1604 | `api/routers/study.py` | W4 抽过 `study_helpers.py` |
| 1503 | `api/routers/web_session.py` | 未拆 |
| 1480 | `api/session/projector.py` | 未拆 |
| 1442 | `api/session/service.py` | 已拆 3 模块 |
| 1413 | `core/autoresearch.py` | 部分拆出 runner_* |
| 1387 | `core/compute_factor.py` | 未拆 |
| 1298 | `core/agent/loop.py` | W5 抽 6 mixin（原 105KB） |
| 1248 | `core/db.py` | 未拆 |
| 1149 | `core/agent/builtin_tools/factor_tools.py` | 未拆 |
| 1110 | `cli/__init__.py` | W2 合并 CLI 后 |

**结论**：巨文件**不再紧急**。最大 1775 行属「大但可控」，median 136 说明 63% 文件 <200 行。继续拆 ROI 低——除非某文件正好要改。

## 2. Ruff 现状（71 errors）

| 类别 | 数量 | 可 autofix | 性质 |
|---|---|---|---|
| F401 unused-import | 55 | 是 | 多为 TYPE_CHECKING / re-export；**autofix 有误删 re-export 风险**（曾发生 `_metric_pass_set`） |
| C901 complexity | 8 | 否 | 真拆分债，见 §4 |
| F811 redefined | 4 | 否 | `autoresearch.py` 自 import 反模式，见 §5 |
| I001 import sort | 4 | 是 | 低风险 |

## 3. W5 拆分残留 NameError bug（已修 12）

上一轮发现的 `loop_stream.py` 缺 `import uuid` 是**系统性缺陷**，ruff F821 一次性暴露全部：

| 文件 | 缺失 | 严重度 |
|---|---|---|
| `api/session/loop_events.py` | `logging` + `logger` | 高（3 处 except/debug 分支） |
| `core/agent/loop_telemetry.py` | `_get_hook_loop` | 高（sync hook bridge） |
| `core/study/runner_knowledge.py` | `json` | 中（任务文案组装） |
| `core/study/stub_agents.py` | `os` | 中（阈值 env 读取） |
| `core/study/runner_langgraph.py` | `time` + `StudyGraph` fwd-ref | 中（3 处超时循环） |
| `api/routers/study_helpers.py` | `StudyScheduler` fwd-ref | 低（annotation） |
| `core/agent/loop_stream.py` | `uuid` | 已修于 `53fb948` |

全部修复于 `3bdde1d`。**ruff F821 = 0**。

## 4. 复杂度 top（C901）

| 复杂度 | 函数 | 文件 |
|---|---|---|
| **32** | `_run_loop` | `core/study/runner.py:362` |
| 24 | `run_round_phases` | `core/study/phase_engine.py:22` |
| 22 | `study_dispatch_action` | `api/routers/study.py:1260` |
| 21 | `build_langgraph` | `core/study/langgraph_engine.py:288` |
| 20 | `migrate_study_history` | `scripts/migrate_study_history.py:62` |
| 16 | `emit` | `core/agent/event_store.py:132` |
| 16 | `extract_agent_schema` | `core/agent/prompt_schema_extractor.py:347` |
| 16 | `_apply_event` | `core/study/_archived/event_store.py:386` ← archived 目录，应排除 lint |

**`runner.py::_run_loop` 复杂度 32** 是全库最高——单函数 2× 阈值。

## 5. F811：`autoresearch.py` 自 import 反模式（4 处）

```python
# 顶层（noqa F401 re-export 意图）
from .study.lazy_detect import detect_lazy_behavior  # noqa: E402,F401

# 函数体内又 import 一遍——同一个模块！
def _run_lazy_detection(...):
    from strategy_research.core.autoresearch import (   # ← 自我 import
        detect_lazy_behavior,
        read_agent_history,
        ...
    )
```

位置：line 762/866/870/1206。注释声称「local imports keep the module import graph light」，但**顶层已有同名** → F811。函数体内的自 import 全部多余（运行时同命名空间）。

**修法**：删除 4 处局部 `from ... autoresearch import (...)` 块，直接用顶层名字。低风险，但需逐个验证 `read_agent_history` 等名字是否在顶层可见。

## 6. 死代码 DELETE-CANDIDATE（B1-B17）

已有体系：17 处标签 + 守门测试 `tests/test_dead_code_labels.py`（锁标签存在，不锁删除）。

| ID | 文件 | 标签理由 |
|---|---|---|
| B1 | `core/llm/builder.py` | 整模块，0 production callers |
| B2 | `core/llm/config.py:710` | legacy CLI flag |
| B3 | `core/agent/progress.py` | 整模块，loop 用 inline callback |
| B4 | `core/agent/to_llm_message.py` | 整模块，loop 用 inline projection |
| B5 | `core/hooks/unified.py` | superseded by composite.AgentHook |
| B6 | `core/hooks/adapter.py` | 同上 |
| B7 | `core/swarm/runtime.py:108` | GroundingProvider never read |
| B8 | `api/routers/goal.py:33` | 字段被 handler 静默丢弃 |
| B9 | `core/agent/compact.py:174` | L1 path removed，flag 死 |
| B10 | `core/agent/compact.py:795` | 0 production callers |
| B11 | `api/routers/strategy.py:3` | 2 个 endpoint 0 callers |
| B12 | `api/routers/memory.py:3` | legacy, test-only |
| B13-16 | events/event_bus_v2 | W3b 已迁 `tests/_fixtures` |
| B17 | `webui/frontend/.../Skeleton.tsx` | never imported |
| +3 | `api/session/store.py:66/194/281` | 0 production callers |

**未执行删除**。执行前需：逐个跑 `rg <symbol> src/` 确认 importer 只有 tests，删 + 改守门测试（`EXPECTED_LABELS` 同步缩减）。

**已验证为过时的说法**：
- `to_llm_message.py` docstring 说 0 callers —— `compaction_message.py::to_llm_message()` 是**同名方法**，不是本模块函数；标签正确。
- `workflow/dag.py:85` 说 no callers —— 实际 18 个 importer（`goal/dag_renderer.py`、`cli/tui/widgets/dag_view.py` 等）。**标签过时，应撤销或缩小到具体函数**。

## 7. 双引擎 / 双入口

| 概念 | A 路径 | B 路径 | 规模 |
|---|---|---|---|
| Study 执行引擎 | `core/study/phase_engine.py` (24复杂度) | `core/study/langgraph_engine.py` + `runner_langgraph.py` | 11 vs 6 文件 |
| AgentLoop 构造 | `core/agent/chat_loop.py::build_chat_agent_loop` | `core/agent/role_factory.py` (22 引用) | 中 |

`runner.py` 负责引擎分派（`_run_loop` 复杂度 32 可能与双引擎分派耦合）。

## 8. 下一步 ROI 排序

| 选项 | 工作量 | 风险 | 收益 |
|---|---|---|---|
| **A. 执行 B1-B12 删除** | 中 | 中（需逐个验证 importer + 改守门测试） | 减 ~2000 行真死代码 |
| **B. 修 autoresearch 4 处自 import** | 小 | 低 | F811 4→0，ruff 71→67 |
| **C. `runner.py::_run_loop` 拆分**（32→<15） | 中 | 中（核心执行路径） | 降低后续改动风险 |
| **D. autofix F401+I001**（59 处） | 小 | **中**（历史有 re-export 误删） | ruff 71→~12 |
| **E. 双引擎收口** | 大 | 高 | 删一整条路径 |
| **F. 巨文件继续拆** | 大 | 中 | 低（max 1775 不紧急） |

**推荐**：B（小步快跑，ruff 减到 67）→ A（真死代码清理）→ D（最后做，逐条人工确认）。
E/F 除非有具体改动需求，否则不动。
