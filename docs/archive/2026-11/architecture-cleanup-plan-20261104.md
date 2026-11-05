# AgentNodes 架构清理与优化规划（2026-11-04 · W14）

> 前置：`docs/archive/2026-11/split-surface-20261104.md`（切分面实测）、`docs/archive/2026-11/reuse-review-20261104.md`（复用性评审）
> 本文回答三个问题：**清什么、怎么重构、性能在哪**。
> 所有数字均来自本轮实测（graphify 图 + AST + 包级依赖矩阵），非二手引用。

---

## 0. 结论先行

拆分完成后，AgentNodes 剩下的不是「一个干净的框架」，而是**一个带着业务期遗产的框架**。
三个最贵的结构问题，按代价排序：

| # | 问题 | 实测证据 | 代价 |
|---|---|---|---|
| **1** | `AgentLoop` 是 6 个 Mixin 拼起来的 1279 行类，`__init__` 173 行，`_run_loop_core` 246 行 | 见 §2.1 | 无法单测、无法替换、MRO 变更即静默失效 |
| **2** | 同步/异步边界靠线程桥接，一次 `run()` 要横跨两个 event loop | `sync_bridge` 2 个全局 loop + `run_coroutine_threadsafe` 5 处调用 | 竞态、顺序错乱、调试困难 |
| **3** | 24 处模块级可变全局态（含 9 套 registry、8 个单例缓存） | 见 §2.3 | 测试互相污染（本轮已实测到抖动）、无法多实例 |
| — | 另有 `foundation/registry.py` 零生产引用（91 行 + 348 行测试） | §2.4 | 抽象比 91:15，该删不该用 |

**先说一个反直觉的结论**（§4）：这个项目的性能瓶颈**不在算法**，
`@lru_cache` 全库只有 1 处，而热路径上每次工具调用都要重新走权限门、守卫、哈希、序列化。
优化点应放在「每轮重复的固定开销」，而不是「算得更快」。

---

## 1. 清理：分三层，按「可回滚性」排序

### 1.1 第一层：移除业务遗留（机械删除，已在做）

| 步骤 | 内容 | 规模 | 状态 |
|---|---|---:|---|
| 2a | 170 个业务测试 → `testcase/tests/` | 41,367 行 | ✅ `875fd883` |
| 2b | 3 个死文件（covariance/ic_utils/risk_parity） | 246 行 | ✅ `ed217002` |
| **2e** | `apps/` 全包 + 17 个业务接口/工具 | 62,430 + 6,552 行 | 待做 |
| **2g** | kernel 13 处接缝解耦 | 113 条边 | 待做 |

**2e 与 2g 的顺序必须是「先 2g 后 2e」**，理由在 `split-surface` §5.1 已证：
17 个业务接口被 `app.py:92,187-215` / `chat.py:32` / `schedule.py:197` 装配，
先删 apps 会留下无法启动的中间态。

### 1.2 第二层：移除框架内的死抽象

| 对象 | 证据 | 动作 |
|---|---|---|
| `foundation/registry.py` (`Registry[T]`, 91 行) | **生产引用 0 个**；6 个独有能力零使用；唯一体现意图的 `restricted()` 已在 `ToolRegistry` 独立重写 | **删除**（连带 3 个测试文件 348 行 + `test_layer_matrix.py:99,105` 两处登记） |
| `kernel/seq_generator.py` (94 行) | 消费者只有 3 个（`api/routers/web_session.py`、`api/session/models.py`、`test_layer_matrix.py`），全部属会话层 | 随 §3.5 会话层下沉一起迁移，再退役（**不是直接删**） |
| `kernel/agent/builtin_tools/` 6 个业务工具 | 已在 2e 范围 | 随 apps 走 |
| `interfaces/mcp/server.py` 业务工具注册段 | 905 行混合体 | 保留骨架 + 入口点注册（见 §3.2） |

