"""ConfigHub 骨架单元测试 —— 分层装载 / fail-fast / workers 钉死 / 指纹。"""
from __future__ import annotations

import pytest

from agentnodes.kernel.orchestration.config_hub import (
    ConfigError,
    GatewayConfig,
    PlatformConfig,
    config_fingerprint,
    load_platform_config,
)


def test_defaults_load_clean():
    config = load_platform_config(env={})
    assert config.gateway.workers == 1
    assert config.gateway.auto_resume is False
    assert config.gateway.grace_seconds == 300.0
    assert config.mailbox.cap == 20
    assert config.mailbox.overflow == "summarize"


def test_file_layer_overrides_defaults(tmp_path):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        "gateway:\n  grace_seconds: 60\nmailbox:\n  cap: 5\n",
        encoding="utf-8",
    )
    config = load_platform_config(file_path=str(cfg_file), env={})
    assert config.gateway.grace_seconds == 60.0
    assert config.mailbox.cap == 5
    assert config.gateway.workers == 1  # 未覆盖处保持默认


def test_env_layer_overrides_file_layer():
    cfg_file = None  # 只测 env 层直接盖默认
    config = load_platform_config(
        env={"QNODES_MAILBOX__CAP": "3", "QNODES_GATEWAY__AUTO_RESUME": "true"}
    )
    assert config.mailbox.cap == 3
    # env 字符串 "true" 经 _coerce 转 bool——确认转换生效
    assert config.gateway.auto_resume is True


def test_env_bool_string_coercion_fail_fast_on_garbage():
    with pytest.raises(ConfigError):
        load_platform_config(env={"QNODES_GATEWAY__AUTO_RESUME": "yes-please"})


def test_unknown_key_fail_fast():
    with pytest.raises(ConfigError, match="未知配置键"):
        load_platform_config(env={"QNODES_MAILBOX__NOPE": "1"})


def test_workers_is_pinned_even_in_file(tmp_path):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text("gateway:\n  workers: 4\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="workers"):
        load_platform_config(file_path=str(cfg_file), env={})


def test_workers_is_pinned_in_env():
    with pytest.raises(ConfigError, match="workers"):
        load_platform_config(env={"QNODES_GATEWAY__WORKERS": "4"})


def test_bad_type_fail_fast():
    with pytest.raises(ConfigError, match="cap"):
        load_platform_config(env={"QNODES_MAILBOX__CAP": "many"})


def test_bad_overflow_enum_fail_fast():
    with pytest.raises(ConfigError, match="overflow"):
        load_platform_config(env={"QNODES_MAILBOX__OVERFLOW": "explode"})


def test_missing_file_fail_fast():
    with pytest.raises(ConfigError, match="不存在"):
        load_platform_config(file_path="Z:/no/such.yaml", env={})


def test_fingerprint_stable_and_value_sensitive():
    base = load_platform_config(env={})
    changed = load_platform_config(env={"QNODES_MAILBOX__CAP": "7"})
    assert config_fingerprint(base) == config_fingerprint(load_platform_config(env={}))
    assert config_fingerprint(base) != config_fingerprint(changed)
    assert len(config_fingerprint(base)) == 16  # 短指纹，进事件流


def test_platform_config_is_frozen():
    config = load_platform_config(env={})
    with pytest.raises(Exception):
        config.mailbox.cap = 99  # type: ignore[misc]
    with pytest.raises(Exception):
        config.gateway = GatewayConfig()  # type: ignore[misc]
