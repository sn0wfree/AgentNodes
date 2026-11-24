# -*- coding: utf-8 -*-
"""P1-1 C 批 hygiene 门单测（R-019：注入违规必红）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "arch"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_selftest_passes():
    """R-019：三类注入违规全抓、合法用法零误报。"""
    mod = _load("check_token_hygiene")
    assert mod.selftest() == 0


def test_injected_bare_hex_is_detected(tmp_path):
    src = tmp_path / "src"
    (src / "styles").mkdir(parents=True)
    (src / "styles" / "globals.css").write_text(":root { --viz-1: #3b82f6; }", encoding="utf-8")
    (src / "Bad.tsx").write_text("<div style={{ color: '#deadbeef' }} />", encoding="utf-8")
    mod = _load("check_token_hygiene")
    hits = mod.find_violations(src)
    assert any(f == "Bad.tsx" and k == "bare-hex" for f, _, k in hits)


def test_font_sizes_are_not_flagged_this_batch(tmp_path):
    """用户裁决「保守」：9/10/11px 字号留后续批，本门不检测字号。"""
    src = tmp_path / "src"
    (src / "styles").mkdir(parents=True)
    (src / "styles" / "globals.css").write_text(":root {}", encoding="utf-8")
    (src / "Clean.tsx").write_text("<div className='text-[9px] text-[10px] text-[11px]' />", encoding="utf-8")
    mod = _load("check_token_hygiene")
    hits = mod.find_violations(src)
    assert not any(k == "off-scale-font" for _, _, k in hits)


def test_gradient_three_conditions(tmp_path):
    src = tmp_path / "src"
    (src / "styles").mkdir(parents=True)
    (src / "styles" / "globals.css").write_text(":root {}", encoding="utf-8")
    (src / "Bad.tsx").write_text(
        "a: linear-gradient(90deg, #ff0000, #0000ff);\n"
        "b: linear-gradient(90deg, #fff, #fff);\n"
        "c: -webkit-mask-image: linear-gradient(90deg, #fff, transparent);\n",
        encoding="utf-8",
    )
    mod = _load("check_token_hygiene")
    hits = mod.find_violations(src)
    bad_lines = [ln for f, ln, k in hits if k == "bad-gradient"]
    assert bad_lines == [1]  # 仅多色装饰渐变命中；单色垫片/mask 不报


def test_real_repo_hygiene_baseline():
    """真仓：违规数不超过 C 批开工基线（门锁定现状，新增即红）。"""
    mod = _load("check_token_hygiene")
    hits = mod.find_violations(mod.SRC)
    # C 批收尾：viz 收编 + bad-gradient 修正 + 图表语义色白名单登记 → 真仓归零
    assert len(hits) == 0, f"hygiene 门应归零，实得 {len(hits)}: {hits[:5]}"


def test_semantic_tokens_declared_in_both_themes():
    """语义色统一 C1 契约：Tailwind 映射的每个 var(--x) 必须在 globals.css
    的 dark(:root) 与 light([data-theme='light']) 两段都声明——保证「单点可调、
    双主题自适应」，防止只加映射漏加某主题声明导致该主题色失效。"""
    import re

    tw = (ROOT / "webui" / "frontend" / "tailwind.config.js").read_text(encoding="utf-8")
    css = (ROOT / "webui" / "frontend" / "src" / "styles" / "globals.css").read_text(encoding="utf-8")
    # 只取 colors 段里 success/danger/thinking/info/warning 的 var() 映射
    semantic = set(re.findall(r"'([\w-]+)':\s*'var\((--[\w-]+)\)'", tw))
    semantic = {v for _k, v in semantic if any(
        p in v for p in ("success", "danger", "thinking", "info", "warning")
    )}
    assert semantic, "tailwind.config.js 未映射任何语义色变量"

    def block(start_marker: str, end_marker: str | None) -> str:
        i = css.index(start_marker)
        j = css.index(end_marker, i) if end_marker else len(css)
        return css[i:j]

    dark = block(":root,\n[data-theme='dark'] {", "/* ── Design tokens · light")
    light = block("[data-theme='light'] {", "/* ── L1 primitives")

    for var in sorted(semantic):
        assert re.search(rf"{re.escape(var)}\s*:", dark), f"{var} 缺 dark 声明"
        assert re.search(rf"{re.escape(var)}\s*:", light), f"{var} 缺 light 声明"

