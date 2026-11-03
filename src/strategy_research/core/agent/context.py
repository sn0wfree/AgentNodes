"""ContextBuilder: build system + user messages for AgentLoop.

Responsibilities:
    - System prompt: role description + tool definitions + workspace state +
                     frozen PersistentMemory snapshot
    - Initial user message: task + <recalled-memories> block (auto-recall)
    - Token estimation (rough chars/4 heuristic)
    - Message formatting for OpenAI Chat Completions

Design notes:
    - Memory snapshot is captured ONCE at ContextBuilder construction (frozen).
      Subsequent memory writes do NOT affect this builder's snapshot.
    - find_relevant is invoked on every build_initial_messages() call (fresh).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..agent.tools import ToolRegistry
from ..llm.config import LLMConfig
from strategy_research.kernel.memory.persistent import PersistentMemory

logger = logging.getLogger(__name__)

# Rough token estimator: ~4 chars per token for English; ~1.5 chars/token for Chinese.
CHARS_PER_TOKEN = 3.0


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token count for messages list (shared impl).

    Args:
        messages: List of {"role": ..., "content": ..., ...} dicts.

    Returns:
        Estimated token count.
    """
    from strategy_research.foundation.utils.token_utils import estimate_tokens as _shared
    return _shared(messages)


class ContextBuilder:
    """Build prompts for AgentLoop.

    Typical usage:
        builder = ContextBuilder(
            config=cfg, registry=registry, memory=mem, workspace=ws,
        )
        system_prompt = builder.build_system_prompt()
        messages = builder.build_initial_messages("improve momentum_20_60")
        # ... loop ...
    """

    SYSTEM_PROMPT_HEADER = """你是策略研究助手，使用工具修改 workspace 中的 strategy.py 并跑回测。

工作区: {workspace}

可用工具:
{tool_list}

{memory_snapshot}

工作流程:
1. 读 workspace 当前 state (strategy.py + results.tsv)
2. 分析 metrics (calmar / sharpe / max_dd)
3. 用 write_file 改 strategy.py (AST guard 会拦截危险代码)
4. 用 run_backtest 跑回测
5. 用 list_history 列历史对比
6. 用 git_diff 看改动
7. 总结结果

约束:
- 每次只做 1 个改动, 跑 1 个回测, 评估
- 不要用 subprocess / 网络 / 写 workspace 外的文件
- 保持 strategy.py 模板格式 (PARAMS / FACTOR_EXPRS / FACTOR_WEIGHT_METHOD)

{user_section}"""

    def __init__(
        self,
        config: LLMConfig,
        registry: ToolRegistry,
        memory: PersistentMemory | None = None,
        workspace: Path | None = None,
        system_prompt: str | None = None,
        user_message_prefix: str | None = None,
    ):
        self.config = config
        self.registry = registry
        self.memory = memory
        self.workspace = workspace
        self._custom_system_prompt = system_prompt
        self._user_message_prefix = user_message_prefix
        # Snapshot memory ONCE at construction (frozen).
        self._memory_snapshot = memory.snapshot if memory else ""
        self._system_prompt_cache: str | None = None

    # ── Public API ───────────────────────────────

    def build_system_prompt(self) -> str:
        """Build the system prompt. Cached after first call."""
        if self._system_prompt_cache is not None:
            return self._system_prompt_cache

        if self._custom_system_prompt:
            prompt = self._custom_system_prompt
            prompt = prompt.replace("{tool_list}", self._format_tool_list())
            prompt = prompt.replace(
                "{workspace}",
                str(self.workspace) if self.workspace else "(unset)",
            )
        else:
            tool_list = self._format_tool_list()
            workspace_str = str(self.workspace) if self.workspace else "(unset)"

            prompt = self.SYSTEM_PROMPT_HEADER.format(
                workspace=workspace_str,
                tool_list=tool_list,
                memory_snapshot=self._format_memory_snapshot(),
                user_section="",
            )
        self._system_prompt_cache = prompt
        return prompt

    def build_initial_messages(
        self,
        task: str,
        history: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build initial message list: system + history + user (with recalled memories).

        Args:
            task: The user's task description.
            history: Optional list of prior conversation turns in OpenAI
                ``{"role": ..., "content": ...}`` format. Inserted between
                the system prompt and the current user message so the LLM
                has full conversation context.

        Returns:
            List of message dicts ready for OpenAI Chat Completions.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt()},
        ]
        if history:
            messages.extend(history)
        full_task = task
        if self._user_message_prefix:
            full_task = self._user_message_prefix + "\n\n" + task
        user_msg = self._build_user_message(full_task)
        messages.append(user_msg)
        return messages

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate token count for the current messages (including system)."""
        return estimate_tokens(messages)

    # ── Internal helpers ──────────────────────────

    def _format_tool_list(self) -> str:
        """Format tool list for system prompt (paradigm v2 briefs).

        Each tool's registration-time brief is a single lightweight line
        (name, category, one-line purpose, required params, side effects);
        full details are fetched on demand via the tool_help tool.
        """
        try:
            tools = self.registry.all_tools()
        except AttributeError:
            # Legacy registry (e.g. test fakes) without all_tools()
            tools = []
        if not tools:
            definitions = self.registry.get_definitions()
            if not definitions:
                return "(no tools available)"
            lines = []
            for t in definitions:
                fn = t.get("function", {})
                lines.append(f"- {fn.get('name', '?')}: {fn.get('description', '')[:120]}")
            return "\n".join(lines)
        lines = []
        for tool in tools:
            if tool.brief:
                lines.append(tool.brief)
            else:
                lines.append(f"- {tool.name}: {tool.description[:120]}")
        return "\n".join(lines)

    def _format_memory_snapshot(self) -> str:
        """Format memory snapshot block."""
        if not self._memory_snapshot:
            return "## 记忆 (memory)\n(empty)"
        return f"## 记忆 (memory)\n{self._memory_snapshot}"

    def _build_user_message(self, task: str) -> dict[str, Any]:
        """Build user message with auto-recalled memories."""
        content = task
        recalled = self._recall_relevant(task)
        if recalled:
            content += "\n\n<recalled-memories>\n" + recalled + "\n</recalled-memories>"
        return {"role": "user", "content": content}

    def _recall_relevant(self, task: str, max_entries: int = 5) -> str:
        """Find relevant memories and format as text block.

        B4 Step 4-5: the cross-session branch was removed with the legacy
        session stack. ``session_manager`` was threaded through AgentLoop but
        no domain caller ever supplied one (measured: zero assembly points),
        so the branch was dead. PersistentMemory recall is unaffected.
        """
        lines: list[str] = []

        if self.memory:
            try:
                entries = self.memory.find_relevant(task)[:max_entries]
                for e in entries:
                    lines.append(f"- {e.title}: {e.description or '(no desc)'}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory find_relevant failed: %s", exc)

        return "\n".join(lines[:max_entries])


__all__ = [
    "ContextBuilder",
    "estimate_tokens",
    "CHARS_PER_TOKEN",
]
