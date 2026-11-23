"""批 D · LLM 配置双路径兼容 + config.yaml 新格式。

设计：docs/design/llm-config-dual-path-and-yaml-20261111.md。

核心不变量：

1. **`~/.quantnodes/llm.json` 必须能读到**（本批修的真 bug：2026-11-05
   统一数据目录时 CONFIG_PATH 改了、CONFIG_PATHS 没跟上，旧路径退出链）；
2. 链顺序 config.yaml → `.agentnodes/llm.json` → `.quantnodes/llm.json`；
3. `AGENTNODES_LLM_CONFIG` **单文件覆盖**，命中即不建链；
4. **坏文件不静默跳过**（返回 {}，不是 None）——否则用户的坏配置看起来
   像"没配置"；
5. YAML 与 JSON **同一套 `llm` 段 schema**（不是第二套格式）。
"""
from __future__ import annotations

import json

import pytest

from agentnodes.foundation.llm import config as C
from agentnodes.foundation.llm import quantnodes_bridge as QB


# ── 1. 路径链定义 ────────────────────────────────────────────────────


def test_config_paths_has_three_entries():
    """链必须是三元素：config.yaml → .agentnodes/llm.json → .quantnodes/llm.json。"""
    assert len(QB.CONFIG_PATHS) == 3
    names = [p.name for p in QB.CONFIG_PATHS]
    assert names == ["config.yaml", "llm.json", "llm.json"]


def test_quantnodes_path_is_in_the_chain():
    """**本批核心回归**：quantnodes 的 llm.json 必须在读取链上。

    2026-11-05 的 bd3f6009 把 CONFIG_PATH 从 quantnodes 目录改到
    agentnodes 目录，但 CONFIG_PATHS 仍是 `(CONFIG_PATH,)`——旧路径就此
    退出读取链，而十几处 docstring 仍宣称它是"兼容回退"。
    """
    from agentnodes.foundation.config.paths import PROFILES

    qn_home = PROFILES["quantnodes"].home_dir_name
    quantnodes_path = QB.CONFIG_PATHS[-1]
    assert quantnodes_path == C.Path.home() / qn_home / "llm.json"
    assert quantnodes_path in QB.CONFIG_PATHS


def test_chain_dirs_come_from_profiles():
    """链里的目录名来自 ``foundation.config.paths.PROFILES``，不是硬编码字面量。

    路径字面量门禁（``check_path_literals``）拦下过一版硬编码旧品牌；
    这条测试保证"取自 PROFILES"这个性质不被后续改动破坏。
    """
    from agentnodes.foundation.config.paths import PROFILES

    an = PROFILES["agentnodes"].home_dir_name
    qn = PROFILES["quantnodes"].home_dir_name
    assert QB.CONFIG_PATHS[0].parent.name == an
    assert QB.CONFIG_PATHS[1].parent.name == an
    assert QB.CONFIG_PATHS[2].parent.name == qn


def test_switching_profile_moves_the_chain(monkeypatch):
    """profile 的目录名若改变，链跟着变（证明不是硬编码）。"""
    from agentnodes.foundation.config import paths as P

    fake_profiles = {
        "agentnodes": P.DataProfile("agentnodes", ".renamed-an", "x.db"),
        "quantnodes": P.DataProfile("quantnodes", ".renamed-qn", "y.db"),
    }
    monkeypatch.setattr(P, "PROFILES", fake_profiles)
    paths = QB._llm_config_paths()
    assert paths[0].parent.name == ".renamed-an"
    assert paths[2].parent.name == ".renamed-qn"


def test_config_path_is_default_write_target():
    """CONFIG_PATH 现在是"默认写入目标"，不再是"唯一读取位置"。"""
    assert QB.CONFIG_PATH == C.Path.home() / ".agentnodes" / "llm.json"
    assert QB.CONFIG_PATH in QB.CONFIG_PATHS


# ── 2. 链的解析优先级 ────────────────────────────────────────────────


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    """把三个候选路径指到 tmp_path 下（monkeypatch CONFIG_PATHS）。"""
    an = tmp_path / ".agentnodes"
    qn = tmp_path / ".quantnodes"
    an.mkdir()
    qn.mkdir()
    paths = (an / "config.yaml", an / "llm.json", qn / "llm.json")
    monkeypatch.setattr(QB, "CONFIG_PATHS", paths)
    monkeypatch.setattr(C, "CONFIG_PATHS", paths)
    return {"agentnodes": an, "quantnodes": qn, "paths": paths}


