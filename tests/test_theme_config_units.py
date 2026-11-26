"""主题配置单元测试（§9 批 6/7）——纯函数层，不经 HTTP。

覆盖：
- `_is_color_value`：颜色值判定（hex/rgb/hsl/color-mix/gradient/RGB 通道/变量别名）
- `_iter_theme_declarations`：CSS 变量按主题块归属（多行值、注释、复合选择器）
- `_apply_role_families`：角色→色相映射展开（默认不变、跨角色、未知族、目标族自身）

这些是 `/api/system/theme` 的内部契约，端到端测试（test_system_theme_api.py）
只覆盖结果，这里锁定中间层的语义。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.interfaces.api.routers.system import (  # noqa: E402
    _FAMILY_ROLE,
    _PALETTE_FAMILIES,
    _ROLE_FAMILIES,
    _apply_role_families,
    _is_color_value,
    _iter_theme_declarations,
)


# ── _is_color_value ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value",
    [
        "#fff",
        "#ffffff",
        "#ffffffff",
        "rgb(1, 2, 3)",
        "rgba(1, 2, 3, 0.5)",
        "hsl(120, 50%, 50%)",
        "hsla(120, 50%, 50%, 0.4)",
        "color-mix(in srgb, var(--primary) 65%, transparent)",
        "linear-gradient(160deg, #0a0a12 0%, #0f172a 100%)",
        "radial-gradient(circle, #fff, #000)",
        "transparent",
        "white",
        "black",
        "currentColor",
        "var(--foreground)",
        "14 165 233",
    ],
)
def test_is_color_value_accepts_color_values(value):
    assert _is_color_value(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "0.5rem",
        "4px",
        "9999px",
        "cubic-bezier(0.34, 1.56, 0.64, 1)",
        "1.5",
        "0 2px 12px -4px var(--shadow)",
        "",
        "   ",
    ],
)
def test_is_color_value_rejects_non_color_values(value):
    assert _is_color_value(value) is False


def test_is_color_value_accepts_rgb_channels_only_within_range():
    assert _is_color_value("14 165 233") is True
    assert _is_color_value("14 165 300") is False  # >255 不合法


# ── _iter_theme_declarations ───────────────────────────────────────

def test_iter_theme_declarations_assigns_by_block_selector():
    css = """
:root,
[data-theme='dark'] {
  --a: #111111;
  --b: rgba(1,2,3,0.5);
}
[data-theme='light'] {
  --a: #222222;
}
"""
    out = list(_iter_theme_declarations(css))
    assert out == [
        ("dark", "--a", "#111111"),
        ("dark", "--b", "rgba(1,2,3,0.5)"),
        ("light", "--a", "#222222"),
    ]


def test_iter_theme_declarations_handles_multiline_values():
    """多值 shadow 跨行声明必须整体归属（逐行匹配会漏）。"""
    css = """
:root {
  --shadow-soft:
    0 2px 6px rgba(0,0,0,0.35),
    0 0 0 1px rgba(51,65,85,0.35);
}
"""
    out = list(_iter_theme_declarations(css))
    assert len(out) == 1
    mode, name, value = out[0]
    assert mode == "dark"
    assert name == "--shadow-soft"
    assert "0 2px 6px" in value and "1px rgba(51,65,85,0.35)" in value


def test_iter_theme_declarations_ignores_comments():
    css = """
:root {
  /* --fake: #ff0000; */
  --real: #00ff00;
}
"""
    names = [name for _, name, _ in _iter_theme_declarations(css)]
    assert names == ["--real"]


def test_iter_theme_declarations_resets_mode_after_block():
    """普通选择器块（无主题标记）不应把变量算进任何主题。"""
    css = """