> **为什么不该「让 9 个 registry 继承 `Registry[T]`」**：抽象比 91:15，
> 9 个实现里 `HypothesisRegistry` 的 create/derive/link/contradicts、
> `ToolRegistry` 的 execute/guard/check_guards 语义完全不同，
> 基类只能覆盖 dict 存取，收益仅是省掉几行 `self._items` 样板。
> Rule of One：零生产引用说明连一个潜在第二实现都不存在。

### 1.3 第三层：拆 `kernel/agent`（真正的重构，不是删除）

`kernel/agent` 21,705 行 / 92 文件，占 kernel 的 63%。内部按职责实测分布：

```
loop + 6 mixins     2,791 loc   8 files   ← 最纠缠
tools               1,568 loc   5 files
prompt/role         1,193 loc   3 files
compaction          1,174 loc   3 files
context               849 loc   3 files
memory                812 loc   1 file    ← 单文件最大
events                706 loc   1 file
other               3,268 loc  17 files
```

拆法见 §3.1（不是按行数切，而是按**变更原因**切）。

---

## 2. 三个最贵的结构问题（带证据）

### 2.1 `AgentLoop`：6 Mixin + 173 行构造 + 246 行主循环

```python
# kernel/agent/loop.py:238
class AgentLoop(LoopStreamMixin, LoopTelemetryMixin, LoopProgressMixin,
                LoopLifecycleMixin, LoopToolsMixin, LoopCompactionMixin):
```

方法规模实测：

```
__init__           L254-426   173 行   ← 构造 6 个协作者 + 读 20+ 个开关
_run_loop_core     L774-1019  246 行   ← 一轮的完整编排
_trace_llm_request L1109-1203  95 行
_get_response      L1021-1077  57 行
```

**Mixin 的具体代价（逐条命中本仓库）**：

| mixin 代价 | 本仓库实例 |
|---|---|
| 隐式耦合（依赖宿主属性，签名里看不到） | 每个 Mixin 声明 `_permission_evaluator: Any` / `_recent_hashes: Any` 等"宿主接口" |
| 同名方法遮蔽 | 6 个 Mixin 都有 `_maybe_compact` / `_amaybe_compact` 形态相近的方法 |
| `super()` 链顺序敏感 | `loop.py` 里 Mixin 的 `super().__init__()` 调用依赖 MRO 顺序 |
| MRO 冲突运行时才暴露 | W10-1 断环时发现 `loop_compaction` 反向 import `loop` |
| 无法静态分析 | 1,279 + 1,300 行要拼起来才能看到 `AgentLoop` 全貌 |

**根因**：`loop.py` 同时承担 5 件事——LLM 调用、工具编排、上下文压缩、
事件发射、预算控制。这 5 件事的**变更频率完全不同**（压缩逻辑改得最勤，
预算几乎不动），却挤在一个类里。

### 2.2 同步/异步双轨：一次 `run()` 横跨两个 event loop

```
loop.py:715          run()  →  _run_coro_in_sync(arun())
loop_tools.py:229    _execute_tool_call → _run_coro_in_sync(...)   ← 又从同步回到异步
loop_tools.py:266    _execute_tool_with_heartbeat → 同上
loop_telemetry.py:36 asyncio.run_coroutine_threadsafe(coro, _get_hook_loop())  ← 第三个 loop
```

`sync_bridge` 持有一个**全局** `_HOOK_LOOP` + 锁，所有 hook 回调都要
`run_coroutine_threadsafe` 投递过去。实测调用点 5 处。

**为什么这是问题**（不只是「不好看」）：
- 同步 `run()` 内部用 `run_coroutine_threadsafe` 回到另一个 loop → 每次工具调用多一次线程切换
- 两个 loop 之间没有统一的取消语义，`_with_iteration_timeout` 只能各自实现一份（`loop.py:649` async 版 + `L666` sync 版，逻辑重复）
- 调试时调用栈跨线程，traceback 断裂
- 本轮实测的 `clean_data` 抖动（`split-surface` §7.4）就是这类「连接生命周期 + 线程」问题的同族

