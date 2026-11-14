"""E1–E5 能力可用性端到端冒烟（第一阶段完成标志）。

对应设计：phase1-definition-of-done-20261107.md §2（A-chat / A-exec /
A-coding）+ batch7-capability-enable-20261107.md。

判定标准不是「能 import」，是「工具真的能执行并返回结果」。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig
from agentnodes.kernel.agent.chat_loop import build_chat_agent_loop
from agentnodes.kernel.agent.tool_context import build_tool_context


@pytest.fixture
def chat_loop(tmp_path: Path):
    """默认配置的 chat loop（E1 基线：不开任何 opt-in）。"""
    return build_chat_agent_loop(
        LLMConfig(api_key="sk-test"), "sess-e2e", workspace=tmp_path
    )


@pytest.fixture
def coding_loop(tmp_path: Path):
    """同默认——E2/E3 也应在默认配置下可用（不再需要 opt-in）。"""
    return build_chat_agent_loop(
        LLMConfig(api_key="sk-test"), "sess-e2e", workspace=tmp_path
    )


def _tools(loop) -> dict:
    return {t.name: t for t in loop.registry.all_tools()}


def _ctx(tmp_path: Path):
    return build_tool_context(tool_call_id="e2e", workspace=tmp_path)


def _run(loop, tmp_path: Path, name: str, **params) -> dict:
    """经**生产路径**执行一个工具调用，返回解析后的工具输出。

    **批次 V1（批次 13 附录 §2 的直接后果）**：旧实现走
    ``loop.registry.execute()``，那是**第三条**路径——生产 chat 的
    ``react.py`` 用它，但 ``AgentLoop`` 用的是
    ``_aexecute_tool_call``。于是 E1–E5 全绿也不能证明生产路径的
    权限闸在工作（P0 sync 绕过漏洞正是因此藏住的）。

    现在走 ``_aexecute_tool_call``（AgentLoop 的真实执行入口，
    U6-2 后 sync 包装与 core 的 async_mode 参数已删除）。
    """
    import asyncio

    from agentnodes.foundation.llm import ToolCall
    from agentnodes.kernel.agent.loop import LoopResult

    tc = ToolCall(id="e2e", name=name, arguments=params)
    result = LoopResult()
    msg = asyncio.run(loop._aexecute_tool_call(tc, result))
    return _parse_tool_msg(msg)


def _parse_tool_msg(msg: dict) -> dict:
    """把工具结果消息解析成 dict（错误消息是纯文本，需包一层）。"""
    content = msg.get("content") or ""
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = {"status": "error", "error": content}
    # 权限拒绝由 loop 层渲染成 "[Tool error]: PermissionDeniedError: ..."，
    # 归一成与 registry.execute 相同的形状，便于断言复用。
    if isinstance(parsed, dict) and "permission_denied" not in parsed:
        err = str(parsed.get("error") or "")
        if "PermissionDeniedError" in err or "Permission denied" in err:
            parsed["permission_denied"] = True
            parsed["status"] = "error"
    return parsed if isinstance(parsed, dict) else {"status": "ok", "result": parsed}


def _run_registry(loop, tmp_path: Path, name: str, **params) -> dict:
    """**第三条路径**：``ToolRegistry.execute``（生产消费者：react.py）。

    与 ``_run`` 分开保留——两条生产路径都要有覆盖，不能只测一条。
    """
    from agentnodes.kernel.agent.tool_context import build_tool_context

    ctx = build_tool_context(
        tool_call_id="e2e",
        workspace=tmp_path,
        write_roots=params.pop("_write_roots", None),
        permission_evaluator=getattr(loop, "_permission_evaluator", None),
        permission_gateway=getattr(loop, "_permission_gateway", None),
    )
    return json.loads(loop.registry.execute(name, params, ctx=ctx))


# ── E1 · chat 基线 ────────────────────────────────────────────────────


class TestE1Chat:
    def test_loop_builds_with_tools(self, chat_loop):
        names = _tools(chat_loop)
        assert len(names) >= 15
        for need in ("read", "write", "list", "tool_help"):
            assert need in names, need

    def test_permission_gate_wired_by_default(self, chat_loop):
        """批次 7：evaluator 默认接线（此前 loop 里从来没人赋值）。"""
        assert getattr(chat_loop, "_permission_evaluator", None) is not None

    def test_shell_present_by_default(self, chat_loop):
        """批次 7：shell 默认在 registry（E2 前提）。"""
        assert "shell" in _tools(chat_loop)


# ── E2 · 执行代码 ─────────────────────────────────────────────────────


class TestE2Exec:
    def test_shell_runs_and_returns_stdout(self, coding_loop, tmp_path):
        out = _run(coding_loop, tmp_path, "shell", command="echo hello-agentnodes")
        assert out["status"] == "ok", out
        assert "hello-agentnodes" in out["stdout"]
        assert out["exit_code"] == 0

    def test_command_triple(self, coding_loop, tmp_path):
        """C8：stdout / stderr / exit_code 三元组分离（跨平台写法）。"""
        out = _run(
            coding_loop, tmp_path, "shell",
            command=(
                'python -c "import sys;'
                "print('out');"
                "print('err', file=sys.stderr);"
                "raise SystemExit(3)\""
            ),
        )
        assert out["stdout"].strip() == "out", out
        assert out["stderr"].strip() == "err", out
        assert out["exit_code"] == 3, out

    def test_hard_deny_blocks_sudo_at_loop_level(
        self, coding_loop, tmp_path,
    ):
        """L2 默认下 shell 自动，但 hard_deny（H2 提权）仍拦。

        **V1 关键**：这条断言在 sync 模式下曾经必红——sync 分支绕过权限。
        """
        out = _run(coding_loop, tmp_path, "shell", command="sudo rm x")
        assert out["status"] == "error", out
        assert out.get("permission_denied") is True, out


# ── E3/E4 · coding 闭环 ───────────────────────────────────────────────


class TestE3E4Coding:
    def test_write_workspace_root_file(self, coding_loop, tmp_path):
        """E3 核心：能写工作区根下的任意相对路径（不再限 4 目录）。"""
        out = _run(coding_loop, tmp_path, "write",
                   path="hello.py", content="print('hi')\n")
        assert out["status"] == "ok", out
        assert (tmp_path / "hello.py").exists()

    def test_write_nested_path(self, coding_loop, tmp_path):
        out = _run(coding_loop, tmp_path, "write",
                   path="src/pkg/main.py", content="x = 1\n")
        assert out["status"] == "ok", out
        assert (tmp_path / "src" / "pkg" / "main.py").exists()

    def test_full_loop_write_read_execute(self, coding_loop, tmp_path):
        """E4：write → read → 执行，输出可见。"""
        w = _run(coding_loop, tmp_path, "write",
                 path="hello.py", content="print('hi')\n")
        assert w["status"] == "ok", w

        r = _run(coding_loop, tmp_path, "read",
                 path="hello.py")
        assert "print" in json.dumps(r, ensure_ascii=False), r

        run = _run(coding_loop, tmp_path, "shell",
                   command="python hello.py")
        assert run["status"] == "ok", run
        assert "hi" in run["stdout"], run

    def test_escape_still_blocked(self, coding_loop, tmp_path):
        """E5：开放工作区 ≠ 无边界——`..` 逃逸与绝对路径仍拒。

        **附带回归**：批次 7 修了 ``lstrip("./")`` 吃掉 ``../`` 的潜伏
        bug——旧代码会把 ``../escape.txt`` 改写成 ``escape.txt`` 写进
        工作区（路径悄悄变掉）；现在必须原样被逃逸检查拒绝。
        """
        out = _run(coding_loop, tmp_path, "write",
                   path="../escape.txt", content="x")
        assert out["status"] == "error", out
        assert not (tmp_path.parent / "escape.txt").exists()
        # 改写路径也不允许出现（lstrip bug 回归）
        assert not (tmp_path / "escape.txt").exists()

        out2 = _run(coding_loop, tmp_path, "write",
                    path=str(tmp_path / "abs.txt"), content="x")
        assert out2["status"] == "error", out2
        assert not (tmp_path / "abs.txt").exists()

    def test_explicit_write_roots_still_tighten(self, tmp_path: Path):
        """收紧能力保留：显式 write_roots 传参 → 恢复目录白名单语义。"""
        from agentnodes.kernel.agent.builtin_tools import (
            build_default_registry,
        )
        from agentnodes.kernel.agent.tool_context import build_tool_context

        reg = build_default_registry()
        ctx = build_tool_context(
            tool_call_id="tight",
            workspace=tmp_path,
            write_roots=("strategies",),
        )
        ok = json.loads(reg.execute(
            "write", {"path": "strategies/a.py", "content": "x=1\n"}, ctx=ctx
        ))
        assert ok["status"] == "ok"
        blocked = json.loads(reg.execute(
            "write", {"path": "hello.py", "content": "x=1\n"}, ctx=ctx
        ))
        assert blocked["status"] == "error"

    def test_ask_without_channel_fails_closed(self, tmp_path: Path):
        """批次 7 安全洞 #3 回归：ask 且无 gateway → DENY（非放行）。"""
        from agentnodes.kernel.agent.builtin_tools import (
            build_default_registry,
        )
        from agentnodes.kernel.agent.tool_context import build_tool_context
        from agentnodes.kernel.permission import PermissionEvaluator
        from agentnodes.kernel.permission.schema import (
            MODE_PRESETS,
            PermissionMode,
        )

        reg = build_default_registry()
        # L1 下 shell=ask；ctx 不接 gateway → 必须拒（旧版会放行）
        ctx = build_tool_context(
            tool_call_id="askc",
            workspace=tmp_path,
            permission_evaluator=PermissionEvaluator(
                ruleset=list(MODE_PRESETS[PermissionMode.L1_WORKSPACE_EDIT.value])
            ),
        )
        out = json.loads(reg.execute("shell", {"command": "echo x"}, ctx=ctx))
        assert out["status"] == "error"
        assert out.get("permission_denied") is True
