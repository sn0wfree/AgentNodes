"""Tests for GET /api/system/theme — 主题外置配置文件（配色统一阶段 3）。

契约：
- theme.json 为 {"theme": {"dark": {...}, "light": {...}}}
- 文件不存在 → 返回内置默认（不报错）
- 文件存在 → 返回配置值（允许部分覆盖，缺项由前端 CSS 默认值兜底）
- 非法 JSON → 返回内置默认（防御，不炸服务）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from agentnodes.interfaces.api.app import create_app

    return TestClient(create_app())


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    theme = tmp_path / "theme.json"
    monkeypatch.setattr(
        "agentnodes.interfaces.api.routers.system._THEME_JSON_PATH", theme
    )
    return theme


def test_theme_returns_builtin_default_when_file_missing(client, isolated):
    resp = client.get("/api/system/theme")
    assert resp.status_code == 200
    body = resp.json()
    assert "dark" in body and "light" in body
    assert body["dark"].get("--background")
    assert body["light"].get("--background")


def test_theme_returns_configured_values(client, isolated):
    isolated.write_text(json.dumps({
        "theme": {
            "dark": {"--background": "#111111", "--accent": "#ff0000"},
            "light": {"--background": "#ffffff"},
        }
    }), encoding="utf-8")
    resp = client.get("/api/system/theme")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dark"]["--background"] == "#111111"
    assert body["dark"]["--accent"] == "#ff0000"
    assert body["light"]["--background"] == "#ffffff"


def test_theme_invalid_json_falls_back_to_default(client, isolated):
    isolated.write_text("{not json", encoding="utf-8")
    resp = client.get("/api/system/theme")
    assert resp.status_code == 200
    assert resp.json()["dark"].get("--background")


def test_theme_partial_config_does_not_break(client, isolated):
    isolated.write_text(json.dumps({"theme": {"dark": {"--accent": "#00ff00"}}}), encoding="utf-8")
    resp = client.get("/api/system/theme")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dark"]["--accent"] == "#00ff00"
    assert body["light"].get("--background")


def test_builtin_theme_covers_all_css_color_variables(client, isolated):
    """§8 第 1 步 / §9 批 1 契约：内置默认必须从 globals.css 提取全部颜色变量。

    判定谓词复用后端 `_is_color_value` + `_iter_theme_declarations`（单一事实源），
    避免「测试正则与后端正则漂移」导致的假绿/假红。
    """
    from agentnodes.interfaces.api.routers.system import (
        _is_color_value,
        _iter_theme_declarations,
    )

    resp = client.get("/api/system/theme")
    body = resp.json()

    css_path = Path(__file__).resolve().parents[1] / "webui/frontend/src/styles/globals.css"
    css = css_path.read_text(encoding="utf-8")

    expected: dict[str, set[str]] = {"dark": set(), "light": set()}
    for mode, name, value in _iter_theme_declarations(css):
        if _is_color_value(value, name):
            expected[mode].add(name)

    for mode in ("dark", "light"):
        missing = expected[mode] - set(body[mode])
        assert not missing, f"{mode} 内置默认缺少 CSS 颜色变量: {sorted(missing)}"


def test_later_dark_blocks_are_not_misclassified_as_light(client, isolated):
    """§9 批 1：workflow/DAG 段的 dark 令牌必须取 dark 值（旧单次切分 bug）。

    这些键在 dark/light 都有声明，不能靠「是否出现」判断归属，必须按值判断：
    dark 块 L551 用 #0a0a12 系渐变，light 块 L561 用 #f6f6f0 系。
    """
    body = client.get("/api/system/theme").json()
    assert "0a0a12" in body["dark"]["--canvas-bg"].lower()
    assert "f6f6f0" in body["light"]["--canvas-bg"].lower()
    # dark 的 edge-stroke 是深灰 #475569，light 是浅灰 #cbd5e1
    assert body["dark"]["--edge-stroke"].lower() == "#475569"
    assert body["light"]["--edge-stroke"].lower() == "#cbd5e1"


def test_gradient_and_shadow_variables_are_exported(client, isolated):
    """§9 批 1：gradient / 多值 shadow 变量也必须可被 theme.json 覆盖。"""
    body = client.get("/api/system/theme").json()
    for mode in ("dark", "light"):
        for key in ("--canvas-bg", "--dag-card-shadow"):
            assert key in body[mode], f"{mode} 缺少 {key}"
            assert "gradient" in body[mode]["--canvas-bg"]
            assert "rgba(" in body[mode]["--dag-card-shadow"]


def test_role_mapping_switches_hue_without_component_changes(client, isolated):
    """§9 批 7：配置只存「色族→色相」参数，改一个词即可整体换色相。

    这是「参数存在配置文件里，而不是把全部色值存下来」的契约：
    theme.json 写 roles={"emerald": "green"}，emerald 族的语义槽应改指
    green 的材质；其它色族（含同属 success 的 green、danger 的 red）不受影响。
    """
    isolated.write_text(json.dumps({
        "theme": {
            "dark": {"roles": {"emerald": "green"}},
            "light": {"roles": {"emerald": "green"}},
        }
    }), encoding="utf-8")
    body = client.get("/api/system/theme").json()
    for mode in ("dark", "light"):
        # emerald 槽改指 green 材质
        assert body[mode]["--success-emerald-400-rgb"] == "var(--green-400-rgb)"
        assert body[mode]["--success-emerald-500-rgb"] == "var(--green-500-rgb)"
        # 材质本身不动（代码级常量）
        assert body[mode]["--emerald-400-rgb"] == "52 211 153"
        assert body[mode]["--green-400-rgb"] == "74 222 128"
        # 同角色的其它族不受影响（green 自身槽保持）
        assert body[mode]["--success-green-400-rgb"] == "var(--green-400-rgb)"
        # 其它角色不受影响
        assert body[mode]["--danger-red-400-rgb"] == "var(--red-400-rgb)"
        assert body[mode]["--info-sky-400-rgb"] == "var(--sky-400-rgb)"


def test_role_mapping_can_unify_multiple_families(client, isolated):
    """§9 批 7：两族各自指定同一目标 → 该语义系统一色相（red/rose 各自指定）。"""
    isolated.write_text(json.dumps({
        "theme": {"dark": {"roles": {"rose": "amber", "red": "amber"}}}
    }), encoding="utf-8")
    body = client.get("/api/system/theme").json()
    assert body["dark"]["--danger-rose-400-rgb"] == "var(--amber-400-rgb)"
    assert body["dark"]["--danger-red-400-rgb"] == "var(--amber-400-rgb)"
    # 未列出的族保持自身
    assert body["dark"]["--danger-pink-400-rgb"] == "var(--pink-400-rgb)"


def test_role_mapping_default_keeps_builtin_palette(client, isolated):
    """§9 批 7：未配置 roles 时，内置默认色板必须保持原值（视觉零回归）。"""
    body = client.get("/api/system/theme").json()
    assert body["dark"]["--emerald-400-rgb"] == "52 211 153"
    assert body["dark"]["--rose-400-rgb"] == "251 113 133"
    assert body["dark"]["--sky-400-rgb"] == "56 189 248"
    # 槽默认指向自身材质
    assert body["dark"]["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"
    assert body["dark"]["--danger-red-400-rgb"] == "var(--red-400-rgb)"


def test_overrides_layer_applies_after_roles(client, isolated):
    """§9 批 7：精确覆盖（overrides）优先级高于色族映射。"""
    isolated.write_text(json.dumps({
        "theme": {
            "dark": {
                "roles": {"emerald": "green"},
                "overrides": {"--success-emerald-400-rgb": "1 2 3"},
            }
        }
    }), encoding="utf-8")
    body = client.get("/api/system/theme").json()
    assert body["dark"]["--success-emerald-400-rgb"] == "1 2 3"
    # 未被覆盖的档位仍跟随色族映射
    assert body["dark"]["--success-emerald-500-rgb"] == "var(--green-500-rgb)"


def test_unknown_role_family_is_ignored(client, isolated):
    """§9 批 7：未知色族名不应破坏主题（防御非法配置）。"""
    isolated.write_text(json.dumps({
        "theme": {"dark": {"roles": {"emerald": "chartreuse"}}}
    }), encoding="utf-8")
    resp = client.get("/api/system/theme")
    assert resp.status_code == 200
    body = resp.json()
    # 未知目标 → 槽保持自身材质
    assert body["dark"]["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"
    assert body["dark"]["--emerald-400-rgb"] == "52 211 153"


def test_theme_can_override_gradient_and_shadow_keys(client, isolated):
    """theme.json 覆盖 gradient/shadow 键后 API 必须返回配置值。"""
    gradient = "linear-gradient(45deg, #010203, #040506)"
    shadow = "0 0 0 2px #123456"
    isolated.write_text(json.dumps({
        "theme": {
            "dark": {"--canvas-bg": gradient, "--dag-card-shadow": shadow},
            "light": {},
        }
    }), encoding="utf-8")
    body = client.get("/api/system/theme").json()
    assert body["dark"]["--canvas-bg"] == gradient
    assert body["dark"]["--dag-card-shadow"] == shadow


# ── 配置文件形态解析（用户可能写出各种形状，都不能炸服务） ──────────

@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="空对象"),
        pytest.param({"theme": {}}, id="theme 为空"),
        pytest.param({"theme": {"dark": {}}}, id="只有 dark"),
        pytest.param({"theme": {"dark": "not-a-dict"}}, id="dark 不是字典"),
        pytest.param({"theme": {"dark": {"roles": "not-a-dict"}}}, id="roles 不是字典"),
        pytest.param({"theme": {"dark": {"overrides": []}}}, id="overrides 是列表"),
        pytest.param({"theme": {"dark": {"roles": {"emerald": 42}}}}, id="roles 值是数字"),
        pytest.param({"theme": {"dark": {"overrides": {"--x": 42}}}}, id="overrides 值是数字"),
        pytest.param({"theme": {"dark": {"unknown-key": "x"}}}, id="未知顶层键"),
        pytest.param([1, 2, 3], id="根不是对象"),
    ],
)
def test_malformed_config_never_breaks_the_endpoint(client, isolated, payload):
    """任何畸形配置都应回退内置默认，而不是 500。"""
    isolated.write_text(json.dumps(payload), encoding="utf-8")
    resp = client.get("/api/system/theme")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dark"].get("--emerald-400-rgb") == "52 211 153"


def test_flat_legacy_format_still_works(client, isolated):
    """旧写法（直接 "--var": "value"）保持兼容。"""
    isolated.write_text(json.dumps({
        "theme": {"dark": {"--surface-solid": "#123456"}, "light": {}}
    }), encoding="utf-8")
    body = client.get("/api/system/theme").json()
    assert body["dark"]["--surface-solid"] == "#123456"


def test_roles_and_overrides_combine(client, isolated):
    """roles 与 overrides 同时存在：overrides 优先级更高。"""
    isolated.write_text(json.dumps({
        "theme": {
            "dark": {
                "roles": {"emerald": "green"},
                "overrides": {"--success-emerald-400-rgb": "9 9 9"},
            }
        }
    }), encoding="utf-8")
    body = client.get("/api/system/theme").json()
    assert body["dark"]["--success-emerald-400-rgb"] == "9 9 9"
    assert body["dark"]["--success-emerald-500-rgb"] == "var(--green-500-rgb)"


def test_roles_apply_per_theme_independently(client, isolated):
    """roles 按主题独立生效（dark 改 emerald，light 不改）。"""
    isolated.write_text(json.dumps({
        "theme": {
            "dark": {"roles": {"emerald": "green"}},
            "light": {"roles": {"emerald": "emerald"}},
        }
    }), encoding="utf-8")
    body = client.get("/api/system/theme").json()
    assert body["dark"]["--success-emerald-400-rgb"] == "var(--green-400-rgb)"
    assert body["light"]["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"
