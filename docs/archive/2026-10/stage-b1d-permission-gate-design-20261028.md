# B1-d 权限网关移植 — 调查与设计 — 2026-10-28

> 安全关键项。目标：让 react 节点的工具执行经过与 AgentLoop 相同的
> 权限闸门（evaluator 规则判定 + ASK 时的 gateway 人工审批）。

## 1. 调查结论：react 节点**完全绕过**权限闸门

这是 B1 迁移中风险最高的一处，且现状已是一个真实缺口：

```python
# core/runtimes/nodes/react.py:93
result = registry.execute(tc.name, dict(tc.arguments or {}))
```

对比 AgentLoop 的路径（`loop_tools.py:64-80`）：

```python
kwargs["ctx"] = ToolContext(
    workspace=..., session_id=..., strategy_dir=..., runs_dir=...,
    results_tsv=..., write_roots=..., read_roots=...,
    emit_progress=..., emit_event=..., message_id=...,
    permission_evaluator=getattr(self, "_permission_evaluator", None),  # ← 关键
    permission_gateway=getattr(self, "_permission_gateway", None),      # ← 关键
    tool_call_id=tc.id, data_store=..., sandbox=...,
)
```

`ToolContext` 是权限判定的唯一入口（`tools.py:650-692`：`ctx.permission_evaluator`
判定 allow/deny/ask；ASK 时走 `ctx.permission_gateway`）。**react 节点不构造
ToolContext → 三道闸门（安全规则、ASK 审批、沙箱）全部失效**。

## 2. 影响面（实测）

| 能力 | AgentLoop | react 现状 |
|---|---|---|
| 权限规则判定（allow/deny/ask） | ✅ | ❌ 绕过 |
| ASK 人工审批（permission_request SSE） | ✅ | ❌ 绕过 |
| workspace/session_id 注入 | ✅ | ❌ 缺失（工具拿不到工作区） |
| emit_event（工具内发事件） | ✅ | ❌ 缺失 |
| sandbox / data_store | ✅ | ❌ 缺失 |
| tool_call_id（审批配对） | ✅ | ❌ 缺失 |

**这不只是「迁移未完成」，而是新引擎当前就在跑的工具调用没有权限保护。**
P5-1b 引入 react 到 chat 后，这条路径已在生产可用。

## 3. 设计

### 3.1 核心：react 节点接受 context factory

不把 15 个字段塞进 `ReactConfig`（那样 react 要 import 具体域类型，
破坏「节点不 import 工具实现」的边界）。改为**注入工厂**：

```python
@dataclass
class ReactConfig:
    ...
    ctx_factory: Callable[[str, dict], Any] | None = None
    # 签名: (tool_call_id, params) -> ToolContext | None
    # None = 旧行为（无 ctx，供纯测试/无权限场景）
```

节点内部：
```python
ctx = cfg.ctx_factory(tc.id, kwargs) if cfg.ctx_factory else None
result = registry.execute(tc.name, kwargs, ctx=ctx)
```

### 3.2 registry.execute 增加可选 ctx

```python
def execute(self, name, params, *, ctx: Any = None) -> str:
    return tool.invoke(dict(params), ctx=ctx)   # ctx 走 kwargs 注入
```

**兼容性**：`ctx=None` 时行为与今天完全一致（`invoke` 已支持无 ctx 调用）。

### 3.3 SessionController 提供 factory

复用 AgentLoop 的构造逻辑——抽成一个共享函数（与
`assemble_loop_parts` 同样的「单一事实源」手法）：

```python
# core/agent/tool_context.py（新）
def build_tool_context_factory(
    *, workspace, session_id, permission_evaluator, permission_gateway,
    emit_event, message_id_getter, ...
) -> Callable[[str, dict], ToolContext]
```

AgentLoop 的 `_execute_tool_call_core` 也改为调用它——保证两条路径
**同一实现**，避免权限语义漂移。

## 4. 验收（负路径优先）

| 测试 | 断言 |
|---|---|
| evaluator=DENY | 工具不执行，返回权限拒绝错误 |
| evaluator=ASK + gateway 批准 | 执行，gateway.respond 被调用 |
| evaluator=ASK + gateway 拒绝 | 不执行，返回拒绝原因 |
| evaluator=ASK + 无 gateway | **fail-open 但有 warning**（保持现有语义，不改变） |
| react 路径带 ctx | ToolContext 携带 session_id/workspace/tool_call_id |
| 无 factory | 行为与今天一致（回归） |

## 5. 风险

| 风险 | 缓解 |
|---|---|
| fail-open 语义被误改 | 显式测试钉死现状（`tools.py:688` 的 warning 分支） |
| registry.execute 签名变更破坏调用方 | 关键字参数 + 默认 None，所有现有调用零改动 |
| AgentLoop 与 react 的 ctx 内容漂移 | 共享 factory，单一事实源 |
| 权限测试需要异步 gateway | 现有 `test_permission_ainvoke` 提供模式 |

## 6. 本次范围

只做「react 能拿到 ctx 且权限闸门生效」这一条链路。不做：
- 沙箱/data_store 的完整移植（跟着 factory 自然带过来即可，但不在本轮验收）
- 子 agent 委派（B1-e 之后单独处理）
