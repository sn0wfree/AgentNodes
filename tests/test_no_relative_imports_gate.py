# -*- coding: utf-8 -*-
"""``check_no_relative_imports`` 门禁的单元测试（合成树 + 真仓 + 自检三件套）。

钉住（docs/design/absolute-imports-migration-20261117.md）：
src/agentnodes 内 ImportFrom 的 level 必须全为 0（绝对导入）。
R-009/R-019：真仓清查必须有「注入必红」反向检验，否则扫描器失效时是恒真空断言。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "arch" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def test_detects_single_and_double_dot(tmp_path):
    """单点 / 双点 / 三点相对导入全部检出，行号精确。"""
    mod = _load("check_no_relative_imports")
    pkg = tmp_path / "pkg" / "sub"
    pkg.mkdir(parents=True)
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "m.py").write_text(
        "from .models import A\n"          # 1
        "from ..sibling import B\n"        # 2
        "from agentnodes.pkg.models import C\n"  # 3 绝对 → 不报
        "from . import helpers\n"          # 4
        "from ...top import D\n",          # 5
        encoding="utf-8",
    )
    hits = mod.find_relative_imports(tmp_path)
    assert {h[1] for h in hits} == {1, 2, 4, 5}, hits
    assert all(h[0] == "pkg/sub/m.py" for h in hits)


def test_absolute_only_clean(tmp_path):
    mod = _load("check_no_relative_imports")
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "m.py").write_text(
        "import os\n"
        "from agentnodes.pkg.models import A\n"
        "from agentnodes import kernel\n",
        encoding="utf-8",
    )
    assert mod.find_relative_imports(tmp_path) == []


def test_function_level_relative_import_detected(tmp_path):
    """函数内惰性相对导入同样违规（迁移时位置保留但路径必须绝对）。"""
    mod = _load("check_no_relative_imports")
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "m.py").write_text(
        "def f():\n"
        "    from .models import A\n"
        "    return A\n",
        encoding="utf-8",
    )
    hits = mod.find_relative_imports(tmp_path)
    assert len(hits) == 1 and hits[0][1] == 2


def test_real_repo_zero():
    """真实仓库当前状态即基准：src/agentnodes 相对导入 0 处。

    失败意味着有人写回了 `from .x import y` —— 改成
    `from agentnodes.<pkg>.x import y`（见迁移设计文档）。
    """
    mod = _load("check_no_relative_imports")
    hits = mod.find_relative_imports(mod.SRC)
    detail = "\n".join(f"{r}:{ln} from {f} import ..." for r, ln, f in hits[:20])
    assert hits == [], f"src/ 出现 {len(hits)} 处相对导入：\n{detail}"


def test_main_contract(monkeypatch, capsys, tmp_path):
    """main 契约：真仓 rc=0；注入后 rc=1 并报出 文件:行号。"""
    mod = _load("check_no_relative_imports")
    assert mod.main([]) == 0
    assert "[OK]" in capsys.readouterr().out

    p = tmp_path / "pkg"
    p.mkdir()
    (p / "bad.py").write_text("from .x import y\n", encoding="utf-8")
    monkeypatch.setattr(mod, "SRC", tmp_path)
    assert mod.main([]) == 1
    out = capsys.readouterr().out
    assert "pkg/bad.py:1" in out and ".x" in out


def test_selftest_passes():
    mod = _load("check_no_relative_imports")
    assert mod.selftest() == 0
