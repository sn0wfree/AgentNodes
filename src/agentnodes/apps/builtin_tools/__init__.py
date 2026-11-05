"""业务侧内置工具（L2）— 由 apps/capabilities.py 注册为 capability kind。

**来历**：这 6 个模块原住 ``kernel/agent/builtin_tools/``（L3），但它们
调用的全是业务能力（factor / data / backtest / goal），属「L3 里的 L2」。
2026-11-05 批次 6 归位到 L2，经 CapabilityRegistry 由 kernel 解析。

**降级语义**：每个工具的工厂函数在能力缺失时返回 ``None`` 而非抛错——
工具可被发现（``/help`` 里列得出），调用时报「能力未注册」。业务侧
重新注册对应 kind 后自动复活，无需改 kernel。
"""
from __future__ import annotations
