"""P0.1 回归 —— mailbox 配置必须真正接线到 EntryPoint/Mailbox。

缺陷背景（2026-11-02 复核发现）：`MailboxConfig` 早在 config_hub 里声明了
`cap` / `aging_s`，环境变量 `QNODES_MAILBOX__CAP` 也能被正确解析，但
`Gateway.__init__` 构造 `EntryPoint` 时从未把这两个值传下去——因此
**配置永远不生效**，恒用 mailbox.py 的模块默认值（20 / 15.0）。
这与之前 8 个「静默失效」缺陷同类：不报错，功能就是不按配置走。

本文件锁三件事：
1. Gateway 的 EntryPoint 拿到的是配置值（不是模块默认值）；
2. 该值真的下传到了 per-session 的 Mailbox 实例；
3. 非法配置仍然 fail-fast（不因新增接线而吞掉校验错误）。
"""
from __future__ import annotations

import pytest

from strategy_research.core.gateway import reset_gateway
from strategy_research.core.gateway.gateway import Gateway
from strategy_research.core.orchestration.config_hub import ConfigError
from strategy_research.core.orchestration.mailbox import (
    DEFAULT_AGING_S,
    DEFAULT_CAP,
    DEFAULT_STEER_CAP,
)
from strategy_research.core.orchestration.contracts import (
    QueueMode,
    WorkKind,
    WorkRequest,
)


def _gateway() -> Gateway:
    """带 stub client 的 Gateway（不触网、不读真实 LLM 配置）。"""
    return Gateway(client=object(), system="test")


def _request(session_key: str = "chat:s1") -> WorkRequest:
    return WorkRequest(
        session_key=session_key,
        kind=WorkKind.CHAT,
        payload={"text": "hi"},
        queue_mode=QueueMode.FOLLOWUP,
        source="chat",
    )


# ── 默认值：未配置时行为不变（等价性） ────────────────────


def test_defaults_pass_through_unchanged(monkeypatch):
    for key in (
        "QNODES_MAILBOX__CAP",
        "QNODES_MAILBOX__STEER_CAP",
        "QNODES_MAILBOX__AGING_S",
    ):
        monkeypatch.delenv(key, raising=False)
    gw = _gateway()
    assert gw.entry._cap == DEFAULT_CAP
    assert gw.entry._steer_cap == DEFAULT_STEER_CAP
    assert gw.entry._aging_s == DEFAULT_AGING_S


# ── 环境变量必须生效（本缺陷的核心断言） ──────────────────


def test_env_cap_reaches_entry_point(monkeypatch):
    monkeypatch.setenv("QNODES_MAILBOX__CAP", "3")
    gw = _gateway()
    assert gw.entry._cap == 3


def test_env_steer_cap_reaches_entry_point(monkeypatch):
    monkeypatch.setenv("QNODES_MAILBOX__STEER_CAP", "2")
    gw = _gateway()
    assert gw.entry._steer_cap == 2


def test_env_aging_s_reaches_entry_point(monkeypatch):
    monkeypatch.setenv("QNODES_MAILBOX__AGING_S", "0")
    gw = _gateway()
    assert gw.entry._aging_s == 0.0


def test_config_reaches_actual_mailbox_instance(monkeypatch):
    """不止存在 EntryPoint 字段上——真正建出来的 Mailbox 也得是配置值。"""
    monkeypatch.setenv("QNODES_MAILBOX__CAP", "3")
    monkeypatch.setenv("QNODES_MAILBOX__AGING_S", "2.5")
    gw = _gateway()
    gw.entry.submit(_request())
    mailbox = gw.entry._mailbox_for("chat:s1")
    assert mailbox.cap == 3
    assert mailbox.aging_s == 2.5


def test_cap_actually_bounds_queue(monkeypatch):
    """cap=2 时队列保持有界——用行为验证，而不是只看字段。"""
    monkeypatch.setenv("QNODES_MAILBOX__CAP", "2")
    gw = _gateway()
    for i in range(6):
        gw.entry.submit(_request())
    mailbox = gw.entry._mailbox_for("chat:s1")
    # 合并发生在 append 之前：先压到 < cap，再入队 → 稳态 len <= cap。
    assert len(mailbox._queue) <= 2


# ── 非法配置仍 fail-fast（接线不得吞掉校验） ──────────────


def test_bad_cap_still_fails_fast(monkeypatch):
    monkeypatch.setenv("QNODES_MAILBOX__CAP", "many")
    with pytest.raises(ConfigError, match="cap"):
        _gateway()


def test_cap_below_two_is_rejected(monkeypatch):
    """cap=1 会让摘要合并死循环——必须在配置层就拒收。

    2026-11-02 复核发现的真实缺陷：原 `_coalesce_if_full` 对 cap=1 有
    「摘要它自己」的分支，合并后队列长度不变，while 条件恒真 → 无限循环
    （实测 MemoryError）。合并单元是「两条→一条」，故 cap 最小为 2。
    """
    monkeypatch.setenv("QNODES_MAILBOX__CAP", "1")
    with pytest.raises(ConfigError, match="cap"):
        _gateway()


def test_unknown_mailbox_key_still_fails_fast(monkeypatch):
    monkeypatch.setenv("QNODES_MAILBOX__NOPE", "1")
    with pytest.raises(ConfigError, match="未知配置键"):
        _gateway()


# ── 单例路径同样生效 ─────────────────────────────────────


def test_singleton_gateway_honours_env(monkeypatch):
    monkeypatch.setenv("QNODES_MAILBOX__CAP", "4")
    monkeypatch.setenv("QNODES_PLATFORM_STORE", "0")
    reset_gateway()
    try:
        from strategy_research.core.gateway import get_gateway

        gw = get_gateway(client=object(), system="test")
        assert gw.entry._cap == 4
    finally:
        reset_gateway()
