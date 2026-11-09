# V3 · 双执行路径收敛决策

> **上位**：`phase1-completion-verification-plan-20261109.md` §1 V3。
> **输入**：V2 真实 LLM 端到端结果（`v2-live-llm-e2e-20261109.md`）。
> **性质**：**决策文档**（含推荐与代价）。本文件不实施，实施见 V4。
> **计划要求**：「不预设结论，由 V2 数据说话」。
> **实施状态**：**V3-a 已落地**（`task_signature` 进 `LoopSpec.ContextSpec` +
> `_effective_tools` 分支 + 6 条测试）；V3-b / V3-c 未开始。

---

## §0 结论

**统一到路径 A（`AgentLoop`）**，分三步：

1. **V3-a**：给路径 A 补上它唯一缺的那项能力（`task_signature` 工具裁剪）
2. **V3-b**：把 `session_controller` 的节点从 `make_react_node` 换成
   `AgentLoop`（保留 GraphEngine 编排语义）
3. **V3-c**：删除 `react.py`（282 行）

**为什么不是反过来（补 `react.py`）**：见 §2 的能力差集——
路径 A 独有 10 项能力、路径 B 独有 1 项。补齐 1 项 vs 补齐 10 项。

---

## §1 现状（实测）

### 1.1 两条路径的消费者

| | 路径 A：`AgentLoop` | 路径 B：`make_react_node` |
|---|---|---|
| 定义 | `kernel/agent/loop.py`（1,408 行）| `kernel/runtimes/nodes/react.py`（282 行）|
| 直接构造点 | **22 处 / 13 文件** | 3 处 |
| 入口 | `build_chat_agent_loop`（external/api）/ `build_agent_loop`（role_factory）| `session_controller` + `presets/fanout` |
| `.arun` 调用 | 1 处（`service.py`）| — |
| `.run` 调用 | **41 处 / 26 文件**（含 git/storage 的误匹配，实际约 20 处）| — |
| **生产 web chat** | — | ✅ **是**（`singleton.py` 默认 `build_default_registry()`，`session_controller` 走 react 节点）|
| CLI / TUI / evals / scheduled | ✅ | — |

**关键事实**：用户日常用的 web chat 走的是**路径 B**，而路径 B 是能力最弱的那条。

### 1.2 能力差集（代码 grep 实测，非印象）

| 能力 | 路径 A | 路径 B |
|---|---|---|
| 权限闸（permission evaluator）| ✅ | — |
| hard_deny | ✅ | — |
| no-progress 检测 | ✅ | — |
| approval gate | ✅ | — |
| guardrails | ✅ | — |
| hooks | ✅ | — |
| deciders（四值 Decision）| ✅ | — |
| skeleton 状态机 | ✅ | — |
| claim validation | ✅ | — |
| trace writer | ✅ | — |
| **tool_set 裁剪（task_signature）** | — | **✅** |
| 压缩 / 预算 / 流式 / goal 注入 / steering inbox / strategy steps / 子 agent | ✅ | ✅ |

**统计**：两边都有 7 项；**只有 A 有 10 项**；**只有 B 有 1 项**；两边都没有 0 项。

---

## §2 为什么统一到 A（而不是补 B）

### 2.1 数量差异是压倒性的

补 A 的 1 项（`task_signature`）vs 补 B 的 10 项。后者里至少 3 项是
结构性重写而非增量：

| 能力 | 补到 B 的难度 |
|---|---|
| deciders / skeleton 状态机 | **重写整个循环体**（282 → 600+ 行）|
| hooks | 需要 hook 生命周期接入（B 没有 hook 概念）|
| no-progress + approval gate | 需要引入阻塞等待模型（B 是纯同步 for 循环）|
| permission / hard_deny | B 已有 `ctx_factory → registry.execute(ctx=)`，**这条其实有**（见 §2.2）|
| guardrails / claim validation / trace | 中等 |
| **task_signature** | B 已有 |

### 2.2 一个诚实的修正：权限闸两条路径都有

grep 显示路径 B "没有"权限闸，但**这是 grep 的假阴性**：
`react.py` 通过 `ctx_factory` 注入 `ToolContext`（含 `permission_evaluator`），
再由 `registry.execute(ctx=)` 内部调 `enforce_permission_sync`。

