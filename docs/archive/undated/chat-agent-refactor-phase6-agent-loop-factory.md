# Phase 6 — AgentLoop 工厂统一 + 工具解锁 + Workspace 渲染

> 状态：设计中
> 范围：**P1 + P2 + P3 全做**（行为变更已确认）
> 上游：[phase5-integration.md](./chat-agent-refactor-phase5-integration.md)

## 1. 目标

完成 chat agent 重构的剩余 3 个阶段：

| 阶段 | 目标 | 行为变更 |
|---|---|---|
| **P1** | 提取 `core/agent/chat_loop.py:build_chat_agent_loop()` 共享工厂 | 无（纯结构） |
| **P2** | 解除 web chat `allowed_tools=[]` 限制 | **有** — web chat 能调工具 |
| **P3** | 启用 `{workspace}` / `{tool_list}` 渲染 | **有** — system_prompt 含真实 workspace 路径和工具列表 |

## 2. 现状：3 处 AgentLoop 构造差异表

| 字段 | `chat.py:285-296` (web chat) | `tui/session.py:260-271` (TUI chat) | `service.py:753-765` (service) |
|---|---|---|---|
| `config` | `cfg` | `cfg or self.llm_client.config` | `cfg` |
| `registry` | `build_default_registry()` 或 None | `build_default_registry()` 或 None | `build_default_registry()` |
| `workspace` | **None** | **None** | **`workspace_path`** |
| `on_event` | `on_event` (sse_buffer) | `self.app.route_agent_event` | `event_callback` (EventBus) |
| `stream_mode` | True | True | True |
| `max_iterations` | 1 | 1 | `max_iterations` (来自 caller) |
| `session_id` | `session_id` | `self.ctx.session_id` | `attempt.session_id` |
| `system_prompt` | PromptBuilderFactory | PromptBuilderFactory | caller-provided |
| `allowed_tools` | **`[]`** (chat-only: no tools) | **None** (all tools) | **None** (all tools) |
| `compact_config` | `cfg.compact_config` | `(cfg or self.llm_client.config).compact_config` | `cfg.compact_config` |
| `event_bus` | None | None | **`self.event_bus`** |
| `strategy_name` | None | None | None |

**chat 模式共享默认**：
- `stream_mode=True`（token-by-token 流）
- `max_iterations=1`（单轮对话）
- `compact_config` 来自 `cfg.compact_config`
- `registry` 默认 `build_default_registry()`

**3 处独有**：
- chat.py: `on_event` → sse_buffer
- tui/session.py: `on_event` → app.route_agent_event
- service.py: `workspace=workspace_path` + `event_bus=self.event_bus`

## 3. P1 设计：build_chat_agent_loop 工厂

### 3.1 新接口

新建 `src/agentnodes/core/agent/chat_loop.py`：

