"""W10-1: ``loop`` 与 ``loop_compaction`` 之间不得再有 eager import 环。

背景：``loop.py`` 在模块顶层 ``from .loop_compaction import LoopCompactionMixin``，
而 ``loop_compaction.py`` 曾 ``from . import loop as _loop_mod`` 只为读取
``_compaction_persister`` 这一个注册变量。两者构成真环：先导入
``loop_compaction`` 会抛 ``ImportError: cannot import name
'LoopCompactionMixin' from partially initialized module``。

修法：把注册机制（变量 + register/get + 上下文管理器）下沉到 ``compact.py``
（双方都已依赖的中立落点），``loop.py`` 改为 re-export。

本测试用**子进程**验证导入顺序无关性——同进程内前一次 import 会污染
``sys.modules``，测不出这个 bug。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
AGENT = "agentnodes.kernel.agent"


def _run(code: str) -> subprocess.CompletedProcess[str]:
    import os

    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


@pytest.mark.parametrize(
    "first,second",
    [
        ("loop_compaction", "loop"),
        ("loop", "loop_compaction"),
        ("compact", "loop_compaction"),
        ("loop_compaction", "loop_lifecycle"),
    ],
)
def test_import_order_does_not_matter(first: str, second: str) -> None:
    """任意导入顺序都必须成功——这正是断环前不成立的性质。"""
    code = f"import {AGENT}.{first}\nimport {AGENT}.{second}\nprint('ok')"
    result = _run(code)
    assert result.returncode == 0, (
        f"importing {first} then {second} failed:\n{result.stderr}"
    )


def test_loop_compaction_importable_first() -> None:
    """直接 import ``loop_compaction``（断环前会 ImportError）。"""
    result = _run(
        f"from {AGENT}.loop_compaction import LoopCompactionMixin\n"
        "print(LoopCompactionMixin.__name__)"
    )
    assert result.returncode == 0, result.stderr
    assert "LoopCompactionMixin" in result.stdout


def test_registration_is_shared_across_modules() -> None:
    """注册经 ``loop`` 进入后，``compact`` 与 ``loop_compaction`` 必须看到同一对象。

    这是断环的关键前提：注册变量只能有一份，否则 ``loop_compaction`` 的
    读路径会永远看到 ``None`` 并 fail-fast 抛 RuntimeError。
    """
    code = f"""
import {AGENT}.loop_compaction  # noqa: F401  (ensures the reader module is loaded)
import {AGENT}.compact as compact_mod
from {AGENT}.loop import (register_compaction_persister,
                          compaction_persister_registered)

def fake(**kwargs):
    return "persisted"

register_compaction_persister(fake)
assert compact_mod.get_compaction_persister() is fake, "registration invisible to compact"

with compaction_persister_registered(None):
    assert compact_mod.get_compaction_persister() is None

assert compact_mod.get_compaction_persister() is fake, "context manager did not restore"
register_compaction_persister(None)
assert compact_mod.get_compaction_persister() is None
print("ok")
"""
    result = _run(code)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_loop_reexports_legacy_names() -> None:
    """``interfaces/api/app.py`` 与既有测试仍从 ``loop`` 导入这些名字。

    Asserts *behaviour*, not object identity: other tests legitimately reload
    or monkeypatch these modules, and an ``is`` check would then depend on
    execution order (it failed that way in the full suite while passing in
    isolation). What matters is that both import paths reach one shared
    registration slot.
    """
    from agentnodes.kernel.agent import compact, loop

    # Names are importable from ``loop`` (back-compat surface).
    for name in (
        "register_compaction_persister",
        "compaction_persister_registered",
        "get_compaction_persister",
    ):
        assert hasattr(loop, name), name

    # Registering through ``loop`` is visible through ``compact`` — the
    # property that actually breaks cycles and keeps the reader consistent.
    sentinel = lambda **kwargs: "persisted"  # noqa: E731
    try:
        loop.register_compaction_persister(sentinel)
        assert compact.get_compaction_persister() is sentinel
        with loop.compaction_persister_registered(None):
            assert compact.get_compaction_persister() is None
        assert compact.get_compaction_persister() is sentinel
    finally:
        loop.register_compaction_persister(None)
