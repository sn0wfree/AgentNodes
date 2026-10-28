# W-P 设计：编排层 / 执行原语的分工 — 2026-10-28

> 上游：`docs/stage-c1-team-contract-20261028.md`、
> `docs/stage-c1-acceptance-20261028.md`
>
> **本文记录一次方向修正**：原计划「薄 react 节点替换 AgentLoop」被推翻，
> 改为「GraphEngine 编排 + AgentLoop 执行单 turn」。推翻的理由在 §2。

---

## 0. 结论

| 层 | 职责 | 结论 |
|---|---|---|
| **GraphEngine** | 编排：节点 / 边 / 条件路由 / 中断 / 预算树 / fanout | **唯一编排 runtime** |
| **AgentLoop** | 单个 agent turn 的执行原语 | **保留**，不替换 |
| react 节点 | chat 的单轮工具循环 | 保留，**不承担** study/workflow 的执行 |

架构目标从「GraphEngine 是唯一 runtime」精化为
**「GraphEngine 是唯一编排 runtime」**——这两者不同，前者会要求删掉
AgentLoop，后者不会。

---

## 1. 为什么原方案（薄 react 替换）不成立

### 1.1 实测的能力差

核查 `loop_tools.py` 后发现 react 节点缺四项 AgentLoop **无条件**提供的能力：

| 能力 | 位置 | 对 react 的影响 |
|---|---|---|
| 工具结果截断 30k 字符 | `loop_tools.py:214` | **真回归**：长会话 context 膨胀 |
| 工具瞬时错误重试 ×2 | `loop_tools.py:144-159` | **真回归**：失败率上升 |
| 单调 deny guard 检查 | `loop_tools.py:115` | 潜在（当前无生产注册方） |
| 只读工具并行批处理 | `loop_tools.py:276-332` | 疑似死代码（无调用点） |

前两项无条件生效，不是可选特性。原设计稿只列了 3 个缝隙
（LLM 错误、迭代超时、无进展检测），漏掉了这四项。

### 1.2 两次低估 = 判断依据不足

W-P 前后两版设计都低估了差距。这意味着对 `loop.py`（1161 行）+ 4 个 mixin
的实际行为理解不完整。在这样的认知状态下逐个补能力，等于用生产代码试错。

### 1.3 与验收标准冲突

`docs/stage-c1-acceptance-20261028.md` §0 写明：

> 必须在旧执行体还能跑的前提下，证明新执行体行为等价；然后才允许删除旧执行体。

原方案直接把 `run_agent_via_llm` 切到 react，**没有先证等价**。
它违反了刚写下的标准。

---

## 2. 新方案：分工

```
WorkRequest
    ↓
SessionController / scheduler
    ↓
GraphEngine                     ← 编排层（唯一）
  ├── node: researcher ────────→ agent_node() ──→ AgentLoop.run()   ← 执行原语
  ├── node: novelty    (纯 python)
  ├── node: gate       (GraphInterrupt)
  └── node: execution  ────────→ agent_node() ──→ AgentLoop.run()
```

- **GraphEngine 管**：拓扑、条件路由、HITL 中断、预算树、fanout 并行、
  max_hops、事件（`node.started` / `node.completed` / `graph.*`）。
- **AgentLoop 管**：一次 agent turn 内的 LLM↔工具循环、工具结果截断、
  瞬时错误重试、guard 检查、只读并行、审批请求、无进展检测、claims 捕获。
- 两者是**层叠关系**，不是竞争关系。

---

## 3. `agent_node()` 的最终形态

```python
def agent_node(
    role, workspace_path, strategy_name, task, *,
    llm_config=None, client=None, max_iterations=8,
    tools_override=None, loop_strategy=None,
    iteration_timeout_s=None, sink=None, state=None,
) -> dict: ...
```

返回 AgentResult 形状的 dict（不 import `AgentExecutionResult`，避免
W-P 依赖 W-W 的文件）：

```
{"status", "output", "error", "iterations", "tool_calls_made",
 "elapsed_s", "summary", "metrics", "artifacts"}
```

内部实现：`build_agent_loop(...)` → `loop.run(task)` → 映射为 dict。
构造失败（prompt 缺失）仍抛 `RuntimeError`——那是安装损坏，不是运行错误。

### 3.1 `build_agent_loop` 新增两个透传参数

`iteration_timeout_s`、`on_event`：AgentLoop 早已支持（swarm parity 时期加的），
只是这个工厂从未转交。W-W 迁移 swarm 时依赖 per-node timeout，必须通。

### 3.2 `run_agent_via_llm` 回退

恢复为直接走 `build_agent_loop` + `loop.run`，**输出契约逐字节不变**：

