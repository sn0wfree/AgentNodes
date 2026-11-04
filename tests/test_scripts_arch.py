# -*- coding: utf-8 -*-
"""scripts/arch/ 三个架构守卫工具的单元测试。

测试策略：**合成仓库**——用 tmp_path 造 src/strategy_research 最小结构 +
monkeypatch 工具模块的 ROOT/PKG_DIR 指向它，隔离且可控；同时各保留一个
对真实仓库的端到端断言（真实仓库当前状态即基准）。

工具退出码契约（scripts/README.md）：arch/ = 0 通过 · 1 发现问题。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS / "arch" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── check_dangling_imports ───────────────────────────────────────────


@pytest.fixture
def dangling_mod(tmp_path, monkeypatch):
    mod = _load("check_dangling_imports")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "PKG_DIR", str(tmp_path / "src" / "strategy_research"))
    return mod


def _mk_repo(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_dangling_module_exists_true_false(dangling_mod, tmp_path):
    _mk_repo(tmp_path, {
        "src/strategy_research/apps/db.py": "x = 1\n",
        "src/strategy_research/apps/pkg/__init__.py": "",
    })
    assert dangling_mod.module_exists("strategy_research.apps.db")
    assert dangling_mod.module_exists("strategy_research.apps.pkg")  # 目录形
    assert not dangling_mod.module_exists("strategy_research.apps.missing")
    # 第三方模块不在本门禁范围（恒 True）
    assert dangling_mod.module_exists("pandas")


def test_dangling_detects_relative_levels(dangling_mod, tmp_path):
    """level 1/2/3 相对导入解析：只有指向缺失模块的报悬空。

    importer 在 kernel/gateway/service.py（包 kernel.gateway）：
      ..agent.tools   -> kernel.agent.tools（存在）
      ...apps.db      -> apps.db（存在）
      .missing_mod    -> kernel.gateway.missing_mod（悬空）
      ..nope          -> kernel.gateway.nope（悬空）
    """
    _mk_repo(tmp_path, {
        "src/strategy_research/kernel/agent/tools.py": "x = 1\n",
        "src/strategy_research/apps/db.py": "x = 1\n",
        "src/strategy_research/kernel/gateway/service.py": (
            "from ..agent.tools import ok\n"
            "from ...apps.db import ok2\n"
            "from .missing_mod import bad\n"
            "from ..nope import bad2\n"
        ),
    })
    bad = dangling_mod.scan(["src"])
    assert len(bad) == 2, bad
    assert all("gateway" in b for b in bad)


def test_dangling_absolute_missing_detected(dangling_mod, tmp_path):
    """绝对 import 指向不存在模块（搬迁改写漏网的形态）必报。"""
    _mk_repo(tmp_path, {
        "src/strategy_research/kernel/loop.py":
            "from strategy_research.core.agent import gone\n",  # core/ 已删除
    })
    bad = dangling_mod.scan(["src"])
    assert len(bad) == 1
    assert "core.agent" in bad[0]


def test_dangling_real_repo_clean():
    """端到端：真实仓库当前零悬空（基线状态，坏了说明有人搬包未收口）。"""
    mod = _load("check_dangling_imports")
    assert mod.scan(["src", "tests"]) == []


# ── check_path_literals ──────────────────────────────────────────────


@pytest.fixture
def literals_mod(tmp_path, monkeypatch):
    mod = _load("check_path_literals")
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    return mod


def test_literals_core_segment_detected(literals_mod, tmp_path):
    _mk_repo(tmp_path, {
        "src/strategy_research/kernel/swarm.py":
            # 合成内容含原始 pattern——本文件行加豁免（扫描器只看测试文件物理行）
            'presets = Path(__file__).parent / "core" / "swarm" / "presets"\n'  # path-literal: ok
            ,
    })
    bad = literals_mod.scan(set())
    assert len(bad) == 1
    assert "core" in bad[0]


def test_literals_invalid_layer_detected(literals_mod, tmp_path):
    _mk_repo(tmp_path, {
        "tests/test_x.py":
            'p = Path(__file__).parent / "src" / "strategy_research" / "api" / "routers"\n'  # path-literal: ok
            ,
    })
    bad = literals_mod.scan(set())
    assert len(bad) == 1
    assert '层段 "api"' in bad[0]


def test_literals_valid_layer_and_exempt_mark(literals_mod, tmp_path):
    _mk_repo(tmp_path, {
        # templates 是合法层段；core 行有豁免标记
        "tests/test_ok.py": (
            't = base / "strategy_research" / "templates"\n'
            'c = base / "core" / "swarm"  # path-literal: ok\n'
        ),
    })
    assert literals_mod.scan(set()) == []


def test_literals_allow_flag(literals_mod, tmp_path):
    # 豁免标记必须进入"被写入合成仓库的文件内容"——扫描器扫物理文件的行为
    _mk_repo(tmp_path, {
        "tests/test_x.py":
            'p = base / "strategy_research" / "api"  # path-literal: ok\n',
    })
    assert literals_mod.scan({"api"}) == []
    assert literals_mod.scan(set()) == []


def test_literals_real_repo_clean():
    mod = _load("check_path_literals")
    assert mod.scan(set()) == []


# ── graph_layer_audit ────────────────────────────────────────────────


@pytest.fixture
def graph_mod():
    return _load("graph_layer_audit")


def _edge(rel, src_id, tgt_id, src_file, **kw):
    e = {"relation": rel, "source": src_id, "target": tgt_id,
         "source_file": src_file}
    e.update(kw)
    return e


def test_graph_audit_matrix_and_residual(graph_mod):
    """合成图：矩阵按符号定义层统计；core/ 边进残留；import 无关边不计数。"""
    g = {
        "nodes": [
            {"id": "s1", "source_file": "src/strategy_research/kernel/agent/loop.py"},
            {"id": "t1", "source_file": "src/strategy_research/apps/study/runner.py"},
            {"id": "t2", "source_file": "src/strategy_research/core/legacy.py"},
            {"id": "t3", "source_file": "src/strategy_research/foundation/llm/config.py"},
            {"id": "noise", "source_file": "src/strategy_research/kernel/x.py"},
        ],
        "edges": [
            # kernel -> apps：一条 import 符号级边
            _edge("imports", "s1", "t1", "src/strategy_research/kernel/agent/loop.py"),
            # foundation -> apps：另一条
            _edge("imports_from", "t3", "t1", "src/strategy_research/foundation/llm/config.py"),
            # core 残留：无论方向
            _edge("imports", "s1", "t2", "src/strategy_research/kernel/agent/loop.py"),
            # 非 import 关系：不进矩阵
            _edge("calls", "s1", "t1", "src/strategy_research/kernel/agent/loop.py"),
            # 同层：不计
            _edge("imports", "t3", "t3", "src/strategy_research/foundation/llm/config.py"),
        ],
    }
    matrix, residual, indeg, nodes = graph_mod.audit(g)
    assert matrix[("L3", "L2")] == 1
    assert matrix[("L4", "L2")] == 1
    assert matrix[("L3", "L4")] == 0  # calls 边不进 import 矩阵
    assert sum(residual.values()) == 1
    # residual 按 source 端文件记账（谁 import 了 core/ 里的东西，谁被点名）
    assert "src/strategy_research/kernel/agent/loop.py" in residual
    # indeg 含 calls：imports + imports_from + calls 都指向 t1
    assert indeg["t1"] == 3


def test_graph_audit_layer_of_boundaries(graph_mod):
    f = graph_mod.layer_of
    assert f("src/strategy_research/interfaces/api/app.py") == "L1"
    assert f("src\\strategy_research\\apps\\db.py") == "L2"   # Windows 分隔符
    assert f("src/strategy_research/kernel/gateway/x.py") == "L3"
    assert f("src/strategy_research/foundation/llm/config.py") == "L4"
    assert f("src/strategy_research/core/anything.py") == "CORE!"
    assert f("docs/x.md") is None
