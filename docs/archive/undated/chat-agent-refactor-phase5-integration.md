# Phase 5 — PromptBuilder 集成 (4 调用点统一)

> 状态：设计中
> 范围：**P0 — system_prompt 4 处调用点统一**，最大 DRY 收益、最小改动
> 上游：[phase4-prompt-builder.md](./chat-agent-refactor-phase4-prompt-builder.md)

## 1. 目标

消除 4 处并行 `system_prompt` 加载路径，全部走 `PromptBuilderFactory`：

| # | 位置 | 当前实现 | 行数 | 替换为 |
|---|---|---|---|---|
| 1 | `api/routers/chat.py:96-107` `_get_system_prompt()` | 12 行 fallback 链（chat.md → `_CHAT_PROMPT_PATH` → 内联字符串） | 12 | 删除 |
| 2 | `api/routers/chat.py:294` | `system_prompt = _get_system_prompt()` | 1 | `PromptBuilderFactory.get("chat").build_system_prompt("chat", ctx)` |
| 3 | `api/session/service.py:657-659` | `from ..routers.chat import _get_system_prompt` + `_get_system_prompt()` | 3 | `from ...core.agent.prompt_builder import PromptBuilderFactory` + factory |
| 4 | `cli/tui/session.py:250-251` | `from strategy_research.cli.tui import _CHAT_PROMPT_PATH` + `read_text()` | 2 | factory |
| 5 | `cli/tui/session.py:243-247` | `from role_factory import _load_role_system_prompt` + 调用 | 5 | factory |
| 6 | `core/agent/role_factory.py:53-61` `_load_role_system_prompt()` | 9 行读 `.prompts/<role>.md` | 9 | 删除（用 PromptBuilder 替代） |
| 7 | `core/agent/role_factory.py:120` | `system_prompt = _load_role_system_prompt(role)` | 1 | factory |
| 8 | `cli/tui/__init__.py:26-29,46` `_CHAT_PROMPT_PATH` | 模块常量 + `__all__` | 4 | 删除（无引用） |

**总减少**：~37 行分散代码 → 4 个 `PromptBuilderFactory.get(...)` 单行调用

## 2. 设计决策

### 2.1 模板引擎：str.format() 而非 jinja2

**用户反馈**："能否也保持 python 风格" — 指现有 `chat.md` 用 Python `.format()` 占位符（`{workspace}` / `{tool_list}`）。

- **现有资产**（git 跟踪，**事实源**）：
  - `src/strategy_research/templates/.prompts/chat.md` — 2 变量 (`{workspace}`, `{tool_list}`)
  - `src/strategy_research/templates/.prompts/researcher.md` 等 10 个角色 — 9 个纯静态，1 个 (`anti_overfit_analyst.md`) 有 `{strategy_name}` / `{workspace}`
- **Phase 4 误判**：我创建了 `core/agent/templates/*.md.j2`（jinja2 风格），与现有 `.prompts/*.md` 重复且风格不一致
- **Phase 5 修正**：
  - **删除** `core/agent/templates/chat.md.j2` + `researcher.md.j2`
  - **`_TEMPLATES_DIR` 指向 `src/strategy_research/templates/.prompts/`**（与 `role_factory._prompts_dir()` 一致）
  - **`ChatPromptBuilder` 用 `str.format()` 渲染**（兼容现有占位符）
  - **`StaticFilePromptBuilder` 直接 `read_text()`**（无渲染，给 9 角色用）
  - **删除 jinja2 依赖**

### 2.2 双 Builder 策略

| Builder | 角色 | 渲染 | 模板来源 |
|---|---|---|---|
| `ChatPromptBuilder` | `chat` | `str.format(workspace=..., tool_list=...)` | `templates/.prompts/chat.md` |
| `StaticFilePromptBuilder(role)` | 9 角色 + future | 无（直接 `read_text()`） | `templates/.prompts/<role>.md` |