`test_react_node_capability.py` 已实测：路径 B 的 H2/H3 hard_deny **是生效的**。

所以真实差集是 **9 项 vs 1 项**，但结论不变。

### 2.3 V2 的额外证据

V2 抓到两个 bug，都在路径 A 上，且都已修：
- assistant 消息丢失（skeleton）
- 直接构造时权限闸不接线

**这两个 bug 反而支持统一到 A**：修好之后 A 是唯一被真实模型端到端验证过的
路径（5 场景全过）。路径 B 从未跑过真实 LLM 端到端（`test_react_live.py`
需要 `QNODES_LIVE_LLM=1`，本次未跑）。

**风险不对称**：统一到已验证的路径，风险低于留在未验证的路径。

---

## §3 实施方案（三步，每步独立可回滚）

### V3-a：给路径 A 补 `task_signature` 工具裁剪

| 项 | 内容 |
|---|---|
| 现状 | `registry.get_definitions_for(task_signature)` 已存在（`tools.py:875`），但 `AgentLoop._effective_tools` 不传 `task_signature` |
| 改动 | `AgentLoop` 加 `task_signature: str = ""` 构造参数（进 `LoopSpec` 的 `ContextSpec`）；`_effective_tools` 改调 `get_definitions_for(self._task_signature)` |
| 等价性 | 空字符串时 `get_definitions_for` ≡ `get_definitions`（其 docstring 明确保证），所以默认路径**零变化** |
| 验证 | 新增 `test_task_signature_trimming.py`：非空时工具被裁剪、空时全量 |
| 工作量 | 0.3 天 |
| **状态** | ✅ **已完成**（全量回归 7051 passed / 0 failed；`batch_gate` ALL GREEN）|

### V3-b：`session_controller` 换节点

| 项 | 内容 |
|---|---|
| 现状 | `session_controller.py:424-438` 用 `engine.add_node("react", make_react_node(...))` |
| 改动 | 新增 `make_agent_loop_node(loop)` 适配器：把 `AgentLoop.arun()` 包成 GraphEngine 节点（state 进 → messages/answer 出）。`session_controller` 改用它 |
| 保留 | GraphEngine 的多节点编排语义、Ticket/fence 流程、SSE sink 全部不动 |
| 风险 | **中**。react 节点是同步的（GraphEngine 同步驱动），`AgentLoop` 是 async——适配器需要 `_run_coro_in_sync` 桥（已存在）|
| 验证 | `test_session_controller_*` 全过 + 手工跑 web chat 一次 + E1–E5 |
| 工作量 | 1 天 |

**关键前置**：V3-b 前必须确认 GraphEngine 的节点调用约定
（是否允许节点内部起事件循环）。若不允许，适配器要用线程桥。

### V3-c：删除 `react.py`

| 项 | 内容 |
|---|---|
| 前置 | V3-b 全绿且观察一个版本 |
| 改动 | 删 `kernel/runtimes/nodes/react.py`（282 行）+ 更新 `nodes/__init__.py` / `runtimes/__init__.py` |
| 同时 | `presets/fanout.py` 也改用 `AgentLoop`（它现在用 `make_react_node`）|
| 收益 | 消除双实现；能力不再漂移 |
| 工作量 | 0.5 天 |

---

## §4 若用户选择「保留双轨」

如果倾向不迁移，**必须**做这三件事，否则漂移会持续：

1. **补 B 的 9 项能力**（至少 guardrails / hooks / no-progress 三项安全相关）
2. **新增路径对等测试**（像 `test_engine_parity.py` 那样双路径逐字段比对），
   否则下次漂移仍然无人发现
3. **明确"哪条路径是生产"**并写进架构图——当前架构图没有标

**代价**：两套实现永久并存，每次改能力都要改两处。本仓库已有两次
"两条路径能力漂移"的实证（批次 13 的 6 个缺口 + V2 的 2 个 bug）。

---

## §5 建议的执行顺序

```
V3-a (0.3d)  →  V3-b (1d)  →  观察一个版本  →  V3-c (0.5d)
```

**总 1.8 天**，V3-c 可延后。

**立即可做**：V3-a（风险极低，且它是 V3-b 的前置）。

---

## §6 明确不做

- 不动 GraphEngine / Ticket / fence（编排层与执行层分离是对的）
- 不动 `session_controller` 的权限接线（它传 `permission_evaluator` 给
  `ctx_factory`，迁移后要相应传给 `AgentLoop`，但语义不变）
