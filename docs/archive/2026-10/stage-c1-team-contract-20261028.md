# C1 团队契约（阶段 0 冻结）— 2026-10-28

> **本文是并行重构的唯一契约来源。** 多个 worker 同时改代码时，凡是本文没写的，
> 一律视为「未决」——先问协调者，不要自行决定。
>
> 事实全部来自本机实测（commit `4b0f3de0`，分支 `refactor/v6-gateway`），
> 每条结论带 `file:line`。估算一律标注为估算。

---

## 0. 为什么需要这份文档

剩余工作（B2 study 链 / B3 workflow+swarm / B4 数据面 / B5 ContextHub）
彼此耦合在 `role_factory.py`、`executor.py`、`loop.py` 这几个文件上。
如果多个 worker 各自解释「这个能力该注入还是该 import」，合并时就是重写。

B1 阶段的经验已经证明了这一点：B1 花了 5 个阶段、6 个提交，
其中 2 个提交（B1-e、B1-f）的产出是**范围裁定**而不是代码——
即「这个能力根本不需要移植」。裁定的成本高于实现，所以必须前置。

---

## 1. 能力矩阵（唯一权威表）

每个横切能力必须且只能落在一个层。归 W-P（平台运行时）独占还是归域，
以「是否需要 import 域类型」为判据。

| 能力 | 落层 | 机制 | 现状 | 依据 |
|---|---|---|---|---|
| LLM↔工具循环 | 平台（runtimes） | react 节点自带 | ✅ 已实现 | `nodes/react.py:85-149` |
| 预算记账 + 判定 | 平台（runtimes） | `RunBudget.plan_call()` 唯一入口 | ✅ 已实现 | `run_budget.py:38-52` |
| 预算树（父留子吸） | 平台（runtimes） | `reserve()` / `absorb()` | ✅ 已实现 | `run_budget.py:60-107` |
| 权限闸门 | 平台（runtimes） | `ctx_factory` 注入 `ToolContext` | ✅ 已实现 | `nodes/react.py:135-141` |
| 上下文压缩 | 平台（runtimes）**注入** | `compactor` 回调，节点不 import 域 | ✅ 已实现 | `nodes/react.py:88-101` |
| 流式增量 | 平台（runtimes） | `stream=True` → `text_delta` | ✅ 已实现 | `nodes/react.py:199-233` |
| 系统提示装配 | 平台（gateway/agent 共享核） | `assemble_loop_parts` | ✅ 已实现 | `session_controller.py:241-270` |
| 上下文注入 | 平台（context）**注入** | `ContextHub.build()` + 单块前置 | ✅ 已实现 | `context/hub.py:71-124` |
| **HITL 审批门** | **域（study）** | `GraphInterrupt` + payload 落库 | ✅ 已实现 | `study/graph_preset.py:238-304` |
| **暂停/恢复契约** | **域（study）** | 三键重建 + `start="claims"` | ✅ 已实现 | `study/graph_preset.py:131-173` |
| **claims 捕获** | **域（study）** | 图内 `claims` 节点 | ✅ 已实现 | `study/graph_preset.py:307-317` |
| **knowledge hook** | **域（study）** | 轮级 collector，非 loop 能力 | ✅ 已实现（图外） | `study/runner_knowledge.py:19-71` |
| 事件翻译（平台→产品） | 边界层（api） | 显式 mapping 表 | ⚠ 只有 4 个事件 | `api/routers/chat.py:801-813` |
| 单条件路由 | 平台（runtimes） | `add_conditional_edge` | ✅ 已实现 | `graph_engine.py:255-278` |
| fanout 并行 | 平台（runtimes） | T1 线程 / T2 进程 + 预算树 | ✅ 已实现 | `graph_engine.py:312-544` |
| 逐节点重试 | **缺失** | — | ❌ 声明存在但从未实现 | `workflow/definition.py:51` |
| 逐节点超时 | **缺失** | — | ❌ 同上 | `workflow/definition.py:51` |
| 动态子图（plan_* 运行时建图） | **缺失** | 引擎编译期固定节点集 | ❌ | `graph_engine.py:124-131` |
| 分层并行（层内多节点并发 + 层间屏障） | **缺失** | fanout 只并行单节点的分支 | ❌ | `graph_engine.py:312-413` |
| 单节点 skip/retry 重入 | **缺失** | — | ❌ | `swarm/runtime.py:635-706` |
| 检查点续跑（pre_completed） | **缺失** | 平台无，study 域用「重建」代替 | ❌ | `swarm/runtime.py:144-167` |

