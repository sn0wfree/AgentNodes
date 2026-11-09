"""路径 B 能力冒烟：``make_react_node`` 的 E1–E5 等价断言。

**为什么需要这个文件**：系统里有**两条并行的生产执行路径**（批次 13 附录
后的 V1 评估实测）：

- **路径 A**：``AgentLoop`` → ``_execute_tool_call_core``
  （生产消费者：旧 ``/send_async``、scheduled_research、evals、CLI/TUI）
- **路径 B**：``make_react_node`` → ``registry.execute(ctx=...)``
  （生产消费者：**web chat**，经 ``Gateway → SessionController``）

``tests/test_capability_smoke.py`` 覆盖路径 A；``tests/test_react_live.py``
覆盖路径 B 但需要真实 LLM（默认 skip）。**路径 B 在本测试补齐：用 mock
LLM 覆盖它的工具执行、权限闸、工作区边界。**

差异（本测试要钉住的）：
- 路径 B 的工具执行经 ``registry.execute(name, params, ctx=ctx)``
- 路径 B 的权限闸在 ``ToolRegistry.execute`` 内部
  （``tool.enforce_permission_sync``）
- 路径 B **没有** no-progress / guardrails / hooks / deciders
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.tool_context import make_ctx_factory
from agentnodes.kernel.permission import PermissionEvaluator
from agentnodes.kernel.permission.schema import MODE_PRESETS, PermissionMode


class _ScriptedLLM:
    """按脚本返回 LLMResponse 的假客户端（react 节点是同步调用）。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if not self._responses:
            return LLMResponse(content="done", tool_calls=[],
                               finish_reason="stop")
        return self._responses.pop(0)