- 不删 `make_llm_node`（无工具纯 LLM 节点，用途不同）
- 不把 `AgentLoop` 塞进 kernel/runtimes（它在 kernel/agent，归属正确）

---

## §7 开放问题的答案（已核实，不留猜测）

| # | 问题 | **答案** |
|---|---|---|
| 1 | GraphEngine 节点是否允许内部起事件循环？ | **否**。`graph_engine.py:37` 定义 `NodeFn = Callable[[dict], dict]`——**纯同步函数**。所以 `AgentLoop.arun()`（async）必须用 `_run_coro_in_sync`（`runtime/sync_bridge.py`，已存在且成熟）桥接 |
| 2 | `session_controller._max_iterations`（默认 10？）与 `AgentLoop` 语义是否一致？ | **构造器默认是 1**（`session_controller.py:57`），但**生产路径会覆盖**：`chat.py:259` 取 `LLMConfig.max_iterations`（默认 **50**），只有 **plan 模式**才强制为 1（`chat.py:265`）。所以 build 模式生产实际是 **50 轮**。迁移时需保持这个语义（plan=1 / build=cfg.max_iterations）|
| 3 | `presets/fanout.py` 的分支并发语义能否保持？ | **可以**。fanout 走 `branch_worker(payload, branch_state)`（同步 callable），与节点契约一致。换成 `AgentLoop` 只需在 `branch_worker` 内部用同一个 `_run_coro_in_sync` 桥 |
| 4 | web chat 的 SSE 是否依赖 `react.py` 特有事件？ | **是，10 个事件**：`budget.exhausted` / `context.compact_failed` / `context.compacted` / `stream.failed` / `text_delta` / `thinking_delta` / `thinking_done` / `tool.completed` / `tool.ctx_failed` / `tool.started`。其中 `tool.started`/`tool.completed`/`tool.ctx_failed` 是 **react.py 自己的命名**，`AgentLoop` 用的是 `tool_call`/`tool_result`。**前端兼容层是 V3-b 的必做项** |

### §7.2 由答案推出的两个新增工作项

**W1 · 事件名映射层**（Q4 的后果）

`AgentLoop` 发 `tool_call` / `tool_result`，`react.py` 发
`tool.started` / `tool.completed` / `tool.ctx_failed`。前端监听的是后者。

**消费者实测**（全仓 grep，src + webui/frontend + tests）：

| 事件 | 消费者数 | 主要消费点 |
|---|---|---|
| `tool.started` | 10 | `interfaces/api/routers/chat.py`（SSE 路由）、`event_vocab.py` |
| `tool.completed` | 7 | 同上 |
| `tool.ctx_failed` | 1 | 仅 react.py 自身 |
| `text_delta` | 55 | `chat.py` / `event_vocab.py` / `slash_common.py` |
| `thinking_delta` / `thinking_done` | 24 / 25 | `chat.py` / `backfill_event_log.py` / `part_accumulate.py` |
| `context.compacted` / `context.compact_failed` | 2 / 2 | react.py + 测试 |
| `budget.exhausted` | 3 | react.py + 测试 |
| `stream.failed` | 4 | `chat.py` / `nodes/llm.py` |

**结论：不能只改前端**——`chat.py`（SSE 路由）与 `event_vocab.py`
（事件词表）都在消费。迁移必须在**事件发射层**做映射
（`tool_call → tool.started`、`tool_result → tool.completed`），
而不是在消费端逐个改。

`tool.ctx_failed` 只有 react.py 自身引用（无外部消费者），映射时可省。

**W2 · `max_iterations` 语义保持**（Q2 的后果）

生产 build 模式已经是 **50 轮**（`LLMConfig.max_iterations`），
plan 模式 1 轮。迁移到 `AgentLoop` 后必须**保持同一套取值逻辑**
（而不是用 `AgentLoop` 自己的默认值），否则：
- plan 模式可能跑 50 轮 → 成本与行为都变
- build 模式可能跑别的值 → 与今天不同

好消息：Q2 的答案是"两边已经一致"（都最终由 `LLMConfig.max_iterations`
或调用方显式值决定），所以这是**接线问题**不是**语义问题**——
适配器里把 `max_iterations` 透传即可。
