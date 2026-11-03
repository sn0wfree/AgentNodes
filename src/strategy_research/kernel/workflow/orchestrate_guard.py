"""Continuation guard for DAG-orchestrator chat sessions.

The orchestrator LLM sometimes ends a turn with a clarifying question
("需要我继续吗?" / "您希望先做哪步?") instead of driving the incremental
DAG loop to completion. Because AgentLoop stops as soon as a turn has no
tool calls (loop.py: ``if not response.has_tool_calls(): break``), such a
question would terminate the attempt early.

This module provides:

- ``looks_like_question``: a lightweight heuristic that decides whether an
  agent answer is a question / confirmation-seeking close (the kinds of
  turn the guard should restart rather than accept).
- ``CONTINUE_INSTRUCTION``: the system-style message injected as a user
  turn to force continuation without asking the user.
- ``DEFAULT_MAX_CONTINUES``: default bound on automatic continuations.

The heuristic is intentionally conservative: it must not misfire on a
legitimate completion summary ("已完成", "DAG 已完整", plain task recap).
It only fires when the answer ends with (or mostly consists of) question
patterns. Code-fence contents are excluded so embedded DAG JSON / tool
results never trigger the guard.
"""
from __future__ import annotations

import os
import re

DEFAULT_MAX_CONTINUES = 10
"""Upper bound on automatic continuation attempts for one user message.

Configurable via ``SR_ORCHESTRATOR_MAX_CONTINUES`` (int). Once the bound
is exhausted the attempt ends normally (the LLM's last answer is kept),
so a pathological loop can never spin forever.
"""

_CONTINUE_INSTRUCTION = (
    "【系统提示】你刚才的回复没有提交任何 DAG 修改，并以提问/确认结束。"
    "编排会话中不需要向用户提问：请直接调用 submit_dag_step 继续推进任务，"
    "每次只修改一处，直到整个目标被完整拆解为 DAG。"
    "若你认为 DAG 已经完整且无需任何改动，请明确回复 DAG_DONE 并给出最终总结。"
)

# Question close patterns (anchored at end of the answer, so mid-text
# "?" inside a code block or a quoted sentence does not fire).
_QUESTION_CLOSE = re.compile(
    r"("
    r"吗\s*[?？!！]*"
    r"|呢\s*[?？!！]*"
    r"|要不要[^，。；\n]*"
    r"|是否需要[^，。；\n]*"
    r"|可以吗[^，。；\n]*"
    r"|确认[^，。；\n]*"
    r"|继续吗"
    r"|如何[^，。；\n]*[?？]"
    r"|[^，。；\n]{0,12}[?？]"
    r")\s*$"
)

# Question-sounding words anywhere near the end (last line), without
# requiring a question mark — covers "请确认一下方案" style closes.
_WORD_TAIL = re.compile(
    r"(请)?(确认|回复|答复|告诉我|选择一下|定一下|指示一下|继续一下|帮我决定)"
    r"[^。]*$"
)

# Question words closing the answer without a question mark:
# "你想先做什么" / "下一步怎么走" / "先建 A 还是 B".
_WH_TAIL = re.compile(
    r"(做什么|做哪些|怎么(处理|继续|走|办|做|推进|拆)|哪(一步|些|个|几步)[^，。；\n]*|还是[^，。；\n]*)$"
)

_CODE_FENCE = re.compile(r"```[\s\S]*?```")


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks so embedded JSON never counts as a question."""
    return _CODE_FENCE.sub("", text)


def looks_like_question(answer: str) -> bool:
    """Return True when ``answer`` reads like a question / confirmation
    request to the user (rather than a completion summary or progress note).

    Conservative on purpose: only a *tail* match or a question mark in the
    trailing sentence fires, so summaries that merely contain "?" in a
    quoted/code context are safe.
    """
    if not answer:
        return False
    text = _strip_code_blocks(answer).strip()
    if not text:
        return False
    if _QUESTION_CLOSE.search(text):
        return True
    tail = text.splitlines()[-1].strip() if text.splitlines() else text
    if len(tail) < 120 and (_WORD_TAIL.search(tail) or _WH_TAIL.search(tail)):
        return True
    return False


def max_continues() -> int:
    """Resolved continuation bound from env (``SR_ORCHESTRATOR_MAX_CONTINUES``)."""
    raw = os.environ.get("SR_ORCHESTRATOR_MAX_CONTINUES", "").strip()
    if raw.isdigit():
        return max(0, int(raw))
    return DEFAULT_MAX_CONTINUES


def continue_instruction() -> str:
    """The instruction injected between continuation attempts."""
    return _CONTINUE_INSTRUCTION


__all__ = [
    "DEFAULT_MAX_CONTINUES",
    "continue_instruction",
    "looks_like_question",
    "max_continues",
]
