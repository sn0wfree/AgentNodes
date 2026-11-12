"""批次 13 附录 §1/§2 · 双路径权限一致性（P0 回归）。

**这个文件的由来**：批次 13 附录实测发现两个安全缺陷——

1. ``AgentLoop`` 的 **sync 路径完全绕过权限闸**：
   ``_execute_tool_call_core(async_mode=False)`` 直调 ``tool.invoke()``，
   而权限只在 ``ainvoke()`` 里。同一条 ``shell=DENY`` 规则下：
   - sync  → ``echo hi`` 实际执行（exit_code=0）
   - async → 正确拒绝
   ``run()`` 全仓有 118 个调用点（CLI / TUI / scheduled_research / evals），
   生产 web 走 ``arun`` 故未中招。

2. ``evaluator._extract_target()`` 对 shell 只取**命令首词**，
   于是 H3 硬约束（``**/.ssh/**``）对 ``cat ~/.ssh/id_rsa`` 永远不匹配：
   两条路径都放行。

**为什么 E1–E5 没能发现**：``tests/test_capability_smoke.py`` 的 ``_run()``
走 ``loop.registry.execute()``（内部 ``enforce_permission_sync``），
和生产路径 ``_execute_tool_call_core`` 不是同一条。本文件补上这个缺口。

设计：真跑 ``_run_loop_core``（两种 async_mode），用 mock LLM 发一次
工具调用，然后断言工具**没有真正执行**。
"""
from __future__ import annotations
from llm_client_mock import patch_llm_client

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_INNER = r'''
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"{root}\src")

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import AgentLoop
from agentnodes.kernel.agent.runtime.sync_bridge import _run_coro_in_sync
from agentnodes.kernel.permission import PermissionEvaluator
from agentnodes.kernel.permission.schema import (
    DEFAULT_HARD_DENY, MODE_PRESETS, PermissionAction, PermissionMode,
    PermissionRule,
)


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        if not self.responses:
            return LLMResponse(content="done", tool_calls=[],
                               finish_reason="stop")
        return self.responses.pop(0)

    async def achat(self, messages, **kwargs):
        return self.chat(messages, **kwargs)


def _tool_resp(name, args):
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id="c1", name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _build(ws, responses, *, mode=None, rules=None):
    loop = AgentLoop(
        stream_mode=False,
        config=LLMConfig(api_key="sk-test"),
        registry=build_default_registry(),
        workspace=ws,
        max_iterations=2,
        approval_timeout=1,
        approval_on_timeout="reject",
    )
    mock = MockLLM(responses)
    loop.client.chat = mock.chat
    loop.client.achat = mock.achat
    if mode is not None:
        preset = list(MODE_PRESETS[mode])
    else:
        preset = list(MODE_PRESETS[PermissionMode.L2_SANDBOX_EXEC.value])
    if rules:
        preset = preset + list(rules)
    loop._permission_evaluator = PermissionEvaluator(
        ruleset=preset, hard_deny=DEFAULT_HARD_DENY, interactive=True,
    )
    loop._permission_gateway = None
    return loop


def _run_engine(loop, *, async_mode):
    if async_mode:
        return asyncio.run(
            loop._run_loop_core("t", None, None, async_mode=True)
        )
    return _run_coro_in_sync(
        loop._run_loop_core("t", None, None, async_mode=False)
    )


def _tool_output(result):
    for m in result.messages:
        if m.get("role") == "tool":
            return m.get("content") or ""
    return ""


def _verdict(loop, responses, *, async_mode, **kwargs):
    ws = Path(tempfile.mkdtemp())
    (ws / "strategies" / "foo").mkdir(parents=True)
    lp = _build(ws, responses, **kwargs)
    r = _run_engine(lp, async_mode=async_mode)
    out = _tool_output(r)
    denied = ("PermissionDenied" in out
              or "Permission denied" in out
              or "Permission required" in out
              or "permission_denied" in out
              or "denied" in out.lower())
    return {"denied": denied, "output": out[:300]}


def main():
    case = sys.argv[1]
    out = {}
    if case == "explicit_deny":
        # evaluator 明确 deny shell → 两条路径都必须拒绝
        for mode_name, am in (("sync", False), ("async", True)):
            rules = [PermissionRule(permission="shell", pattern="*",
                                    action=PermissionAction.DENY)]
            out[mode_name] = _verdict(
                None, [_tool_resp("shell", {"command": "echo PERMISSION_CANARY"})],
                async_mode=am, rules=rules,
            )
    elif case == "hard_deny_h2":
        # H2 提权：两条路径都必须拒绝（hard_deny 是独立层）
        for mode_name, am in (("sync", False), ("async", True)):
            out[mode_name] = _verdict(
                None,
                [_tool_resp("shell", {"command": "sudo echo PERMISSION_CANARY"})],
                async_mode=am,
            )
    elif case == "hard_deny_h3":
        # H3 凭据：hard_deny 必须看见 shell 命令里的路径词，不只是首词
        for mode_name, am in (("sync", False), ("async", True)):
            out[mode_name] = _verdict(
                None,
                [_tool_resp("shell", {"command": "cat ~/.ssh/id_rsa"})],
                async_mode=am,
            )
    print("@@RESULT@@" + json.dumps(out))


main()
'''.replace("{root}", str(ROOT))