def _tool_resp(name: str, args: dict, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _text_resp(text: str = "完成") -> LLMResponse:
    return LLMResponse(content=text, tool_calls=[], finish_reason="stop")


def _make_node(workspace: Path, responses, *, rules=None, gateway=None):
    """构造 react 节点：权限件经 ctx_factory 注入（与生产一致）。"""
    from agentnodes.kernel.runtimes.nodes.react import (
        ReactConfig, make_react_node,
    )

    registry = build_default_registry()
    evaluator = PermissionEvaluator(
        ruleset=list(rules) if rules is not None
        else list(MODE_PRESETS[PermissionMode.L2_SANDBOX_EXEC.value]),
    )
    ctx_factory = make_ctx_factory(
        workspace=workspace,
        session_id="sess-react-smoke",
        permission_evaluator=evaluator,
        permission_gateway=gateway,
        emit_event=lambda kind, data: None,
    )
    client = _ScriptedLLM(responses)
    node = make_react_node(
        client, registry,
        config=ReactConfig(
            system="你是测试助手。",
            max_iterations=len(responses) + 1,
            ctx_factory=ctx_factory,
        ),
    )
    return node, client


def _run_node(node, workspace: Path) -> dict:
    return node({
        "messages": [{"role": "user", "content": "执行测试任务"}],
        "run_id": "react-smoke:r0",
    })


def _tool_outputs(state: dict) -> list[str]:
    return [m.get("content") or "" for m in state.get("messages") or []
            if m.get("role") == "tool"]


def _parse(outputs: list[str]) -> dict:
    """把最后一个工具输出解析成 dict（权限拒绝是文本）。"""
    if not outputs:
        return {}
    raw = outputs[-1]
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = {"status": "error", "error": raw}
    if isinstance(parsed, dict):
        err = str(parsed.get("error") or "")
        if "PermissionDenied" in err or "Permission denied" in err:
            parsed["permission_denied"] = True
            parsed["status"] = "error"
    return parsed if isinstance(parsed, dict) else {"result": parsed}


# ── E2 · 执行代码（路径 B）───────────────────────────────────────────


def test_react_node_runs_shell(tmp_path: Path):
    """E2：shell 经 registry.execute 执行并返回 stdout。"""
    node, _ = _make_node(
        tmp_path,
        [_tool_resp("shell", {"command": "echo react-path"}), _text_resp()],
    )
    state = _run_node(node, tmp_path)
    out = _parse(_tool_outputs(state))
    assert out.get("status") == "ok", out
    assert "react-path" in out.get("stdout", ""), out


def test_react_node_hard_deny_blocks_sudo(tmp_path: Path):
    """H2 提权在路径 B 上也必须被 hard_deny 拦住。"""
    node, _ = _make_node(
        tmp_path,
        [_tool_resp("shell", {"command": "sudo rm x"}), _text_resp()],
    )
    state = _run_node(node, tmp_path)
    out = _parse(_tool_outputs(state))
    assert out.get("permission_denied") is True, out


def test_react_node_hard_deny_blocks_ssh_path(tmp_path: Path):
    """H3：shell 命令串里的凭据路径必须命中（V1 前两条路径都漏）。"""
    node, _ = _make_node(
        tmp_path,
        [_tool_resp("shell", {"command": "cat ~/.ssh/id_rsa"}), _text_resp()],
    )
    state = _run_node(node, tmp_path)
    out = _parse(_tool_outputs(state))
    assert out.get("permission_denied") is True, out


# ── E3/E4 · coding 闭环（路径 B）─────────────────────────────────────


def test_react_node_write_then_read(tmp_path: Path):
    """E3/E4：write → read 闭环。"""
    node, _ = _make_node(
        tmp_path,
        [
            _tool_resp("write", {"path": "hello.py", "content": "print('hi')\n"}, "c1"),
            _tool_resp("read", {"path": "hello.py"}, "c2"),
            _text_resp(),
        ],
    )
    state = _run_node(node, tmp_path)
    assert (tmp_path / "hello.py").exists(), "文件未创建"
    outs = _tool_outputs(state)
    assert len(outs) >= 2, outs
    assert "print" in json.dumps(_parse(outs[1:]), ensure_ascii=False), outs[1:]


def test_react_node_write_then_execute(tmp_path: Path):
    """E4 最完整形态：write → 执行 → stdout 可见。"""
    node, _ = _make_node(
        tmp_path,
        [
            _tool_resp("write", {"path": "run_me.py", "content": "print('EXEC_OK')\n"}, "c1"),
            _tool_resp("shell", {"command": "python run_me.py"}, "c2"),
            _text_resp(),
        ],
    )
    state = _run_node(node, tmp_path)
    outs = _tool_outputs(state)
    last = _parse(outs)
    assert last.get("status") == "ok", outs
    assert "EXEC_OK" in last.get("stdout", ""), outs


# ── E5 · 工作区边界（路径 B）─────────────────────────────────────────


def test_react_node_escape_blocked(tmp_path: Path):
    """E5：`..` 逃逸仍被拒，且不产生改写后的文件。"""
    node, _ = _make_node(
        tmp_path,
        [_tool_resp("write", {"path": "../escape_b.txt", "content": "x"}), _text_resp()],
    )
    state = _run_node(node, tmp_path)
    out = _parse(_tool_outputs(state))
    assert out.get("status") == "error", out
    assert not (tmp_path.parent / "escape_b.txt").exists()
    assert not (tmp_path / "escape_b.txt").exists()


# ── 能力缺口（明确断言路径 B **没有**的东西）────────────────────────


def test_react_node_has_no_no_progress_detection(tmp_path: Path):
    """路径 B 没有 no-progress 检测——同一个 tool_call 会跑满 max_iterations。

    这条测试**记录差异**而非要求修复：V3 决策时要拿它当证据
    （路径 B 缺 no-progress 意味着失控的循环不会被拦）。
    """
    same = {"command": "echo same"}
    responses = [_tool_resp("shell", same, f"c{i}") for i in range(4)]
    node, client = _make_node(tmp_path, responses)
    state = _run_node(node, tmp_path)
    # 4 次工具调用全部执行（没有 no-progress 提前终止）
    assert len(_tool_outputs(state)) == 4, _tool_outputs(state)
    assert client.calls >= 4
