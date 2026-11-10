"""ChannelAdapter —— 配置驱动的通用 provider 实现（批次 14 · V5）。

## 为什么要这一层

改造前的 provider 层 13 个文件 / 1,304 行，但**真正有逻辑的只有 3 个**
（minimax 的 ``<think>`` 跨 chunk 状态机、deepseek/siliconflow 的 DSML
状态机）。其余文件是"纯元数据 + 几行字段提取"：

    qwen.py      24 行   零逻辑（只有 name/base_url/model 三个属性）
    kimi.py      30 行   两个 ``return None``
    openai.py    44 行   元数据 + 3 行 reasoning 提取
    nvidia.py    72 行   元数据 + 双字段提取 + 402 映射

这些重复代码本身不可怕，可怕的是**元数据散落多处后互相不一致**：
2026-11-10 实测发现 MiniMax 的 max_tokens 有 4 个来源互相矛盾
（adapter 硬编码 32000 / toml 写 128000 / 真实上限 524288 / 用户
llm.json 又一套），而思考字段写错（只查 content 不查
reasoning_content）导致新模型思考内容整体丢失。

## 分层

    L0  模型元数据 + 行为   data/providers/<id>/<model>.toml 的 [limit]/[channel]
    L1  通用 adapter        provider/channel.py（本文件）——只读配置，不写死
    L2  渠道 adapter        provider/minimax.py 等——只放**状态机**等 Python 逻辑
    L3  注册表              provider/__init__.py

**判据（何时该写 Python 而不是配置）**：
- 跨 chunk 的状态机（``<think>``/DSML 标签被 BPE 切开）→ 必须 Python
- 请求体形态改造（非 OpenAI 兼容协议）→ 必须 Python
- 纯字段名 / 标签 / 错误码映射 / 默认值 → 配置

## 配置契约（``[channel]`` 段）

    [channel]
    thinking_fields = ["reasoning_content"]   # 依次尝试的思考字段
    thinking_tags = ["<think>", "</think>"]   # content 内嵌标签（成对）
    stream_buffered_tags = false              # 标签跨 chunk 需缓冲
    stream_options = { include_usage = true } # 流式尾部 usage 快照
    max_tokens_includes_thinking = true       # 预算含思考 token
    headers = { X-Foo = "bar" }               # 额外请求头
    payload = { top_k = 40 }                  # 额外请求体字段

    [[channel.error_rules]]
    status = [400]
    contains = ["2013"]                       # body 子串匹配（任一命中）
    kind = "config"                           # config|quota|rate|auth|server
    message = "..."

外部可用 ``extends = "minimax"`` 声明继承渠道默认（显式，不做隐式合并）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .base import ProviderAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "ChannelSpec",
    "ChannelAdapter",
    "ErrorRule",
]


#: 错误类别 → foundation.llm.errors 里的异常类名。
_ERROR_KINDS = ("config", "quota", "rate", "auth", "server")


@dataclass(frozen=True)
class ErrorRule:
    """一条声明式错误映射规则（``[[channel.error_rules]]``）。"""

    status: tuple[int, ...] = ()
    contains: tuple[str, ...] = ()
    kind: str = "server"
    message: str = ""

    def matches(self, status: int, body_text: str) -> bool:
        """状态码在白名单内 **且** 任一子串命中（contains 为空则只看状态码）。"""
        if self.status and status not in self.status:
            return False
        if self.contains:
            lowered = body_text.lower()
            return any(s.lower() in lowered for s in self.contains)
        return bool(self.status)


@dataclass(frozen=True)
class ChannelSpec:
    """一个模型的渠道行为配置（从 toml 的 ``[channel]`` 段解析）。

    所有字段都有安全默认值：缺失配置 = 旧行为（``<think>`` 不识别、
    无额外头/字段、无错误映射）。
    """

    #: 依次尝试的思考字段名（如 ``reasoning_content`` / ``reasoning``）。
    thinking_fields: tuple[str, ...] = ()
    #: content 内嵌标签对 ``(open, close)``。
    thinking_tags: tuple[str, ...] = ()
    #: 标签是否跨 chunk（为 True 时需要调用方注入跨 chunk 缓冲器）。
    stream_buffered_tags: bool = False
    #: 流式尾部 usage 快照（OpenAI 风格 ``{"include_usage": True}``）。
    stream_options: dict[str, Any] | None = None
    #: ``max_tokens`` 是否包含思考 token（影响 ``min_output_tokens`` 取值）。
    max_tokens_includes_thinking: bool = False
    #: 额外请求头。
    headers: dict[str, str] = field(default_factory=dict)
    #: 额外请求体字段（浅合并，调用方显式传入的优先）。
    payload: dict[str, Any] = field(default_factory=dict)
    #: 错误映射规则（按序首个命中生效）。
    error_rules: tuple[ErrorRule, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ChannelSpec":
        """从 toml ``[channel]`` 段构造（容错：字段类型不对时回落默认）。"""
        if not isinstance(raw, dict):
            return cls()

        def _str_tuple(key: str) -> tuple[str, ...]:
            v = raw.get(key)
            if isinstance(v, str):
                return (v,)
            if isinstance(v, (list, tuple)):
                return tuple(str(x) for x in v if isinstance(x, (str, int, float)))
            return ()

        def _dict(key: str) -> dict[str, Any]:
            v = raw.get(key)
            return dict(v) if isinstance(v, dict) else {}

        rules: list[ErrorRule] = []
        for item in raw.get("error_rules") or ():
            if not isinstance(item, dict):
                continue
            statuses = item.get("status")
            if isinstance(statuses, int):
                statuses = [statuses]
            if not isinstance(statuses, (list, tuple)):
                statuses = []
            kind = str(item.get("kind") or "server").lower()
            if kind not in _ERROR_KINDS:
                kind = "server"
            rules.append(ErrorRule(
                status=tuple(int(s) for s in statuses
                             if isinstance(s, (int, float, str)) and str(s).isdigit()),
                contains=_as_str_tuple(item.get("contains")),
                kind=kind,
                message=str(item.get("message") or ""),
            ))

        opts = raw.get("stream_options")
        return cls(
            thinking_fields=_str_tuple("thinking_fields"),
            thinking_tags=_str_tuple("thinking_tags"),
            stream_buffered_tags=bool(raw.get("stream_buffered_tags", False)),
            stream_options=dict(opts) if isinstance(opts, dict) else None,
            max_tokens_includes_thinking=bool(
                raw.get("max_tokens_includes_thinking", False)
            ),
            headers={str(k): str(v) for k, v in _dict("headers").items()},
            payload=_dict("payload"),
            error_rules=tuple(rules),
        )


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(x) for x in value if isinstance(x, (str, int, float)))
    return ()


class ChannelAdapter(ProviderAdapter):
    """配置驱动的通用 adapter。

    子类只覆盖**渠道专属**部分（``name`` / ``default_base_url`` /
    ``default_model``，以及可选的状态机 ``fix_delta``）。
    所有“按模型变化”的行为都由 ``spec``（``[channel]`` 段）驱动。

    用法::

        class QwenAdapter(ChannelAdapter):
            @property
            def name(self) -> str: return "qwen"
            @property
            def default_base_url(self) -> str: return "https://..."
            @property
            def default_model(self) -> str: return "qwen-plus"

    **spec 来源（自动）**：``get_provider(name)`` 构造实例后调用
    :meth:`attach_model`，按当前生效的模型名装载 toml ``[channel]``。
    直接构造时可传 ``spec=`` 显式指定（测试用）。
    """

    def __init__(self, spec: ChannelSpec | None = None) -> None:
        self.spec = spec or ChannelSpec()
        #: 当前绑定的模型名（``attach_model`` 写入，供诊断/日志）
        self.bound_model: str | None = None

    def attach_model(self, model: str | None) -> "ChannelAdapter":
        """按模型名装载 ``[channel]`` 配置（幂等，返回 self 便于链式调用）。

        装载失败不抛异常（回落空 spec = 旧行为），配置问题绝不该打断对话。
        """
        if not model:
            return self
        try:
            from .channel_spec import load_channel_spec

            self.spec = load_channel_spec(self.name, model)
            self.bound_model = model
        except Exception:  # noqa: BLE001 —— 配置缺失回落默认行为
            logger.debug(
                "channel spec load failed for %s/%s", self.name, model,
                exc_info=True,
            )
        return self

    # ── 思考提取（配置驱动）─────────────────────────────────────

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        """依次尝试 ``thinking_fields``，再试 content 内嵌标签。

        **顺序即语义**：独立字段优先（DeepSeek / MiniMax-M3.1 风格），
        内嵌标签兜底（MiniMax-M3 等旧模型）。2026-11-10 的 MiniMax
        事故正是"只查标签、不查字段"导致的——现在两者都查，且由配置
        声明该模型用哪种。
        """
        for field_name in self.spec.thinking_fields:
            value = delta.get(field_name)
            if value and isinstance(value, str):
                # 流式路径保留 chunk 边界空白（BPE 把前导空格编进 token）
                return self.normalize_thinking(value, strip_edges=False)

        pair = self._tag_pair()
        if pair is not None:
            content = delta.get("content", "") or ""
            match = pair[0].search(content)
            if match:
                return self.normalize_thinking(match.group(1), strip_edges=False)
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        for field_name in self.spec.thinking_fields:
            value = message.get(field_name)
            if value and isinstance(value, str):
                return self.normalize_thinking(value)

        pair = self._tag_pair()
        if pair is not None:
            content = message.get("content", "") or ""
            match = pair[0].search(content)
            if match:
                return self.normalize_thinking(match.group(1))
        return None

    # ── 噪声清洗（配置驱动）─────────────────────────────────────

    def sanitize_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        """从 content 里剥掉内嵌思考标签。

        ``thinking_fields`` 走独立字段、不污染 content → 无需清洗；
        只有 ``thinking_tags`` 配置了才需要剥。
        """
        pair = self._tag_pair()
        if pair is None:
            return dict(delta)
        content = delta.get("content") or ""
        if not pair[0].search(content):
            return dict(delta)
        out = dict(delta)
        out["content"] = pair[0].sub("", content)
        return out

    def sanitize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        pair = self._tag_pair()
        if pair is None:
            return dict(message)
        content = message.get("content") or ""
        if not pair[0].search(content):
            return dict(message)
        out = dict(message)
        out["content"] = pair[0].sub("", content).strip()
        return out

    # ── HTTP 层（配置驱动）──────────────────────────────────────

    def custom_headers(self, config: Any) -> dict[str, str]:
        return dict(self.spec.headers)

    def custom_payload(self, payload: dict[str, Any], config: Any) -> dict[str, Any]:
        if not self.spec.payload:
            return payload
        merged = {**self.spec.payload, **payload}  # 调用方显式值优先
        return merged

    def custom_stream_options(self) -> dict[str, Any] | None:
        return dict(self.spec.stream_options) if self.spec.stream_options else None

    # ── 错误映射（配置驱动）─────────────────────────────────────

    def handle_error(self, status: int, body: Any) -> Exception | None:
        """按 ``[[channel.error_rules]]`` 的声明顺序匹配，首个命中生效。"""
        if not self.spec.error_rules:
            return None
        body_text = str(body)
        for rule in self.spec.error_rules:
            if not rule.matches(status, body_text):
                continue
            return self._make_error(rule, status, body)
        return None

    def _make_error(self, rule: ErrorRule, status: int, body: Any) -> Exception:
        from ..errors import (
            LLMAuthError,
            LLMConfigError,
            LLMError,
            LLMQuotaError,
            LLMRateLimitError,
            LLMServerError,
        )

        cls = {
            "config": LLMConfigError,
            "quota": LLMQuotaError,
            "rate": LLMRateLimitError,
            "auth": LLMAuthError,
            "server": LLMServerError,
        }.get(rule.kind, LLMError)
        text = rule.message or f"{rule.kind} error ({status})"
        return cls(f"{text}: {str(body)[:400]}")

    # ── 内部 ────────────────────────────────────────────────────

    def _tag_pair(self):
        """把 ``thinking_tags`` 编译成 (pattern, open, close)；未配置返回 None。"""
        tags = self.spec.thinking_tags
        if len(tags) < 2:
            return None
        pair = getattr(self, "_tag_pair_cache", None)
        if pair is not None and getattr(self, "_tag_pair_raw", None) == tags:
            return pair
        import re

        open_tag, close_tag = tags[0], tags[1]
        pattern = re.compile(
            re.escape(open_tag) + r"([\s\S]*?)" + re.escape(close_tag)
        )
        self._tag_pair_cache = (pattern, open_tag, close_tag)
        self._tag_pair_raw = tags
        return self._tag_pair_cache