:root { --a: #111111; }
.some-class { --b: #222222; }
[data-theme='light'] { --c: #333333; }
"""
    modes = {name: mode for mode, name, _ in _iter_theme_declarations(css)}
    assert modes["--a"] == "dark"
    assert modes["--c"] == "light"
    assert "--b" not in modes


# ── _apply_role_families ───────────────────────────────────────────

def _base_values() -> dict[str, str]:
    return {
        "--emerald-400-rgb": "52 211 153",
        "--green-400-rgb": "74 222 128",
        "--rose-400-rgb": "251 113 133",
        "--red-400-rgb": "248 113 113",
        "--success-emerald-400-rgb": "var(--emerald-400-rgb)",
        "--success-green-400-rgb": "var(--green-400-rgb)",
        "--danger-rose-400-rgb": "var(--rose-400-rgb)",
        "--danger-red-400-rgb": "var(--red-400-rgb)",
    }


def test_apply_role_families_noop_when_roles_empty():
    values = _base_values()
    _apply_role_families(values, {})
    assert values["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"


def test_apply_role_families_redirects_only_named_family():
    values = _base_values()
    _apply_role_families(values, {"emerald": "green"})
    assert values["--success-emerald-400-rgb"] == "var(--green-400-rgb)"
    # 同角色的其它族不受影响
    assert values["--success-green-400-rgb"] == "var(--green-400-rgb)"
    # 其它角色不受影响
    assert values["--danger-red-400-rgb"] == "var(--red-400-rgb)"
    assert values["--danger-rose-400-rgb"] == "var(--rose-400-rgb)"


def test_apply_role_families_can_unify_multiple_families_to_one():
    values = _base_values()
    _apply_role_families(values, {"rose": "red", "red": "red"})
    assert values["--danger-rose-400-rgb"] == "var(--red-400-rgb)"
    assert values["--danger-red-400-rgb"] == "var(--red-400-rgb)"


def test_apply_role_families_never_touches_material_variables():
    """材质是代码级常量（Tailwind 默认色板），只改槽不改材质。"""
    values = _base_values()
    _apply_role_families(values, {"emerald": "green"})
    assert values["--emerald-400-rgb"] == "52 211 153"
    assert values["--green-400-rgb"] == "74 222 128"


def test_apply_role_families_ignores_unknown_target_family():
    values = _base_values()
    _apply_role_families(values, {"emerald": "chartreuse"})
    assert values["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"


def test_apply_role_families_ignores_unknown_source_family():
    values = _base_values()
    _apply_role_families(values, {"chartreuse": "green"})
    assert values["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"


def test_apply_role_families_ignores_non_string_target():
    values = _base_values()
    _apply_role_families(values, {"emerald": 123})
    assert values["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"


def test_apply_role_families_supports_cross_role_target():
    """跨角色取族合法：danger 可改用 success 的 emerald 色相。"""
    values = _base_values()
    _apply_role_families(values, {"rose": "emerald"})
    assert values["--danger-rose-400-rgb"] == "var(--emerald-400-rgb)"
    # success 侧不受影响
    assert values["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"


# ── 族/角色映射不变量 ─────────────────────────────────────────────

def test_every_role_family_has_a_slot_name():
    """每个角色的每个族都能解析出 --<role>-<family>- 槽前缀。"""
    for role, families in _ROLE_FAMILIES.items():
        for family in families:
            assert _FAMILY_ROLE[family] == role


def test_role_families_are_subset_of_palette_families():
    for families in _ROLE_FAMILIES.values():
        for family in families:
            assert family in _PALETTE_FAMILIES


def test_role_families_have_no_overlap_between_roles():
    """一个族只能属于一个角色，否则槽名会冲突。"""
    seen: set[str] = set()
    for families in _ROLE_FAMILIES.values():
        for family in families:
            assert family not in seen, f"{family} 属于多个角色"
            seen.add(family)


# ── 配置文件格式契约（用户写 theme.json 时必须遵守） ────────────────

def test_example_config_shape_is_valid():
    """仓库内的示例 theme.json 必须符合解析器的契约。

    用户是照着示例改的；示例一旦与解析器脱节，用户照抄就会静默失效。
    """
    example = Path(__file__).resolve().parents[1] / "webui" / "frontend" / "public" / "theme.example.json"
    if not example.exists():
        pytest.skip("示例配置文件不存在")
    raw = json.loads(example.read_text(encoding="utf-8"))
    theme = raw["theme"]
    assert set(theme) >= {"dark", "light"}
    for mode in ("dark", "light"):
        body = theme[mode]
        assert set(body) >= {"roles", "overrides"}
        roles = body["roles"]
        assert isinstance(roles, dict)
        for src, dst in roles.items():
            assert src in _PALETTE_FAMILIES, f"{mode}.roles 未知源色族 {src}"
            assert dst in _PALETTE_FAMILIES, f"{mode}.roles 未知目标色族 {dst}"
        overrides = body["overrides"]
        assert isinstance(overrides, dict)
        for key, val in overrides.items():
            assert key.startswith("--"), f"{mode}.overrides 键必须以 -- 开头: {key}"
            assert isinstance(val, str), f"{mode}.overrides 值必须是字符串: {key}"
