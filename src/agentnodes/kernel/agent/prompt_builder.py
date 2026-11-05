"""PromptBuilder — Phase 4/5 of the Chat Agent refactor + Common Layer (truthfulness).

Unifies four parallel ``system_prompt`` loading paths:
- ``api/routers/chat.py:_get_system_prompt`` (web chat, removed in Phase 5)
- ``api/session/service.py`` (chat service)
- ``cli/tui/session.py`` (TUI chat/goal mode)
- ``core/agent/role_factory._load_role_system_prompt`` (9 Goal roles)

Design (Phase 5):
- Strategy pattern: ``ChatPromptBuilder`` / ``StaticFilePromptBuilder``
- Templates live in ``src/agentnodes/templates/.prompts/*.md`` (the
  actual asset location; Phase 4's ``core/agent/templates/*.md.j2`` was
  removed because it duplicated existing ``.prompts/*.md`` files).
- ``ChatPromptBuilder`` renders ``chat.md`` with ``str.format()`` to
  preserve the existing Python-style placeholders (``{workspace}`` /
  ``{tool_list}``); undeclared placeholders return the raw text (literal
  behavior — matches what callers observed before Phase 4).
- ``StaticFilePromptBuilder(role)`` returns ``<role>.md`` verbatim, no
  rendering. Matches ``role_factory._load_role_system_prompt`` behavior
  where ``{strategy_name}`` / ``{workspace}`` are literal text.
- ``PromptBuilderFactory.get(unknown_role)`` returns ``_NullBuilder``
  instead of raising — preserves the legacy "unknown role → empty
  string → stub fallback" behavior that ``role_factory`` callers depend on.

Common Layer (truthfulness-design):
- ``_common/principles.md`` is always prepended to every role's system
  prompt (values + red lines + thinking mode + examples). Stays in
  context for every conversation so the model cannot "forget" honesty
  constraints.
- ``_common/rules/*.md`` is NOT auto-injected — roles access detailed
  rules on-demand via ``read_file`` (similar to LangChain's
  ``SkillsMiddleware`` pattern).
- ``_COMMON_OPT_OUT`` allows specific roles to skip the common layer if
  needed (reserved slot, currently empty).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── Templates directory ─────────────────────────────────────────────────


_PROMPTS_DIR = (
    Path(__file__).parent.parent.parent / "templates" / ".prompts"
)
# Resolves to: src/agentnodes/templates/.prompts/

# Common layer (truthfulness-design): principles 常驻, rules 按需
_COMMON_DIR = _PROMPTS_DIR / "_common"
_PRINCIPLES_FILE = _COMMON_DIR / "principles.md"


# ── Common layer helpers ───────────────────────────────────────────────


def _load_common(role: str) -> tuple[str, str, str]:
    """Return ``(principles, rule_injected, future)``.

    - **principles**: always loaded from ``_common/principles.md`` and
      prepended to every role's system prompt. Contains values, red
      lines, thinking mode, and examples.
    - **rule_injected**: currently empty — detailed rules in
      ``_common/rules/`` are accessed on-demand via ``read_file``.
    - **future**: reserved slot for future common layer additions.

    Opt-out: roles in ``PromptBuilderFactory._COMMON_OPT_OUT`` get empty
    strings, skipping the common layer entirely.
    """
    if role in PromptBuilderFactory._COMMON_OPT_OUT:
        return "", "", ""
    principles = (
        _PRINCIPLES_FILE.read_text(encoding="utf-8")
        if _PRINCIPLES_FILE.exists()
        else ""
    )
    return principles, "", ""


def _compose(
    principles: str,
    rule: str,
    role_specific: str,
    future: str = "",
) -> str:
    """Join common + role-specific content with ``---`` separators.

    Empty segments are dropped (so opting out produces no extra ``---``).
    """
    parts = [p for p in (principles, rule, role_specific, future) if p]
    return "\n\n---\n\n".join(parts)


# ── Validation ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a built message list (Phase 3 robustness)."""

    ok: bool
    error: str = ""


# ── Message type alias ─────────────────────────────────────────────────


Message = dict[str, Any]


# ── Protocol (Phase 2 contract) ────────────────────────────────────────


class PromptBuilder(Protocol):
    """Build system + user prompts for a given role. See phase2-interfaces.md."""

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str: ...

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]: ...

    def estimate_tokens(self, messages: list[Message]) -> int: ...

    def validate(self, messages: list[Message]) -> ValidationResult: ...


# ── Default token limit ────────────────────────────────────────────────


DEFAULT_TOKEN_LIMIT = 128_000  # matches GPT-4-class context windows


def _estimate_chars(messages: list[Message]) -> int:
    """Cheap char-count proxy: 4 chars ≈ 1 token."""
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


# ── Implementations ────────────────────────────────────────────────────