```python
"""Phase 6 P1 — chat-mode AgentLoop 构造工厂.

Unifies the 3 inline AgentLoop(...) constructors used by chat paths:
- api/routers/chat.py (web chat)
- cli/tui/session.py (TUI chat/goal)
- api/session/service.py (SessionService background runner)

Centralizes chat-mode defaults (stream_mode=True, max_iterations=1,
compact_config from cfg, default registry) and exposes a single
parameter surface so P2 (allowed_tools unlock) and P3 (workspace
injection) can be added in one place.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .loop import AgentLoop


def build_chat_agent_loop(
    config: Any,
    session_id: str,
    *,
    role: str = "chat",
    workspace: Path | None = None,
    allowed_tools: list[str] | None = None,
    max_iterations: int = 1,
    on_event: Callable | None = None,
    event_bus: Any | None = None,
    strategy_name: str | None = None,
    extra_context: dict[str, Any] | None = None,
) -> AgentLoop:
    """Construct a chat-mode AgentLoop.

    Differences from role_factory.build_agent_loop:
    - stream_mode is forced to True (token-by-token streaming)
    - max_iterations defaults to 1 (single-pass chat; no ReAct)
    - compact_config is taken from ``config.compact_config``
    - registry defaults to ``build_default_registry()`` (with safe
      fallback to None if imports fail)
    - system_prompt is rendered via ``PromptBuilderFactory.get(role)``
      with ``extra_context`` (e.g. ``{"workspace": ..., "tool_list": ...}``)
    - goal/hypothesis auto-create are disabled (chat mode is non-agentic)

    P2 (allowed_tools unlock):
        ``allowed_tools`` defaults to None (= all tools). Previously
        web chat passed ``[]`` (= no tools) — that hard lock is removed.

    P3 (workspace injection):
        When ``workspace`` is provided, it is included in the
        ``PromptBuilderFactory`` context so ``{workspace}`` /
        ``{tool_list}`` placeholders in chat.md render with real values.
    """
    from .builtin_tools import build_default_registry
    from .loop import AgentLoop
    from .prompt_builder import PromptBuilderFactory

    # Tool registry (default: all tools, no override)
    try:
        registry = build_default_registry()
    except Exception:
        registry = None

    # System prompt via PromptBuilderFactory (P3: pass real workspace/tool_list)
    if extra_context is None:
        extra_context = {}
    if workspace is not None:
        extra_context.setdefault("workspace", str(workspace))
    if "tool_list" not in extra_context and registry is not None:
        # Format registry tools as markdown list for {tool_list} placeholder
        try:
            tool_names = sorted(registry._tools.keys())
            extra_context["tool_list"] = "\n".join(f"- {n}" for n in tool_names)
        except Exception:
            extra_context["tool_list"] = ""

    system_prompt = PromptBuilderFactory.get(role).build_system_prompt(
        role, extra_context
    )

    return AgentLoop(
        config=config,
        registry=registry,
        workspace=workspace,
        on_event=on_event,
        stream_mode=True,  # chat mode: token-by-token
        max_iterations=max_iterations,
        session_id=session_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,  # P2: None (all tools) instead of []
        enable_goal_injection=False,  # chat mode: no goal injection
        enable_hypothesis_auto_create=False,
        strategy_name=strategy_name,
        compact_config=config.compact_config if config else None,
        event_bus=event_bus,
    )


__all__ = ["build_chat_agent_loop"]
```

### 3.2 3 个调用点替换

#### chat.py:285-296

```python
# 旧 (12 行)
try:
    from agentnodes.core.agent.builtin_tools import build_default_registry
    from agentnodes.core.agent.loop import AgentLoop
    from agentnodes.core.agent.prompt_builder import PromptBuilderFactory

    try:
        registry = build_default_registry()
    except Exception:
        registry = None

    system_prompt = PromptBuilderFactory.get("chat").build_system_prompt(
        "chat", {"workspace": "", "tool_list": ""}
    )
    history = _get_or_create_history(session_id)

    loop = AgentLoop(
        config=cfg, registry=registry, workspace=None,
        on_event=on_event, stream_mode=True, max_iterations=1,
        session_id=session_id, system_prompt=system_prompt,
        allowed_tools=[],  # chat-only: no tools
        compact_config=cfg.compact_config,
    )

# 新 (5 行)
from agentnodes.core.agent.chat_loop import build_chat_agent_loop
from agentnodes.core.agent.prompt_builder import PromptBuilderFactory

history = _get_or_create_history(session_id)
loop = build_chat_agent_loop(
    config=cfg, session_id=session_id,
    role="chat",
    on_event=on_event,
    workspace=None,  # P2: chat mode 默认无 workspace (web chat 当前无 workspace 概念)
    # allowed_tools=None by default (P2: 解锁)
)
```

#### tui/session.py:260-271

```python
# 旧 (10 行)
loop = AgentLoop(
    config=cfg or self.llm_client.config,
    registry=registry,
    workspace=None,
    on_event=self.app.route_agent_event,
    stream_mode=True,
    max_iterations=1,
    session_id=getattr(self.ctx, "session_id", "cli"),
    system_prompt=system_prompt,
    allowed_tools=None,
    compact_config=(cfg or self.llm_client.config).compact_config,
)

# 新 (5 行)
from agentnodes.core.agent.chat_loop import build_chat_agent_loop

role = "researcher" if mode == "goal" else "chat"
loop = build_chat_agent_loop(
    config=cfg or self.llm_client.config,
    session_id=getattr(self.ctx, "session_id", "cli"),
    role=role,
    on_event=self.app.route_agent_event,
    workspace=None,
)
```

