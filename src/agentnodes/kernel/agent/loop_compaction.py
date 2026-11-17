"""上下文压缩（批次 13 · S3-b2：改调既有 make_compactor）。

**去重**：``_maybe_compact_impl``（58 行）原先手写了一遍与
``compaction_runtime.make_compactor`` 等价的逻辑——同样的 overflow 日志、
同样的 ``compact_messages`` 调用、同样的失败回滚、同样的 persist 触发
条件。``make_compactor`` 早已存在且被 ``gateway/session_controller.py``
使用（`tests/test_b1c_react_compaction.py` 7 个测试钉住其语义）。

本文件现在只剩三件事：

1. ``_maybe_compact`` / ``_amaybe_compact``：从 loop 的字段拼出 compactor
   并调用（sync 直接跑，async 在 worker 线程跑并持会话锁）；
2. ``_run_compact_messages``：保留为兼容入口（旧测试直接调它）；
3. ``_persist_compaction_event``：persist 回调本体（137 行，事件溯源 +
   legacy 直写两路）——这部分没有重复实现，保留。

**mixin 形状保留**（阶段 3 删）：``AgentLoop`` 的 MRO 与既有导入点仍引用它。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentnodes.foundation.utils.token_utils import estimate_tokens
from agentnodes.kernel.agent import compact as _compact_mod
from agentnodes.foundation import settings as _settings

logger = logging.getLogger(__name__)

__all__ = ["LoopCompactionMixin"]


class LoopCompactionMixin:
    """压缩 mixin（薄壳：核心逻辑已委托 compaction_runtime.make_compactor）。"""

    # ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _event_bus: Any
    _previous_summary: Any
    cc: Any
    client: Any
    config: Any
    session_id: Any
    threshold_tokens: Any

    # ── compactor 构造 ───────────────────────────────────────────────

    def _build_compactor(self):
        """按 loop 当前字段构造 ``messages -> (messages, applied)`` 压缩器。

        每次调用重新构造：``previous_summary`` 由 compactor 内部的 state
        字典维护，而 ``cc`` / ``client`` / ``threshold_tokens`` 都是构造后
        可变的（测试会直接赋值）。构造开销只有一次闭包创建。
        """
        from agentnodes.kernel.agent.compaction_runtime import make_compactor

        return make_compactor(
            compact_config=self.cc,
            model_context_tokens=getattr(self.config, "model_context_tokens", None),
            model_max_output_tokens=getattr(
                self.config, "model_max_output_tokens", None,
            ),
            llm_client=self.client,
            # ``threshold_tokens`` 只有构造期赋值（AgentLoop.__init__）；
            # 测试用 ``__new__`` 构造时没有该属性——原实现的
            # ``_run_compact_messages`` 也读它，但只在真正调用引擎时。
            threshold_tokens=getattr(self, "threshold_tokens", None),
            previous_summary=getattr(self, "_previous_summary", None),
            persist=self._persist_compaction_event,
            session_id=getattr(self, "session_id", None),
        )

    # ── 公开入口（sync / async）──────────────────────────────────────

    def _maybe_compact(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Apply context compression if over threshold (sync path).

        opencode-aligned trigger formula:
            trigger = threshold_tokens (default derived from model
            context: context - max(model_max_output, buffer))

        If L4 or persistence fails, ``make_compactor`` rolls back and the
        original messages are kept — the LLM doesn't lose context.
        """
        compactor = self._build_compactor()
        out_messages, applied = compactor(messages)
        # ``_previous_summary`` 由 ``_persist_compaction_event`` 在 L4 成功
        # 持久化时回写（它是 compactor 的 persist 回调，天然拿到 summary）。
        return out_messages, applied

    def _run_compact_messages(self, messages: list[dict[str, Any]]):
        """Invoke the compact_messages engine with the loop's config.

        兼容入口：旧测试/调用方直接引用此方法名。行为与
        ``make_compactor`` 内部那次调用完全一致（同一个模块级函数、
        同一组参数）。
        """
        return _compact_mod.compact_messages(
            messages,
            config=self.cc,
            threshold_tokens=self.threshold_tokens,
            model_context_tokens=self.config.model_context_tokens,
            model_max_output_tokens=self.config.model_max_output_tokens,
            llm_client=self.client,
            previous_summary=self._previous_summary,
            session_id=self.session_id,
        )

    async def _amaybe_compact(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Async version — 在 worker 线程跑同步压缩核心。

        Acquires per-session compact lock to prevent concurrent compaction
        (auto from agent loop + manual /compact) on the same session.
        """
        if not self.session_id:
            return await asyncio.to_thread(self._maybe_compact, messages)
        from agentnodes.kernel.agent.compact import _compact_locks

        lock = await _compact_locks.get(self.session_id)
        async with lock:
            return await asyncio.to_thread(self._maybe_compact, messages)

    # ── 压缩事件持久化（无重复实现，原样保留）────────────────────────

    def _persist_compaction_event(
        self,
        summary_text: str,
        recent_text: str,
        compressed_messages: list[dict] | None = None,
    ) -> None:
        """Persist a CompactionMessage event for the L4 layer.

        B6: Event-sourced path. Emits a compact.ended event via the
        injected EventBusV2. The projector handles materialization to
        messages + message_parts tables.

        Falls back to direct persist_message if no event_bus is injected
        (for legacy code paths that haven't migrated yet).

        Args:
            summary_text: LLM-generated summary (non-empty).
            recent_text: Pre-serialized recent messages from compact.
            compressed_messages: Post-L4 messages list (system + recent).
                When provided AND the ``AGENTNODES_COMPACT_INCLUDE_MESSAGES`` env var is
                truthy, this list is included in the ``compact.ended`` event
                so the projector can replace the previous messages table
                contents — keeping the DB consistent with the in-memory
                state already updated by ``compact_messages``.  When the
                flag is off (the default), the previous behaviour is
                preserved: only a marker is emitted, original messages stay
                in DB.

        Raises:
            Exception: propagates critical errors (not silent fail).
        """
        session_id = self.session_id
        if not session_id:
            logger.debug("L4 ran but no session_id; skipping compaction persistence")
            return

        if not summary_text or not summary_text.strip():
            logger.warning("L4 returned empty summary; skipping persistence")
            return

        # L4 成功才回写 previous_summary（compactor 内部另有一份 state，
        # 这里同步到 loop 字段供外部读取——有测试直接断言该字段）。
        self._previous_summary = summary_text

        try:
            from agentnodes.kernel.agent.compaction_message import new_compaction_message

            comp = new_compaction_message(
                session_id=session_id,
                summary=summary_text,
                recent=recent_text or "",
                reason="auto",
            )

            if self._event_bus is not None:
                # B6: Event-sourced path. Emit compact.ended event.
                #
                # Two modes (controlled by AGENTNODES_COMPACT_INCLUDE_MESSAGES):
                # 1. Default: emit only a marker. The DB keeps the original
                #    messages; the marker records that compaction happened.
                # 2. Flag enabled: include the post-L4 messages list so the
                #    projector replaces the DB contents with the compressed
                #    view.
                import os

                include_msgs = (
                    compressed_messages is not None
                    and _settings.get_str("AGENTNODES_COMPACT_INCLUDE_MESSAGES").lower()
                    in ("1", "true", "yes")
                )
                payload: dict[str, Any] = {
                    "summary": comp.summary,
                    "reason": "auto",
                    "compaction_id": comp.id,
                    "metadata": comp.metadata,
                }
                if include_msgs:
                    payload["messages"] = compressed_messages
                self._event_bus.emit(
                    session_id,
                    "compact.ended",
                    payload,
                )
                logger.info(
                    "compaction event emitted: %s (summary=%d chars, "
                    "recent=%d chars, include_messages=%s)",
                    comp.id, len(comp.summary), len(comp.recent),
                    include_msgs,
                )
            else:
                # Legacy fallback: direct DB write via the registered
                # persister (registered by api/ and TUI entry points).
                #
                # Fail-fast: if no persister is registered, raise.
                # Previously this path silently dropped the compaction
                # event, which made misconfigured deployments invisible
                # to operators.
                #
                # Read the persister through the module (NOT a from-import):
                # a from-import binds the value at module load time, so a
                # later register_compaction_persister() call would never be
                # visible here.
                persister = _compact_mod.get_compaction_persister()
                if persister is None:
                    raise RuntimeError(
                        f"compaction_persister not registered; cannot "
                        f"persist compaction event {comp.id}. Register "
                        f"via register_compaction_persister() in the "
                        f"api/cli entry point."
                    )
                persister(
                    session_id=comp.session_id,
                    role="assistant",  # DB compat
                    content=comp.summary,
                    parts=comp.to_parts(),
                    message_id=comp.id,
                    message_type="compaction",
                )
                logger.info(
                    "compaction event persisted (legacy): %s (summary=%d chars, recent=%d chars)",
                    comp.id, len(comp.summary), len(comp.recent),
                )
        except Exception:
            # Critical: propagate with full traceback. The caller will see
            # the error and roll back messages to keep full history intact.
            logger.exception("compaction persistence failed")
            raise