- 成功 → answer 字符串
- 失败 → `{"error", "iterations", "tool_calls_made"}` JSON

study / workflow 的调用点一行不改。

---

## 4. react 节点：全部回退

W-P 期间给 react 加的三项（`on_llm_error` / `iteration_timeout_s` /
`no_progress_window` 占位）全部撤除。原因：

| 参数 | 撤除理由 |
|---|---|
| `on_llm_error`（默认 `"result"`） | **改变 chat 行为**：LLM 失败被节点吸收后，`SessionController` 会看到正常返回并报 `ok=True`，而 `test_b1f_equivalence` 要求 `run.settled{ok:False}` |
| `iteration_timeout_s` | chat 不需要；study/swarm 走 AgentLoop |
| `no_progress_window` | 占位字段，无人消费，留着误导 |

react 继续只服务 chat 单轮，其 docstring 已声明「显式推迟」的能力边界。

---

## 5. 架构门禁（替换原判据）

| 门禁 | 判据 | 理由 |
|---|---|---|
| `test_domain_layers_use_the_agent_factory_not_raw_agentloop` | study/workflow/swarm **不得直接 new** AgentLoop，必须经 `role_factory` | 绕过工厂会跳过提示装配、白名单、预算与权限接线 |
| `test_graph_engine_is_the_only_orchestration_runtime` | 除 `graph_engine.py` 外，`core/` 里不得 import `StateGraph` | 编排只能有一个 |

**原判据作废**：`AgentLoop 构造点 ≤ 1` 不再成立——选项 B 下
AgentLoop 有 3 个构造点（chat_loop / executor / role_factory），
这是设计意图，不是债务。

---

## 6. 对验收标准的修订

`docs/stage-c1-acceptance-20261028.md` §6 需同步：

| 项 | 原表述 | 修订后 |
|---|---|---|
| 终局判定 | AgentLoop 构造点 ≤ 1 | GraphEngine 是唯一编排 runtime；AgentLoop 作为执行原语保留 |
| 迁移含义 | 把执行体换成 react | 把**编排**交给 GraphEngine（study 已做），**执行**保持 AgentLoop |
| B2/B3 的工作量 | 迁移执行体 | **大幅缩小**：只需把各链的编排接入 GraphEngine，执行体不动 |

这是本轮最重要的发现：**B2/B3 的工量被大幅高估了**。study 的编排已在
GraphEngine 里（`graph_preset.py`），执行体本来就是 AgentLoop——它其实
**已经符合选项 B**。swarm/workflow 需要的是把各自的调度器换成
GraphEngine 编排，不需要重写 agent 执行。

---

## 7. 验证

| # | 证据 | 测试 |
|---|---|---|
| E1 | `agent_node` 包装 AgentLoop，不替换 | `test_agent_node_builds_an_agentloop` |
| E2 | `run_agent_via_llm` 三字段 error 契约 | `test_role_factory.py`（18 条全绿，仅恢复 patch 目标） |
| E3 | 结果 dict 字段齐全 | `test_success_maps_to_agent_result_shape` |
| E4 | 运行期异常变 error 结果 | `test_run_exception_becomes_error_result` |
| E5 | 节点旋钮（timeout/on_event）透传 | `test_node_knobs_are_forwarded` |
| E6 | AgentLoop 四项能力仍在 | `test_agentloop_owns_the_four_capabilities_react_lacked` |
| E7 | 域层不绕过工厂 | `test_domain_layers_use_the_agent_factory_not_raw_agentloop` |
| E8 | 编排唯一 | `test_graph_engine_is_the_only_orchestration_runtime` |

回归门：全量 PR 道失败集合 ⊆ 冻结基线（100 条）。

---

## 8. 明确不做

| 项 | 裁定 |
|---|---|
| 删除 AgentLoop | 不做。它是执行原语，不是债务 |
| 把 AgentLoop 的能力搬进 react | 不做。已证明会漏、会退化 |
| 统一 react 与 AgentLoop 的消息语义 | 不做。两者服务不同场景 |
| `SessionController` 改用 `agent_node` | 不做。chat 的 react 路径已等价（B1 八项证据） |

---

## 9. 遗留与后续

| 项 | 状态 |
|---|---|
| `chat_loop.py:129` AgentLoop 构造点 | P5-1c 待删（已挂起） |
| `executor.py:347` AgentLoop 构造点 | swarm 保留（选项 B 下合法） |
| `_restrict_registry` / `_count_tool_calls` / `_first_two_sentences` | W-P 期间新增的辅助函数随方案回退删除 |
| `read_roots` 未透传（预存 bug） | 保持原样，记录待 W-S 决定 |
