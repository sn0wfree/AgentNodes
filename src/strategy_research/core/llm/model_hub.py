"""ModelHub —— provider/model 解析的唯一入口（R5）。

架构图上的 RuntimeHub「解析每个 turn 用什么执行」此前散在 20+ 处
``OpenAICompatClient(LLMConfig.load())`` 调用点，每处各自决定用哪个
provider/model。本模块把**解析序 + 拒绝语义 + 客户端构造**收口到一处。

设计（docs/stage-r5-execution-plane-assessment-20261030.md §2.1）：
- 解析序：显式 overrides > llm.json 的 provider profile > provider 默认。
- **不猜、不降级**：provider 不认识 → ModelHubError（``LLMConfig`` 的
  ``get_provider`` 会静默回落 FallbackAdapter；本模块显式识别并拒绝）。
- 底层仍用 ``LLMConfig.load()``（它已实现四层装载）；本模块只加
  「解析序集中 + 拒绝语义 + 单一构造点」。
- 不含「preset 图绑定」——图上 RuntimeHub 的那一半当前无用户
  （见评估文档 §1.3）。

依赖方向：core/llm 内部模块，不 import 任何 domain。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

__all__ = ["ModelBinding", "ModelHubError", "resolve", "get_client"]


class ModelHubError(Exception):
    """provider/model 解析失败——拒绝执行，不降级为别的 provider。"""


@dataclass(frozen=True)
class ModelBinding:
    """一次解析结果：用哪个 provider/model、来源为何、最终 config。"""

    provider: str
    model: str
    config: Any          # LLMConfig（不改现有契约）
    source: str          # "explicit" | "config" | "provider_default"

    def describe(self) -> dict[str, str]:
        """脱敏摘要（可进事件流；不含任何 key）。"""
        return {
            "provider": self.provider,
            "model": self.model,
            "source": self.source,
        }


def _known_providers() -> set[str]:
    from .provider import _REGISTRY  # noqa: PLC2701 —— 注册表是唯一真相

    return set(_REGISTRY)


def _validate(provider: str) -> None:
    """provider 必须是已注册适配器，或是文档定义的 ``auto`` 模式。

    ``auto`` 是 LLMConfig 的合法值（= 用全局兜底/base_url），不算未知；
    **未知** provider 才拒绝。这是本模块与 ``get_provider`` 的关键差异：
    后者对未知名静默回落 FallbackAdapter，这里明确报错——图上红线
    「不支持则拒绝，不降级」。
    """
    if not provider:
        raise ModelHubError(
            "provider 为空——请在 llm.json 指定 provider 或在调用处显式传入"
        )
    if provider == "auto":
        return  # 文档定义的合法模式：走全局兜底
    known = _known_providers()
    if provider not in known:
        raise ModelHubError(
            f"未知 provider {provider!r}（已注册：{sorted(known)}）——"
            "拒绝执行，不降级到其他 provider"
        )


def resolve(
    *,
    provider: str | None = None,
    model: str | None = None,
    env: Mapping[str, str] | None = None,
) -> ModelBinding:
    """解析 provider/model → ModelBinding。

    解析序：
      1. 显式 ``provider``/``model`` 参数（source=explicit）
      2. llm.json / env 里的 provider profile（source=config）
      3. provider 适配器的默认值（source=provider_default）
    """
    from .config import LLMConfig

    cfg = LLMConfig.load(env=env)

    # 显式参数优先于配置层（LLMConfig.load 的 cli_overrides 走 argparse
    # 的 llm_ 前缀映射，不适合本用途——这里直接 with_config 覆盖）。
    overrides: dict[str, Any] = {}
    if provider:
        overrides["provider"] = provider
    if model:
        overrides["model"] = model
    if overrides:
        cfg = cfg.with_config(**overrides)

    eff_provider = str(getattr(cfg, "provider", "") or "")
    eff_model = str(getattr(cfg, "model", "") or "")
    config_provider = str(getattr(LLMConfig.load(env=env), "provider", "") or "")

    _validate(eff_provider)
    if not eff_model:
        from .provider import get_provider_defaults

        eff_model = str(get_provider_defaults(eff_provider).get("model") or "")
    if not eff_model:
        raise ModelHubError(
            f"provider {eff_provider!r} 没有可用 model——"
            "请在 llm.json 指定 model 或选有默认值的 provider"
        )

    if provider or model:
        source = "explicit"
    elif config_provider and config_provider != "auto":
        source = "config"
    else:
        source = "provider_default"

    return ModelBinding(
        provider=eff_provider, model=eff_model, config=cfg, source=source,
    )


def get_client(**overrides: Any) -> Any:
    """按解析结果构造客户端（生产唯一出口）。

    ``overrides`` 透传 ``resolve``（provider/model/env）。
    """
    from .openai_client import OpenAICompatClient

    binding = resolve(**overrides)
    return OpenAICompatClient(binding.config)