**判定规则（写入架构门禁）**：
- 平台层（`core/runtimes`、`core/context`、`core/orchestration`）**不得 import 域**。
  压缩器、ToolContext 工厂、上下文 provider 全部靠注入。
- 域层（`core/study`、`core/workflow`、`core/swarm`）**可以 import 平台**，
  反向绝对禁止。已有门禁：`tests/test_architecture_gates.py`。

---

## 2. 事件契约

### 2.1 三层事件语义（这是最容易漂移的地方）

```
节点/引擎发出（平台观测语义）        边界翻译（产品语义）        消费方
─────────────────────────────────  ────────────────────────  ──────────────────
node.started / node.completed   →  （不翻译，仅 trace）      trace_projection
tool.started                    →  tool_call                 WebUI / CLI
tool.completed                  →  tool_result               WebUI / CLI
text_delta / thinking_delta     →  同名直通                   WebUI 流式
thinking_done / stream.failed   →  同名直通                   WebUI
budget.exhausted                →  ✗ **当前被丢弃**           ← 见 2.3 缺口
context.compacted               →  ✗ **当前被丢弃**           ← 见 2.3 缺口
context.compact_failed          →  ✗ **当前被丢弃**
graph.interrupted               →  ✗ **当前被丢弃**
graph.fanout / branch.settled   →  ✗ **当前被丢弃**
study_phase / study_round / …   →  域事件直发（不经引擎）      WebUI study 面板
```

**翻译层位置是唯一权威**：`api/routers/chat.py:792-824` 的
`_install_chat_event_bridge`。它的白名单是 4 项（`tool.started`、
`tool.completed`、`text_delta`、`thinking_delta`、`stream.failed`、`thinking_done`
——实际 6 项，但 `mapping` 只有前 2 项，其余靠 `if kind in (...)` 直通）。

### 2.2 域事件名（study，已在前端类型中声明）

`study_phase`、`study_round`、`study_started/completed/failed/cancelled/paused/resumed`、
`study_round_rejected`、`study_claims_captured`、`study_claims_validated`、
`study_knowledge_*`、`study_review`、`study_evidence`、`study_scoreboard`、
`study_budget*`、`study_todos_updated`、`study_parse_retry`、`study_graph_node`。

前端权威清单：`webui/frontend/src/hooks/sse/types.ts:16-107`。
**新增域事件必须先加进这个 union，否则前端静默丢弃。**

### 2.3 已知缺口（契约冻结时必须记录，不要求本轮修）

**缺口 E1：react 路径的预算/压缩事件到不了前端。**
`_install_chat_event_bridge` 的 `mapping` + 直通白名单不含
`budget.exhausted` / `context.compacted` / `context.compact_failed`。
后果：chat 走到预算耗尽时，用户看不到任何提示；压缩发生了也没有 UI 反馈。
旧 `AgentLoop` 路径有 `budget_exhausted` / `compact` 事件（前端已支持）。
**这是 P5-1 迁移遗留的行为差**，不是新 bug。
处置：归 W-C（上下文/事件收口），或随 P5-1c 一并清。**不在 B2/B3 处理。**

**缺口 E2：AgentLoop 的事件族与新引擎不是同一套。**
旧：`tool_call` / `tool_result` / `llm_usage` / `iter_start` / `iter_end` /
`loop_start` / `loop_end` / `compact` / `assistant_message` / `agent_approval_requested`。
新：`tool.started` / `tool.completed` / `node.started` / `node.completed` + 4 个 stream 事件。
**不要试图统一名字**——翻译层的存在就是为此。统一会同时破坏前端两个消费面。

