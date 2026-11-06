"""Tests for scripts/arch/check_tool_refs.py (批次 4c 工具引用门禁).

门禁本身要被测——否则它就是「检查永远绿」的假门禁（本仓库的既有教训）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "arch" / "check_tool_refs.py"


@pytest.fixture(scope="module")
def gate():
    """导入门禁模块（复用同一解释器的 import 能力）。"""
    sys.path.insert(0, str(ROOT / "scripts" / "arch"))
    import check_tool_refs  # noqa: PLC0415

    return check_tool_refs


class TestSelftest:
    def test_selftest_passes(self):
        """门禁自带 6 条断言，必须全过。"""
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--selftest"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ROOT,
        )
        assert r.returncode == 0, f"selftest failed:\n{r.stdout}\n{r.stderr}"
        assert "selftest passed" in r.stdout


class TestRealRepo:
    def test_repo_is_clean(self):
        """真实仓库必须零违规（否则批次 4c 不算完成）。"""
        r = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ROOT,
        )
        assert r.returncode == 0, (
            f"tool refs violation:\n{r.stdout}\n{r.stderr}"
        )


class TestDetection:
    """逐类验证检测能力（用注入参数，不依赖真实仓库状态）。"""

    def test_toolset_dangling(self, gate):
        rep = gate.check(
            installed={"read"},
            toolsets={"bad": {"read", "ghost"}},
            plugins=[],
            templates={},
            pending={},
            executors=(set(), set()),
            pending_executors={},
        )
        assert not rep.ok
        assert any("ghost" in v.missing for v in rep.violations)

    def test_plugin_tool_dangling(self, gate):
        rep = gate.check(
            installed={"read"},
            toolsets={},
            plugins=[("p", {"read", "nope"}, "")],
            templates={},
            pending={},
            executors=(set(), set()),
            pending_executors={},
        )
        assert not rep.ok

    def test_plugin_executor_dangling(self, gate):
        rep = gate.check(
            installed={"read"},
            toolsets={},
            plugins=[("p", set(), "missing_fn")],
            templates={},
            pending={},
            executors=(set(), set()),
            pending_executors={},
        )
        assert any("missing_fn" in v.missing for v in rep.violations)

    def test_template_dangling(self, gate):
        rep = gate.check(
            installed={"read"},
            toolsets={},
            plugins=[],
            templates={"t.json": {"read", "ghost_tool"}},
            pending={},
            executors=(set(), set()),
            pending_executors={},
        )
        assert any(v.surface == "templates" for v in rep.violations)

    def test_pending_tools_is_exempt(self, gate):
        """登记在 PENDING_TOOLS 里的悬空**不算**违规（是待恢复清单）。"""
        rep = gate.check(
            installed={"read"},
            toolsets={},
            plugins=[("p", {"read", "future_tool"}, "")],
            templates={},
            pending={"mod": {"future_tool"}},
            executors=(set(), set()),
            pending_executors={},
        )
        assert rep.ok, [v.where for v in rep.violations]

    def test_unregistered_dangling_still_fails(self, gate):
        """没登记在 PENDING_TOOLS 的悬空必须仍然拦下（豁免不是通配）。"""
        rep = gate.check(
            installed={"read"},
            toolsets={},
            plugins=[("p", {"read", "silent_break"}, "")],
            templates={},
            pending={"mod": {"some_other_tool"}},
            executors=(set(), set()),
            pending_executors={},
        )
        assert not rep.ok
        assert any("silent_break" in v.missing for v in rep.violations)

    def test_pending_executor_is_exempt(self, gate):
        rep = gate.check(
            installed={"read"},
            toolsets={},
            plugins=[("p", set(), "future_fn")],
            templates={},
            pending={},
            executors=(set(), set()),
            pending_executors={"mod": {"future_fn"}},
        )
        assert rep.ok, [v.where for v in rep.violations]

    def test_stale_pending_is_violation(self, gate):
        """清单陈旧（已实现却仍在待恢复清单里）也要报——反向检查。"""
        rep = gate.check(
            installed={"read", "write"},
            toolsets={},
            plugins=[],
            templates={},
            pending={"mod": {"read", "not_yet"}},
            executors=(set(), set()),
            pending_executors={},
        )
        assert not rep.ok
        assert any(v.surface == "pending" for v in rep.violations)

    def test_stale_pending_executor_is_violation(self, gate):
        rep = gate.check(
            installed={"read"},
            toolsets={},
            plugins=[],
            templates={},
            pending={},
            executors=({"live_fn"}, set()),
            pending_executors={"mod": {"live_fn"}},
        )
        assert not rep.ok
        assert any(v.surface == "pending" for v in rep.violations)

    def test_all_green_no_false_positive(self, gate):
        rep = gate.check(
            installed={"read", "write"},
            toolsets={"chat": {"read", "write"}},
            plugins=[("p", {"read"}, "")],
            templates={"t.json": {"write"}},
            pending={},
            executors=(set(), set()),
            pending_executors={},
        )
        assert rep.ok, [v.where for v in rep.violations]


class TestCollectors:
    def test_installed_tools_nonempty(self, gate):
        assert len(gate.installed_tools()) >= 10

    def test_toolsets_are_collected(self, gate):
        ts = gate.collect_toolsets()
        assert "minimal" in ts
        assert "chat" in ts

    def test_templates_collected_are_generic(self, gate):
        """templates/workflows/ 只剩通用模板（量化模板已迁 L2）。"""
        tpl = gate.collect_templates()
        assert "alpha_research.json" not in tpl
        assert "data_quality_audit.json" not in tpl
        assert "plan_execute_auto.json" in tpl
