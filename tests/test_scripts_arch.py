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


def test_dangling_detects_plain_import_alias(dangling_mod, tmp_path):
    """``import a.b.c as x`` 形式的悬空也必须报出。

    背景（2026-11-05）：批次 2 删掉 ``interfaces/api/routers/workflow.py`` 后，
    两个测试文件用 ``import strategy_research.interfaces.api.routers.workflow
    as wf`` 继续引用它。旧的 scan() 只遍历 ``ast.ImportFrom``，**完全不看**
    ``ast.Import``，于是门禁是绿的，而这 24 个用例在 fixture setup 期才炸
    （收集期不报错，所以连 collection 都看不出问题）。
    """
    _mk_repo(tmp_path, {
        "src/strategy_research/kernel/loop.py": (
            "import strategy_research.apps.gone as g\n"
        ),
        "src/strategy_research/apps/kept.py": "x = 1\n",
        "tests/test_x.py": (
            "import strategy_research.kernel.loop\n"
            "import strategy_research.apps.kept as k\n"
        ),
    })
    bad = dangling_mod.scan(["src", "tests"])
    assert len(bad) == 1, bad
    assert "apps.gone" in bad[0]
    assert "test_x.py" not in bad[0]  # 存在的模块不报


def test_dangling_detects_from_package_import_missing_name(dangling_mod, tmp_path):
    """``from <包> import <名字>``：包在、名字不在 → 也要报。

    背景（2026-11-05）：批次 2 删掉 ``interfaces/api/routers/workflow.py`` 后，
    ``tests/test_workflow_control_api.py`` 仍写
    ``from strategy_research.interfaces.api.routers import workflow``。
    宿主包 ``routers/`` 还在，旧门禁只解析 ``node.module``（= routers），
    看不出被 import 的**名字**已经消失 → 又一批 fixture 期才炸的用例。
    """
    _mk_repo(tmp_path, {
        "src/strategy_research/kernel/__init__.py": "",
        "src/strategy_research/kernel/kept.py": "x = 1\n",
        # 没有 kernel/gone.py —— 名字既不是子模块、也不在 __init__ 里
        "tests/test_x.py": (
            "from strategy_research.kernel import kept\n"
            "from strategy_research.kernel import gone\n"
        ),
    })
    bad = dangling_mod.scan(["src", "tests"])
    assert len(bad) == 1, bad
    assert "kernel.gone" in bad[0]


def test_dangling_package_name_from_init_is_not_reported(dangling_mod, tmp_path):
    """包 ``__init__.py`` 里绑定的名字（def/class/import）不算悬空。"""
    _mk_repo(tmp_path, {
        "src/strategy_research/kernel/__init__.py": (
            "from .reg import Registry\n"
            "VERSION = '1'\n"
            "class Base: pass\n"
            "def helper(): pass\n"
        ),
        "src/strategy_research/kernel/reg.py": "class Registry: pass\n",
        "tests/test_x.py": (
            "from strategy_research.kernel import Registry, VERSION, Base, helper\n"
        ),
    })
    assert dangling_mod.scan(["src", "tests"]) == []


def test_dangling_exempt_marker_allows_expected_failure(dangling_mod, tmp_path):
    """``# dangling-import: ok`` 豁免：断言 import 会失败的测试不该被报。

    场景：``with pytest.raises(ImportError): from pkg import gone`` ——
    那个名字本就不该存在，门禁报它就是误报。
    """
    _mk_repo(tmp_path, {
        "src/strategy_research/kernel/__init__.py": "",
        "tests/test_x.py": (
            "from strategy_research.kernel import gone  # dangling-import: ok\n"
        ),
    })
    assert dangling_mod.scan(["src", "tests"]) == []


# ── check_dangling_imports --layers ──────────────────────────────────


