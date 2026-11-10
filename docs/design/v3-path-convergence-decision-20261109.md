# V3 · 双执行路径收敛决策

> **上位**：`phase1-completion-verification-plan-20261109.md` §1 V3。
> **输入**：V2 真实 LLM 端到端结果（`v2-live-llm-e2e-20261109.md`）。
> **性质**：**决策文档**（含推荐与代价）。本文件不实施，实施见 V4。
> **计划要求**：「不预设结论，由 V2 数据说话」。
> **实施状态**：**V3-a / V3-b / V3-c 全部落地**——双执行路径收敛完成，
> 唯一执行引擎是 AgentLoop（2026-11-10）。

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

> **⚠ 生产实跑修正（2026-11-09 冒烟，见 §8）**：上面这行**说错了方向**。
> 前端 Composer 实际调的是 **`/chat/send_async`** → SessionService →
> `build_chat_agent_loop` → **路径 A（AgentLoop）**——生产 web chat 的
> 执行引擎**本来就是 A**（这也是 §2.3 "A 被真实端到端验证过"的由来）。
> 路径 B（gateway `/send`）是 P5-1c ④ 起的新统一入口（receipt 契约、
> E1=C 消费者架构的载体），前端尚未切换、能力最弱。V3-b 换的正是这条
> gateway 路径的引擎；V3-c 删 react.py 后两路归一才真正完成。

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
| **状态** | ✅ **已完成**（真实模型端到端过 + 全量回归绿；详见下方落地实录）|

#### V3-b 落地实录（2026-11-09，与原方案的偏差全部有实测依据）

**落点**：新模块 `kernel/runtimes/nodes/agent_loop_node.py`
（`make_agent_loop_node` / `wrap_async_capable_client` /
`make_loop_event_sink`）；`session_controller._build_engine` 的默认图
换成 AgentLoop，**节点名沿用 `"react"`**（`test_react_node_receives_assembled_prompt`
钉住节点名，消费方按名路由）。

**Q4/W1 的实测修正——事件词表方向反了**：原方案假设「AgentLoop 发
`tool_call`/`tool_result`，前端听 react 的 `tool.started`/`tool.completed`，
所以要做映射层」。落地时逐消费者核对（`event_v2.EventType` 注释、
`projection_handlers.py:284`、前端 `messageHandlers.ts`/`textHandlers.ts`）
发现：**产品词表本来就是 AgentLoop 的**——
- `EventType` 注释明示 "Names match the existing SSE event names emitted
  by AgentLoop"；react 的 `tool.started`/`tool.completed` 根本不在
  事件注册表里（projector 视为 unknown 丢弃）；
- 前端 `toolCall` handler 读 `message_id/id/name/arguments`——正是
  AgentLoop `tool_call` 的 payload；react 经旧 bridge 翻译后的 payload
  `{run_id, tool, iteration}` **缺 id/name/arguments**（前端拿到
  undefined）；
- 前端 `textDelta` 要求 `text_id` 路由，react 的 `text_delta{run_id,delta}`
  没有 `text_id` → chunk 被丢弃（只剩 legacy 全局缓冲兜底）。

所以 V3-b 的真实工作是**删翻译而不是加翻译**：`chat.py` bridge 的映射表
追加 `tool_call`/`tool_result` 直通 + AgentLoop 的 3-step 流式词表
（`text.started/text.ended/thinking_start/thinking_end`）进直通表；
react 词表映射保留到 V3-c（fanout 仍会发）。另修两个顺带发现：
- `message_id_holder` 原来到 `run.answer` 才置 → 整个流式阶段的 chunk
  全部无 `message_id`。现在 `run.started` 就置（run_id 即 assistant
  message id，run.answer 落库用的同一个）；
- `llm_request`（system_prompt/tools_schema）经
  `make_loop_event_sink` 剥大字段——react 从不发它，不剥会把事件日志
  撑大三个量级（`event_vocab.strip_llm_request_fields` 既有约定）。

**契约对等的关键决定**（每条都有对应测试或注释）：