### 2.3 模块级可变全局态：24 处

实测清单（节选，按危害排序）：

| 位置 | 形态 | 危害 |
|---|---|---|
| `kernel/agent/loop.py:93` `_goal_store_cache: dict[Path, Any]` | 进程级缓存 | 换 workspace 不清 → 读到别的库 |
| `kernel/agent/builtin_tools/todo_tools.py:37` `_todos: dict[str, list]` | 全局任务表 | **多会话互相覆盖** |
| `kernel/workflow/node_types.py:82` `_fns: dict[str, Callable]` | 全局注册表 | 测试间污染 |
| `kernel/agent/exec_registry.py:16-17` `_python_executors` / `_evaluators` | 全局注册表 | 同上 |
| `kernel/gateway/singleton.py:15` `_singleton: dict[str, Any]` | Service Locator | 隐藏依赖 |
| `kernel/agent/runtime/sync_bridge.py:20` `_HOOK_LOOP` | 全局 event loop | §2.2 |
| `interfaces/api/routers/chat.py:71,79` `_session_service_cache` / `_permission_gateway_cache` | 路由级缓存 | 进程内串号风险 |
| `interfaces/api/routers/study_helpers.py:59` `_scheduler_cache` | 同上 | 随 2e 删除 |
| `foundation/storage/data_store.py:273` `_DEFAULT_DATA_STORE` | 全局单例 | 已用注入槽兜底（W10-3） |

**注意**：不是所有全局态都该消灭。`_HOOK_LOOP`（线程池）和
`_default_catalog`（配置缓存）有正当理由。**该消灭的是「按 key 缓存业务对象」那类**
——它们让测试互相污染，且无法多实例。

**本轮已实测到后果**：`test_data_clean_tool` 在 xdist 下 4/12 抖动。

### 2.4 包级依赖：5 对反向/循环边

```
kernel.agent <-> kernel.workflow   (2 / 4)
kernel.agent <-> kernel.memory     (3 / 1)
kernel.agent <-> interfaces.api    (2 / 28)   ← 最严重：L3 反向依赖 L1
kernel.swarm <-> kernel.workflow   (1 / 6)
interfaces.cli <-> interfaces.api  (2 / 7)
```

`kernel.agent → interfaces.api` 实测只有 2 处，都是 lazy import：

```
kernel/agent/event_store.py:610        from ...interfaces.api.session.projector import Projector
kernel/agent/memory_manager.py:160     from ...interfaces.api.session.web_session_db import _ensure_schema
```

**这两处是「投影器」和「web session schema」**——它们本质是 L3 能力，
只是物理上还住在 `interfaces/api/session/`（5,924 行）。
**解法不是改 import，而是把 `interfaces/api/session/` 整体下沉到 `kernel/session/`**
（这与 W10 台账 §二的待办 #1 是同一件事）。

---

## 3. 重构：按设计模式落到具体落点

### 3.1 `AgentLoop` → 组合 + 管道（Pattern: Strategy + Pipeline + Extract Class）

**不新增 Mixin，也不做「大爆炸式」拆除**（调研明确：6 个 Mixin 的拆除是独立大工程，
需先写出可测的行为契约）。分三步走：

#### Step 1：先给 `AgentLoop` 写行为契约（不改结构）

在 `tests/` 建 `test_agent_loop_contract.py`，用现有 1,279 行代码**提取**出
每条可观察行为，形成一份"架构特征测试"（characterization test）。
没有这层网，后面任何拆分都无法证明等价。

#### Step 2：把 5 个职责抽成协作者，Mixin 退化为薄转发