---

## 3. 暂停 / 恢复契约（study，不可变更）

这是唯一有持久化语义的契约，改动会破坏已落库的审批单。

### 3.1 暂停产出（六字段 dict，`runner._handle_hitl_pause` 直接消费）

```python
{
    "round": int,
    "run_name": str,
    "paused_for_approval": True,
    "study_id": str,
    "interrupt_id": str,     # 必需——runner.py:423-428 读
    "hypothesis": str,       # 必需
}
```

### 3.2 持久化 payload（五字段 + 两扩展）

```python
{
    "type": "novelty_gate",
    "study_id": str,
    "round_num": int,
    "hypothesis": str,              # 重建必需
    "predicted_affected": list,     # 重建必需
    "researcher_output": dict,      # 重建必需
    "message": str,
}
```

### 3.3 恢复（方案 A：重建 + start=）

```python
engine.run({
    "researcher_output": ...,
    "predicted_affected": ...,
    "hypothesis": ...,
}, start="claims")
```

**硬约束**：claims 及之后的任何节点，若新增了对某个 state 键的读取，
**必须同时把该键写进暂停 payload**。这是「暂停点持久化后继要读的每一个键」
原则。违反它 → 恢复时 KeyError 或者更糟：静默用空值算出错误的回测。

### 3.4 谁来写这条约束

归 W-S（研究链）。W-P 修改 claims/execution/evaluation 节点时必须通知 W-S。
因为节点在图里，改节点 = 改暂停契约。

---

## 4. 结果信封契约（三条链各自的输出形状）

**不要试图统一这三个信封。** 它们是三条链对外的 ABI，
统一会改动 API 响应、DB schema、前端解析三处。

| 链 | 类型 | 字段 | 位置 |
|---|---|---|---|
| chat | `RunResult` | `turn_id, run_id, ok, answer, error` | `gateway/session_controller.py:24-31` |
| study | `RoundOutcome` | `metrics, verdict, eval_result, agent_outputs, strategist_output, hypothesis, predicted_affected, captured_predictions, backtest_error` | `study/phase_engine.py:39-48` |
| swarm | `AgentResult` | `agent_id, status, output, error, elapsed_s, summary, artifacts, metrics` | `swarm/runtime.py:61-76` |
| workflow 节点 | `AgentExecutionResult` | `agent_id, status, output, error, elapsed_s, summary, metrics, artifacts` | `agent/executor.py:44-70` |

**注**：`AgentResult` 与 `AgentExecutionResult` 字段几乎相同但**是两个类**，
分属 swarm 与 workflow。这是历史遗留，**本轮不合并**（合并要改 246+367 个测试的断言）。

---

## 5. 文件所有权（防 merge 冲突）

一个文件在任一时刻**只能有一个写者**。跨 owner 的修改走「请求—代改」。

| Owner | 独占可写 | 只读（须请求代改） |
|---|---|---|
| **W-P** 平台运行时 | `core/runtimes/**`（含 nodes/presets/executor_bridge）、`core/agent/role_factory.py`、`core/agent/loop.py`、`core/agent/tool_context.py`、`core/agent/compaction_runtime.py` | `core/context/**`（W-C） |
| **W-S** 研究链 | `core/study/**`、`core/autoresearch.py` | `role_factory.py`、`core/runtimes/**` |
| **W-W** 工作流+swarm | `core/workflow/**`、`core/swarm/**`、`core/agent/executor.py` | `role_factory.py`、`core/runtimes/**` |
| **W-D** 数据面+上下文 | `core/agent/memory_manager.py`、`core/session/**`、`core/context/**`、`core/agent/context_providers.py` | — |
| **C** 协调/文档/审计 | `docs/**`、`tests/test_architecture_gates.py`、基线清单 | 全部只读 |