class ChatPromptBuilder:
    """Loads ``chat.md`` and renders with plain ``str.replace()``.

    Variables in ``chat.md`` (4 total):
        ``{workspace}``    — workspace root path
        ``{tool_list}``    — comma-separated tool names
        ``{name}``         — strategy name placeholder in path examples
        ``{策略名}``       — Chinese equivalent of ``{name}``

    Why ``str.replace()`` instead of ``str.format()``:
        ``chat.md`` contains Python dict literals (``{"top_n": 10, ...}``)
        that ``str.format()`` interprets as ``{key:format_spec}`` syntax and
        raises ``KeyError``. ``str.replace()`` is a literal substring
        replacement — no parsing — so it coexists with Python code samples.

    Fallback: ``FALLBACK_PROMPT`` constant when ``chat.md`` is missing.
    """

    FALLBACK_PROMPT = (
        "你是 QuantNodes-Research 的量化金融助手。"
        "用自然语言回复，简洁直接。"
    )

    # (placeholder_in_text, context_key) pairs. Order matters when one
    # placeholder is a prefix of another (none currently overlap).
    _PLACEHOLDERS: tuple[tuple[str, str], ...] = (
        ("{workspace}", "workspace"),
        ("{tool_list}", "tool_list"),
        ("{name}", "name"),
        ("{策略名}", "策略名"),
    )

    def __init__(self, token_limit: int = DEFAULT_TOKEN_LIMIT) -> None:
        self._token_limit = token_limit
        self._path = _PROMPTS_DIR / "chat.md"

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        if not self._path.exists():
            return self.FALLBACK_PROMPT
        text = self._path.read_text(encoding="utf-8")
        for placeholder, key in self._PLACEHOLDERS:
            text = text.replace(placeholder, context.get(key, ""))
        top, middle, future = _load_common(role)
        return _compose(top, middle, text, future)

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]:
        system = self.build_system_prompt("chat", context)
        messages: list[Message] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_query})
        return messages

    def estimate_tokens(self, messages: list[Message]) -> int:
        return _estimate_chars(messages) // 4

    def validate(self, messages: list[Message]) -> ValidationResult:
        tokens = self.estimate_tokens(messages)
        if tokens > self._token_limit:
            return ValidationResult(
                ok=False,
                error=(
                    f"Messages exceed token limit: {tokens}/{self._token_limit}. "
                    "Consider /clear or /compact."
                ),
            )
        return ValidationResult(ok=True)


class StaticFilePromptBuilder:
    """Loads ``<role>.md`` as-is (no rendering).

    Used for the 9 roles in ``role_factory._ROLE_PROMPT_FILES``. Placeholders
    like ``{strategy_name}`` / ``{workspace}`` are returned as literal text —
    matches existing ``role_factory._load_role_system_prompt`` behavior where
    ``_prompts_dir() / <role>.md`` is read with ``read_text()`` and never
    formatted.
    """

    def __init__(self, role: str, token_limit: int = DEFAULT_TOKEN_LIMIT) -> None:
        self._role = role
        self._token_limit = token_limit
        self._path = _PROMPTS_DIR / f"{role}.md"
        # RSI tier-1 覆盖（施工方案 §4.7）：装配期问一次并存下，
        # build 时使用。None = 未启用/无覆盖，行为与改动前一致。
        self._harness_override: dict[str, Any] | None = None
        # RSI tier-1 skill 块：装配期解析出的方法论正文，追加在 prompt 末尾。
        # None/空 = 未启用，行为与改动前逐字节一致。
        self._harness_skill_block: str | None = None

    def apply_harness_override(self, override: dict[str, Any] | None) -> None:
        """装入装配期解析出的 prompt 覆盖项（``None``/空 = 不覆盖）。"""
        self._harness_override = dict(override) if override else None

    def apply_skill_block(self, block: str | None) -> None:
        """装入 skill 方法论块（``None``/空 = 不注入）。

        与 `apply_harness_override` 分开：覆盖是**替换**正文，skill 是
        **追加**在正文之后。两者的 RSI 语义不同，混在一个方法里会让
        "只换正文"和"只加方法论"互相干扰。
        """
        self._harness_skill_block = block or None

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        if not self._path.exists() and not self._harness_skill_block:
            return ""
        text = self._path.read_text(encoding="utf-8") if self._path.exists() else ""
        # tier-1 覆盖：仅替换 role prompt 正文，common 层（principles）不动——
        # 那是 Tier 0 方法论，H3 禁止 RSI 触碰。
        override = self._harness_override or {}
        if isinstance(override.get("text"), str):
            text = override["text"]
        # tier-1 skill：追加在正文之后（排在 common 层之前，这样
        # 通用原则仍是最外层、最稳的约束）。
        block = self._harness_skill_block
        if block:
            text = f"{text}\n\n{block}" if text else block
        top, middle, future = _load_common(role)
        return _compose(top, middle, text, future)

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]:
        system = self.build_system_prompt(self._role, context)
        messages: list[Message] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_query})
        return messages

    def estimate_tokens(self, messages: list[Message]) -> int:
        return _estimate_chars(messages) // 4

    def validate(self, messages: list[Message]) -> ValidationResult:
        tokens = self.estimate_tokens(messages)
        if tokens > self._token_limit:
            return ValidationResult(
                ok=False,
                error=f"Messages exceed token limit: {tokens}/{self._token_limit}.",
            )
        return ValidationResult(ok=True)