- **保留占位符字面量行为**：当前所有调用点都返回字面量 markdown（`{workspace}` 不会被替换），保持兼容
- **未来启用渲染**：调用方传 `context={"workspace": "/w", "tool_list": "..."}` 即可激活 `.format()`
- **不破坏现有 stub fallback**：9 角色 prompt 缺失时返回 `""`，与 `role_factory` 原行为一致

### 2.3 PromptBuilderFactory 注册机制

```python
class PromptBuilderFactory:
    _BUILDERS = {
        "chat": ChatPromptBuilder,
        "researcher": StaticFilePromptBuilder("researcher"),
        "data_quality": StaticFilePromptBuilder("data_quality"),
        # ... 10 个角色
    }
    # 兼容现有 fallback：未知 role 返回空字符串（不抛异常）
    # 与 role_factory._ROLE_PROMPT_FILES.get(role, "") 行为对齐
```

**与 Phase 4 不同**：
- Phase 4 设计未知 role 抛 `ValueError`
- Phase 5 改为未知 role 返回 `""`（兼容现有 `role_factory` 行为，9 角色外部仍可能传未知角色名）
- 这是**行为兼容的妥协**，不是设计倒退

### 2.4 chat.py 的内联字符串 fallback

```python
# 当前 chat.py:107
return "你是 QuantNodes-Research 的量化金融助手。用自然语言回复，简洁直接。"
```

移到 `ChatPromptBuilder.__init__` 的 `_FALLBACK_PROMPT` 常量（当 chat.md 缺失时返回）。

## 3. 实施步骤（7 步）

| 步骤 | 内容 | 改动量 | 风险 |
|---|---|---|---|
| 1 | 重写 `prompt_builder.py`：删 jinja2 + 新增 `StaticFilePromptBuilder` + 改 `PromptBuilderFactory` 注册 10 角色 + `_TEMPLATES_DIR` 指向 `.prompts/` | ~150 行 | 低 |
| 2 | 删除 `core/agent/templates/chat.md.j2` + `researcher.md.j2` | -2 文件 | 低 |
| 3 | 改写 `tests/test_prompt_builder.py`：12 测试改用新路径 + `.format()` 渲染验证 | ~190 行 | 低 |
| 4 | 删除 `chat.py:_get_system_prompt()` + 改 `:294` 调用点为 factory | -12 +1 行 | 中（需确保 service.py 不破） |
| 5 | 改 `service.py:657-659`：从 prompt_builder 导入，删除从 chat.py 的依赖 | -3 行 | 中（api 链路） |
| 6 | 改 `tui/session.py:243-251`：2 调用点都改 factory；删除 `_CHAT_PROMPT_PATH` import | -2 行 | 中（cli 链路） |
| 7 | 删除 `role_factory._load_role_system_prompt` + 改 `:120` 为 factory；删除 `cli/tui/__init__.py:_CHAT_PROMPT_PATH` + `__all__` | -13 +1 行 | 中 |

**总改动**：~7 个文件 +370/-90 行

## 4. PromptBuilder 重构（步骤 1 细节）

### 4.1 新接口

