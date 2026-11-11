"""权限判定的**真实调用形态**测试（R-018）。

来源：docs/design/asset-value-review-20261111.md §1 —— 3 个实测可达绕过。
教训：`test_permission_hardening.py` 等 64 条全绿，但断言用的 target 全是
``/abs/path/.env`` 或 ``config/.env``——**没有一条测裸相对路径**，也没有
一条用 registry 里的真实工具名。本文件补的就是这些形态。

写法遵循：
- R-009（断言必须有正向前提）：每条红测试都带一条「深度形态能拦」的
  前提断言，证明 hard_deny 机器本身在岗，失败定位到具体缺口；
- R-010（未实现的目标状态用 strict xfail 钉住）：修实现前这些断言是
  红的，``xfail(strict=True, raises=AssertionError)``；落地后 XPASS
  → strict 判失败 → 强制摘 marker。

三个被钉的缺陷（2026-11-11 实测）：
1. hard_deny ``**/.env`` 对裸相对路径 ``.env`` 不匹配（`_glob_match` 把
   ``**`` 替换成 ``*`` 后仍要求一个 ``/``）→ ``read .env`` 读出密钥（P0）。
   同根：``.aws/credentials`` 裸形态也放行（``.ssh`` 当年被手工补过裸规则
   ``.ssh/**``，其余模式没有——逐条手补是补不完的，修法在 `_glob_match`）；
2. registry 真实工具名 ``webfetch`` vs 枚举值 ``web_fetch`` → 云 metadata
   的 hard_deny 对真实名不生效；
3. ``run_bg_command`` 不在 ``Permission`` 枚举 → `_extract_target` 拿不到
   命令串，H2 提权规则完全失效。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.chat_loop import build_default_permission_evaluator
from agentnodes.kernel.agent.tool_context import build_tool_context
from agentnodes.kernel.permission import PermissionDeniedError


# ── 前提：生产装配形态（R-018：工具名取 registry，evaluator 取生产 builder）──


@pytest.fixture(scope="module")
def evaluator():
    return build_default_permission_evaluator()


# ── 正向前提（必须一直绿：证明 hard_deny 机器在岗）─────────────────────


def test_deep_env_path_denied(evaluator):
    """前提：`config/.env`（带目录前缀）被 hard_deny 拦——机器是好的。"""
    d = evaluator.evaluate("read", {"path": "config/.env"})
    assert d.action.value == "deny", f"前提失败：config/.env -> {d.action.value}"
    assert getattr(d, "hard", False), "前提失败：config/.env 不是 hard deny"


def test_shell_ssh_key_denied(evaluator):
    """前提：shell 整串 target（批次 13 附录 P0 修）对 ssh key 生效。"""
    d = evaluator.evaluate("shell", {"command": "cat ~/.ssh/id_rsa"})
    assert d.action.value == "deny"
    assert getattr(d, "hard", False)


def test_webfetch_enum_name_metadata_denied(evaluator):
    """前提：枚举名 `web_fetch` 能拦云 metadata——洞只在真实工具名。"""
    d = evaluator.evaluate(
        "web_fetch", {"url": "http://169.254.169.254/latest/meta-data/"}
    )
    assert d.action.value == "deny"
    assert getattr(d, "hard", False)


# ── 缺陷 1（P0）：hard_deny 不匹配裸相对路径 ────────────────────────────


def test_bare_env_denied(evaluator):
    """`read .env`——模型最自然的写法，修复前恰好是唯一被放过的形态。"""
    # 前提（同一 evaluator 实例）：深度形态能拦
    assert evaluator.evaluate("read", {"path": "sub/.env"}).action.value == "deny"
    d = evaluator.evaluate("read", {"path": ".env"})
    assert d.action.value == "deny", f"read .env -> {d.action.value}（裸相对路径被放行）"
    assert getattr(d, "hard", False), "read .env 未走 hard_deny"


def test_bare_env_local_denied(evaluator):
    d = evaluator.evaluate("read", {"path": ".env.local"})
    assert d.action.value == "deny", f"read .env.local -> {d.action.value}"
    assert getattr(d, "hard", False)


def test_bare_ssh_key_denied_control(evaluator):
    """裸 .ssh 已被拦——因为 schema.py:112 手工补过 `.ssh/**` 裸形态规则。

    这条是**对照组**：它证明规则作者知道裸形态问题，但修法是逐条手工补
    （.ssh 补了，.env / .aws / .kube 漏了）。通用修法在 `_glob_match`。
    """
    d = evaluator.evaluate("read", {"path": ".ssh/id_rsa"})
    assert d.action.value == "deny", f"read .ssh/id_rsa -> {d.action.value}"
    assert getattr(d, "hard", False)


def test_bare_aws_credentials_denied(evaluator):
    """与 .env 同根：.ssh 当年手工补了裸规则，.aws/.kube 没补。"""
    d = evaluator.evaluate("read", {"path": ".aws/credentials"})
    assert d.action.value == "deny", f"read .aws/credentials -> {d.action.value}"
    assert getattr(d, "hard", False)


# ── 缺陷 2：registry 真实工具名 `webfetch` vs 枚举值 `web_fetch` ────────


def test_webfetch_real_name_metadata_denied(evaluator):
    """registry 里的真实名字是 `webfetch`（见 build_default_registry）。"""
    from agentnodes.kernel.agent.builtin_tools import build_default_registry
    assert "webfetch" in build_default_registry()._tools, "前提失败：registry 没有 webfetch"
    d = evaluator.evaluate(
        "webfetch", {"url": "http://169.254.169.254/latest/meta-data/"}
    )
    assert d.action.value == "deny", (
        f"webfetch(metadata) -> {d.action.value}（真实工具名未触发 hard_deny）"
    )
    assert getattr(d, "hard", False)


# ── 缺陷 3：run_bg_command 不在 Permission 枚举 ─────────────────────────


def test_run_bg_command_sudo_denied(evaluator):
    """run_bg_command 能起后台进程——恰是最该拦 sudo 的工具。"""
    d = evaluator.evaluate("run_bg_command", {"command": "sudo rm -rf /", "action": "start"})
    assert d.action.value == "deny", (
        f"run_bg_command(sudo) -> {d.action.value}（target={d.target!r}）"
    )
    assert getattr(d, "hard", False)


def test_run_bg_command_ssh_key_denied(evaluator):
    d = evaluator.evaluate(
        "run_bg_command", {"command": "cat ~/.ssh/id_rsa", "action": "start"}
    )
    assert d.action.value == "deny", (
        f"run_bg_command(ssh key) -> {d.action.value}（target={d.target!r}）"
    )
    assert getattr(d, "hard", False)


# ── E2E：真实 registry 工具 + 生产装配 evaluator（不经 LLM）────────────


def _make_ctx(tmp_path: Path):
    return build_tool_context(
        tool_call_id="c1", workspace=tmp_path, session_id="s",
        permission_evaluator=build_default_permission_evaluator(),
    )


def test_e2e_read_bare_env_denied(tmp_path):
    """走 tool.ainvoke（生产调用链）：read .env 必须被拒，不能读出内容。"""
    (tmp_path / ".env").write_text(
        "MINIMAX_API_KEY=nvap-SHOULD-NOT-LEAK\n", encoding="utf-8"
    )
    tool = build_default_registry()._tools["read"]
    ctx = _make_ctx(tmp_path)
    denied = False
    try:
        # 生产调用形态（loop_tools.py:161）：kwargs 里带 ctx
        out = asyncio.run(tool.ainvoke({"path": ".env", "ctx": ctx}, ctx))
    except PermissionDeniedError:
        denied = True
    assert denied, f"read .env 未被拒，工具返回了：{str(out)[:120]!r}"


def test_e2e_webfetch_fail_closed(tmp_path):
    """回归网：webfetch 无论走 ask（无通道 fail-closed）还是 deny 都必须拒。"""
    tool = build_default_registry()._tools["webfetch"]
    ctx = _make_ctx(tmp_path)
    with pytest.raises(PermissionDeniedError):
        asyncio.run(tool.ainvoke(
            {"url": "http://169.254.169.254/latest/meta-data/", "ctx": ctx}, ctx,
        ))


def test_e2e_run_bg_command_fail_closed(tmp_path):
    """回归网：run_bg_command 良性/提权命令当前走 ask fail-closed，修复后走 deny。"""
    tool = build_default_registry()._tools["run_bg_command"]
    ctx = _make_ctx(tmp_path)
    # sudo --version：即使闸失效被执行也无害（Windows 无 sudo；Linux 只打印版本）
    with pytest.raises(PermissionDeniedError):
        asyncio.run(tool.ainvoke(
            {"action": "start", "command": "sudo --version", "ctx": ctx}, ctx,
        ))