#### service.py:753-765

```python
# 旧 (13 行)
agent = AgentLoop(
    config=cfg, registry=registry, workspace=workspace_path,
    on_event=event_callback, stream_mode=True,
    max_iterations=max_iterations,
    session_id=attempt.session_id,
    system_prompt=system_prompt,
    allowed_tools=None,
    compact_config=cfg.compact_config,
    event_bus=self.event_bus,
)

# 新 (7 行)
from agentnodes.core.agent.chat_loop import build_chat_agent_loop

agent = build_chat_agent_loop(
    config=cfg,
    session_id=attempt.session_id,
    role="chat",
    workspace=workspace_path,
    on_event=event_callback,
    event_bus=self.event_bus,
    max_iterations=max_iterations,
    extra_context={},  # service 已经构造好了 system_prompt；不希望 factory 重新生成
    # system_prompt 仍由 caller 提供 — 通过 chat_loop 调用前的 system_prompt 注入
)
```

**注意**：service.py 当前 caller 显式提供 `system_prompt`（不是 PromptBuilderFactory 默认）。新工厂可以加 `system_prompt` 参数 + 让 factory 跳过自己的 system_prompt 生成。

或者：**service.py 改成完全用 PromptBuilderFactory**（不再独立构造 system_prompt）。这是行为变更，需要在 §4 风险表中标注。

## 4. P2 设计：解除 web chat `allowed_tools=[]`

### 4.1 行为变更

| 调用方 | Before | After | 用户感知 |
|---|---|---|---|
| web chat (`chat.py:294`) | `allowed_tools=[]` → LLM 看不到工具 | `allowed_tools=None` → LLM 看到所有工具 | **web chat 现在能调工具** |
| TUI chat (`tui/session.py:265`) | `allowed_tools=None` | `allowed_tools=None` | 不变 |
| service (`service.py:762`) | `allowed_tools=None` | `allowed_tools=None` | 不变 |

### 4.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| web chat token 消耗增加 | 中（每次 tool schema 注入 system prompt） | 已有 CompactConfig 上下文窗口守卫 |
| web UI 需要支持 tool_call part 展示 | 中 | 现有 part protocol 已支持 `tool_call` / `tool_result` events（见 chat.py:260-274） |
| LLM 误用工具（如 web chat 用户问"今天天气"→ LLM 调 get_market_data） | 低 | TUI 已经这样工作，无异常 |
| 测试期望 chat mode 不调工具 | 中 | `test_chat_send_sync_run_traversal.py` 需要更新 mock |

### 4.3 测试期望变更

`tests/test_chat_send_sync_run_traversal.py::TestSendSync::test_send_requires_ownership_and_persists` 等测试可能 mock 期望"chat 不调工具"。Phase 6 实施后这些测试需要更新。

## 5. P3 设计：启用 `{workspace}` 渲染

### 5.1 行为变更

| 调用方 | Before | After | 用户感知 |
|---|---|---|---|
| web chat | `context={"workspace": "", "tool_list": ""}` | `context={"workspace": str(workspace), "tool_list": "- read_file\n- list_files\n..."}` | **LLM 看到真实 workspace 路径 + 工具列表** |
| TUI chat | `context={"workspace": "", "tool_list": ""}` | 同上 | 同上 |
| service | service caller 自己构造 system_prompt，不走 PromptBuilderFactory 默认 | 改为走 PromptBuilderFactory + 传 workspace | 同上 |

