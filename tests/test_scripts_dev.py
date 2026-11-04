# -*- coding: utf-8 -*-
"""scripts/dev/ 两个开发辅助工具的单元测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"dev_{name}", SCRIPTS / "dev" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── clear_pyc ────────────────────────────────────────────────────────


@pytest.fixture
def cp(tmp_path, monkeypatch):
    mod = _load("clear_pyc")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "SCAN_ROOTS", ("src", "tests"))
    return mod


def test_clear_pyc_removes_and_prunes(cp, tmp_path, capsys):
    _d = tmp_path / "src" / "strategy_research" / "apps"
    (_d / "__pycache__").mkdir(parents=True)
    (_d / "__pycache__" / "db.cpython-312.pyc").write_bytes(b"x")
    (_d / "db.py").write_text("x = 1\n", encoding="utf-8")
    (_d / "__pycache___nested").mkdir()  # 名字不同不删

    sys.argv = ["clear_pyc.py"]
    assert cp.main() == 0
    assert not (_d / "__pycache__").exists()
    assert (_d / "db.py").exists()
    assert (_d / "__pycache___nested").exists()  # 只清精确名


def test_clear_pyc_dry_run_keeps(cp, tmp_path, capsys):
    d = tmp_path / "tests" / "__pycache__"
    d.mkdir(parents=True)
    sys.argv = ["clear_pyc.py", "-n"]
    assert cp.main() == 0
    assert d.exists()  # dry-run 不删


# ── diff_pytest_failures ─────────────────────────────────────────────


@pytest.fixture
def dp():
    return _load("diff_pytest_failures")


def _log(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def test_dp_parses_standard_failed_lines(dp, tmp_path, capsys):
    a = _log(tmp_path, "a.log", [
        "FAILED tests/test_x.py::test_a - AssertionError",
        "FAILED tests/test_x.py::test_b",
        "3 failed, 10 passed",
    ])
    b = _log(tmp_path, "b.log", [
        "FAILED tests/test_x.py::test_b",
        "FAILED tests/test_y.py::test_c",
        "2 failed, 11 passed",
    ])
    sys.argv = ["diff", a, b]
    assert dp.main() == 0
    out = capsys.readouterr().out
    assert "before: 2" in out and "after : 2" in out
    assert "STILL 1 | FIXED 1 | NEW 1" in out
    assert "+ tests/test_y.py::test_c" in out
    assert "- tests/test_x.py::test_a" in out


def test_dp_group_by_file(dp, tmp_path, capsys):
    a = _log(tmp_path, "a.log", [])
    b = _log(tmp_path, "b.log", [
        "FAILED tests/test_m.py::t1",
        "FAILED tests/test_m.py::t2",
        "FAILED tests/test_n.py::t3",
    ])
    sys.argv = ["diff", a, b, "--group-by-file"]
    assert dp.main() == 0
    out = capsys.readouterr().out
    assert "tests/test_m.py" in out and "tests/test_n.py" in out


def test_dp_empty_sets(dp, tmp_path, capsys):
    a = _log(tmp_path, "a.log", ["10 passed"])
    b = _log(tmp_path, "b.log", ["10 passed"])
    sys.argv = ["diff", a, b]
    assert dp.main() == 0
    assert "STILL 0 | FIXED 0 | NEW 0" in capsys.readouterr().out