```python
from pathlib import Path
from typing import Any, Protocol

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "templates" / ".prompts"
# resolves to: src/strategy_research/templates/.prompts/

class PromptBuilder(Protocol):
    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str: ...
    def build_messages(self, user_query: str, history: list[Message], context: dict[str, Any]) -> list[Message]: ...
    def estimate_tokens(self, messages: list[Message]) -> int: ...
    def validate(self, messages: list[Message]) -> ValidationResult: ...


class ChatPromptBuilder:
    """Loads chat.md and renders with str.format() for Python-style placeholders.
    
    Variables: {workspace}, {tool_list}
    Fallback: 内联常量 "你是 QuantNodes-Research..." when chat.md missing
    """
    FALLBACK_PROMPT = "你是 QuantNodes-Research 的量化金融助手。用自然语言回复，简洁直接。"
    
    def __init__(self) -> None:
        self._path = _PROMPTS_DIR / "chat.md"
    
    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        if not self._path.exists():
            return self.FALLBACK_PROMPT
        text = self._path.read_text(encoding="utf-8")
        try:
            return text.format(
                workspace=context.get("workspace", ""),
                tool_list=context.get("tool_list", ""),
            )
        except KeyError:
            return text  # 未声明的占位符 → 返回原文（占位符字面量）


class StaticFilePromptBuilder:
    """Loads <role>.md as-is (no rendering). For 9 roles in role_factory.
    
    Placeholders like {strategy_name} / {workspace} are returned as literals —
    matches existing role_factory behavior.
    """
    def __init__(self, role: str) -> None:
        self._role = role
        self._path = _PROMPTS_DIR / f"{role}.md"
    
    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")


class PromptBuilderFactory:
    _BUILDERS: dict[str, PromptBuilder] = {
        "chat": ChatPromptBuilder(),
        "researcher": StaticFilePromptBuilder("researcher"),
        "data_quality": StaticFilePromptBuilder("data_quality"),
        "factor_analyst": StaticFilePromptBuilder("factor_analyst"),
        "strategist": StaticFilePromptBuilder("strategist"),
        "portfolio_construction": StaticFilePromptBuilder("portfolio_construction"),
        "risk_controller": StaticFilePromptBuilder("risk_controller"),
        "attribution_analyst": StaticFilePromptBuilder("attribution_analyst"),
        "anti_overfit_analyst": StaticFilePromptBuilder("anti_overfit_analyst"),
        "backtest_diagnostics": StaticFilePromptBuilder("backtest_diagnostics"),
        "critic": StaticFilePromptBuilder("critic"),
    }
    
    @classmethod
    def get(cls, role: str) -> PromptBuilder:
        """Get builder for role. Returns NullBuilder for unknown roles (returns '').
        
        兼容 role_factory 既有行为：未知 role 不抛异常，返回空字符串让 stub fallback 接管。
        """
        if role not in cls._BUILDERS:
            return _NullBuilder()
        return cls._BUILDERS[role]
    
    @classmethod
    def register(cls, role: str, builder: PromptBuilder) -> None: ...
    @classmethod
    def list_roles(cls) -> list[str]: ...


class _NullBuilder:
    """Returned by PromptBuilderFactory.get(unknown_role) for backward compat."""
    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        return ""
    def build_messages(self, *args, **kwargs) -> list[Message]:
        return []
    def estimate_tokens(self, messages): return 0
    def validate(self, messages) -> ValidationResult:
        return ValidationResult(ok=True)
```

### 4.2 关键变化

| 项 | Phase 4 | Phase 5 |
|---|---|---|
| 模板引擎 | jinja2 | str.format() |
| `_TEMPLATES_DIR` | `core/agent/templates/` (新) | `src/strategy_research/templates/.prompts/` (事实源) |
| 模板文件 | `*.md.j2` (新建) | `*.md` (复用现有) |
| 9 角色支持 | 仅 `chat` + `researcher` | 10 个全部 |
| 未知 role | `ValueError` | `""` (兼容 role_factory) |
| jinja2 依赖 | 需要 | **删除** |

## 5. 调用点替换（步骤 4-7 细节）

### 5.1 chat.py

```python
# 删除 line 96-107 (12 行 _get_system_prompt 函数)
# 删除 line 103 (from ... import _CHAT_PROMPT_PATH)

# line 294 替换:
# 旧: system_prompt = _get_system_prompt()
# 新:
from strategy_research.core.agent.prompt_builder import PromptBuilderFactory
system_prompt = PromptBuilderFactory.get("chat").build_system_prompt(
    "chat", {"workspace": "", "tool_list": ""}
)
```

### 5.2 service.py

```python
# 旧 (line 654-661):
if system_prompt is None:
    try:
        from ..routers.chat import _get_system_prompt
        system_prompt = _get_system_prompt()
    except Exception:
        system_prompt = "你是 QuantNodes-Research 的量化金融助手。"

# 新:
if system_prompt is None:
    from ...core.agent.prompt_builder import PromptBuilderFactory
    system_prompt = PromptBuilderFactory.get("chat").build_system_prompt(
        "chat", {"workspace": "", "tool_list": ""}
    )
```

