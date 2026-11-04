"""Compaction tunables — pure dataclass (zero business logic).

2026-11-04（W5-8）：从 ``core/agent/compact.py`` 原位抽出。llm（foundation 层）
的 ``config.py`` 需要 ``CompactConfig`` 读取 ``llm.json``，若留在 L3 会构成
L4→L3 上行违规。本模块只含数据形状，机制实现在 agent/compact.py（L3）。
``core.agent.compact.CompactConfig`` 经 re-export 保持 API 不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompactConfig:
    """All tunable compaction parameters.

    Configurable via ``~/.quantnodes/llm.json`` ``"compact"`` section.

    All defaults follow the opencode approach (packages/core/src/session/
    compaction.ts). The current implementation is L4-only (matches opencode).
    """

    enabled: bool = True

    # ── Trigger ──────────────────────────────────────────────────
    # None = derive from model context (opencode-style):
    #   trigger = context - max(model_max_output, buffer)
    # Explicit int = use as absolute threshold.
    threshold_tokens: int | None = None
    # Opencode DEFAULT_BUFFER: leave this much room for the response
    compaction_buffer_tokens: int = 20_000
    # Fallback trigger when model context is unknown
    fallback_threshold_tokens: int = 8_000

    # ── Layer ratios (relative to threshold_tokens) ──────────────
    # DEPRECATED since Phase A: L1/L3 ratios are no longer used.
    # Kept for backward compat with existing llm.json files.
    # Ignored at runtime; only llm_summarize_ratio is used.
    microcompact_ratio: float = 0.9        # DEPRECATED: was L1 ratio (0.9)
    llm_summarize_ratio: float = 0.80      # L4: 80% (opencode-aligned: trigger earlier for more room)
    hard_truncate_ratio: float = 0.99      # DEPRECATED: was L3 ratio (0.99)
    overflow_ratio: float = 0.99          # DEPRECATED: was overflow detection (0.99)

    # ── L1: Smart Microcompact (DEPRECATED) ─────────────────────
    # DEPRECATED since Phase A: L1 layer removed.
    # Fields kept for backward compat with existing llm.json files.
    # Ignored at runtime.
    microcompact_tool_result_chars: int = 2_000  # DEPRECATED
    tool_truncate_chars: dict[str, int] = field(default_factory=dict)  # DEPRECATED
    collapse_keep_recent: int = 4         # DEPRECATED: was L3 keep_recent
    serialize_tool_max_chars: int = 2_000  # NEW: used by _serialize_message for L4 input

    # ── L4: LLM Summarize ─────────────────────────────────────────
    # Opencode formula: actual max_tokens = min(model_max_output, this)
    # i.e. this is a CAP, not the absolute value.
    preserve_recent_tokens: int | None = None  # None = dynamic (25% of context)
    tail_turns: int = 2                        # keep last N turns verbatim
    quality_decay: bool = False                # score turns by recency × content quality
    summary_output_tokens: int = 4_096          # CAP, see opencode line 183
    enable_incremental_summary: bool = True
    summary_template: str | None = None        # None = DEFAULT_SUMMARY_TEMPLATE
    # Minimum number of messages required from L4 (safety check).
    # If L4 produces fewer, the compaction is aborted to avoid
    # provider errors like MiniMax 2013 "chat content is empty".
    l4_min_messages: int = 2
    # Token estimation: avg chars per token. Used by _estimate_tokens.
    chars_per_token: float = 3.0

    # ── History projection ────────────────────────
    # When True, send ALL compaction messages in history (legacy behavior).
    # When False (default), only the MOST RECENT compaction is included
    # in LLM context. Older compactions are hidden from LLM but kept in DB
    # so the UI can still render them as audit history.
    # See docs/compaction-history-filter.md for the opencode-aligned rationale.
    keep_all_compactions_in_history: bool = False

    # ── Goal event messages ────────────────────────────────────────
    # goal_* SSE events are persisted as message_type='goal' messages
    # and DO enter the LLM context (agent tracks goal evolution). The
    # evidence text in the LLM-facing `content` is truncated to this
    # many characters to bound context cost; the full text always
    # lives in the message metadata (UI / audit).
    goal_evidence_truncate_chars: int = 100

    # ── RSI tier-1 placement hook (施工方案 §4.7) ──────────────────
    # 装配期由 **kernel 侧**（harness_resolver.apply_compaction_overrides）
    # 计算覆盖项后用 dataclasses.replace() 写入，本模块不 import kernel。
    #
    # 为什么不做成实例方法：foundation 是 L4，kernel 是 L3；L4→L3 是上行
    # import，会打破层门禁（H2）。配置类的正确姿态是「提供字段，不拉依赖」。
    harness_task_signature: str = ""