**为什么 `role_factory.py` 归 W-P**：study（`autoresearch.spawn_agent:615`）和
workflow（`node_types.py:160-163`）都通过 `run_agent_via_llm` 调它。
两条链各自改 = 必然冲突。W-P 先把它收敛成稳定的 `agent_node()` 工厂，
W-S / W-W 只调用。

**为什么 `loop.py` 归 W-P**：1161 行，是旧执行体核心。W-S/W-W 只读它，
不允许「顺手改一行」。

---

## 6. 验收协议（没有这条，127 个预存失败会淹没所有信号）

### 6.1 基线

协调者在干净 commit 上跑一次 PR 道，产出
`tests/baselines/pr-lane-failures.json`（失败 node id 列表）。

命令（本机实测解释器是 `D:\tools\python312\python.exe`，不是默认 `python`）：

```
D:\tools\python312\python.exe -m pytest tests -q --tb=no \
  -m "not llm and not e2e and not slow" --no-header > raw.log 2>&1
D:\tools\python312\python.exe scripts/c1_baseline.py raw.log
```

**冻结基线（实测，commit `4b0f3de0`，2026-10-28）**：

| 指标 | 值 |
|---|---|
| failed | 93 |
| error | 7 |
| **总计（门禁比对单位）** | **100** |
| passed | 9638 |
| skipped | 70 |
| 墙钟 | ~370s |

**与路线图记录的 127 不符，原因是测量口径不同，不是回归**：
路线图那次包含了 `test_webui_e2e` 等套件（当时尚未分道，跑到中途被 timeout 打断），
且 Windows 环境差异导致部分 POSIX 依赖测试的失败集合不同。
**以本文件冻结的 100 条 node id 为唯一基准**——它是可复现的
（`--tb=no` + `-m "not llm and not e2e and not slow"` + 本机 py312）。

失败最集中的文件（前 12）：

```
8  tests/test_route_agent_event_thinking.py
7  tests/test_study_runner_integration.py
6  tests/test_cli_server_cmd.py
5  tests/test_bg_tools.py
5  tests/test_combo_tools.py
5  tests/test_tui_event_routing.py
4  tests/test_optimizer_logging.py
4  tests/test_workflow_executors.py
3  tests/test_api_container.py
3  tests/test_compact_opencode_retention.py
3  tests/test_route_agent_event_think_strip.py
3  tests/test_stage6_e2e.py
```

### 6.2 每个 worker 的绿条件

> 自己 owner 范围内的测试文件中，失败集合 ⊆ （基线 ∩ 自己的文件），
> 且自己新增的测试全绿。

**不接受**「全量 127 个预存失败减少到 X 个」之外的任何口头判断。
失败必须对照基线 node id，不是对照数量。

工具（阶段 0 一并交付，已实测）：

```bash
# 冻结基线（协调者，只需一次）
D:\tools\python312\python.exe -m pytest tests -q --tb=no \
  -m "not llm and not e2e and not slow" --no-header > raw.log 2>&1
D:\tools\python312\python.exe scripts/c1_baseline.py raw.log
# -> tests/baselines/pr-lane-failures.json

# worker 自查（每次提交前）
D:\tools\python312\python.exe -m pytest <自己的子集> -q --tb=no > my.log 2>&1
D:\tools\python312\python.exe scripts/c1_gate.py my.log
# exit 0 = 无回归；exit 1 = 有基线外失败（打印 node id + 文件）
```

`c1_gate.py` 支持 `--files <前缀>...` 只看自己 owner 范围：
```
D:\tools\python312\python.exe scripts/c1_gate.py my.log --files test_workflow test_swarm
```

**为什么用集合包含而不是数量**：把一条回归和一条碰巧修好的测试抵掉，
数量不变——这是数量门永远看不见的失效模式。

### 6.3 分道（每个 worker 只跑自己的子集）