def _run_case(name: str) -> dict:
    """在干净环境的子进程里跑一个用例。

    **必须清 PYTEST_* 环境变量**：``sys.executable -c`` 会继承它们，
    使子进程在 import 阶段加载 pytest 插件（含 hypothesis）——
    部分环境会用应用控制策略阻断 hypothesis 的原生 DLL，表现为与
    本测试无关的 ``ImportError: DLL load failed``。
    """
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("PYTEST") and not k.startswith("PYTHONSTARTUP")
    }
    r = subprocess.run(
        [sys.executable, "-c", _INNER, name],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=180, env=env,
    )
    if r.returncode != 0:
        pytest.fail(f"{name} subprocess failed:\n{r.stdout}\n{r.stderr}")
    for line in r.stdout.splitlines():
        if line.startswith("@@RESULT@@"):
            return json.loads(line[len("@@RESULT@@"):])
    pytest.fail(f"{name}: no result marker:\n{r.stdout}\n{r.stderr}")


# ── 1. 显式 deny 规则：两条路径一致 ─────────────────────────────────


def test_explicit_deny_blocks_both_paths():
    """evaluator 明确 deny shell 时，sync 与 async 都必须拒绝。

    修复前实测：sync 真的执行了 ``echo PERMISSION_CANARY``
    （``exit_code: 0``），async 正确拒绝。
    """
    res = _run_case("explicit_deny")
    assert res["async"]["denied"] is True, (
        f"async 路径未拒绝（场景失效）: {res['async']}"
    )
    assert res["sync"]["denied"] is True, (
        f"sync 路径绕过权限闸（P0 漏洞）: {res['sync']}"
    )


# ── 2. hard_deny H2（提权）：两条路径一致 ────────────────────────────


def test_hard_deny_h2_blocks_both_paths():
    """H2 提权（sudo）必须被 hard_deny 拦住——这是不可覆盖的独立层。"""
    res = _run_case("hard_deny_h2")
    assert res["async"]["denied"] is True, (
        f"async 未拦住 sudo（hard_deny 失效）: {res['async']}"
    )
    assert res["sync"]["denied"] is True, (
        f"sync 未拦住 sudo（hard_deny 失效）: {res['sync']}"
    )


# ── 3. hard_deny H3（凭据路径）：shell 命令串里的路径词 ──────────────


def test_hard_deny_h3_matches_path_inside_shell_command():
    """``cat ~/.ssh/id_rsa`` 必须命中 H3（``**/.ssh/**``）。

    修复前实测：两条路径都放行——``_extract_target`` 对 shell 只取
    命令首词 ``cat``，H3 的路径模式永远匹配不上。
    """
    res = _run_case("hard_deny_h3")
    assert res["sync"]["denied"] is True, (
        f"sync 未拦住读取 SSH 私钥（H3 对 shell 无感知）: {res['sync']}"
    )
    assert res["async"]["denied"] is True, (
        f"async 未拦住读取 SSH 私钥（H3 对 shell 无感知）: {res['async']}"
    )