### 5.3 tui/session.py

```python
# 旧 (line 240-253):
if mode == "goal":
    try:
        from strategy_research.core.agent.role_factory import _load_role_system_prompt
        system_prompt = _load_role_system_prompt("researcher")
    except Exception:
        system_prompt = ""
else:
    try:
        from strategy_research.cli.tui import _CHAT_PROMPT_PATH
        system_prompt = _CHAT_PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        system_prompt = ""

# 新:
from strategy_research.core.agent.prompt_builder import PromptBuilderFactory
system_prompt = PromptBuilderFactory.get("researcher" if mode == "goal" else "chat").build_system_prompt(
    "researcher" if mode == "goal" else "chat",
    {"workspace": "", "tool_list": ""},
)
```

### 5.4 role_factory.py

```python
# 删除 line 53-61 (_load_role_system_prompt 函数)
# 删除 line 47-50 (_prompts_dir 函数 — 不再需要)

# line 120 替换:
# 旧: system_prompt = _load_role_system_prompt(role)
# 新:
from .prompt_builder import PromptBuilderFactory
system_prompt = PromptBuilderFactory.get(role).build_system_prompt(role, {})
```

### 5.5 cli/tui/__init__.py

```python
# 删除 line 23-29 (_CHAT_PROMPT_PATH 定义)
# 删除 line 24 注释
# 删除 line 46 __all__ 中的 "_CHAT_PROMPT_PATH"
```

## 6. 测试策略

### 6.1 test_prompt_builder.py 重写（步骤 3）

保留 12 个测试，改用新路径 + `.format()` 验证：

| # | 测试 | 边界条件 |
|---|---|---|
| 1 | `test_chat_builder_empty_context` | workspace="" tool_list="" → 占位符替换为空字符串 |
| 2 | `test_chat_builder_with_workspace` | workspace="/w" → `{workspace}` → `/w` |
| 3 | `test_chat_builder_special_chars` | workspace 含 `"` / `\n` → 正确渲染（str.format 无转义问题） |
| 4 | `test_chat_builder_unicode` | 中文 / emoji → 正确 |
| 5 | `test_chat_builder_template_missing` | chat.md 不存在 → 返回 FALLBACK_PROMPT |
| 6 | `test_chat_builder_unrendered_placeholder` | context 缺 key → 返回原文（占位符字面量） |
| 7 | `test_chat_builder_validate_ok` | tokens < 128K → ok=True |
| 8 | `test_chat_builder_validate_overflow` | tokens > 128K → ok=False |
| 9 | `test_static_builder_researcher` | researcher.md 内容正确加载 |
| 10 | `test_factory_returns_chat_for_chat` | role="chat" → ChatPromptBuilder 实例 |
| 11 | `test_factory_returns_static_for_researcher` | role="researcher" → StaticFilePromptBuilder 实例 |
| 12 | `test_factory_unknown_role_returns_empty` | role="unknown" → 返回 ""（不抛异常） |

### 6.2 test_prompt_builder_integration.py 新建

验证 4 调用点替换后行为不变：

| # | 测试 | 验证 |
|---|---|---|
| 1 | `test_chat_py_no_more_get_system_prompt` | `from chat import _get_system_prompt` ImportError |
| 2 | `test_service_uses_factory` | mock PromptBuilderFactory.get，验证 service.py 走工厂 |
| 3 | `test_tui_session_uses_factory` | mock factory，验证 tui/session.py 走工厂 |
| 4 | `test_role_factory_uses_factory` | role_factory.build_agent_loop 走工厂 |
| 5 | `test_no_chat_prompt_path_constant` | `from cli.tui import _CHAT_PROMPT_PATH` ImportError |

### 6.3 现有测试（不能破）

- `tests/test_chat_send_sync_run_traversal.py` (6 测试)
- `tests/test_role_factory.py` (16 测试) — 验证 9 角色 AgentLoop 构造
- `tests/test_streaming_chat_path.py` (baseline 11F — 与本次无关)

