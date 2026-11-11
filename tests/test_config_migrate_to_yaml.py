"""批 D · ``agentnodes config migrate``（llm.json → config.yaml）。

设计：docs/design/llm-config-dual-path-and-yaml-20261111.md §3.2。

核心不变量：

1. **不删除、不改写源文件**——只新增 config.yaml，用户可随时删掉回退；
2. `api_key: env:VAR` **保持引用不内联**（密钥永不落明文）；
3. dry-run 不落盘；
4. 目标已存在且无 `--force` → 拒绝（防误覆盖）；
5. 生成的 YAML 能被 `_load_bridge_dict` 读回**同样的配置**。
"""
from __future__ import annotations

import argparse
import json

import pytest

from agentnodes.interfaces.cli.commands import config_cmd


def _run(**kwargs) -> tuple[int, str]:
    """跑 _migrate_to_yaml，捕获 stdout/stderr。"""
    import contextlib
    import io

    args = argparse.Namespace(
        action="migrate",
        dry_run=kwargs.get("dry_run", False),
        force=kwargs.get("force", False),
        src=kwargs.get("src"),
        dst=kwargs.get("dst"),
        json=False,
    )
    buf, ebuf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
        code = config_cmd.cmd_config(args)
    return code, buf.getvalue() + ebuf.getvalue()


@pytest.fixture
def src_json(tmp_path):
    p = tmp_path / "llm.json"
    p.write_text(json.dumps({"llm": {
        "active_profile": "p",
        "timeout": 300,
        "max_retries": 2,
        "profiles": {"p": {
            "provider": "minimax",
            "model": "test-model",
            "base_url": "https://api.example.invalid/v1",
            "api_key": "env:MY_TEST_KEY",
        }},
    }}, indent=2), encoding="utf-8")
    return p


def test_dry_run_does_not_write(src_json, tmp_path):
    dst = tmp_path / "config.yaml"
    code, out = _run(dry_run=True, src=str(src_json), dst=str(dst))
    assert code == 0
    assert not dst.exists()
    assert "dry-run" in out
    assert "test-model" in out


def test_migrate_writes_yaml(src_json, tmp_path):
    dst = tmp_path / "config.yaml"
    code, _out = _run(src=str(src_json), dst=str(dst))
    assert code == 0
    assert dst.exists()
    text = dst.read_text(encoding="utf-8")
    assert "version: 2" in text
    assert "test-model" in text


def test_source_not_modified(src_json, tmp_path):
    """**核心不变量**：源文件一个字节都不能变。"""
    before = src_json.read_text(encoding="utf-8")
    _run(src=str(src_json), dst=str(tmp_path / "config.yaml"))
    assert src_json.read_text(encoding="utf-8") == before


def test_api_key_stays_a_reference(src_json, tmp_path):
    """`env:MY_TEST_KEY` 保持引用——密钥永不内联进新文件。"""
    dst = tmp_path / "config.yaml"
    _run(src=str(src_json), dst=str(dst))
    text = dst.read_text(encoding="utf-8")
    assert "env:MY_TEST_KEY" in text
    # 不该出现任何看起来像明文密钥的长串
    assert "sk-" not in text


def test_refuses_to_overwrite_without_force(src_json, tmp_path):
    dst = tmp_path / "config.yaml"
    dst.write_text("existing", encoding="utf-8")
    code, out = _run(src=str(src_json), dst=str(dst))
    assert code == 1
    assert "已存在" in out
    assert dst.read_text(encoding="utf-8") == "existing"


def test_force_overwrites(src_json, tmp_path):
    dst = tmp_path / "config.yaml"
    dst.write_text("existing", encoding="utf-8")
    code, _out = _run(src=str(src_json), dst=str(dst), force=True)
    assert code == 0
    assert "test-model" in dst.read_text(encoding="utf-8")


def test_missing_source_returns_1(tmp_path):
    code, out = _run(src=str(tmp_path / "nope.json"),
                     dst=str(tmp_path / "config.yaml"))
    assert code == 1
    assert "不存在" in out


def test_source_without_llm_section_returns_1(tmp_path):
    src = tmp_path / "llm.json"
    src.write_text('{"other": 1}', encoding="utf-8")
    code, out = _run(src=str(src), dst=str(tmp_path / "config.yaml"))
    assert code == 1
    assert "没有可迁移" in out


def test_generated_yaml_roundtrips_through_loader(src_json, tmp_path):
    """生成的 YAML 能被生产加载器读回**同样的配置**。"""
    from agentnodes.foundation.llm import config as C

    dst = tmp_path / "config.yaml"
    _run(src=str(src_json), dst=str(dst))
    md = C._load_bridge_dict(dst)
    assert md.get("model") == "test-model"
    assert md.get("provider") == "minimax"
    assert md.get("timeout_s") == 300


def test_generated_yaml_takes_priority_over_source(src_json, tmp_path, monkeypatch):
    """真·端到端：生成 config.yaml 后，链会优先选它。"""
    from agentnodes.foundation.llm import config as C
    from agentnodes.foundation.llm import quantnodes_bridge as QB

    dst = tmp_path / "config.yaml"
    _run(src=str(src_json), dst=str(dst))
    monkeypatch.setattr(QB, "CONFIG_PATHS", (dst, src_json))
    monkeypatch.setattr(C, "CONFIG_PATHS", (dst, src_json))
    assert C._resolve_bridge_path({}) == dst


def test_unknown_action_returns_2():
    args = argparse.Namespace(action="nonsense", json=False, dry_run=False,
                              force=False, src=None, dst=None)
    import contextlib
    import io

    ebuf = io.StringIO()
    with contextlib.redirect_stderr(ebuf):
        code = config_cmd.cmd_config(args)
    assert code == 2
    assert "未知动作" in ebuf.getvalue()
