"""E6 · kernel 独立运行验收——删掉 apps 后 agent 仍可用。

**这是「通用平台」的唯一硬证明**（phase1-definition-of-done §D3）：
不是 "kernel 里没有量化名词"，是**没有 apps 也能用**。

实现：子进程 + meta_path 阻断器——任何 ``agentnodes.apps.*`` import
一律 ImportError，等价于 apps 目录不存在。然后在被阻断的环境里
构建 chat agent 并跑 E1–E5 核心链（write → read → shell）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_STANDALONE_SCRIPT = r'''
import sys

# ── 阻断 agentnodes.apps.*（等价于目录不存在）──────────────────────
class _AppsBlocker:
    """meta_path finder：agentnodes.apps 及其子模块一律拒绝。"""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "agentnodes.apps" or fullname.startswith("agentnodes.apps."):
            raise ImportError(
                f"[E6] apps blocked (kernel must be standalone): {fullname}"
            )
        return None

# 清掉可能已缓存的 apps 模块
for _m in list(sys.modules):
    if _m == "agentnodes.apps" or _m.startswith("agentnodes.apps."):
        del sys.modules[_m]
sys.meta_path.insert(0, _AppsBlocker())

# ── E1：chat 构建在无 apps 环境下成功 ──────────────────────────────
import json
import tempfile
from pathlib import Path

from agentnodes.foundation.llm import LLMConfig
from agentnodes.kernel.agent.factory.chat_loop import build_chat_agent_loop
from agentnodes.kernel.agent.tools.context import build_tool_context

ws = Path(tempfile.mkdtemp())
loop = build_chat_agent_loop(
    LLMConfig(api_key="sk-test"), "e6-standalone", workspace=ws
)

names = {t.name for t in loop.registry.all_tools()}
assert len(names) >= 15, f"too few tools: {sorted(names)}"
for need in ("read", "write", "shell", "list", "tool_help"):
    assert need in names, f"missing {need}"

# 权限闸默认接线（批次 7）
assert getattr(loop, "_permission_evaluator", None) is not None, (
    "permission gate not wired in standalone mode"
)

# 业务工具缺席（无 apps = 无 goal.* kind = 无 tool.goal 组）
assert "create_goal" not in names, "business tool leaked into standalone kernel"

# ── E2/E3/E4：write → read → shell 闭环 ────────────────────────────
ctx = build_tool_context(
    tool_call_id="e6",
    workspace=ws,
    permission_evaluator=loop._permission_evaluator,
)

w = json.loads(loop.registry.execute(
    "write", {"path": "hello.py", "content": "print('hi')\n"}, ctx=ctx
))
assert w["status"] == "ok", w

r = loop.registry.execute("read", {"path": "hello.py"}, ctx=ctx)
assert "print" in str(r), r

run = json.loads(loop.registry.execute(
    "shell", {"command": "python hello.py"}, ctx=ctx
))
assert run["status"] == "ok", run
assert "hi" in run["stdout"], run

# ── E5：边界仍成立 ─────────────────────────────────────────────────
esc = json.loads(loop.registry.execute(
    "write", {"path": "../escape.txt", "content": "x"}, ctx=ctx
))
assert esc["status"] == "error", esc

sud = json.loads(loop.registry.execute(
    "shell", {"command": "sudo rm x"}, ctx=ctx
))
assert sud["status"] == "error", sud
assert sud.get("permission_denied") is True

print("E6 STANDALONE OK")
'''


@pytest.mark.flaky(reruns=2, reruns_delay=3)
def test_kernel_standalone_without_apps():
    """无 apps 环境：chat 构建 + write/read/shell 闭环 + 权限边界全过。

    flaky(reruns=2)：本测试在 8 worker 并行下偶发子进程栈溢出
    （Windows returncode 0xC00000FD，内存压力下解释器启动期分配失败），
    单跑稳定通过 —— 重跑一次新子进程即恢复。断言本身是确定性的。
    """
    r = subprocess.run(
        [sys.executable, "-c", _STANDALONE_SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=120,
    )
    combined = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0, f"E6 failed:\n{combined[-3000:]}"
    assert "E6 STANDALONE OK" in r.stdout
