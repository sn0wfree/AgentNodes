# -*- coding: utf-8 -*-
"""scripts/migration/ 三个搬迁工具的单元测试（合成仓库，monkeypatch ROOT）。

搬迁协议（docs/physical-layer-migration-plan-20261103.md §1.2）中三个工具
各管一段：find_importers（普查）→ rewrite_import_prefix（文本级）→
fix_relative_imports（解析级）。本文件验证各自的解析/改写语义。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(pkg_dir: str, name: str):
    spec = importlib.util.spec_from_file_location(
        f"{pkg_dir}_{name}", SCRIPTS / pkg_dir / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def rip(tmp_path, monkeypatch):
    """rewrite_import_prefix with ROOT -> tmp_path."""
    mod = _load("migration", "rewrite_import_prefix")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    return mod


@pytest.fixture
def fri(tmp_path, monkeypatch):
    """fix_relative_imports with ROOT -> tmp_path."""
    mod = _load("migration", "fix_relative_imports")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    return mod


@pytest.fixture
def fi(tmp_path, monkeypatch):
    """find_importers with ROOT -> tmp_path."""
    mod = _load("migration", "find_importers")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    return mod


def _mk(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── rewrite_import_prefix ────────────────────────────────────────────


def test_rip_three_forms(rip, tmp_path):
    """module-form / pkg-import-form / path-form 三形态一次命中。"""
    p = _mk(tmp_path, "src/strategy_research/kernel/x.py", (
        "from strategy_research.core.tools import clean_data\n"
        "import strategy_research.core.tools\n"
        "from strategy_research.core import tools as t\n"
        'KEY = "core/tools.py"\n'
    ))
    n = rip.rewrite("strategy_research.core.tools", "strategy_research.apps.tools", [])
    assert n == 1
    out = p.read_text(encoding="utf-8")
    assert "from strategy_research.apps.tools import clean_data" in out
    assert "import strategy_research.apps.tools" in out
    assert "from strategy_research.apps import tools as t" in out
    assert '"apps/tools.py"' in out
    assert "core" not in out


def test_rip_full_prefix_no_false_positive(rip, tmp_path):
    """同名前缀陷阱：core.tools 不碰 core.agent.tools（历史踩点）。"""
    p = _mk(tmp_path, "src/strategy_research/kernel/y.py",
            "from strategy_research.core.agent.tools import BaseTool\n")
    rip.rewrite("strategy_research.core.tools", "strategy_research.apps.tools", [])
    assert "core.agent.tools" in p.read_text(encoding="utf-8")


def test_rip_explicit_file_list(rip, tmp_path):
    p = _mk(tmp_path, "src/strategy_research/kernel/z.py",
            "from strategy_research.core.db import get_connection\n")
    rip.rewrite("strategy_research.core.db", "strategy_research.apps.db",
                [str(p)])
    assert "strategy_research.apps.db" in p.read_text(encoding="utf-8")


# ── fix_relative_imports ─────────────────────────────────────────────


def test_fri_rewrites_resolved_relatives(fri, tmp_path):
    """解析到 core.<tail> 的相对导入改写为新绝对路径。

    工具的 old_prefix 恒为 strategy_research.core.<tail>（迁移专用语义）。
    布局须让 PEP 328 解析真实命中：importer 在 core/agent/builtin_tools/
    （包 3 层深），level-3 剥 2 → strategy_research.core + tools ✓
    （W2 期间 factor_tools 的真实形态）。
    """
    p = _mk(tmp_path, "src/strategy_research/core/agent/builtin_tools/bt.py", (
        "def f():\n"
        "    from ...tools.data_clean import clean_data\n"
        "    return clean_data\n"
        "\n"
        "def g():\n"
        "    from .data_clean_helpers import h  # 解析为 builtin_tools 内部，不命中\n"
    ))
    stmts, files = fri.run("tools", "strategy_research.apps.tools")
    assert stmts == 1 and files == 1
    out = p.read_text(encoding="utf-8")
    assert "from strategy_research.apps.tools.data_clean import clean_data" in out
    assert "from .data_clean_helpers import h" in out


def test_fri_check_mode_no_write(fri, tmp_path):
    p = _mk(tmp_path, "src/strategy_research/core/agent/builtin_tools/bt.py",
            "def f():\n    from ...db import get_connection\n")
    before = p.read_text(encoding="utf-8")
    stmts, files = fri.run("db", None)  # new_mod=None → --check
    assert stmts == 1 and files == 0
    assert p.read_text(encoding="utf-8") == before


def test_fri_suffix_preserved(fri, tmp_path):
    """解析目标带子路径时（core.tools.data_clean），新路径保留子路径。"""
    p = _mk(tmp_path, "src/strategy_research/core/agent/builtin_tools/ft.py",
            "def f():\n    from ...tools.data_clean import clean_data\n")
    fri.run("tools", "strategy_research.apps.tools")
    assert "strategy_research.apps.tools.data_clean" in p.read_text(encoding="utf-8")


# ── find_importers ───────────────────────────────────────────────────


def test_fi_absolute_and_relative(fi, tmp_path, capsys):
    _mk(tmp_path, "src/strategy_research/kernel/a.py",
        "from strategy_research.core.db import get_connection\n")
    # 相对命中的布局须真实解析到 core.db：importer 在 core/agent/builtin_tools/
    # （3 层深包），level-3 剥 2 → strategy_research.core + db ✓（W2 真实形态）
    _mk(tmp_path, "src/strategy_research/core/agent/builtin_tools/b.py",
        "def f():\n    from ...db import init_db\n")
    # 干扰项：agent/tools 不应被 core/tools 查询命中；无关 import 不算
    _mk(tmp_path, "src/strategy_research/kernel/agent/tools.py",
        "from strategy_research.core.tools import clean\n")  # 命中 core/tools ✓
    _mk(tmp_path, "src/strategy_research/kernel/c.py",
        "from strategy_research.apps.db import other\n")  # 不命中

    sys.argv = ["find_importers.py", "db", "tools", "--by-file", "--root", "src"]
    assert fi.main() == 0
    out = capsys.readouterr().out
    assert "strategy_research.core.db: 2 statements" in out
    assert "strategy_research.core.tools: 1 statements" in out
    # builtin_tools/b.py 的 level-3 相对导入被解析命中（工具输出完整绝对路径）。
    # 断言字符串里含 "core" 段拼接属测试自指，物理行加豁免（check 工具扫描
    # 的是测试文件物理行）。
    expected = os.path.join(fi.ROOT, "src", "strategy_research", "core", "agent",  # path-literal: ok
                            "builtin_tools", "b.py")
    assert expected in out  # path-literal: ok
    # 干扰项 c.py 不在 db 结果里
    db_section = out.split("== strategy_research.core.db")[1].split("==")[0]
    assert (str(tmp_path / "src" / "strategy_research" / "kernel" / "c.py")
            not in db_section)
