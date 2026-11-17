# -*- coding: utf-8 -*-
"""``check_logging_assembly`` 门禁的单元测试（合成仓库 + 真仓端到端三件套）。

模板照 ``test_scripts_arch.py``（check_kernel_nouns 三件套形状）。钉住两条不变量
（docs/design/logging-unified-hub-design-20261117.md §12）：

- **① 装配泄漏 = 0**：basicConfig / dictConfig / addHandler / removeHandler /
  setLevel / Formatter / StreamHandler / FileHandler / RotatingFileHandler
  只许出现在 foundation/observability/；
- **② root 误用 = 0**：无参 getLogger()（拿 root logger）只许在 observability 包内。

R-009/R-019：真仓清查必须有「注入违规必红」的反向检验，否则扫描器静默失效时
真仓清查是恒真空断言。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS / "arch" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


# ── 合成树：各违规类型逐一命中 ──────────────────────────────────────────

def test_detects_each_assembly_call(tmp_path):
    """九类装配调用逐一检出（① 装配泄漏）。"""
    mod = _load("check_logging_assembly")
    src = tmp_path / "pkg"
    src.mkdir()
    cases = {
        "a_basic.py": "import logging\nlogging.basicConfig(level=1)\n",
        "b_dict.py": "import logging.config\nlogging.config.dictConfig({})\n",
        "c_add.py": "import logging\nlogging.getLogger('x').addHandler(None)\n",
        "d_rm.py": "import logging\nlogging.getLogger('x').removeHandler(None)\n",
        "e_lvl.py": "import logging\nlogging.getLogger('x').setLevel(1)\n",
        "f_fmt.py": "import logging\nlogging.Formatter()\n",
        "g_stream.py": "import logging\nlogging.StreamHandler()\n",
        "h_file.py": "import logging\nlogging.FileHandler('a.log')\n",
        "i_rot.py": "from logging.handlers import RotatingFileHandler\nRotatingFileHandler('a.log')\n",
    }
    for name, body in cases.items():
        (src / name).write_text(body, encoding="utf-8")
    hits = mod.find_violations(tmp_path)
    kinds = {kind for _, _, kind in hits}
    assert len(hits) == 9, hits
    assert all(kind.startswith("assembly-leak") for kind in kinds)
    # 每个文件恰命中一行
    assert {rel for rel, _, _ in hits} == {f"pkg/{n}" for n in cases}


def test_detects_noarg_getlogger_root_misuse(tmp_path):
    """② 无参 getLogger() = 拿 root，越权读写全局配置。"""
    mod = _load("check_logging_assembly")
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "m.py").write_text(
        "import logging\nROOT = logging.getLogger()\n", encoding="utf-8")
    hits = mod.find_violations(tmp_path)
    assert len(hits) == 1
    rel, ln, kind = hits[0]
    assert (rel, ln) == ("pkg/m.py", 2)
    assert kind.startswith("root-misuse")


def test_clean_idiomatic_module_not_flagged(tmp_path):
    """官方成语模块（import + getLogger(__name__) + 打日志）零命中。"""
    mod = _load("check_logging_assembly")
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "clean.py").write_text(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "logger.info('fine %s', 1)\n",
        encoding="utf-8",
    )
    assert mod.find_violations(tmp_path) == []


def test_observability_prefix_exempt(tmp_path):
    """豁免前缀命中必须生效：foundation/observability/ 下的装配调用不报。"""
    mod = _load("check_logging_assembly")
    obs = tmp_path / "foundation" / "observability"
    obs.mkdir(parents=True)
    (obs / "cfg.py").write_text(
        "import logging\nlogging.basicConfig(level=1)\nROOT = logging.getLogger()\n",
        encoding="utf-8",
    )
    assert mod.find_violations(tmp_path) == []


def test_non_call_setLevel_reference_not_flagged(tmp_path):
    """只盯**调用**：同名属性引用/字符串/普通名字不算装配（防误报）。"""
    mod = _load("check_logging_assembly")
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "n.py").write_text(
        "setLevel = 1\n"
        "formatter_text = 'Formatter'\n"
        "class StreamHandler:\n"
        "    pass\n"
        "x = StreamHandler\n",
        encoding="utf-8",
    )
    assert mod.find_violations(tmp_path) == []


# ── 真仓端到端 + main 契约 ──────────────────────────────────────────────

def test_real_repo_clean():
    """真实仓库当前状态即基准：装配泄漏 0 / root 误用 0。

    失败意味着有人在 observability 之外做了装配 —— 正确处置是把配置交给
    configure_logging（foundation/observability/logging_setup.py），
    而不是往 EXEMPT_PREFIXES 里加豁免。
    """
    mod = _load("check_logging_assembly")
    hits = mod.find_violations(mod.SRC)
    detail = "\n".join(f"{r}:{l} {k}" for r, l, k in hits[:20])
    assert hits == [], f"src/ 出现装配纪律违规（门禁红）：\n{detail}"


def test_main_exit_code_contract(monkeypatch, capsys, tmp_path):
    """main 契约：0 违规 → rc 0；有违规 → rc 1 并报出文件:行号（README 口径）。"""
    mod = _load("check_logging_assembly")

    # 真仓干净 → 0
    assert mod.main([]) == 0
    out = capsys.readouterr().out
    assert "[OK]" in out

    # 注入违规的合成仓 → 1 + 位置报告（SRC 换成 tmp_path，不碰真仓/仓库外）
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "bad.py").write_text(
        "import logging\nlogging.basicConfig(level=1)\n", encoding="utf-8")
    monkeypatch.setattr(mod, "SRC", tmp_path)
    rc = mod.main([])
    captured = capsys.readouterr().out
    assert rc == 1
    assert "pkg/bad.py:2" in captured
    assert "assembly-leak" in captured


def test_selftest_passes():
    """R-019：门禁自带注入自检必须过（两类违规被抓、合规文件零误报）。"""
    mod = _load("check_logging_assembly")
    assert mod.selftest() == 0