class _NullBuilder:
    """Returned by ``PromptBuilderFactory.get(unknown_role)``.

    Preserves the legacy ``role_factory._load_role_system_prompt`` behavior:
    unknown role → empty string → stub fallback in ``build_agent_loop``.
    """

    def build_system_prompt(self, role: str, context: dict[str, Any]) -> str:
        return ""

    def build_messages(
        self,
        user_query: str,
        history: list[Message],
        context: dict[str, Any],
    ) -> list[Message]:
        return []

    def estimate_tokens(self, messages: list[Message]) -> int:
        return 0

    def validate(self, messages: list[Message]) -> ValidationResult:
        return ValidationResult(ok=True)


# ── Factory (strategy dispatch) ───────────────────────────────────────


class PromptBuilderFactory:
    """Switch by role / mode. Mirrors ``AgentRunnerFactory`` pattern.

    Unknown roles return ``_NullBuilder()`` (empty prompt) instead of
    raising — preserves backward compatibility with callers that pass
    arbitrary role names (e.g. ``role_factory.build_agent_loop``).

    DSH-inspired: auto-discovers prompts from ``templates/.prompts/*.md``
    at first lookup for unknown roles (cached).
    """

    _BUILDERS: dict[str, PromptBuilder] = {
        "chat": ChatPromptBuilder(),
        # 2026-11-05 批次 7（修正）：role_factory 的 14 个角色是**框架多角色
        # 编排能力**——researcher→strategist→critic 等构成通用的研究/评审
        # 角色链，与量化业务无关，因此提示词仍随包分发。
        # 只有 9 个纯业务角色（market_scanner / regime_classifier /
        # stress_tester / position_auditor / report_writer / benchmark_compare /
        # concentration_check / icarus_review）归档到
        # testcase/golden/templates/prompts/。
        "researcher": StaticFilePromptBuilder("researcher"),
        "data_quality": StaticFilePromptBuilder("data_quality"),
        "factor_analyst": StaticFilePromptBuilder("factor_analyst"),
        "strategist": StaticFilePromptBuilder("strategist"),
        "portfolio_construction": StaticFilePromptBuilder(
            "portfolio_construction"
        ),
        "risk_controller": StaticFilePromptBuilder("risk_controller"),
        "attribution_analyst": StaticFilePromptBuilder("attribution_analyst"),
        "anti_overfit_analyst": StaticFilePromptBuilder("anti_overfit_analyst"),
        "backtest_diagnostics": StaticFilePromptBuilder("backtest_diagnostics"),
        "workflow_orchestrator": StaticFilePromptBuilder("workflow_orchestrator"),
        "critic": StaticFilePromptBuilder("critic"),
        # Study v2 inter-round roles (design §10-11)
        "study_reviewer": StaticFilePromptBuilder("study_reviewer"),
        "study_collector": StaticFilePromptBuilder("study_collector"),
    }

    # Roles in this set skip the common layer entirely (no principles.md
    # prepend). Reserved slot for future opt-out use; currently empty.
    _COMMON_OPT_OUT: set[str] = set()

    # DSH-inspired: auto-discovered prompts (cached after first scan)
    _discovered: dict[str, PromptBuilder] | None = None

    @classmethod
    def get(cls, role: str) -> PromptBuilder:
        builder = cls.get_for_signature(role, "")
        return builder

    @classmethod
    def get_for_signature(cls, role: str, task_signature: str) -> PromptBuilder:
        """`get()` + 装配期 tier-1 覆盖（施工方案 §4.7 的实际消费点）。

        ``task_signature`` 为空时与 ``get()`` 完全等价——这是默认路径，
        保证未启用 RSI 时行为零变化。

        有覆盖时，在返回的 builder 上装入 override（若它是
        ``StaticFilePromptBuilder``；其它 builder 类型没有正文可替换，
        记录一条 debug 后原样返回）。
        """
        if role in cls._BUILDERS:
            builder = cls._BUILDERS[role]
        else:
            # DSH-inspired: auto-discover from templates/.prompts/*.md
            if cls._discovered is None:
                cls._discovered = cls._discover_prompts()
            builder = cls._discovered.get(role, _NullBuilder())

        if not task_signature:
            # 无 signature = 不走 RSI：清掉可能残留的覆盖，保证 get() 与
            # 改动前逐字节一致（builder 是进程级缓存实例，会跨调用复用）。
            if isinstance(builder, StaticFilePromptBuilder):
                builder.apply_harness_override(None)
                builder.apply_skill_block(None)
            return builder

        # skill 块与 prompt 覆盖**独立**：前者是追加方法论，后者是替换正文。
        # 任一为空都要清掉残留（同一 builder 实例跨调用复用）。
        if isinstance(builder, StaticFilePromptBuilder):
            builder.apply_skill_block(
                cls._resolve_skill_block(task_signature)
            )

        override = cls.resolve_harness_for(role, task_signature)
        if not override:
            # 同一个 builder 例会被多次 get 到（_BUILDERS / _discovered 是
            # 进程级缓存）。前一次装入的覆盖必须清掉，否则「这次没覆盖」
            # 会沿用「上次的覆盖」——那是跨 task_signature 的状态泄漏。
            if isinstance(builder, StaticFilePromptBuilder):
                builder.apply_harness_override(None)
            return builder
        if isinstance(builder, StaticFilePromptBuilder):
            builder.apply_harness_override(override)
        else:
            logger.debug(
                "harness override for %r ignored: builder is %s",
                role, type(builder).__name__,
            )
        return builder

    @classmethod
    def resolve_harness_for(cls, role: str, task_signature: str) -> dict[str, Any]:
        """装配期问一次：该 task_signature 下这个 role 的 prompt 覆盖项。

        施工方案 §4.7：**只加调用，不改内部逻辑**。返回的 dict 由调用方
        决定怎么用（当前实现是覆盖角色 prompt 文件的版本标记）；未启用
        RSI 时返回空 dict，``get()`` 的行为完全不变。
        """
        try:
            from agentnodes.kernel.agent.harness_resolver import (
                resolve_harness,
            )

            cfg = resolve_harness(task_signature)
            return dict(getattr(cfg, "prompt_version", {}) or {}).get(role, {}) or {}
        except Exception:  # noqa: BLE001 —— 装配期不得因 RSI 崩溃
            return {}

    @classmethod
    def _resolve_skill_block(cls, task_signature: str) -> str | None:
        """装配期问一次：该 task_signature 下的 skill 方法论块。

        返回 ``None`` = 不注入（未启用 RSI、skill_set 为 None、或全部
        技能名都失效）。技能正文由 ``harness_resolver.apply_skill_set``
        从 kernel 的 SkillRegistry 读取——不在 prompt_builder 里直接读
        文件，避免两处各自实现一套查找逻辑。
        """
        try:
            from agentnodes.kernel.agent.harness_resolver import (
                _load_skills,
                resolve_harness,
            )

            cfg = resolve_harness(task_signature)
            names = getattr(cfg, "skill_set", None)
            if not names:
                return None
            contents = _load_skills(sorted(names))
            if not contents:
                return None
            return "\n\n".join(
                f"## 方法论：{n}\n\n{body}" for n, body in contents
            )
        except Exception:  # noqa: BLE001 —— 装配期不得因 skill 崩溃
            return None

    @classmethod
    def list_roles(cls) -> list[str]:
        # Include discovered roles
        discovered = cls._discovered or cls._discover_prompts()
        return list(set(cls._BUILDERS.keys()) | set(discovered.keys()))

    @classmethod
    def register(cls, role: str, builder: PromptBuilder) -> None:
        """Register a new builder at runtime (mirrors ``AgentRunnerRegistry``)."""
        cls._BUILDERS[role] = builder

    @classmethod
    def _discover_prompts(cls) -> dict[str, PromptBuilder]:
        """Scan templates/.prompts/*.md for role prompt files."""
        # Reuse the module-level constant: this file sits in kernel/agent/, so
        # a bare ``parent.parent`` would resolve to kernel/templates/ and the
        # scan would silently find nothing.
        prompts_dir = _PROMPTS_DIR
        if not prompts_dir.exists():
            return {}

        builders: dict[str, PromptBuilder] = {}
        for md_file in prompts_dir.glob("*.md"):
            role = md_file.stem  # "researcher.md" → "researcher"
            if role not in cls._BUILDERS:  # don't override hardcoded
                builders[role] = StaticFilePromptBuilder(role)
                logger.debug("Auto-discovered prompt: %s", role)
        return builders


__all__ = [
    "ChatPromptBuilder",
    "Message",
    "PromptBuilder",
    "PromptBuilderFactory",
    "StaticFilePromptBuilder",
    "ValidationResult",
    "_compose",
    "_load_common",
]