| Owner | 验收子集 |
|---|---|
| W-P | `test_graph_engine*` `test_react_node` `test_b1*` `test_agent_loop*` `test_role_factory*` `test_executor*` `test_run_budget` `test_fanout*` |
| W-S | `test_role_factory*` `test_spawn_agent*` `test_hitl*` `test_study*` `test_research_preset` `test_dag_planner` `test_phase_engine*` `test_runner_*` |
| W-W | `test_workflow*` `test_swarm*` `test_agent_executor*` `test_goal_*` `test_v05*` |
| W-D | `test_context_hub` `test_context_providers*` `test_memory*` `test_session*` `test_sse_single_track` |

### 6.4 合并纪律

- 每个 worker 在**自己的 git worktree** 内提交，不 rebase 别人的分支。
- 协调者按依赖顺序 merge：W-P → (W-S ∥ W-W) → W-D。
- 冲突由协调者裁决。worker 不解决跨 owner 冲突。

---

## 6.5 less-is-more（2026-10-28 起，**硬约束**）

> **能用现有代码就不新写；能改造完成就不重写。**

背景：W-P 首版在零生产调用方的情况下新写了 `agent_node`（约 80 行）和一个
与 `executor.first_two_sentences` 重复的 `_first_two_sentences`。全部删除。
这条约束就是为防再次发生。

执行细则：

1. **新函数前先列现有入口**。设计文档里必须给出查过的类/函数名
   + 为什么不能复用。列不出 = 禁止新写。
2. **零生产调用方 = 死代码**。发现即删，不留「以后会用」。
3. **同包纯函数禁止复制**，直接 import。「依赖方向」不适用于同包内的
   无状态纯函数（如 summary 提取）。
4. **改造优先于新建**。扩参数/加注入点 = 改造（允许）；
   平行的第二套实现 = 新建（禁止）。
5. **现有适配器优先复用**。swarm/workflow 的 agent 执行适配已存在于
   `AgentExecutor.execute()`（信封/超时/事件齐全），编排迁移时直接用，
   不要再造 `agent_node` 式的第二适配器。

现有入口速查（写新代码前先查这张表）：

| 需求 | 已有入口 | 不要再写 |
|---|---|---|
| study/workflow 跑一个 role | `role_factory.run_agent_via_llm` | 第二个 role 执行入口 |
| swarm/workflow 执行 agent 节点 | `AgentExecutor.execute`（executor.py:101） | node 级适配器 |
| chat 单轮带工具循环 | `make_react_node`（runtimes/nodes/react.py） | 第二个工具循环 |
| role 提示/白名单装配 | `assemble_loop_parts`（loop_assembly.py） | 第二套装配核 |
| 结果信封 | `AgentExecutionResult` / `LoopResult` / `RoundOutcome` | 第四种信封 |
| 上下文收集 | `ContextHub.build`（core/context） | 第二套收集器 |
| 预算 | `RunBudget.plan_call` | 第二套预算判定 |

---

## 7. 环境陷阱（实测，会让方案直接失效）

### 7.1 worktree 默认不隔离代码（**最高优先级**）

editable install 把**主检出**的 `src` 写死进了 `.pth`：

```
D:\tools\python312\Lib\site-packages\__editable__.quantnodes_strategy_research-0.6.0.pth
内容: D:\Github\quantnodes-strategy-research\src
```

实测：在 `D:\Github\qn-wt-probe` worktree 里放探针标记后跑测试，
`marker visible: False`——**导入的是主检出的代码**。
设 `PYTHONPATH=<worktree>\src` 后 `marker visible: True`。

**结论**：每个 worker 启动前必须
```
set PYTHONPATH=<自己的 worktree 绝对路径>\src
```
否则四个 worker 都在改同一份代码却以为互相隔离。

### 7.2 解释器

`D:\tools\python312\python.exe`（pytest 9.1.1）。默认 `python` 是 3.13，无 pytest。

### 7.3 无 xdist

仓库内并行只能靠多进程/多 worktree。12 核，但并发路数控制在 **4 以内**
（历史上并行两个大 pytest 曾 OOM）。

### 7.4 Windows shell

