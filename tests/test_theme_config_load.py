"""主题配置加载 · 单元测试（§9 批 9）

直接测 `_load_theme()`（不经 HTTP），覆盖文件级边界：
- 文件不存在 / 不可读 → 回退内置默认
- 非 UTF-8 编码 → 回退内置默认
- JSON 非法 → 回退内置默认
- 根不是对象 / 缺 theme 键 → 回退内置默认
- 主题值不是字典 → 跳过该主题
- roles / overrides 类型不符 → 忽略对应层
- 扁平写法与两层写法混合 → 都生效
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentnodes.interfaces.api.routers.system import (  # noqa: E402
    _BUILTIN_THEME,
    _THEME_JSON_PATH,
    _load_theme,
)


@pytest.fixture
def theme_file(tmp_path, monkeypatch):
    """把 _THEME_JSON_PATH 指向临时文件（默认不存在）。"""
    path = tmp_path / "theme.json"
    monkeypatch.setattr(
        "agentnodes.interfaces.api.routers.system._THEME_JSON_PATH", path
    )
    return path


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_missing_file_falls_back_to_builtin(theme_file):
    assert not theme_file.exists()
    result = _load_theme()
    assert result["dark"]["--emerald-400-rgb"] == "52 211 153"
    assert result["light"]["--emerald-400-rgb"] == "52 211 153"


def test_unreadable_file_falls_back_to_builtin(theme_file, monkeypatch):
    """文件存在但读取抛 OSError（如权限/路径是目录）→ 回退内置。"""
    theme_file.mkdir()  # 目录 → read_text 抛 IsADirectoryError(OSError)
    result = _load_theme()
    assert result["dark"]["--emerald-400-rgb"] == "52 211 153"


def test_invalid_json_falls_back_to_builtin(theme_file):
    write(theme_file, "{not json")
    result = _load_theme()
    assert result["dark"]["--emerald-400-rgb"] == "52 211 153"


def test_non_utf8_file_falls_back_to_builtin(theme_file):
    theme_file.write_bytes(b"\xff\xfe\x00invalid")
    result = _load_theme()
    assert result["dark"]["--emerald-400-rgb"] == "52 211 153"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="根是数组"),
        pytest.param("字符串", id="根是字符串"),
        pytest.param(123, id="根是数字"),
        pytest.param({}, id="缺 theme 键"),
        pytest.param({"theme": []}, id="theme 是数组"),
        pytest.param({"theme": "x"}, id="theme 是字符串"),
    ],
)
def test_root_shape_failures_fall_back(theme_file, payload):
    write(theme_file, json.dumps(payload))
    result = _load_theme()
    assert result["dark"]["--emerald-400-rgb"] == "52 211 153"


@pytest.mark.parametrize(
    "dark_value",
    [
        pytest.param("not-a-dict", id="字符串"),
        pytest.param([], id="数组"),
        pytest.param(None, id="null"),
        pytest.param(7, id="数字"),
    ],
)
def test_non_dict_theme_is_skipped(theme_file, dark_value):
    """主题值不是字典 → 跳过该主题（保留内置），另一主题仍可配置。"""
    write(theme_file, json.dumps({
        "theme": {"dark": dark_value, "light": {"--emerald-400-rgb": "1 2 3"}}
    }))
    result = _load_theme()
    assert result["dark"]["--emerald-400-rgb"] == "52 211 153"
    assert result["light"]["--emerald-400-rgb"] == "1 2 3"


def test_roles_of_wrong_type_is_ignored(theme_file):
    write(theme_file, json.dumps({
        "theme": {"dark": {"roles": "not-a-dict", "--emerald-400-rgb": "1 2 3"}}
    }))
    result = _load_theme()
    # roles 层被忽略，但扁平层仍生效
    assert result["dark"]["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"
    assert result["dark"]["--emerald-400-rgb"] == "1 2 3"


def test_overrides_of_wrong_type_is_ignored(theme_file):
    write(theme_file, json.dumps({
        "theme": {"dark": {"overrides": ["not", "a", "dict"]}}
    }))
    result = _load_theme()
    assert result["dark"]["--emerald-400-rgb"] == "52 211 153"


def test_non_string_keys_and_values_are_ignored(theme_file):
    write(theme_file, json.dumps({
        "theme": {"dark": {"--ok": "1 2 3", "--bad": 42, "nope": "x"}}
    }))
    result = _load_theme()
    assert result["dark"]["--ok"] == "1 2 3"
    assert "--bad" not in result["dark"]
    assert "nope" not in result["dark"]


def test_flat_and_two_layer_formats_can_mix(theme_file):
    """同一主题里既有 roles/overrides 又有扁平键 → 三者都生效。"""
    write(theme_file, json.dumps({
        "theme": {
            "dark": {
                "roles": {"emerald": "green"},
                "overrides": {"--success-emerald-400-rgb": "9 9 9"},
                "--surface-solid": "#123456",
            }
        }
    }))
    result = _load_theme()
    assert result["dark"]["--success-emerald-400-rgb"] == "9 9 9"
    assert result["dark"]["--success-emerald-500-rgb"] == "var(--green-500-rgb)"
    assert result["dark"]["--surface-solid"] == "#123456"


def test_result_is_a_copy_not_the_builtin_object(theme_file):
    """返回值必须是副本 —— 否则调用方改动会污染模块级内置默认。"""
    result = _load_theme()
    result["dark"]["--emerald-400-rgb"] = "0 0 0"
    assert _BUILTIN_THEME["dark"]["--emerald-400-rgb"] == "52 211 153"


def test_builtin_theme_is_not_mutated_across_calls(theme_file):
    write(theme_file, json.dumps({
        "theme": {"dark": {"roles": {"emerald": "green"}}}
    }))
    _load_theme()
    assert _BUILTIN_THEME["dark"]["--emerald-400-rgb"] == "52 211 153"
    assert _BUILTIN_THEME["dark"]["--success-emerald-400-rgb"] == "var(--emerald-400-rgb)"


def test_default_theme_path_is_under_user_home():
    """默认配置路径应在用户目录（~/.agentnodes/theme.json），非仓库内。"""
    assert _THEME_JSON_PATH.name == "theme.json"
    assert ".agentnodes" in str(_THEME_JSON_PATH)