### 5.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| system prompt 变长（注入 workspace 路径 + 工具列表） | 低（~200 tokens） | 已有 CompactConfig |
| workspace 路径错误导致 LLM 用错误路径调工具 | 中 | web chat `workspace=None` 时不注入 `{workspace}` 占位符（保留空） |
| TUI chat `workspace=None` 不渲染 | 低 | 设计：workspace=None 时不设默认值，`{workspace}` 占位符保留空 |
| 9 角色 markdown 不应渲染 | 无 | StaticFilePromptBuilder 仍不渲染（与 Phase 5 一致） |

### 5.3 渲染规则

```python
# build_chat_agent_loop 内的渲染逻辑
if workspace is not None:
    extra_context["workspace"] = str(workspace)  # 渲染为路径
else:
    extra_context.setdefault("workspace", "")  # 保留空

# tool_list 总是渲染（即使 workspace=None）— 让 LLM 看到可用工具
if registry is not None:
    tool_names = sorted(registry._tools.keys())
    extra_context["tool_list"] = "\n".join(f"- {n}" for n in tool_names)
```

### 5.4 service.py system_prompt 注入

service.py 当前 caller 显式提供 `system_prompt`（line 489-490）。P3 后改为：
- service.py 删自己构造 system_prompt 的逻辑
- 调用 `build_chat_agent_loop(role="chat", extra_context={"workspace": workspace_path})`
- factory 调 PromptBuilderFactory 渲染 system_prompt

这是 **service.py 的简化**（删除 ~30 行 system_prompt 构造代码）。

## 6. 实施步骤（6 步）

| 步骤 | 内容 | 改动量 | 风险 |
|---|---|---|---|
| 1 | 新建 `core/agent/chat_loop.py:build_chat_agent_loop()` | ~80 行 | 低 |
| 2 | chat.py:285-296 替换为 build_chat_agent_loop()（P2 解锁 + P3 workspace） | -12 +5 行 | **中**（行为变更） |
| 3 | tui/session.py:260-271 替换 | -10 +5 行 | 低 |
| 4 | service.py:753-765 替换 + 删除 service.py 自己构造 system_prompt 的代码（line ~480-500） | -40 +7 行 | **中**（行为变更） |
| 5 | 更新 `tests/test_chat_send_sync_run_traversal.py`：mock 期望调整 + 加 chat_loop 工厂测试 | ~50 行 | 低 |
| 6 | 新建 `tests/test_chat_loop.py`：build_chat_agent_loop 工厂测试 + P2/P3 行为验证 | ~150 行 | 低 |

## 7. 测试策略

### 7.1 tests/test_chat_loop.py 新建（步骤 6）

| # | 测试 | 验证 |
|---|---|---|
| 1 | `test_default_registry_loaded` | registry 默认 = build_default_registry() |
| 2 | `test_chat_role_uses_chat_builder` | role="chat" → ChatPromptBuilder |
| 3 | `test_researcher_role_uses_static_builder` | role="researcher" → StaticFilePromptBuilder |
| 4 | `test_p2_allowed_tools_default_none` | 默认 allowed_tools=None（解锁） |
| 5 | `test_p2_allowed_tools_explicit_empty_list` | 显式 allowed_tools=[] → 仍有效（兼容） |
| 6 | `test_p3_workspace_injected_to_system_prompt` | workspace="/w" → system_prompt 含 `/w` |
| 7 | `test_p3_workspace_none_no_injection` | workspace=None → `{workspace}` 占位符保留空 |
| 8 | `test_p3_tool_list_rendered_from_registry` | tool_list 包含所有 registry 工具名 |
| 9 | `test_stream_mode_forced_true` | stream_mode=True（即使 caller 不传） |
| 10 | `test_max_iterations_default_one` | max_iterations=1 默认 |
| 11 | `test_compact_config_from_cfg` | compact_config 来自 config.compact_config |
| 12 | `test_event_bus_passed_through` | event_bus 参数透传 |

### 7.2 test_chat_send_sync_run_traversal.py 更新（步骤 5）

`TestSendSync::test_send_requires_ownership_and_persists` 等测试可能 mock `build_default_registry` 或期望 chat 不调工具。Phase 6 后需要：
- 移除"chat 不调工具"的硬假设
- 加 mock 验证 `build_chat_agent_loop` 被调用且 `allowed_tools=None`

