# C1 验收标准 — 2026-10-28

> 配套：`docs/stage-c1-team-contract-20261028.md`（契约，回答「怎么改」）
> 本文回答「改成什么样算过」。

---

## 0. 一句话标准

> **每一条链的迁移，必须在「旧执行体还能跑」的前提下证明新执行体行为等价；
> 然后才允许删除旧执行体。删除本身不是验收项。**

这条顺序不能反。反过来做（先删再补）会产生一种无法证伪的状态：
没有任何对照物能证明新路径少了什么。

---

## 1. 为什么不能用「测试全绿」当标准

PR 道冻结基线是 **100 条失败**（93 failed + 7 error，见
`tests/baselines/pr-lane-failures.json`）。这个数字必须在迁移中**下降**，
但下降本身不能作为证据，因为有两类作弊面：

**作弊面 A：删测试。** 删掉 `test_workflow_executors.py` 的 28 条，
其中 4 条基线失败立刻「消失」。看起来是进展，实际是丢覆盖。

**作弊面 B：抵消。** 引入 1 条回归、顺手修好 1 条预存失败，失败总数不变。
数量门看不见。

两类作弊面各有一个执法工具（§2.2 / §2.3），都是集合比对，不是计数。

---

## 2. 三道门（每个 worker 提交前必须全过）

### 2.1 门一：行为等价（唯一真正重要的门）

**每条链迁移必须给出「新旧同输入 → 同输出」的证据，落为测试。**

不是「看起来一样」，是可执行的断言。B1 已经建立了范式，
八项等价性证据在 `tests/test_b1f_equivalence.py`：系统提示、上下文注入、
压缩、权限闸门、流式、工具事件、历史顺序、错误路径。

C1 三条链的等价性证据清单：

| 链 | 必须证明的等价项 | 参照 |
|---|---|---|
| **study** | ① 轮次结果 `RoundOutcome` 九字段等价<br>② `agent_outputs` 键集合与内容等价（researcher + 4 exec + 4 eval）<br>③ `study_phase` 事件序等价<br>④ HITL 暂停六字段等价 + payload 七字段<br>⑤ 恢复从 `claims` 续跑、不重跑 researcher/novelty<br>⑥ 预算入账等价 | `test_research_preset.py`（已有 graph vs legacy 对照）<br>`test_hitl_gate_graph.py` |
| **swarm** | ① `SwarmResult.agent_results` 逐 agent `status/output/error` 等价<br>② 分层屏障语义（层内并行、层间等待）<br>③ 预算三维记账等价（含 token 维度——旧代码此项未执行）<br>④ `skip`/`retry` 分支语义<br>⑤ 层钩子调用序等价 | `test_agent_loop_swarm_parity.py`（16 条，已有）<br>`test_v05_unit_runtime_context.py` |
| **workflow** | ① 节点 `AgentExecutionResult` 信封等价<br>② 段门 `awaiting_approval` → `approve/reject` → replan<br>③ planner 动态节点（`plan_*`）生成与拓扑序<br>④ 连续失败 2 次停止规则<br>⑤ `max_segments` 重规划上限 | `test_workflow_segment_loop.py`（20 条）<br>`test_workflow_node_types.py` |

**判定方式**：新测试必须能在**旧路径仍然可用**时同时跑两条路径并比对。
做不到这一点的项（例如旧路径已删），必须显式记录为「无法验证」，不许默认为通过。

### 2.2 门二：测试集合不缩水

```
D:\tools\python312\python.exe -m pytest tests --collect-only -q \
    -m "not llm and not e2e and not slow" > collect.log 2>&1
D:\tools\python312\python.exe scripts/c1_inventory.py verify collect.log
```

冻结清单：`tests/baselines/pr-lane-nodeids.json` = **9822 条 node id**
（与 pytest 自身统计逐条一致，commit `bb6c65a7`）。

- exit 0 → 无测试消失
- exit 1 → 有测试消失，打印文件与数量

**允许缩水，但必须是显式的**：删测试的提交必须同一提交内重新冻结清单
（`--allow-shrink` 用于该提交），让 diff 同时展示「删了什么」和
「覆盖去哪了」。审阅者看到的是两半，不是一半。

### 2.3 门三：失败集合不扩张