```
现在                        目标
─────────────────────────────────────────────────────────
AgentLoop(Mixin × 6)   →    AgentLoop (只做编排 + 生命周期)
                              ├─ LlmInvoker        (调 LLM + 重试 + trace)
                              ├─ ToolOrchestrator  (批调度 + 权限门 + 守卫 + 心跳)
                              ├─ Compactor         (压缩触发 + 持久化)
                              └─ EventEmitter      (事件发射 + hook 投递)

6 个 Mixin → 保留同名方法作**转发**（一行 self._x.y(...)）
             → 外部调用点零改动 → 可分批迁移、随时回滚
```

**为什么用组合而不是继续 Mixin**：
- Mixin 的隐式耦合消失了（协作者通过构造参数传入，签名可见）
- 每个协作者可以独立单测（现在是"必须造一个 AgentLoop 才能测压缩"）
- `__init__` 173 行 → 每个协作者 20-40 行

**为什么保留转发层**：`loop.py` 的 `_execute_tool_call` 等方法被
`loop_tools.py`、测试、`runtimes/nodes/react.py` 调用，直接改名会引发连锁。
转发层是一行代码，但换来「零调用点改动」+「渐进迁移」。

#### Step 3：拆 `_run_loop_core`（246 行）

它现在是「一轮」的全部编排。按**决策点**切：

```
_run_loop_core
  ├─ _prepare_turn()       前置：预算检查 / 上下文注入 / 工具集解析
  ├─ _invoke_and_stream()  LLM 调用 + 流式事件
  ├─ _dispatch_tools()     工具批调度（已有 _aexecute_tool_batch）
  └─ _finalize_turn()      结果记录 / compaction 检查 / 预算记账
```

每个 40-60 行，可独立测试。**先切分、后改语义**（两件事不要混在一个 commit）。

### 3.2 工具注册：从「硬编码 + try/except」到「入口点协议」（Pattern: Plugin + Registry）

现状（`builtin_tools/__init__.py`）：18 个域，6 个业务域用 `try: import ... except: pass`
包着。这是「特性开关」，不是注册机制。

**已经存在但没被用上的东西**：`_discover_tool_plugins()`（`__init__.py:137`）
已经实现了 `importlib.metadata.entry_points(group="strategy_research.tools")`。

**目标形态**：

```
框架侧（AgentNodes）                 业务侧（quantnodes）
───────────────────────────         ──────────────────────────
ToolRegistry + 发现机制      ←───    [project.entry-points."agentnodes.tools"]
builtin: file/shell/todo/            backtest = "quantnodes.tools:register"
        web/subagent/help            factor   = "quantnodes.tools:register"
```

**收益**：
- 框架侧不再需要 `try/except` 包业务导入（6 处 → 0）
- 业务侧可以独立发版，框架不感知
- 契约清晰：工具插件只依赖 `ToolRegistry` + `BaseTool`，是真正的端口

**代价**：需要一次性的 `pyproject.toml` 契约 + 一个 `check_plugin_contract.py` 门禁。

### 3.3 同步/异步：单向收敛（Pattern: 单一权威 + 适配层）

**目标：框架内部只有 async，同步入口只做一次桥接。**

```
现在                                   目标
────────────────────────────────────────────────
run() → _run_coro_in_sync(arun())      run() → asyncio.run(arun())    ← 只一次
  └ 内部 5 处又回到 sync_bridge          └ 内部全 async，无二次桥接
  └ 全局 _HOOK_LOOP 投递 hook            └ hook 在同一个 loop 上 await
```

**分三步**（每步可独立验证）：

| 步 | 动作 | 验证 |
|---|---|---|
| a | hook 投递改为「传入当前 loop」而非全局 `_HOOK_LOOP` | 删除 `_HOOK_LOOP` 后 hook 测试全绿 |
| b | `loop_tools.py` 的 sync 分支（`_execute_tool_call`）改为 async 包装 | sync 入口测试仍绿 |
| c | `sync_bridge` 只剩「入口处一次 `asyncio.run`」 | 全库 `run_coroutine_threadsafe` 归零 |

