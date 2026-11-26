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
    """§8 第 1 步契约：内置默认必须从 globals.css 提取全部颜色变量
    （排除 space/radius/font/alpha 等非颜色 token），防手写 13 项的覆盖缺口。"""
    import re

    resp = client.get("/api/system/theme")
    body = resp.json()

    css_path = Path(__file__).resolve().parents[1] / "webui/frontend/src/styles/globals.css"
    css = css_path.read_text(encoding="utf-8")

    def color_vars_of(block: str) -> set[str]:
        # dark = :root 块（到 light 前）；light = [data-theme='light'] 块
        if block == "dark":
            seg = css[: css.index("[data-theme='light']")]
        else:
            seg = css[css.index("[data-theme='light']") :]
        out = set()
        for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", seg):
            name, value = m.group(1), m.group(2).strip()
            # 颜色值判定：hex / rgb(a) / hsl / color-mix / var(颜色) / 具名颜色
            if re.match(r"#|rgba?\(|hsla?\(|color-mix\(|transparent|white|black", value):
                out.add(name)
        return out

    for mode in ("dark", "light"):
        missing = color_vars_of(mode) - set(body[mode])
        assert not missing, f"{mode} 内置默认缺少 CSS 颜色变量: {sorted(missing)}"