```
D:\tools\python312\python.exe -m pytest <自己的子集> -q --tb=no > my.log 2>&1
D:\tools\python312\python.exe scripts/c1_gate.py my.log --files <自己的前缀>
```

判定：`当前失败集合 ⊆ 基线失败集合`，**按 node id 比对**。

`--files` 同时收窄两侧（已修）：不加它时，窄范围运行会把 96 条未运行的
基线项误报为「现在通过了」。

---

## 3. 分链验收细则

### 3.1 study 链（W-S）

**不可变更项**（契约 §3，改动即破坏已落库审批单）：

- 暂停产出六字段：`round / run_name / paused_for_approval / study_id / interrupt_id / hypothesis`
- 持久化 payload：`type / study_id / round_num / hypothesis / predicted_affected / researcher_output / message`
- 恢复只读三键，`start="claims"`

**新增硬约束（本轮确立）**：

> 任何 `claims`/`execution`/`evaluation` 节点新增读取一个 state 键，
> 必须同一提交内把该键加入暂停 payload，并补一条「暂停→恢复」测试。

理由：恢复路径不从 graph 起点跑，缺失的键不会有任何报错，
只会以 `{}`/`[]` 静默参与回测——产出错误指标而不崩溃，是最坏的一类 bug。

**范围裁定**（B1-e 已论证，不重复）：`inject()`、hypothesis 自动创建不移植。

**验收子集**（44 文件 / 692 测试）：
```
test_role_factory  test_spawn_agent  test_hitl  test_study
test_research_preset  test_dag_planner  test_phase_engine
test_runner_  test_autoresearch
```

### 3.2 swarm 链（W-W 前半，先做）

**为什么 swarm 先做**：DAG 直接映射 `add_node`/`add_edge`，
缺口是 6 个适配器能力（分层并行、钩子桥、skip/retry 重入、
检查点续跑、AgentExecutor 适配、预算映射），不是结构性改造。
workflow 需要动态子图 + 段门重规划，风险高一个量级。

**必须补齐的旧代码缺陷**（迁移是修它们的唯一时机）：

| 缺陷 | 位置 | 处置 |
|---|---|---|
| `max_retries` 从未被执行 | `dag_config.py:26-29` vs `executor.py:321-345` | 迁移时实现，或显式删除字段 |
| `budget_token` 从未被检查 | `swarm/runtime.py:236-245` | 迁移时接入 `RunBudget` |
| `redirect` 分支未实现 | `swarm/runtime.py:703-706` | 迁移时实现，或标记 `NotImplementedError` |
| `max_retries` 泄漏进 prompt | `_NON_PROMPT_KEYS` 缺失 | 迁移时加入 |

**判定**：这四项在迁移后必须有明确处置（实现 or 显式不支持）。
「保持原样」不可接受——那等于把静默缺陷带进新引擎。

### 3.3 workflow 链（W-W 后半）

**结论先行**：workflow 只有 **25-35% 能用现有 GraphEngine 表达**。
三个结构性缺口必须先解决，否则不要开始：

1. **动态子图**：planner 在运行时产出 `plan_*` 节点（`executor.py:245-294`），
   而 GraphEngine 在 `add_node` 阶段固定节点集（`graph_engine.py:124-131`）。
2. **段门 + 重规划**：审批切点与 `approve/reject → replan` 是命令式循环
   （`executor.py:96-121, 342-383`），不是边。
3. **扇入信封**：`AgentResult` + 上游归约 + 逐节点持久化
   （`node_types.py:207-267`、`store.py:253-267`）。

**允许的替代结论**：如果评估后认为 workflow 迁到 GraphEngine 的收益
不足以覆盖上述改造，**明确记录「workflow 保留现有执行方式」也是合法验收**。
架构目标是「GraphEngine 是默认 runtime」，不是「唯一 runtime」。
暂不迁移必须写进文档并说明触发条件，不能沉默。

**必须在迁移前修的空声明**（否则迁完还是假的）：
`node_timeout_seconds` / `node_max_retries`（`definition.py:51`）声明存在
但零读取；`decorators.py` 的 `with_retry/with_timeout/with_validation`
零调用方。

---

## 4. 全局验收（阶段 3，协调者执行）

