"""Event-envelope protocol version contract (zero business knowledge).

事件信封的 schema 版本契约：唯一真源 + fail-fast 校验。

从 ``core/orchestration/contracts.py`` 拆出（2026-11-03 W4b）。原文件同时承载
两类内容：① 事件协议版本（纯协议，零业务）② 工单业务模型（WorkKind/WorkRequest
等）。因为 ① 被 L4 的 ``storage.platform_store`` 与 L3 的 ``events`` /
``agent.event_store`` 使用，把 ① 留在 L3 会制造 L4→L3 上行违规；而 ② 是业务
概念，放 L4 违反「零业务知识」定义。故按语义边界拆分，各归其位。

单版本 + 拒绝，不做协商：回放/订阅遇到不认识的版本必须明确报错，不能猜语义。
"""

from __future__ import annotations

# R4：协议版本（事件信封 schema_v 的唯一来源）。
PROTOCOL_VERSION = "1"


class ProtocolVersionError(Exception):
    """事件版本与本进程 PROTOCOL_VERSION 不匹配 → 拒绝回放/订阅。"""


def check_event_version(schema_v: str | None) -> None:
    """校验事件信封版本；None 视为历史默认（"1"）。不匹配即抛。"""
    if (schema_v or "1") != PROTOCOL_VERSION:
        raise ProtocolVersionError(
            f"事件 schema_v={schema_v!r} 与本进程协议版本 "
            f"{PROTOCOL_VERSION!r} 不匹配——拒绝回放（不做跨版本猜测）"
        )


__all__ = ["PROTOCOL_VERSION", "ProtocolVersionError", "check_event_version"]