| 决定 | 理由 |
|---|---|
| client 整体注入 `loop.client = caller 的 client` | AgentLoop 构造器自建 client；生产要复用 ModelHub 实例，等价性测试的脚本假 client（只有 `chat`）必须原样可用。骨架引擎要 `achat`/`astream` → `wrap_async_capable_client` 原生四件套直通、缺的用 `asyncio.to_thread` 桥补齐 |
| `injectors=[]` + `enable_goal/memory_injection=False` | pre-run 注入契约不变：仍由 controller `_inject_context` 经注入的 hub 做（P5-4）。否则 loop 自带 injector 链会与 hub 双注入（goal/todos 各来一份） |
| `approval_timeout=30.0` + `approval_on_timeout="continue"` | web chat 没有 loop 级审批提问通道（`agent_approval_requested` 不进 SSE bridge 直通表）；默认 1800s 阻塞等一个永远不来的批准会挂住消费者线程。30s 后按 continue 继续——终态与 react（无此闸，循环到 max_iter）一致 |
| compact_config 经 `apply_compaction_overrides` 传入 loop | 保持装配期 harness 覆盖语义（原 `_build_compactor` 的行为）；压缩器本体由 loop 内部构造（同一 `make_compactor`），`_build_compactor` 删除 |
| 权限闸 `setattr` 挂载（chat_loop 同型） | `ToolContext` 由 `loop._build_tool_kwargs` 无条件构造，ctx 比 react 的 `ctx_factory` 更全（workspace/emit_event/data_store/sandbox 一并就位） |
| `state["usage"]` 保持 RunBudget.snapshot 形状 | fanout join 与测试读它；数值来自 loop 的 A1 预算四字段 |
| `result.error` 非空 → 节点重新抛出 | react 语义：异常冒泡 → controller 捕获 → `run.failed{error}`（`DefaultLLMCallStep` 已把任意 LLM 异常折叠进 `result.error`） |

**顺带的行为变更（有意，均有注释与测试）**：

1. **todo 归属会话从条件变无条件**：react 时代「无 hub/workspace/权限
   就无 ctx → todowrite 落共享 default 桶」；AgentLoop 的 ToolContext
   无条件携带 session_id → 永远归属会话桶。这是 B-c 想要的方向性结果
   （`test_b5_wiring` 改名 + 断言收紧到物理会话 id 精确匹配）。
2. **压缩在默认构造下激活**：`compact_config=None` 时 loop 回落
   `config.compact_config`（与 chat_loop 取值序一致）；且压缩事件获得
   L4 持久化（react 的 compactor 没有 persist 回调）。app.py 已注册
   persister，生产可用；持久化失败由 `make_compactor` 回滚、不丢上下文。
3. **fake client 保真度缺口暴露**：`_ToolCallClient._Resp` 缺
   `finish_reason`、b5 的 `_RealRegistry` 缺 loop 契约方法（`get`/
   `all_tools`/`check_guards`）——都按生产形状补齐；还修了
   `_clean_todos` fixture 只清两个桶导致 e2e 断言被残留桶假绿的隔离
   缺陷（断言改为物理会话 id 精确匹配后，残留桶再也骗不过）。

**已知遗留（记入 W3，非本批）**：web chat 的 assistant 占位消息要等
`message_received` 事件带 `assistant_message_id` 才会 seed，而
`_send_via_gateway` 的 emit 没带这个字段——流式 chunk 即使有
message_id 也落不进消息（今天也一样，`appendStreamingText` 兜底）。
修它要动 SSE 语义（比如 run.started 时补推一条 seed），归入前端接线批。

**验证**：新增 `tests/test_agent_loop_node.py`（14 条：async 壳 3 +
节点契约 5 + controller 级 5 + 真实模型端到端 1）；b1f/b5/b1c 的
controller 级测试全部在新路径上跑绿；真实模型端到端
（`QNODES_LIVE_LLM=1`，MiniMax-M3.1-Flash，calculator 工具循环）**3.16s
通过**。

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
| **状态** | ✅ **已完成**（见下方落地实录）|

#### V3-c 落地实录（2026-11-10）

**删除**：`react.py`（282 行）、`scripts/stage1b_demo.py`（批次 9a 起因旧包名
`agentnodes.core` 已损坏，实测 ModuleNotFoundError，功能由适配器 live 测试
覆盖）；react 直测文件 4 个（`test_react_node` / `test_react_node_capability`
/ `test_p51b_streaming_tools` / `test_run_budget_integration` / `test_react_live`）。

**fanout 迁移**（`branch_worker` → AgentLoop，F2 预算树语义保真）：