def _write_json(path, model: str) -> None:
    path.write_text(json.dumps({"llm": {
        "active_profile": "p",
        "profiles": {"p": {"provider": "minimax", "model": model}},
    }}), encoding="utf-8")


def _write_yaml(path, model: str) -> None:
    path.write_text(
        "version: 2\n"
        "llm:\n"
        "  active_profile: p\n"
        "  profiles:\n"
        "    p:\n"
        "      provider: minimax\n"
        f"      model: {model}\n",
        encoding="utf-8",
    )


def test_yaml_wins_over_both_json(sandbox_home):
    """config.yaml 优先于两个 llm.json。"""
    _write_yaml(sandbox_home["agentnodes"] / "config.yaml", "from-yaml")
    _write_json(sandbox_home["agentnodes"] / "llm.json", "from-an-json")
    _write_json(sandbox_home["quantnodes"] / "llm.json", "from-qn-json")

    resolved = C._resolve_bridge_path({})
    assert resolved.name == "config.yaml"
    assert C._load_bridge_dict(resolved).get("model") == "from-yaml"


def test_agentnodes_json_wins_over_quantnodes(sandbox_home):
    """没有 config.yaml 时，.agentnodes 优先于 .quantnodes。"""
    _write_json(sandbox_home["agentnodes"] / "llm.json", "from-an-json")
    _write_json(sandbox_home["quantnodes"] / "llm.json", "from-qn-json")

    resolved = C._resolve_bridge_path({})
    assert resolved == sandbox_home["paths"][1]
    assert C._load_bridge_dict(resolved).get("model") == "from-an-json"


def test_quantnodes_json_is_read_when_it_is_the_only_one(sandbox_home):
    """**本批核心回归**：只有 quantnodes 的 llm.json 时能读到。

    这正是修复前完全失效的场景——quantnodes 用户只有那份配置。
    """
    _write_json(sandbox_home["quantnodes"] / "llm.json", "from-qn-json")

    resolved = C._resolve_bridge_path({})
    assert resolved == sandbox_home["paths"][2]
    assert C._load_bridge_dict(resolved).get("model") == "from-qn-json"


def test_falls_back_to_write_target_when_nothing_exists(sandbox_home):
    """三个候选都不存在 → 返回写入目标（给诊断用，不崩）。"""
    resolved = C._resolve_bridge_path({})
    assert resolved == QB.CONFIG_PATH


# ── 3. env 单文件覆盖 ────────────────────────────────────────────────


def test_env_override_uses_single_file_no_chain(sandbox_home, tmp_path):
    """AGENTNODES_LLM_CONFIG 命中即只用它，不建链。"""
    _write_yaml(sandbox_home["agentnodes"] / "config.yaml", "from-yaml")
    custom = tmp_path / "custom.json"
    _write_json(custom, "from-env")

    resolved = C._resolve_bridge_path({"AGENTNODES_LLM_CONFIG": str(custom)})
    assert resolved == custom
    assert C._load_bridge_dict(resolved).get("model") == "from-env"


# ── 4. 坏文件不静默跳过 ──────────────────────────────────────────────


def test_broken_yaml_returns_empty_not_none(tmp_path):
    """坏 YAML → {}（"找到了但为空"），**不是** None（"没找到"）。

    返回 None 会让链跳到下一个候选——用户的坏 config.yaml 被静默忽略，
    看起来像"没配置"。这是批 E 治的那类静默失效。
    """
    bad = tmp_path / "config.yaml"
    bad.write_text("llm: [this is: not: valid: yaml", encoding="utf-8")
    assert QB._load_any_path(bad) == {}


def test_broken_json_returns_empty_not_none(tmp_path):
    bad = tmp_path / "llm.json"
    bad.write_text("{not json", encoding="utf-8")
    assert QB._load_any_path(bad) == {}


def test_missing_file_returns_none(tmp_path):
    """真不存在才返回 None（让链继续）。"""
    assert QB._load_any_path(tmp_path / "nope.yaml") is None
    assert QB._load_any_path(tmp_path / "nope.json") is None


def test_broken_yaml_blocks_fallback(sandbox_home):
    """坏 config.yaml **阻止**回退到 llm.json（不静默跳过）。"""
    (sandbox_home["agentnodes"] / "config.yaml").write_text(
        "llm: [bad: yaml", encoding="utf-8")
    _write_json(sandbox_home["agentnodes"] / "llm.json", "should-not-be-used")

    resolved = C._resolve_bridge_path({})
    assert resolved.name == "config.yaml"      # 选中了坏的那个
    assert C._load_bridge_dict(resolved) == {}  # 解析为空