- `;` 和 `|` 在部分调用里被吞掉，命令要拆开。
- 多行 `python -c` 会静默失败，必须写临时脚本文件。
- 不要用 PowerShell `Set-Content` 编辑含中文的 UTF-8 文件（破坏编码）。

---

## 8. 阶段划分与依赖

```
阶段 0（本文，串行）
  ├── 契约冻结（能力矩阵 / 事件表 / 暂停契约 / 所有权）
  └── 基线清单
        ↓
阶段 1（并行 4 路）
  ├── W-P：运行时缝 + agent_node() + 引擎扩展   ← 关键路径
  ├── W-D：MemoryManager 事件化 + 旧会话栈退役
  ├── W-C：ContextHub W1/W2
  └── C ：文档 + 审计门②
        ↓
阶段 2（并行 3 路，消费 W-P）
  ├── W-S：study 链迁 react（四 spawn 点 + 保 HITL 三键）
  ├── W-W：swarm 先（60-70% 可表达）→ workflow 后（25-35%）
  └── C ：文档收口
        ↓
阶段 3（串行）
  └── 集成 · 全量回归 · P5-1c（若解禁）· 最终验收
```

**W-W 内部顺序不可颠倒**：swarm 的 DAG 直接映射 `add_node`/`add_edge`，
缺 6 项适配器能力；workflow 需要动态子图 + 段门重规划，属结构性改造。
先做 workflow 会把风险前置到最不确定的地方。

---

## 9. 范围裁定（明确不做什么）

这些是 B1 已经验证过的「不需要移植」结论，**不要重新讨论**：

| 事项 | 裁定 | 依据 |
|---|---|---|
| steering `inject()` / `ainject()` | 不移植 | 零生产调用方，chat 的插话是新 turn（mailbox 准入） |
| `enable_hypothesis_auto_create` | 不移植到 chat | `chat_loop.py:140` 显式传 False |
| 检查点（SqliteSaver 式全量快照） | 不做 | 方案 A（重建）已落地，见 §3 |
| 统一 `AgentResult` / `AgentExecutionResult` | 不做 | 见 §4 注 |
| 统一新旧事件名 | 不做 | 见 §2.3 缺口 E2 |
| 修 §2.3 缺口 E1 | 不在 B2/B3 | 归 W-C 或 P5-1c |

---

## 10. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/archive/2026-10/project-status-and-roadmap-20261028.md` | 现状 + 路线图（本契约的上游） |
| `docs/archive/2026-10/stage-b1-chat-to-react-design-20261028.md` | chat 链迁移（已完成，模式参照） |
| `docs/archive/2026-10/stage-b1c-compaction-port-design-20261028.md` | 注入式压缩（W-P 的范式样板） |
| `docs/archive/2026-10/stage-b1d-permission-gate-design-20261028.md` | 注入式权限（同上） |
| `docs/archive/2026-10/stage-b1e-inbox-hypothesis-scope-20261028.md` | 范围裁定方法（§9 的样板） |
| `docs/archive/2026-10/stage-hitl-gate-graph-design-20261027.md` | HITL 门（§3 的来源） |
| `docs/archive/2026-10/hitl-resume-industry-research-20261027.md` | 恢复方案选型（方案 A） |
| `docs/archive/2026-10/stage-p54-context-hub-w0-design-20261027.md` | ContextHub W0 |
| `docs/architecture-diagram.html` | v6.2 五层目标架构图 |

---

## 附：本文的验证方式

本文每条事实都可用下列方式复验（协调者在冻结时已执行）：

```bash
# 能力矩阵：react 节点边界
sed -n '1,70p' src/strategy_research/core/runtimes/nodes/react.py

# 事件翻译层
sed -n '792,824p' src/strategy_research/api/routers/chat.py

# 暂停契约
sed -n '238,304p' src/strategy_research/core/study/graph_preset.py

# 前端事件权威清单
sed -n '16,107p' webui/frontend/src/hooks/sse/types.ts

# worktree 隔离陷阱
cat "D:\tools\python312\Lib\site-packages\__editable__.quantnodes_strategy_research-0.6.0.pth"
```