| react 机制 | AgentLoop 等价 |
|---|---|
| `ReactConfig(budget=slice)` 注入 RunBudget 同一实例记账 | 入口 `budget.plan_call()`（0 切片即连坐，不起 loop）→ 运行中切片 limits 注入 `BudgetStopStep._limits`（三维 reason `budget:tokens/cost/time` 与 react **同源**——都出自 `evaluate_budget`）→ 收尾 `budget.record(usage)` 回写切片，父线程 absorb 回流 |
| `stop_reason.startswith("budget:")` 连坐判断 | `result.stop_reason or result.finished_reason`（预算耗尽时 = `budget:tokens` 等，天然匹配） |
| 分支事件带 `run_id` | AgentLoop 事件无 run_id → `branch_sink` 注入 `{"run_id": branch_id, ...}` |
| `test_branch_budget_slice_actually_used`（父账 12 tokens）| 结束 record → absorb → 父账 12 ✓ |
| 分支业务失败不连坐 | `result.error` → 重新 raise（GraphEngine run_one 捕获为 error 条目）|

**有意的行为变化**：分支消息 = `[system(默认任务模板), user]`（react 的
`system=None` 时无 system 消息）——子代理获得工具清单/工作区上下文，
代价是每分支 prompt 变长。空注册表时模板显示 "(no tools available)"。

**测试处置**：
- 改写：`test_fanout_preset`（fake 保真：`finish_reason` + registry 完整契约
  + 真工具实例；事件词表断言 → `tool_call/tool_result`；独立分支断言改
  内容集合——原顺序断言在并发线程下本就非确定，react 时代碰巧稳定）；
- 等价改写：`test_b1c_react_compaction` 的压缩时机/消息到达两条 →
  AgentLoop 版（hook `_maybe_compact`）；`test_b1f` 收敛到 controller 级
  4 条；`test_agent_loop_node` 补工具事件顺序断言（迁自 b1f react 段）；
- 删除：react 直测 5 文件（约 60 条）——语义覆盖去向：工具循环/预算 →
  fanout + node 测试；流式聚合 → loop_stream（V2 真机验证过）；权限接线 →
  `test_agent_loop_node` + `test_b1d` registry 级保留。

**验证**：V3-c 触碰面 48 条全绿；全量回归 + batch_gate 见提交信息。

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

**W1 · 事件名映射层**（Q4 的后果）——**V3-b 落地时修正了方向**

`AgentLoop` 发 `tool_call` / `tool_result`，`react.py` 发
`tool.started` / `tool.completed` / `tool.ctx_failed`。原方案假设
「前端监听的是后者」——**实测相反**：

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

**修正后的结论**：`tool.started` 的 10 个"消费者"里没有一个是最终
消费——`chat.py:553` 的 bridge 把它**翻译成** `tool_call` 再推 SSE；
`event_v2.EventType` 注册表（projector/持久化的唯一口径）里只有
`tool_call`/`tool_result`，没有 react 词表；前端 handlers 读的
`message_id/id/name/arguments` 正是 AgentLoop payload 的形状。**产品
词表 = AgentLoop 词表**。所以 W1 的实施形态是：bridge 追加
`tool_call`/`tool_result` 直通 + AgentLoop 流式词表进直通表（已做），
V3-c 删掉 react 词表映射即可，无需任何前端改动。

`tool.ctx_failed` 只有 react.py 自身引用（无外部消费者），V3-c 随
react.py 一并消失。

**W2 · `max_iterations` 语义保持**（Q2 的后果）

生产 build 模式已经是 **50 轮**（`LLMConfig.max_iterations`），
plan 模式 1 轮。迁移到 `AgentLoop` 后必须**保持同一套取值逻辑**
（而不是用 `AgentLoop` 自己的默认值），否则：
- plan 模式可能跑 50 轮 → 成本与行为都变
- build 模式可能跑别的值 → 与今天不同

好消息：Q2 的答案是"两边已经一致"（都最终由 `LLMConfig.max_iterations`
或调用方显式值决定），所以这是**接线问题**不是**语义问题**——
V3-b 的适配器把 controller 的 `max_iterations` 透传给 AgentLoop 构造
参数，语义保持 ✅（已落地）。

**W3 · assistant 占位消息 seed**（V3-b 落地时新发现）——**已修复（2026-11-10）**

前端只有在 `message_received` 事件带 `assistant_message_id` 时才创建
assistant 占位消息（`queueHandlers.messageReceived`），而提交时 run_id
尚不存在，receipt 的 emit 带不了；`updateMessage` 对不存在的消息是
no-op → 流式 chunk（即使 V3-b 后已带 message_id）落不进任何消息。

