"""Unit tests for the DAG-orchestrator continuation guard.

Covers ``looks_like_question`` heuristics and the continuation bound
resolver (``max_continues``). The guard's integration point lives in
``api/session/service.py::_run_with_agent``.
"""
import os

import pytest

from strategy_research.kernel.workflow.orchestrate_guard import (
    DEFAULT_MAX_CONTINUES,
    continue_instruction,
    looks_like_question,
    max_continues,
)

QUESTION_ANSWERS = [
    "需要我继续吗",
    "需要我继续吗？",
    "这样可以吗？",
    "这样设计可以吗",
    "您希望先做哪一步呢？",
    "你想先做什么",
    "想先建数据节点还是回测节点",
    "是否继续推进？",
    "是否需要我先建数据检查节点？",
    "请确认一下方案",
    "接下来拆成哪几步比较好",
    "下一步怎么推进？",
    "你觉得拆成哪几步合适",
    "要不要我再加一个数据节点",
    "请告诉我下一步怎么做",
    "我已经建好 hypothesis 节点。是否继续？",
]

NON_QUESTION_ANSWERS = [
    "",
    "已完成。DAG 已完整覆盖目标。",
    "当前 DAG: 1 节点 / 0 连线",
    "总结：已建 hypothesis、data_check、backtest 三个节点。",
    "已按目标完成全部拆解，共 5 个节点 4 条连线。",
    "DAG 已完整，无需改动。",
    "完成。下一步可手动调整。",
    "任务目标无法达成：用户要求的 `star_trek` 节点类型不存在，建议改用 tool 节点。",
    "```json\n{\"nodes\": [{\"id\": \"hypothesis\", \"label\": \"提出假设\"}]}\n```\n已应用。",
    "已应用：新增 hypothesis 节点。",
]


@pytest.mark.parametrize("answer", QUESTION_ANSWERS)
def test_looks_like_question_positive(answer: str) -> None:
    assert looks_like_question(answer), f"expected question: {answer!r}"


@pytest.mark.parametrize("answer", NON_QUESTION_ANSWERS)
def test_looks_like_question_negative(answer: str) -> None:
    assert not looks_like_question(answer), f"expected non-question: {answer!r}"


def test_code_blocks_never_count_as_questions() -> None:
    answer = (
        "我提交了这一步。\n"
        "```json\n"
        '{"nodes": [{"id": "a", "label": "这是？还是？"}]}\n'
        "```"
    )
    assert not looks_like_question(answer)


def test_question_mark_inside_sentence_only_does_not_fire() -> None:
    assert not looks_like_question("用户提出的任务（是否涉及回测？）已经完成拆解。")


def test_max_continues_default_and_env() -> None:
    assert max_continues() == DEFAULT_MAX_CONTINUES
    os.environ["SR_ORCHESTRATOR_MAX_CONTINUES"] = "3"
    try:
        assert max_continues() == 3
    finally:
        os.environ.pop("SR_ORCHESTRATOR_MAX_CONTINUES", None)
    assert max_continues() == DEFAULT_MAX_CONTINUES


def test_max_continues_ignores_garbage() -> None:
    os.environ["SR_ORCHESTRATOR_MAX_CONTINUES"] = "not-a-number"
    try:
        assert max_continues() == DEFAULT_MAX_CONTINUES
    finally:
        os.environ.pop("SR_ORCHESTRATOR_MAX_CONTINUES", None)


def test_continue_instruction_mentions_tool_and_done_marker() -> None:
    instr = continue_instruction()
    assert "submit_dag_step" in instr
    assert "DAG_DONE" in instr