def test_public_load_does_not_skip_broken_high_priority_config(sandbox_home, caplog):
    (sandbox_home["agentnodes"] / "config.yaml").write_text(
        "llm: [bad: yaml", encoding="utf-8"
    )
    _write_json(sandbox_home["quantnodes"] / "llm.json", "fallback-must-not-win")

    cfg = C.LLMConfig.load(env={}, load_dotenv=False)

    assert cfg.model == "gpt-4o-mini"
    assert "config.yaml" in caplog.text


def test_public_load_explicit_file_overrides_existing_chain(sandbox_home, tmp_path):
    _write_yaml(sandbox_home["agentnodes"] / "config.yaml", "chain-model")
    explicit = tmp_path / "explicit.json"
    _write_json(explicit, "explicit-model")

    cfg = C.LLMConfig.load(
        env={"AGENTNODES_LLM_CONFIG": str(explicit)}, load_dotenv=False
    )

    assert cfg.model == "explicit-model"


def test_empty_yaml_file_is_explicit_empty_config(tmp_path):
    """空 YAML 文件 = 显式空配置（与 JSON 的 {} 同义），不是"没找到"。"""
    empty = tmp_path / "config.yaml"
    empty.write_text("", encoding="utf-8")
    assert QB._load_any_path(empty) == {}


def test_yaml_without_llm_section_is_empty(tmp_path):
    """有内容但没有 llm 段 → {}。"""
    p = tmp_path / "config.yaml"
    p.write_text("version: 2\nother: thing\n", encoding="utf-8")
    assert QB._load_any_path(p) == {}


def test_yaml_top_level_not_mapping(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert QB._load_any_path(p) == {}


# ── 5. YAML 与 JSON 同一套 schema ────────────────────────────────────


def test_yaml_and_json_produce_same_config(tmp_path):
    """同一份逻辑配置，YAML 与 JSON 解析出**完全相同**的字段。

    这是"不是第二套 schema"的证据：profiles / active_profile / timeout
    的解析路径共用。
    """
    body = {
        "active_profile": "p",
        "timeout": 123,
        "max_retries": 5,
        "profiles": {"p": {
            "provider": "minimax",
            "model": "m1",
            "base_url": "https://example.invalid/v1",
        }},
    }
    js = tmp_path / "a.json"
    js.write_text(json.dumps({"llm": body}), encoding="utf-8")
    yml = tmp_path / "a.yaml"
    yml.write_text(
        "version: 2\nllm:\n  active_profile: p\n  timeout: 123\n"
        "  max_retries: 5\n  profiles:\n    p:\n      provider: minimax\n"
        "      model: m1\n      base_url: https://example.invalid/v1\n",
        encoding="utf-8",
    )

    assert C._load_bridge_dict(js) == C._load_bridge_dict(yml)


def test_yaml_supports_env_api_key_reference(tmp_path, monkeypatch):
    """YAML 里的 ``api_key: env:VAR`` 与 JSON 同语法、同展开。"""
    monkeypatch.setenv("BATCH_D_TEST_KEY", "sk-from-yaml")
    p = tmp_path / "config.yaml"
    p.write_text(
        "llm:\n  active_profile: p\n  profiles:\n    p:\n"
        "      provider: minimax\n      model: m\n"
        "      api_key: env:BATCH_D_TEST_KEY\n",
        encoding="utf-8",
    )
    md = C._load_bridge_dict(p)
    assert md.get("api_key") == "sk-from-yaml"


def test_yaml_never_inlines_plaintext_key(tmp_path):
    """YAML 里写 env: 引用时，config 不持有明文（与 llm.json 同规则）。"""
    p = tmp_path / "config.yaml"
    p.write_text(
        "llm:\n  active_profile: p\n  profiles:\n    p:\n"
        "      provider: minimax\n      model: m\n"
        "      api_key: env:NOT_SET_ANYWHERE_XYZ\n",
        encoding="utf-8",
    )
    md = C._load_bridge_dict(p)
    assert md.get("api_key") in ("", None)


# ── 6. yml 后缀同样支持 ──────────────────────────────────────────────


def test_yml_suffix_also_parsed_as_yaml(tmp_path):
    p = tmp_path / "config.yml"
    p.write_text(
        "llm:\n  active_profile: p\n  profiles:\n    p:\n"
        "      provider: minimax\n      model: from-yml\n",
        encoding="utf-8",
    )
    assert C._load_bridge_dict(p).get("model") == "from-yml"
