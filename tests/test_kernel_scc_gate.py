# -*- coding: utf-8 -*-
"""check_kernel_scc 门禁单测（顺序④尾部）。"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "arch" / "check_kernel_scc.py"
PROBE_PY = r"D:\tools\python312\python.exe"


def _load_module():
    """按路径加载门禁脚本（测试用，避免 scripts 非包）。"""
    spec = __import__("importlib.util", fromlist=["util"]).spec_from_file_location(
        "check_kernel_scc", GATE,
    )
    mod = __import__("importlib.util", fromlist=["util"]).module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_module()


class TestGateBehaviour:
    def test_current_repo_has_no_module_level_cycle(self, gate):
        """当前 kernel 模块级 import 图无环（硬门禁通过）。"""
        edges = gate.collect(module_level_only=True)
        adj: dict[str, set[str]] = {}
        for (a, b) in edges:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set())
        assert gate.tarjan_sccs(adj) == [], "模块级 import 环必须为 0"

    def test_lazy_scc_all_registered(self, gate):
        """全量（含 lazy）SCC 必须在 KNOWN_LAZY_SCCS 登记 owner。"""
        edges = gate.collect(module_level_only=False)
        adj: dict[str, set[str]] = {}
        for (a, b) in edges:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set())
        for comp in gate.tarjan_sccs(adj):
            assert frozenset(comp) in gate.KNOWN_LAZY_SCCS, (
                f"未登记的 lazy 环：{sorted(comp)}——须先记录 owner 与理由"
            )

    def test_known_lazy_scc_has_reason(self, gate):
        """已登记的 lazy 环必须写明 owner/理由（不得空登记）。"""
        for key, reason in gate.KNOWN_LAZY_SCCS.items():
            assert reason.strip(), f"{sorted(key)} 的登记理由为空"
            assert len(reason) >= 20, f"{sorted(key)} 的登记理由过短"

    def test_gate_exits_zero_on_current_repo(self):
        r = subprocess.run(
            [PROBE_PY, str(GATE)], capture_output=True, text=True,
            encoding="utf-8", timeout=120,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "模块级 import 图无环" in r.stdout

    def test_gate_quiet_mode_silent_on_success(self):
        r = subprocess.run(
            [PROBE_PY, str(GATE), "--quiet"], capture_output=True, text=True,
            encoding="utf-8", timeout=120,
        )
        assert r.returncode == 0
        assert r.stdout.strip() == ""


class TestGateDetectsInjectedCycle:
    """R-019：门禁的价值 = 注入违规后会不会红。"""

    def test_injected_module_level_cycle_is_detected(self, tmp_path, gate):
        """在临时 kernel 树上注入模块级环 → SCC 必须报出。"""
        k = tmp_path / "src" / "agentnodes" / "kernel"
        (k / "pkg_a").mkdir(parents=True)
        (k / "pkg_b").mkdir(parents=True)
        (k / "pkg_a" / "__init__.py").write_text("", encoding="utf-8")
        (k / "pkg_b" / "__init__.py").write_text("", encoding="utf-8")
        (k / "pkg_a" / "mod.py").write_text(
            "from agentnodes.kernel.pkg_b.mod import thing\n", encoding="utf-8",
        )
        (k / "pkg_b" / "mod.py").write_text(
            "from agentnodes.kernel.pkg_a.mod import other\n", encoding="utf-8",
        )

        orig_kernel = gate.KERNEL
        orig_subs = gate.SUBS
        try:
            gate.KERNEL = k
            gate.SUBS = sorted(p.name for p in k.iterdir()
                               if p.is_dir() and (p / "__init__.py").exists())
            edges = gate.collect(module_level_only=True)
            adj: dict[str, set[str]] = {}
            for (a, b) in edges:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set())
            sccs = gate.tarjan_sccs(adj)
            assert sccs, "注入的模块级环必须被检出"
            assert set(sccs[0]) == {"pkg_a", "pkg_b"}
        finally:
            gate.KERNEL = orig_kernel
            gate.SUBS = orig_subs

    def test_injected_lazy_cycle_is_not_module_level(self, tmp_path, gate):
        """lazy 环（函数内 import）不算模块级环——判定口径正确性。"""
        k = tmp_path / "src" / "agentnodes" / "kernel"
        (k / "pkg_a").mkdir(parents=True)
        (k / "pkg_b").mkdir(parents=True)
        (k / "pkg_a" / "__init__.py").write_text("", encoding="utf-8")
        (k / "pkg_b" / "__init__.py").write_text("", encoding="utf-8")
        (k / "pkg_a" / "mod.py").write_text(
            "def go():\n    from agentnodes.kernel.pkg_b.mod import thing\n"
            "    return thing\n", encoding="utf-8",
        )
        (k / "pkg_b" / "mod.py").write_text(
            "def go():\n    from agentnodes.kernel.pkg_a.mod import other\n"
            "    return other\n", encoding="utf-8",
        )

        orig_kernel = gate.KERNEL
        orig_subs = gate.SUBS
        try:
            gate.KERNEL = k
            gate.SUBS = sorted(p.name for p in k.iterdir()
                               if p.is_dir() and (p / "__init__.py").exists())
            mod_edges = gate.collect(module_level_only=True)
            assert mod_edges == {}, "函数内 import 不应算模块级边"
            all_edges = gate.collect(module_level_only=False)
            adj: dict[str, set[str]] = {}
            for (a, b) in all_edges:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set())
            assert gate.tarjan_sccs(adj), "lazy 环应被全量口径检出"
        finally:
            gate.KERNEL = orig_kernel
            gate.SUBS = orig_subs