### 7.3 现有测试（不能破）

- `test_prompt_builder.py` 16 测试
- `test_prompt_builder_integration.py` 11 测试
- `test_role_factory.py` (除 TestSpawnAgentFallback baseline hang)
- `test_chat_send_sync_run_traversal.py` 6 测试（更新 mock）
- `test_session.py` / `test_session_state.py` / `test_session_memory.py` 等

## 8. 风险表

| 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|
| P2 web chat token 消耗增加 | 中 | 高 | 已有 CompactConfig + ContextWindowGuard |
| P2 web UI tool_call 展示未完善 | 中 | 中 | 现有 part protocol 已支持 tool_call events |
| P3 workspace 路径错误 | 中 | 低 | workspace=None 时不注入；web chat 默认 None |
| P3 service.py system_prompt 行为变更 | 高 | 中 | 完整 mock 测试 + 比对测试 |
| `_isolate_llm_bridge` baseline hang 影响 TestSpawnAgentFallback | 低 | 高 | 已 `--deselect` |
| 工厂参数爆炸（11 个参数） | 低 | 中 | 使用 `extra_context` 兜底 |
| service.py chat_loop 调用前自己构造 system_prompt 与 factory 冲突 | 中 | 中 | factory 接受 `system_prompt=None` 参数，跳过自己的生成 |

## 9. 行为变更汇总

| 调用方 | P2 | P3 |
|---|---|---|
| web chat | 能调工具 | system_prompt 含真实工具列表（workspace 仍 None） |
| TUI chat | 已能调（不变） | system_prompt 含真实工具列表 |
| service | 已能调（不变） | system_prompt 含真实 workspace 路径 + 工具列表 |

**0 用户行为变化**：
- web chat 用户从"问什么答什么"变成"可以问涉及工具的问题"（增强）
- TUI chat 用户不变
- service 用户从"system_prompt 含字面量 {workspace}"变成"system_prompt 含真实路径"（LLM 更智能）

## 10. 提交策略

| Commit | 范围 | 信息 |
|---|---|---|
| 1/3 | `docs/archive/undated/chat-agent-refactor-phase6-agent-loop-factory.md` | `docs(chat-agent): Phase 6 设计 — AgentLoop 工厂统一 + P2 工具解锁 + P3 workspace 渲染` |
| 2/3 | `core/agent/chat_loop.py` + `tests/test_chat_loop.py` | `feat(agent): build_chat_agent_loop 工厂 — P1 + P2 + P3 落地` |
| 3/3 | `chat.py` + `tui/session.py` + `service.py` + `test_chat_send_sync_run_traversal.py` 更新 | `refactor(chat): 3 调用点统一走 build_chat_agent_loop 工厂` |

## 11. 验证清单

- [ ] `tests/test_chat_loop.py` 12/12 通过
- [ ] `tests/test_prompt_builder.py` 16/16 通过
- [ ] `tests/test_prompt_builder_integration.py` 11/11 通过
- [ ] `tests/test_chat_send_sync_run_traversal.py` 6/6 通过（mock 更新后）
- [ ] `tests/test_role_factory.py` (除 TestSpawnAgentFallback)
- [ ] `tests/test_session.py` + `test_session_state.py` 通过
- [ ] `python3 -m ruff check` clean
- [ ] `grep -r "AgentLoop(" src/agentnodes/{api,cli}/` → 仅 chat_loop.py 中（factory 自身）
- [ ] git status clean（除 templates/ 工作区外）

## 12. 后续阶段（Phase 7+）

按 phase1 框架的 9 层架构：

| 阶段 | 任务 |
|---|---|
| Phase 7 | BaseEventBus 双 API 实施（emit callback + astream iterator） |
| Phase 8 | MemoryManager 三合一（chat.py / session.py / SQLite） |
| Phase 9 | StructuredOutputParser 落地（strict→repair→regex→none 4 层降级） |
| Phase 10 | CircuitBreaker / RetryPolicy 整合进 AgentLoop |