| # | 项 | 判定 |
|---|---|---|
| G1 | PR 道全量 | 失败集合 ⊆ 冻结基线，或下降且每条下降有对应提交说明 |
| G2 | 测试集合 | `c1_inventory verify` exit 0（或 refreeze 与删除同提交） |
| G3 | 架构门禁 | `test_architecture_gates.py` 13 条全绿 |
| G4 | lint | `ruff check src` 干净 |
| G5 | pyright | 回归门禁 `scripts/pyright_regressions.py` 无新增 |
| G6 | 三链等价 | §2.1 清单逐项有对应测试 |
| G7 | 遗留声明 | §3.2 四项缺陷有明确处置 |
| G8 | 文档 | 每条链一份迁移记录，含范围裁定与未做项 |

**G1 的严格表述**：不是「失败数从 100 降到 X」，而是
「当前失败集合是冻结基线的子集，且删除的每一条都能在 git log 里找到
对应提交与理由」。

---

## 5. 反向验收：什么情况算失败（常见误判）

| 现象 | 是不是通过 | 理由 |
|---|---|---|
| 失败数从 100 降到 40 | **不一定** | 可能是删了测试，跑 G2 |
| 新测试全绿 | **不算** | 新测试可以测无关的东西，看 G6 清单 |
| 「旧代码已经没人调用了」 | **不算** | 必须在旧路径可用时证明等价 |
| workflow 决定不迁 | **可以是** | 但必须写文档 + 触发条件（§3.3） |
| 轻量链迁完、重量链不动 | **可以是** | 分阶段验收，每阶段独立成立 |
| AgentLoop 还剩构造点 | **不算失败** | 见 §6 |

---

## 6. 终局判定：AgentLoop 什么时候算退役

今天是 **3 个真实构造点**：

| 站点 | 归属 | 退役条件 |
|---|---|---|
| `chat_loop.py:129` | chat 旧分支 | P5-1c（已挂起，`new_entry_enabled()` 默认 True） |
| `role_factory.py:144` | study + workflow | study 迁完 + workflow 有结论（迁或明确不迁） |
| `executor.py:347` | swarm + subagent | swarm 迁完 |

**Founder 判定条件（写入架构门禁测试）**：

> `AgentLoop` 的构造点 ≤ 1，且剩余的那一个属于已挂起的 P5-1c。

不到这个条件就宣布「C1 完成」是不诚实的：架构图上写着「GraphEngine
唯一 runtime」，代码里还有三处在跑旧引擎。

**但允许部分完成**：study 迁完、workflow 明确不迁，
则构造点降到 2（role_factory 的 workflow 分支 + executor 的 swarm 分支），
此时验收结论应表述为「study 链完成，workflow 明确保留，swarm 待办」，
而不是「C1 完成」。

---

## 7. 交付物清单（每个 worker）

每个 owner 提交时必须包含：

1. **设计文档** `docs/stage-c1-<owner>-<topic>-20261028.md`
   - 范围裁定（做了什么 / 明确不做什么 / 为什么）
   - 改动面（文件级）
   - 等价性证据链（对应 §2.1 的哪几项）
2. **代码**
3. **等价性测试**（§2.1 对应项）
4. **三条命令的输出**（贴进 PR 描述或提交信息）：
   ```
   pytest <子集> -q --tb=no            → c1_gate.py 的结果
   pytest --collect-only               → c1_inventory.py verify 的结果
   ruff check src                       → 干净
   ```

---

## 8. 冻结的基线数字（本机实测，复现命令见各节）

| 项 | 值 | 来源 |
|---|---|---|
| PR 道失败 | 93 failed + 7 error = 100 | `tests/baselines/pr-lane-failures.json` |
| PR 道通过 | 9638 | 同上 |
| PR 道收集 | 9822 / 12895（3073 分道排除） | `tests/baselines/pr-lane-nodeids.json` |
| 墙钟 | ~370s | 实测 |
| AgentLoop 构造点 | 3 | `chat_loop.py:129` / `role_factory.py:144` / `executor.py:347` |
| 架构门禁 | 13 条 | `test_architecture_gates.py` |

**解释器是 `D:\tools\python312\python.exe`，不是默认 `python`。**
**worktree 必须设 `PYTHONPATH=<worktree>\src`，否则导入主检出代码**（契约 §7.1）。