def test_layer_matrix_module_is_importable():
    """共享层定义模块可独立加载（两个门禁的唯一事实源）。"""
    spec = importlib.util.spec_from_file_location(
        "layer_matrix", SCRIPTS / "arch" / "layer_matrix.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.ORDER == ["L1", "L2", "L3", "L4"]
    assert mod.layer_of("strategy_research.kernel.agent") == "L3"
    assert mod.layer_of("strategy_research.apps.study") == "L2"


def test_layers_output_matches_gate(tmp_path, monkeypatch, capsys):
    """``--layers`` 报的 L3→L2 必须与 test_layer_matrix 的 _collect 一致。

    这是本开关存在的全部意义：同一张表、同一套算法 → 两个门禁不可能
    给出不同的数字。若这条测试失败，说明有人复制了第二份层归属表。
    """
    # 不能假设 sys.path 已含 scripts/arch——单独跑本文件时并没有。
    sys.path.insert(0, str(SCRIPTS / "arch"))
    import layer_matrix as lm
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    import test_layer_matrix as tlm

    gate_viol, _ = tlm._collect()
    script_viol, _ = lm.collect(tlm.SRC)

    for cluster in ("L3>L2", "L4>L2", "L4>L3", "L3>L1", "L2>L1"):
        assert script_viol.get(cluster, 0) == gate_viol.get(cluster, 0), (
            f"{cluster} 两个门禁不一致：script={script_viol.get(cluster, 0)} "
            f"gate={gate_viol.get(cluster, 0)}"
        )


def test_layers_mode_reports_l3l2(tmp_path, monkeypatch, capsys):
    """合成仓库：L3 模块 import L2 → --layers 应报 L3→L2 = 1。"""
    _mk_repo(tmp_path, {
        "src/strategy_research/__init__.py": "",
        "src/strategy_research/kernel/__init__.py": "",
        "src/strategy_research/kernel/agent/__init__.py": "",
        "src/strategy_research/kernel/agent/tool.py": (
            "from strategy_research.apps.db import get_conn\n"
        ),
        "src/strategy_research/apps/__init__.py": "",
        "src/strategy_research/apps/db.py": "def get_conn(): pass\n",
    })
    mod = _load("check_dangling_imports")
    mod.PKG_DIR = str(tmp_path / "src" / "strategy_research")

    # 直接驱动共享实现，避免依赖 ROOT 的全局状态
    sys.path.insert(0, str(SCRIPTS / "arch"))
    import layer_matrix as lm
    viol, _ = lm.collect(Path(mod.PKG_DIR))
    assert viol.get("L3>L2", 0) == 1, f"实测 {dict(viol)}"


def test_layers_mode_exit_code_on_real_repo():
    """真实仓库 --layers 出口：L3→L2=0 但有 3 条别的上行（seam 触点）。"""
    mod = _load("check_dangling_imports")
    rc = mod.report_layers()
    # 上行总数 > 0（L2>L1=1 + L3>L1=2），故 exit 1；但 L3→L2 必须是 0。
    import layer_matrix as lm
    viol, _ = lm.collect(Path(mod.PKG_DIR))
    assert viol.get("L3>L2", 0) == 0
    assert rc == (1 if sum(viol.values()) else 0)


# ── sync_architecture_diagram ────────────────────────────────────────
#
# 用户要求「每次大的修改后自动更新架构图」。手工记得更新不可靠——本仓库
# 出现过「图上写 46 条违规、实际 0」这种过时 6 个版本的情况。这条测试把
# 「图与代码一致」变成 CI 断言：任何增删包、搬模块的改动若没同步图，
# 这里就会红。


def test_architecture_diagram_matches_code():
    """架构图上的层体量与跨层边数必须与 AST 实测一致。"""
    mod = _load("sync_architecture_diagram")
    ok, report = mod.check()
    assert ok, (
        "架构图已过时，请更新 docs/architecture-diagram.html：\n"
        + "\n".join(report)
    )


def test_architecture_diagram_structurally_valid():
    """图本身的结构问题（标签错配 / 未转义 & / rect 越界）单独断言。

    与上一测试分开：那条测「数据是否过时」（更新图就能修），这条测
    「图是否坏掉」（手误改坏 HTML 才会红），失败含义不同。
    """
    mod = _load("sync_architecture_diagram")
    _ok, report = mod.check()
    text = "\n".join(report)
    assert "[X] HTML 结构" not in text, text
    assert "[X] 未转义 &" not in text, text
    assert "[X] rect 越界" not in text, text


def test_architecture_diagram_write_is_idempotent():
    """``--write`` 回写后图必须与代码一致，且**再写一次不改任何字节**。

    幂等是 --write 可信的前提：每次大改后无脑跑一次即可，不该越写越乱。
    """
    mod = _load("sync_architecture_diagram")
    before = mod.DIAGRAM.read_text(encoding="utf-8")
    try:
        ok, report = mod.write()
        assert ok, "\n".join(report)
        after = mod.DIAGRAM.read_text(encoding="utf-8")
        # 幂等：第二次写入不应产生任何变化
        ok2, report2 = mod.write()
        assert ok2, "\n".join(report2)
        assert mod.DIAGRAM.read_text(encoding="utf-8") == after
        # 回写后必须与代码一致
        ok3, report3 = mod.check()
        assert ok3, "\n".join(report3)
    finally:
        mod.DIAGRAM.write_text(before, encoding="utf-8")


def test_architecture_diagram_write_fixes_stale_numbers(tmp_path, monkeypatch):
    """合成一张**过时**的图：--write 应把数字改成 AST 实测值。

    这条把「用户要求每次大改后自动更新架构图」变成可执行断言 ——
    以前这一步靠人记得手改数字（错过一次就红一次）。
    """
    mod = _load("sync_architecture_diagram")
    real = mod.DIAGRAM.read_text(encoding="utf-8")
    stale = real.replace("466 文件", "999 文件").replace(
        "L1→L2 91", "L1→L2 7").replace("104,185", "1,234")
    tmp = tmp_path / "architecture-diagram.html"
    tmp.write_text(stale, encoding="utf-8")
    monkeypatch.setattr(mod, "DIAGRAM", tmp)

    ok, report = mod.write()
    assert ok, "\n".join(report)
    fixed = tmp.read_text(encoding="utf-8")
    assert "999 文件" not in fixed
    assert "L1→L2 7 " not in fixed
    ok2, report2 = mod.check(text=fixed)
    assert ok2, "\n".join(report2)


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