**注意**：`sync_bridge` 当前有 2 个全局 loop 的隐患（W11 已删掉 `loop.py` 里那份重复实现），
但 `loop_telemetry.py:36` 仍在用它。**这是竞态源，优先级高**。

### 3.4 全局态：按「生命周期」分类处置

不是全删，而是问「这个状态的生命周期应该绑在谁身上」：

| 类别 | 处置 | 模式 |
|---|---|---|
| 线程池 / event loop（`_HOOK_LOOP`） | 保留，但改为**显式注入**，不用模块全局 | Dependency Injection（最小化） |
| 进程级配置缓存（`_default_catalog`、`_SECRET_CACHE`） | 保留，加显式 `reset()` 供测试 | Facade |
| 按 key 缓存业务对象（`_goal_store_cache`、`_todos`、`_session_service_cache`） | **下沉为实例状态**，随 owner 生命周期 | Extract Class |
| 全局注册表（`_fns`、`_python_executors`、`_evaluators`） | 改为**构造时传入**的实例注册表 | Registry as instance |

**验收标准**：`pytest -p no:randomly` 下重复跑 3 次，失败集必须逐字一致。
这是唯一能证明「测试不再互相污染」的方法。

### 3.5 会话层下沉：`interfaces/api/session/` → `kernel/session/`

14 文件 / 5,924 行，现在只导入了 1 处 apps（`web_session_state.py`），
且被 `kernel/agent/event_store.py` 反向依赖。

**这一步的收益不只是消除反向依赖**：它是 L1→L3 边界重划（"无状态·无编排"的判据），
也是把 5,924 行从"接口层"移到"平台机制层"的分类修正。

**前置**：先解 `routers/ ↔ session/` 的 35 处互引（W10 台账 §二的待办 #1）。

---

## 4. 性能：瓶颈不在算法，在每轮固定开销

### 4.1 实测：这个库几乎不做缓存

```
@lru_cache / @cache 全库命中：1 处（kernel/agent/redaction.py）
```

对比：热路径上**每次工具调用**都要走：

```
registry.check_guards(name, args)     →  纯字典查找 + 断言（快）
permission gate                       →  可能读 DB / 读配置
payload hash（_recent_hashes）         →  对参数做 hash
json 序列化（tool result）             →  每次都做
trace 事件发射                         →  每次建 dict + 投递
```

一次 `arun()` 循环里，**这些固定开销按工具调用次数线性放大**。
而 LLM 调用本身是秒级——所以真正可优化的是「轮次内的重复计算」，不是"算得更快"。

### 4.2 五个具体优化点（按 ROI 排序）

| # | 优化 | 证据 | 手法 | 预估收益 |
|---|---|---|---|---|
| **1** | 工具定义（`definitions()`）每轮重建 | `tools.py:856` `get_definitions()` = `[t.to_openai_schema() for t in ...]`，每次调用重建全部 schema；`gateway.py:46` 每轮转一次 | 首次构建后按 registry 版本号缓存 | 每轮省 N 次 schema 构造，N=工具数 |
| **2** | `ToolContext` 每工具调用新建 | 工具签名要求 `ctx` | 每轮复用一个 ctx（不可变字段冻结） | 省对象分配 |
| **3** | 参数 hash 对同一 tool_call 重复计算 | `loop_tools.py` `_recent_hashes` | 在 `ToolCall` 上缓存 hash | 少量 |
| **4** | tool result 多次 json 序列化 | 存储 + 事件 + 返回各一次 | 序列化一次，复用字符串 | 工具输出大时明显 |
| **5** | 同步桥接的线程切换 | §2.2 的 5 处 | 随 §3.3 一并消除 | 每次工具调用省 1 次上下文切换 |

> **不要做的事**：给 `definitions()` 加 `@lru_cache` 而不带失效机制——
> 工具可以运行时注册（`register()`），缓存会读到过期集合。
> 必须用「版本号 + 失效」而不是裸 `lru_cache`。