**修复（三件套）**：
1. **桥在 run.started 补推 seed**：完整 message_received 形状（含
   user_mid + content + `assistant_message_id=run_id` + status）——
   projector 首事件胜出（真实 user 事件已在途，同 id 去重）零副作用；
   前端幂等（user 覆盖同内容 + 新建占位进入流式态）。sync/wait 路径
   **不带** seed——保持「引擎失败不落任何行」的既有不变量
   （`test_engine_failure_writes_nothing`）。
2. **桥安装时序**：先装桥后 submit——`work.enqueued` 在 submit 内
   唤醒消费者，晚装会错过 run.started（seed 与 message_id 注入全落空）。
   receipt 路径 return 时**不**注销桥（turn 交给消费者，run.answer
   落库靠它；收尾事件自注销）。
3. **流式打开**：`singleton.get_gateway` 默认 `stream=True`——生产服务
   在启动时构造单例，chat 入口的 stream 参数是无效调用（真机复验第一
   轮抓到：seed 到了但 delta 为 0，根因即此）。适配器对无
   stream/astream 的 client（测试假件）自动降级非流式，默认开安全。

**真机复验**：seed 先于首个 delta 到达（占位先落）；text_delta ×16
打字机跑通且全带 message_id；text/thinking 3-step 协议直通；终答正确。
测试：`tests/test_w3_sse_seed.py` 9 条。

---

## §8 生产实跑观察记录（2026-11-09，V3-b 落地后）

用户指令：「先在生产 web chat 上跑一段再动（V3-c）」。实跑方式：
`agentnodes serve` 起真实服务（隔离 workspace + 隔离 sessions.db，
`AN_ALLOW_REGISTRATION=1`），HTTP/SSE 全真链路驱动，MiniMax-M3.1-Flash。

### 8.1 结果总表

| # | 用例 | 路径 | 结果 |
|---|---|---|---|
| E1 | 读文件并回答内容 | `/send_async`（**前端真实路径**，SessionService→AgentLoop，build=50 轮） | ✅ 正确读出 `V3-B SMOKE OK` |
| E2 | 写第二个文件 + 报两个文件名（多工具+历史） | 同上 | ✅ 第二轮正确引用第一轮写的文件；复跑时还自行回读验证 |
| A1 | 写文件 | `/send`（gateway 路径，V3-b 换的引擎） | ⚠️ 工具**执行成功**（文件落盘），但轮次终止于 max_iterations=1，无最终答复（见 8.2 W4） |
| A2 | receipt + SSE 流 | `/send` | ✅ 全通：`tool_call`/`tool_result` payload 完整（id/name/arguments/message_id）、run.answer、assistant_message 落库 |
| A3 | 多轮历史（gateway 路径） | `/send` | ⚠️ 同 W4（单轮上限） |
| D | 持久化 | messages 表 | ✅ user/assistant 成对落库、排序正确 |

服务日志：MiniMax 调用全程 HTTP 200；除下述 8.3 的 `tool_help` 摩擦外
**零异常**——适配器、压缩、持久化、事件链路无一报错。

### 8.2 W4（已修复，2026-11-10）：gateway `/send` 路径 max_iterations=1

**现象**：`/send` 的每个 turn 只跑 1 轮——模型发工具调用、工具执行完
就终止（"Reached max_iterations=1 without a final answer."），没有
第二轮 LLM 调用把工具结果变成答复。

**根因**：`singleton.get_gateway()` 不传 `max_iterations` →
`SessionController` 默认值 1。而 `/send_async`（chat.py:259）早已按
`LLMConfig.max_iterations`（build=50 / plan=1）取值——**两条 HTTP
路径取值不一致**。

**定性**：**既有缺陷，非 V3-b 引入**——react 节点吃的是同一个
`max_iterations=1`，行为一模一样（react 的文案是
"[达到最大迭代次数 1，任务未完成]"）。V3-b 只是把文案变清晰了。
W2 决策（"build=cfg.max_iterations"）在 gateway 路径上从未被接线。

**修复（已落地）**：
- `singleton.get_gateway()` 读 `LLMConfig.load().max_iterations`
  （装载失败回退 50）传给 `Gateway(max_iterations=...)` → controller；
- per-turn：`/send` 的 `body.mode="plan"` → payload
  `max_iterations=1`（`api_chat_request` 新增可选字段，
  `session_controller._execute` per-turn 覆盖，与
  `_current_session_id` 同一先例）；
