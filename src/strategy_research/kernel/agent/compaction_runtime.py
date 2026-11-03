"""可注入的上下文压缩器（B1-c）。

背景：压缩逻辑原本长在 ``LoopCompactionMixin`` 上（依赖 ``self.cc`` /
``self.config`` / ``self.client`` / ``self._persist_compaction_event``）。
react 节点在 ``core/runtimes/``（平台层），不能 import ``core/agent/``
（域）——架构门禁会拒绝反向依赖。

所以把核心逻辑抽成**纯函数 + 工厂**：AgentLoop 的 mixin 方法与 react 的
注入点共用同一实现，压缩语义不可能漂移。

用法::

    compactor = make_compactor(
        compact_config=cfg, model_context_tokens=..., llm_client=client,
    )
    messages, applied = compactor(messages)   # 未超阈值时 applied == []
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from strategy_research.foundation.utils.token_utils import estimate_tokens
from . import compact as _compact_mod

logger = logging.getLogger(__name__)

Compactor = Callable[[list[dict[str, Any]]], "tuple[list[dict[str, Any]], list[str]]"]


def make_compactor(
    *,
    compact_config: Any,
    model_context_tokens: int | None,
    model_max_output_tokens: int | None,
    llm_client: Any,
    threshold_tokens: int | None = None,
    previous_summary: str | None = None,
    persist: Callable[..., Any] | None = None,
    session_id: str | None = None,
) -> Compactor:
    """Build a ``messages -> (messages, applied_layers)`` compressor.

    Semantics match ``LoopCompactionMixin._maybe_compact_impl`` exactly
    (same engine call, same rollback-on-failure rules):

    - below threshold → messages unchanged, ``applied`` empty;
    - L4 runs → messages replaced, layers reported;
    - L4 or persistence raises → **roll back to the original list**, so the
      model keeps full history rather than a half-compacted one.
    """
    state = {"previous_summary": previous_summary}

    def compactor(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        original = list(messages)

        # Overflow logging — parity with the mixin (log only, no forcing).
        if model_context_tokens:
            usable = model_context_tokens - 4096
            tokens = estimate_tokens(messages)
            if compact_config is not None and tokens >= usable * getattr(
                compact_config, "overflow_ratio", 1.0,
            ):
                logger.debug(
                    "Overflow detected: %d tokens >= %d usable * %s",
                    tokens, usable, getattr(compact_config, "overflow_ratio", "?"),
                )

        try:
            new_messages, applied, summary_text, recent_text = (
                _compact_mod.compact_messages(
                    messages,
                    config=compact_config,
                    threshold_tokens=threshold_tokens,
                    model_context_tokens=model_context_tokens,
                    model_max_output_tokens=model_max_output_tokens,
                    llm_client=llm_client,
                    previous_summary=state["previous_summary"],
                    session_id=session_id or "",
                )
            )
        except Exception:
            logger.exception("L4 compaction failed; keeping full history")
            return original, []

        if summary_text and any(
            str(layer).startswith("llm_summarize") for layer in applied
        ):
            state["previous_summary"] = summary_text
            if persist is not None:
                try:
                    persist(summary_text, recent_text or "", new_messages)
                except Exception:
                    logger.exception(
                        "compaction persistence failed; rolling back",
                    )
                    return original, []

        return new_messages, applied

    return compactor


__all__ = ["Compactor", "make_compactor"]