### 4.3 真正值得先测的：一次 `arun()` 的火焰图

在优化之前，先量。建议脚本（属交付物，不是临时脚本）：

```
scripts/dev/profile_agent_turn.py
  - 固定 1 个 mock LLM + 1 个 mock 工具
  - cProfile 跑 100 轮 arun()
  - 输出 top 30 累积耗时
```

**没有这个基线，§4.2 的五项都是猜测。** 我的判断是：
LLM 客户端与序列化占大头，权限门次之——但必须以火焰图为准。

---

## 5. 执行顺序（每步可独立验证/回滚）

```
阶段 A · 清理（不改语义，纯删除 + 归档）
  A1  先 2g：kernel 13 处接缝解耦（能力注入 + 入口点注册）
  A2  再 2e：apps/ + 17 个业务接口/工具 原子移除
  A3  删死抽象：foundation/registry.py + seq_generator.py（核查后）
  A4  验收：全量测试失败集 ⊇ 基线失败集、零 apps 引用、门禁绿

阶段 B · 结构（改结构不改行为）
  B1  AgentLoop 行为契约测试（characterization）
  B2  抽 4 个协作者，Mixin 退化为转发
  B3  拆 _run_loop_core 为 4 个阶段方法
  B4  全局态按生命周期分类处置（§3.4）
  B5  验收：B1 的契约测试全绿 + 重复跑 3 次失败集一致

阶段 C · 性能（先量后改）
  C1  建 profile 脚本，拿到火焰图基线
  C2  按 §4.2 ROI 逐项实施，每项附前后对比
  C3  验收：单轮 P50 延迟下降，且契约测试全绿
```

**为什么 A 在 B 前**：A 是纯删除，风险最低且能缩小 B 的搜索空间
（删掉 68,982 行业务代码后，`kernel.agent` 的耦合面会变小）。
**为什么 C 在 B 后**：B 会改变调用结构，先在旧结构上做性能优化等于白做。

---

## 6. 明确不做（以及为什么）

| 事项 | 理由 |
|---|---|
| 让 9 个 registry 继承 `Registry[T]` | 抽象比 91:15，Rule of One（零生产引用），§1.2 |
| 引入 DI 容器 | 已有 9 套 registry + 24 处全局态，再加一层会放大 service-locator 化 |
| 给 `apps`/`kernel` 新增 Mixin | MRO 冲突 / 隐式耦合 / 难静态分析，§2.1 |
| 批量收窄 407 处 `except Exception` | 会掩盖真实错误；必须先补测试（本轮已实证：`clean_data` 的错被自身降级路径掩盖） |
| 把 alpha_zoo 468 文件当"代码重复"抽函数 | 是 YAML 转换产物，重复是设计使然（`reuse-review` §0.3） |
| 合并 `alpha_zoo_ops` 与 `compute_factor` 的 16 个同名函数 | 逐字比对 16/16 全不同（`reuse-review` §0.1） |
| 大爆炸式重构 `AgentLoop` | 1,279 行无契约网，必然引入语义漂移；必须先 B1 |
| 给 `definitions()` 加裸 `lru_cache` | 工具可运行时注册，缓存会读到过期集合 |

---

## 7. 与既有文档的关系

| 文档 | 关系 |
|---|---|
| `split-surface-20261104.md` | 本文的执行依据（切分面实测、2e/2g 的边界） |
| `reuse-review-20261104.md` | §1.2 的 registry 判定、§6 的「不要做」清单 |
| `w10-followups-20261104.md` | §3.5 会话下沉的前置条件（routers↔session 35 处互引） |
| `layered-skeleton-design-20261103.md` | §3.5 需要重划 L1/L3 边界 |
| `agentnodes-split-plan-20261104.md` | 阶段 2/3 的具体步骤，本文的 A 阶段与之对应 |