## 7. 风险表

| 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|
| `_get_system_prompt` 12 行 fallback 链行为丢失 | 中 | 低 | 内联 `FALLBACK_PROMPT` 常量保留；chat.md 缺失时返回 |
| `service.py` 链路意外回归 | 高 | 中 | 完整 mock 测试 + 现有 `test_chat_send_sync_run_traversal.py` 验证 |
| `tui/session.py` 链路意外回归 | 高 | 中 | TUI 启动测试 + `_CHAT_PROMPT_PATH` import 失败的 monkeypatch 测试 |
| `role_factory` 9 角色 PromptBuilder 缺角色 | 中 | 低 | factory 一次性注册 10 角色（hardcode dict）；单测验证每个 role |
| 现有 `chat.md` `{workspace}` 字面量行为变更 | 中 | 中 | 默认 context=`{}` → `.format()` 缺 key 抛 `KeyError` → 改为 try/except 返回原文（保留字面量） |
| 删 `templates/*.md.j2` 导致 Phase 4 测试找不到 | 低 | 低 | 步骤 3 同步重写测试 |

## 8. 行为变更表

| 调用方 | Before | After | 用户感知 |
|---|---|---|---|
| web chat (`chat.py:294`) | 12 行 fallback 链返回字面量 markdown | factory 返回字面量 markdown（无 workspace/tool_list 注入） | **完全不变** |
| service (`service.py:659`) | 同上 | 同上 | **完全不变** |
| TUI chat (`tui/session.py:251`) | `_CHAT_PROMPT_PATH.read_text()` | factory | **完全不变** |
| TUI goal (`tui/session.py:245`) | `role_factory._load_role_system_prompt("researcher")` | factory | **完全不变** |
| 9 角色 AgentLoop (`role_factory:120`) | `_load_role_system_prompt(role)` | factory | **完全不变**（9 角色都是纯静态 markdown） |

**0 行为变更** — 这次重构是纯结构改进，不引入产品行为差异。

## 9. 未来扩展（P1/P2 — 不在本 Phase）

- **P1**: 真正启用 `{workspace}` 渲染（在调用方传 context `{"workspace": "/w", "tool_list": "..."}`）— 需要先确认 chat 模式是否真的需要工具（当前 `allowed_tools=[]` 强制禁用）
- **P2**: 提取 `chat_loop_factory.py` 统一 web/TUI 的 AgentLoop 构造（registry / workspace / allowed_tools）
- **P3**: 解除 web chat 的 `allowed_tools=[]` 限制，让 web 也能调工具（行为变更需产品确认）

## 10. 提交策略

| Commit | 范围 | 信息 |
|---|---|---|
| 1/3 | `docs/archive/undated/chat-agent-refactor-phase5-integration.md` | `docs(chat-agent): Phase 5 设计 — PromptBuilder 4 调用点统一` |
| 2/3 | `prompt_builder.py` 重写 + `test_prompt_builder.py` 重写 + 删 `templates/*.md.j2` | `refactor(chat): PromptBuilder 改 str.format() + 10 角色注册` |
| 3/3 | `chat.py` + `service.py` + `tui/session.py` + `role_factory.py` + `cli/tui/__init__.py` + `test_prompt_builder_integration.py` | `refactor(chat): 4 调用点统一走 PromptBuilderFactory` |

## 11. 验证清单

- [ ] `tests/test_prompt_builder.py` 12/12 通过
- [ ] `tests/test_prompt_builder_integration.py` 5/5 通过
- [ ] `tests/test_chat_send_sync_run_traversal.py` 6/6 通过
- [ ] `tests/test_role_factory.py` 16/16 通过
- [ ] `tests/test_session.py` + `test_session_state.py` 通过
- [ ] `python3 -m ruff check` clean
- [ ] `grep -r "_get_system_prompt\|_CHAT_PROMPT_PATH\|_load_role_system_prompt" src/` → 0 命中
- [ ] git status clean（除 templates/ 工作区外）