- 直构 `Gateway()` 不传 → controller 默认 1（既有测试路径零变化）。

**真机复验**（MiniMax，隔离 workspace）：build 模式写文件任务完整完成
（"已写好并读回验证……内容 W4 OK"，文件落盘核对一致）；plan 模式单轮
分析正确作答。测试：`tests/test_gateway_max_iterations_wiring.py` 9 条。

#### 8.2-b 复验中抓到的第二个接线缺口（已一并修复）：gateway 不传 workspace

W4 复验第一轮：所有工作区工具（write/list/read/shell/git_diff…）报
**"missing workspace context"**，模型只能靠从工具错误信息里拼出绝对
路径塞参数侥幸写成功（它自己的 attempt 总结原话："前几次 write 调用
因 missing workspace context 报错失败，重试后才成功落盘"；50 轮里
大多耗在盲探 websearch/read_document/task 委派上）。

**根因**：`Gateway.__init__` 把解析出的 workspace 只挂自身属性
（controller 构造**之后**才赋值），从未传入 controller →
`controller._workspace=None` → AgentLoop/ToolContext.workspace=None。
react 时代同样如此（ctx_factory 也吃 None）——**既有缺陷**；
SessionService 路径（chat_loop）一直有 workspace，所以前端正常。

**修复**：`Gateway.__init__` 把 workspace 解析提前到 controller 构造前
并传入（`SessionController(workspace=...)` 参数本就存在，只是没人传）。

**经验**：`/send` 这条"P5-1c ④ 统一入口"的装配根（singleton）从未被
端到端真正用过——W4 一次实跑抓出两个接线缺口（max_iterations、
workspace），都是"参数存在但没人传"的静默缺省。gateway 路径的第三个
同类缺口已记录：**permission evaluator 也不接线**（gateway 路径工具
无权限检查，chat.py 的 PermissionGateway 只服务 /permission/respond）
——修复涉及权限模式选型（L1/L2/env），归权限批，见 W5。

### 8.3 观察到的既有摩擦（W5 已修第一条；其余记录待排期）

- **`tool_help` ASK 被拒**：SessionService 路径的模型调 `tool_help`
  → L1 预设未列该工具 → ASK → 该上下文无审批通道 → `PermissionDeniedError`
  （结构化错误进工具结果，模型自行绕开改用 `read`，任务仍完成）。
  代价 = 一次浪费的往返 + 两条 ERROR 日志。修法（调规则集或给
  tool_help 自动放行）属权限批。
- **`no default DataStore registered`**：裸 workspace 的预期信息
  （批次 5 删了 DuckDB store），非错误。

### 8.3-b W5（已修复，2026-11-10）：gateway /send 权限接线

重估（§9）时确认这是**安全暴露**而非普通缺口，提到 V3-c 之前修：

**不设防组合**（W4 之前 + W4 之后各自成立）：
- `singleton.get_gateway()` 从不传 evaluator → gateway 路径
  `permission_evaluator=None` → 工具**无权限检查**；
- `build_default_registry()` 无条件注册 `shell` + `run_bg_command`
  （`register_shell_tools` 内部无闸——注释说的 "opt-in" 闸只在
  chat_loop 调用侧存在）；
- 叠加：任何登录用户经 `/send` 可让 agent 执行任意命令。同期
  `/send_async` 有 evaluator + shell opt-in——同一家公司的两个门，
  一个查证件一个不查。冒烟期间模型真的调到了 shell（当时因 workspace
  缺失失败；W4 修好 workspace 等于把这条路修通了——这正是把 W5
  提前的原因）。

**修复（两路同构）**：
- chat_loop 的默认选型逻辑提取为
  `build_default_permission_evaluator()`（env `AGENTNODES_PERMISSION_MODE`
  → 否则 L2_SANDBOX_EXEC，interactive=True，ASK fail-closed），
  chat_loop 与 singleton 共享同一实现——漂移即测试失败；
- `Gateway.__init__` 新增 `permission_evaluator` 参数（None = 不接线，
  直构测试路径行为不变），singleton 缺省装配默认 evaluator；
- singleton 装配 registry 后按 `AGENTNODES_ALLOW_SHELL_TOOLS`（与
  `/send_async` 同判据）剔除 `shell` / `run_bg_command`——默认注册表
  级 opt-in off。

