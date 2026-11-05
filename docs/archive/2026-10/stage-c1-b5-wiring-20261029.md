# B5 前置接线：todos 在 chat 生产路径可见 — 2026-10-29

> 上游：`docs/archive/2026-10/stage-c1-b5-contexthub-w1w2-design-20261028.md` §2.3 / §5.2 / §5.3
> 用户决策：context_hub 接线采用**选项 A**（Gateway 显式形参，组装根注入）。

---

## 0. 范围与裁决

W1 的 provider 在 chat react 路径会稳定返回空（B5 §2.3 三个缺陷叠加）。
本刀打通「todo 写得进、读得出」的最短链路，W1 本体（provider + W2 仲裁）随后。

| ID | 改动 | 文件 |
|---|---|---|
| **B-a** | `TodoWriteTool` 的 session_id 兜底读 `ctx.session_id` | `builtin_tools/todo_tools.py:168` |
| **R-1** | `Gateway` 增加 `context_hub` 形参并透传；`singleton` 组装 hub（选项 A） | `gateway/gateway.py`、`gateway/singleton.py` |
| **B-c** | hub 非空时 react 路径构造**仅带 session_id** 的 `ToolContext` | `gateway/session_controller.py` |
| **W1-p** | `TodoContextProvider`（W1 唯一新增类，文档 §2.2 原样落地） | `agent/context_providers.py` |

### 明确不在本刀（如实记录）

**goal/memory 到 react 路径的注入**。`PreRunContextProvider.__init__(loop, injectors)`
需要 AgentLoop 实例（B5 §5.2 的派单建议在此处不可行——react 无 loop）。
要把它带到 react 路径，需要「无 loop 的 goal/memory provider」，是独立刀。
**现状对齐说明**：生产默认走 Gateway（`QNODES_NEW_ENTRY` 默认 True），
goal/memory 在该路径**本来就缺失**（B1-b 的 hub 从未接线）——本刀不使其
更坏，也不假装修好；旧 `/send_async` 路径的注入不受影响。

**D-2（role_factory 传 session_id）**：study/swarm 的 todowrite 今天就共享
`"default"` 桶（AgentLoop `_build_tool_kwargs` 注入 `session_id=None`）。
B-a 不改变这一点（ctx 兜底在 kwargs 之后）。D-2 需要穿 `run_agent_via_llm`
参数链，属 study 侧决定，暂缓。

---

## 1. 实施细节

### 1.1 B-a（todo_tools.py:168）

```python
session_id = str(
    kwargs.get("session_id")
    or getattr(ctx, "session_id", None)
    or "default"
)
```

向后兼容：kwargs 显式指定仍优先（AgentLoop 旧路径行为不变）；
ctx 兜底使 react 路径（B-c 提供 ctx）落到正确会话；两者皆无 → `"default"`
（脚本/REPL 现状）。

### 1.2 R-1（选项 A）

- `Gateway.__init__` 增加 `context_hub: Any | None = None`，透传
  `SessionController(context_hub=...)`（controller 侧已支持，`session_controller.py:48`）。
- `singleton.get_gateway()` 组装：`ContextHub` + 注册 `TodoContextProvider`，
  失败降级 None（与 registry 降级同款）。chat.py 调用点零改动。

### 1.3 B-c（session_controller._build_engine）

现有构造条件 `if evaluator or gateway or workspace` 扩一项
`or self._context_hub is not None`，构造仅带 `session_id` 的
`ToolContext`（无 evaluator → `enforce_permission_sync` 直接 return，
权限行为零变化，`tools.py:878-880` 实证）。

**条件语义**：hub 非空 = 生产 Gateway 路径（选项 A 只在那里组装）。
测试直构 SessionController（不传 hub）→ 行为不变，规避
「FakeRegistry 不接受 ctx」的历史回归（B1-d 教训）。

`make_ctx_factory(session_id=self._current_session_id or None)`：
engine 每 turn 重建（`_execute:109`），闭包取到的是当前会话。

### 1.4 W1-p（TodoContextProvider）

按 B5 §2.2 落地：单条 `kind="todo"` item、content 复用
`_format_todos_snapshot`（零格式漂移）、tokens 留给中枢估算、
`priority=0`（单来源无比较对象，注释说明）、`created_at` 不编造。

---

## 2. 测试（tests/test_b5_wiring.py，新）

| # | 断言 |
|---|---|
| T1 | B-a：无 kwargs、ctx 带 session_id → `TodoStore.get(sess)` 命中 |
| T2 | B-a：kwargs 显式 session_id 仍优先（旧路径不回归） |
| T3 | R-1：`Gateway(context_hub=hub)` → `controller._context_hub is hub` |
| T4 | R-1：`get_gateway()` 组装的 hub 含 todos provider（reset 后构造） |
| T5 | B-c：hub 非空 → react `ReactConfig.ctx_factory` 非 None，产出 ctx.session_id 正确 |
| T6 | B-c：hub 为 None（既有测试路径）→ ctx_factory 仍 None（零回归） |
| T7 | 端到端：react 节点 + 真 ToolRegistry 执行 todowrite → 落正确会话；`hub.build` 返回 kind=todo item |

回归门：`test_b5_wiring + test_context_hub + test_context_providers_bridge
+ test_b1_chat_react_wiring + test_b1d_permission_gate + test_todo_tools
+ test_session_controller` → `c1_gate.py`。

---

## 3. less-is-more 调查表

| 需求 | 查过 | 结论 |
|---|---|---|
| 读 todos | `TodoStore.get`（todo_tools.py:39） | 复用 |
| 渲染 | `_format_todos_snapshot`（todo_tools.py:64） | 复用，禁重写 |
| hub 组装 | `ContextHub.register`（hub.py:64） | 复用 |
| ctx 构造 | `make_ctx_factory`（tool_context.py:60） | 复用（session_id 参数已存在） |
| gateway 透传 | SessionController 已有 `context_hub` 形参（session_controller.py:48） | 只补 Gateway 一层 |
| goal/memory 到 react | `PreRunContextProvider(loop, injectors)` | **不可复用**（需 loop），如实推迟，见 §0 |
