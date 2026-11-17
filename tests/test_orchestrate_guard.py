"""Unit tests for the continuation guard（chat 通用，2026-11-13 下沉）.

Covers ``looks_like_question`` heuristics and the continuation bound
resolver (``max_continues``). The guard's integration point lives in
``api/session/service.py::_run_with_guard``.

**归属变更**：模块原在 ``kernel/workflow/orchestrate_guard.py``，已下沉到
``kernel/agent/guards/text_guard.py``（切断 langgraph 隐式依赖，见
docs/design/langgraph-implicit-dep-fix-20261113.md）。**本文件所有断言
零改动**——它们钉的是行为契约，与模块位置无关。
"""
import os

import pytest

from agentnodes.kernel.agent.guards.text_guard import (
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
    os.environ["AGENTNODES_ORCH_MAX_CONTINUES"] = "3"
    try:
        assert max_continues() == 3
    finally:
        os.environ.pop("AGENTNODES_ORCH_MAX_CONTINUES", None)
    assert max_continues() == DEFAULT_MAX_CONTINUES


def test_max_continues_ignores_garbage() -> None:
    os.environ["AGENTNODES_ORCH_MAX_CONTINUES"] = "not-a-number"
    try:
        assert max_continues() == DEFAULT_MAX_CONTINUES
    finally:
        os.environ.pop("AGENTNODES_ORCH_MAX_CONTINUES", None)


def test_continue_instruction_mentions_tool_and_done_marker() -> None:
    instr = continue_instruction()
    assert "submit_dag_step" in instr
    assert "DAG_DONE" in instr


# ── 依赖链切断（本批核心断言，设计文档 §4 第 1/3 条）──────────────


def test_import_does_not_load_heavy_framework_modules() -> None:
    """import text_guard 不得拉起 langgraph / swarm / runtimes / langchain。

    **为什么这是核心断言**：模块原在 ``kernel/workflow/`` 包里，从包内
    import 会触发 ``workflow/__init__`` → ``types`` → ``swarm`` →
    ``runtimes`` → ``graph_engine.py:30`` 硬 import ``langgraph``（可选
    extra）。实测后果（WSL e2e）：按文档装 ``[api]`` 的用户在 chat 第一条
    消息就炸 ``ModuleNotFoundError``；修前该测试挂 127s（拉整条链），
    修后 7s。

    用**子进程**验证——当前进程可能因其它测试已加载这些模块，污染判定。
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import agentnodes.kernel.agent.guards.text_guard as tg\n"
        "heavy = [m for m in ('langgraph', 'langchain_core',\n"
        "                     'agentnodes.kernel.swarm',\n"
        "                     'agentnodes.kernel.runtimes') if m in sys.modules]\n"
        "print('HEAVY=' + (','.join(heavy) if heavy else ''))\n"
        "print('OK=' + str(tg.looks_like_question('需要我继续吗')))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       cwd=str(root), timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    assert "HEAVY=\n" in r.stdout, (
        f"import text_guard 拉起了重依赖: {r.stdout!r}（应为空）"
    )
    assert "OK=True" in r.stdout, "功能失效——looks_like_question 行为变了"


def test_legacy_path_import_is_also_light() -> None:
    """旧路径（``kernel.workflow.orchestrate_guard``）同样零重依赖。

    靠 ``workflow/__init__.py`` 的 PEP 562 懒加载实现——包导入不再有副作用。
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import agentnodes.kernel.workflow.orchestrate_guard as g\n"
        "heavy = [m for m in ('langgraph', 'langchain_core',\n"
        "                     'agentnodes.kernel.swarm',\n"
        "                     'agentnodes.kernel.runtimes') if m in sys.modules]\n"
        "print('HEAVY=' + (','.join(heavy) if heavy else ''))\n"
        "print('OK=' + str(g.looks_like_question('需要我继续吗')))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       cwd=str(root), timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    assert "HEAVY=\n" in r.stdout, (
        f"旧路径仍拉起重依赖: {r.stdout!r}（workflow/__init__ 的 types 懒加载失效）"
    )
    assert "OK=True" in r.stdout
