"""Agent 事件词汇表 —— 总结级类型的唯一来源。

为什么需要它：``AgentLoop._emit`` 对每个流式 chunk 都发 ``text_delta`` /
``thinking_delta``（``loop_stream.py``）。chat 转发全量是因为 SSE 语义；
study 一轮 9 个 agent × 每次 LLM 调用几百个 chunk，不过滤就是每轮几千行
event_log。

总结级词汇与 ``api/session/trace_projection.py`` 的投影口径一致：排障时
需要的是「哪次 LLM 调用、哪个工具、第几轮迭代」，不是每个 token。

消费方：
- ``study/runner.py::_make_agent_on_event``（agent 事件 → study emitter）
- ``swarm/runtime.py``（agent 事件 → 调用方回调）
"""
from __future__ import annotations

SUMMARY_EVENT_TYPES: frozenset[str] = frozenset({
    # LLM 调用（排障核心：请求/响应/用量）
    "llm_request", "llm_response", "llm_usage",
    # 循环生命周期
    "loop_start", "loop_end", "loop_final",
    "iter_start", "iter_end",
    # 工具
    "tool_call", "tool_result", "tool_error",
    # 压缩与错误
    "compression", "compact", "error",
    # 人工干预与预算（study 自主跑时的关键停点）
    "agent_approval_requested", "budget_exhausted",
})

#: ``llm_request`` 携带的大字段。chat 路径经 forwarder offload 到
#: trace-blobs；study/goal 的转发 sink 直发 EventStore，必须先剥离
#: （hash + len 已足够定位「prompt 变没变」，体积却差三个量级）。
LLM_REQUEST_STRIP_FIELDS: frozenset[str] = frozenset({
    "system_prompt", "tools_schema",
})


def strip_llm_request_fields(event_type: str, data: dict) -> dict:
    """剥离 ``llm_request`` 的大字段（其余事件原样返回新副本）。

    这是转发 sink 的约定动作：不 offload（没有 blob 通道）也不全文落库
    （会淹没 event_log），保留 ``*_hash`` / ``*_len`` 指纹用于比对。
    """
    if event_type != "llm_request":
        return data
    return {k: v for k, v in data.items() if k not in LLM_REQUEST_STRIP_FIELDS}


__all__ = ["SUMMARY_EVENT_TYPES", "LLM_REQUEST_STRIP_FIELDS",
           "strip_llm_request_fields"]