**选型说明**：默认 L2（非 L1）与 chat_loop 的既有默认一致——CLI/TUI
无提问通道时 L2 是既定决策（shell 自动、沙箱边界 + PathWhitelist 把守、
hard_deny 兜底）；gateway 无审批通道，同理。`plan` 模式的 shell 恒禁
在 gateway 路径由 evaluator 兜底（shell 本就不在注册表）。未来接
SSE approval 通道（`/api/chat/permission/respond` 已存在）后可切 L1。

**验证**：
- 新增 `tests/test_gateway_permission_wiring.py` 9 条（透传/默认 None/
  端到端 deny/singleton 默认装配/env 覆盖/显式优先/shell 闸 ×2/端到端
  registry 缺 shell）；
- chat_loop + permission 系 135 条全绿（提取无回归）；
- **真机复验**：要求跑 shell → 模型如实报告"本轮没有 shell 工具"，
  只调了自动放行的 `tool_help` 排查，零执行；写文件（L2 ALLOW）→
  完整走通且落盘核对一致。

### 8.4 观察结论

前端生产路径（SessionService→AgentLoop）**本批未受影响且功能完整**；
V3-b 换的 gateway 路径链路全通，唯一拦路的是**既有**的 W4 单轮上限。
V3-c（删 react.py）的前置观察可以开始积累；W4 修复建议排在 V3-c 之前
（它是 gateway 路径可用性的前提，改动极小）。

### 8.5 冒烟产物

脚本与数据在 `tmp/webchat-smoke/`（gitignore 内）；冒烟用户 ×3 已从
真实 user DB 删除；sessions.db 为隔离文件不触碰生产数据。

---

## §9 阶段性重估（2026-11-10，W4 落地后）

用户指令：「再评估一下」。以下全部基于当日实测取证，非印象。

### 9.1 记分板

| 维度 | 状态 |
|---|---|
| 双执行路径 | **已归一**：SessionService / gateway / evals 全部跑 AgentLoop；`react.py`（282 行）成无生产消费者孤岛（剩 `presets/fanout.py`——其生产消费者 = 0，只剩 2 个测试文件——和 `stage1b_demo.py` 演示脚本）|
| gateway `/send` 装配 | W4 后功能完整（50 轮 + workspace + 事件直通 + 持久化）+ W5 后权限对齐，真机验证 |
| 观察期实质 | gateway 路径累计 3 轮真机 + 30+ 单测；**前端不使用 /send**（Composer 走 /send_async），生产 chat 流量未被触碰 |
| 回归 | 7074+ passed / 0 failed；batch_gate ALL GREEN |
| loop.py | 1,437 行（V3-b +21；≤650 的目标在 V4）|

### 9.2 风险重排

1. **W5 从"记录"升级为安全暴露**（已按此执行，见 §8.3-b）——shell 无闸
   不是理论风险，冒烟里模型真的调到了 shell。
2. **观察期对 V3-c 已经花得差不多**：V3-c 的爆炸半径不在生产链路
   （react.py 只剩 test-only 的 fanout 在用），真正要盯的是 fanout 迁
   适配器后的分支并发语义（有 opt-in 的 `fanout_live` 真机测试兜底）。
   观察期真正还欠的是 **W5 之后给 /send 放真实流量**。
3. **W3 优先级下调**：前端流式本就靠 `appendStreamingText` 兜底
   （react 时代连 text_id 都没有），W4 后 gateway 事件带全 payload——
   体验改进而非缺陷，归前端接线批。
4. 批 C/D/E、V4 无新信息，维持原判。

### 9.3 重排后的执行序

```
W5 权限接线（0.5d）          ← 唯一安全项，已执行 ✅
  → /send 放真实流量，观察期正式开始
  → V3-c 删 react.py + 迁 fanout（0.5d，爆炸半径 = 测试）
  → W3 + SSE 前端接线批（体验）
  → 批 C/D/E（等用户点头）→ V4（RSI 前）
```

### 9.4 一次实跑抓出三个装配缺口的教训

`/send` 这条 P5-1c ④ "统一入口"的装配根（singleton）此前从未被端到端
真正用过——三个缺口（max_iterations、workspace、permission）全是
**"参数/闸门存在但装配根没人传"**的静默缺省，且每个都绕过了既有
单测（单测直构 Gateway，不经过 singleton）。教训已体现在 W4/W5 的
测试形状里：**singleton 级装配测试**（`test_gateway_max_iterations_wiring`
/ `test_gateway_permission_wiring`）从此成为入口路径变更的必改点。
剩余未知的同类缺口需要真实流量暴露——这正是观察期的目的。
