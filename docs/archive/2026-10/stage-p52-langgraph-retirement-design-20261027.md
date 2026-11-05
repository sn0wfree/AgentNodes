# P5-2 langgraph 分支退役设计 — 2026-10-27

> 前置全部就绪后的执行设计。HITL 独有能力已吸收完毕：
> 暂停段 `71d5cf34`、resume 段 `8623d42d`（决策记录
> `hitl-resume-industry-research-20261027.md`）。本文档 = 调查结论 +
> 删/留清单 + 兼容语义 + 测试处置。

## 1. 使用面调查结论

| 查证项 | 结论 |
|---|---|
| src 内 import 方 | 仅 `runner.py:124`（mixin）与 `runner_langgraph.py` 自身 |
| WebUI 引用 | 零（engine 字段从不传） |
| **真实存量数据** | `~/.quantnodes-research/goals.db` 的 studies 表**为空**（只读实测）——零行为影响 |
| 脏值回落 | `_resolve_branch_fn` 对未知 engine 值 warning + 回落 phases（`phase_engine.py:386-392`），删 registry 项后 `engine='langgraph'` 自动安全回落 |
| `langgraph` 包依赖 | **保留**——新 GraphEngine 的 StateGraph 基座就是它，退役的只是 study 分支 |
| CLI/API | `/study/start` 的 `engine` 字段保留（接受任意值，脏值回落），注释更新 |

## 2. 删 / 留清单

**删（约 1000 行产品代码）：**

| 位置 | 内容 |
|---|---|
| `core/study/langgraph_engine.py` | 整文件（679 行：图构建、profile、门节点、checkpoint、resume） |
| `core/study/runner_langgraph.py` | 整文件（155 行：mixin，`_run_round_via_langgraph` / `_resume_round_langgraph`） |
| `core/study/runner.py` | mixin import + 基类项；`_rebuild_phase_outputs`（~60 行，活调用方仅 langgraph 引擎；`_archived/dag_engine` 为归档引用不运行）；resume 分流简化为单路径（图路径唯一） |
| `core/study/phase_engine.py` | `_run_langgraph_branch` + registry `"langgraph"` 项 + 顶部死 import（`state_store as ss`） |
| `core/study/store.py` | `get_checkpoint_conn`（活调用方仅 langgraph 引擎）；checkpoint **DDL** 两表（新库不再建表） |
| `scripts/` ×6 | 修剪 langgraph 引用：`engine_probe` 删「langgraph 分支依赖方向」一节；`arch_probe`/`layer_probe`/`triad_probe`/`extract_mixin_attrs`/`declare_mixin_fields` 删 langgraph 条目（脚本本身是多文件分析工具，保留） |

**留（附理由）：**

| 位置 | 理由 |
|---|---|
| `studies.engine` 字段 + 列 | DB 兼容；'langgraph' 值回落 phases（warning） |
| 已有库的 `langgraph_checkpoints`/`langgraph_writes` 表 | 存量数据留存不写（只删 DDL，已有库的表自然保留） |
| `engine_common.py` 整文件 | session/interrupt helpers 活跃（bootstrap/迁移脚本/测试）；`build_agent_ctx` 等三个 helper 被 `_archived` 引用，删除会破坏归档可运行性——只清注释 |
| `core/study/_archived/` | 历史归档零运行时成本 |
| `core/runtimes/graph_engine.py` | 与本分支无关（新引擎），其对 langgraph 包的使用保留 |
| runner 的 `paused_for_approval`/`_handle_hitl_pause` | 图路径 HITL 的活跃机制 |

## 3. 测试处置

**整删（3 文件，~1500 行）：** `test_langgraph_interrupt_id.py`、
`test_langgraph_parity.py`、`test_resume_round_langgraph.py`。

**修剪（6 文件）：**

| 文件 | 动作 |
|---|---|
| `test_hitl_resume_rebuild.py` | 删 `test_resume_round_langgraph_forwards_profile`；**保留 3 个 `_wait_for_approval` 测试**（runner 轮询循环是引擎无关的活跃机制），文件名保留 |
| `test_phase_engine_round_outcome.py` | dispatch 表形状断言改 `{"phases"}` |
| `test_study_e2e_api.py` | `test_study_lifecycle_langgraph` 实际用 engine="phases"（名字历史遗留）→ 改名 `test_study_lifecycle`；`test_study_graph_structure` 用 engine="langgraph" 断言通用图结构 → **保留作为脏值回落 E2E** |
| `test_study_engine_dispatch.py` | dag 回落测试扩展参数化 `langgraph`（回落契约钉死） |
| `test_engine_common_helpers.py` / `test_study_hitl_respond.py` / `test_study_store_round_lifecycle.py` | 引擎无关，仅清 docstring 提及 |
| `test_phase_engine_researcher_defense.py` | 仅清注释提及 |

## 4. 兼容语义（删除后的行为）

1. `engine='langgraph'` 的 study（存量或新建）：每轮 warning
   `unknown engine 'langgraph'; falling back to phases`，走图路径——
   丢并行 profile 与 SqliteSaver 检查点（hitl 语义由 `study.hitl` 列
   承接，不受 engine 影响）。
2. 新库不再建 checkpoint 两表；已有库的表静默留存。
3. `verify_invariants` / 架构门禁不受影响（平台层本就禁止 import study 域）。

## 5. 验收

1. `git grep -l "langgraph_engine\|runner_langgraph" src/ tests/` 仅剩
   `_archived` 与文档。
2. 回归：HITL 套件 + research_preset + dispatch + architecture gates +
   e2e + engine_common 全绿。
3. lint 干净；`import strategy_research.core.study.runner` 无 langgraph 残留。
4. 全量分道与阶段 4 基线同集合。
