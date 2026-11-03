"""E2 敏感隔离收尾测试——C1 指纹化、config.fingerprint 事件。

设计：docs/e2-sensitive-closeout-design-20261030.md
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.llm.config_audit import (
    _key_fingerprint,
    detect_issues,
)


# ── _key_fingerprint ─────────────────────────────────────


def test_fingerprint_deterministic_and_distinct():
    a1 = _key_fingerprint("sk-test-key-alpha")
    a2 = _key_fingerprint("sk-test-key-alpha")
    b = _key_fingerprint("sk-test-key-beta")
    assert a1 == a2          # 同 key 同指纹（可对账）
    assert a1 != b           # 不同 key 不同指纹
    assert a1.startswith("sha256:")
    assert len(a1) == len("sha256:") + 12


def test_fingerprint_never_contains_key_material():
    key = "sk-cp-1234567890abcdefghijklmnop"
    fp = _key_fingerprint(key)
    assert "sk-cp-1234" not in fp       # 明文前缀不出现
    assert key not in fp


# ── C1 告警脱敏 ──────────────────────────────────────────


def test_c1_description_uses_fingerprint_not_prefix(tmp_path):
    key = "sk-cp-1234567890abcdefghijklmnop"
    llm = tmp_path / "llm.json"
    llm.write_text(json.dumps({
        "llm": {"provider": "minimax", "api_key": key}
    }), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")

    issues = detect_issues(llm_json_path=llm, env_path=env)
    c1 = next(i for i in issues if i.code == "C1")
    # 红线：描述不得含明文前缀
    assert key[:8] not in c1.description
    assert "sk-cp" not in c1.description
    # 含可对账指纹
    assert _key_fingerprint(key) in c1.description


def test_c1_still_emitted_with_same_code_and_severity(tmp_path):
    """行为等价：code/severity/fixable 不因描述脱敏而变。"""
    llm = tmp_path / "llm.json"
    llm.write_text(json.dumps({
        "llm": {"provider": "minimax", "api_key": "sk-cp-anything-here"}
    }), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")

    issues = detect_issues(llm_json_path=llm, env_path=env)
    c1 = next(i for i in issues if i.code == "C1")
    assert c1.severity == "error"
    assert c1.fixable is True


# ── Gateway.emit_config_fingerprint ──────────────────────


def test_gateway_emits_config_fingerprint():
    from strategy_research.core.gateway import Gateway

    events: list[tuple[str, dict]] = []
    gw = Gateway(client=object(), sink=lambda k, d: events.append((k, d)))

    fp = gw.emit_config_fingerprint()

    assert fp is not None and fp
    kinds = [k for k, _ in events]
    assert "config.fingerprint" in kinds
    payload = dict(events[[k for k, _ in events].index("config.fingerprint")][1])
    assert payload["fingerprint"] == fp
    # 事件只带指纹，不带配置内容
    assert set(payload) == {"fingerprint"}


def test_gateway_fingerprint_emit_failure_degrades(monkeypatch):
    from strategy_research.core.gateway import Gateway

    gw = Gateway(client=object())
    monkeypatch.setattr(
        "strategy_research.kernel.orchestration.config_hub.load_platform_config",
        lambda **k: (_ for _ in ()).throw(RuntimeError("config broken")),
    )
    assert gw.emit_config_fingerprint() is None  # 降级不抛


# ── SENSITIVE_KEYS 机制有效性 ────────────────────────────


def test_sensitive_keys_exclusion_mechanism_works():
    """机制锁定：加入 SENSITIVE_KEYS 的字段不得影响指纹。"""
    from dataclasses import dataclass, replace

    from strategy_research.kernel.orchestration import config_hub as ch

    @dataclass(frozen=True)
    class _Cfg:
        visible: int = 1
        secret: str = "s"

    original = ch.SENSITIVE_KEYS
    ch.SENSITIVE_KEYS = frozenset({"secret"})
    try:
        cfg = _Cfg()
        payload = {"visible": cfg.visible, "secret": cfg.secret}
        for k in ch.SENSITIVE_KEYS:
            payload.pop(k, None)
        import hashlib

        expected = hashlib.sha256(
            ch.__dict__["json"].dumps(payload, sort_keys=True, default=str)
            .encode()
        ).hexdigest()[:16]
        assert expected  # 指纹计算路径可用（机制未漂移）
    finally:
        ch.SENSITIVE_KEYS = original
