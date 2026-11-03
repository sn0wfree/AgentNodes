"""数据源注册表。

管理数据源注册、fallback 链、源解析。
"""

from __future__ import annotations

import importlib
import logging
from typing import Optional, Type

logger = logging.getLogger(__name__)


# ============================================================
# 注册表
# ============================================================

LOADER_REGISTRY: dict[str, Type] = {}


def register(cls: Type) -> Type:
    """注册数据源 loader"""
    LOADER_REGISTRY[cls.name] = cls
    return cls


def get_loader(name: str) -> Optional[Type]:
    """获取已注册的 loader 类"""
    _ensure_registered()
    return LOADER_REGISTRY.get(name)


def list_loaders() -> list[str]:
    """列出所有已注册的 loader"""
    _ensure_registered()
    return list(LOADER_REGISTRY.keys())


# ============================================================
# 懒加载注册
# ============================================================

_registered = False

_loader_modules = [
    "strategy_research.apps.data_source.tencent_loader",
    "strategy_research.apps.data_source.tushare_loader",
    "strategy_research.apps.data_source.akshare_loader",
    "strategy_research.apps.data_source.ifind_loader",
    "strategy_research.apps.data_source.fred_loader",
    "strategy_research.apps.data_source.yfinance_loader",
    "strategy_research.apps.data_source.eastmoney_loader",
    "strategy_research.apps.data_source.local_loader",
]


def _ensure_registered() -> None:
    """确保所有 loader 模块已导入"""
    global _registered
    if _registered:
        return
    _registered = True
    import logging
    logger = logging.getLogger(__name__)
    for mod_name in _loader_modules:
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            logger.debug("Skipping loader module %s (dependency missing): %s", mod_name, e)


# ============================================================
# Fallback 链
# ============================================================

FALLBACK_CHAINS: dict[str, list[str]] = {
    "a_share":  ["tencent", "eastmoney", "akshare", "tushare", "local"],
    "etf":      ["tencent", "akshare", "tushare", "local"],
    "index":    ["tencent", "akshare", "tushare", "local"],
    "hk":       ["eastmoney", "yfinance", "akshare", "ifind", "local"],
    "us":       ["yfinance", "akshare", "ifind", "local"],
    "macro":    ["fred", "ifind", "akshare", "tushare", "local"],
    "fund":     ["tushare", "akshare", "local"],
    "futures":  ["tushare", "akshare", "local"],
    "forex":    ["akshare", "yfinance", "fred", "local"],
    "crypto":   ["yfinance", "akshare", "local"],
}

# 不允许降级到网络源的 source
_NO_NETWORK_FALLBACK: frozenset[str] = frozenset({"local"})


# ============================================================
# 源解析
# ============================================================

class NoAvailableSourceError(Exception):
    """没有可用的数据源"""
    pass


def resolve_loader(market: str):
    """根据市场类型解析最佳可用 loader。

    遍历 FALLBACK_CHAINS[market]，返回第一个可用的 loader 实例。
    """
    _ensure_registered()
    chain = FALLBACK_CHAINS.get(market, [])
    tried = []

    for name in chain:
        if name not in LOADER_REGISTRY:
            continue
        tried.append(name)
        try:
            loader = LOADER_REGISTRY[name]()
        except Exception as exc:
            logger.debug("loader %s 构造失败: %s", name, exc)
            continue
        if loader.is_available():
            return loader

    raise NoAvailableSourceError(
        f"市场 '{market}' 没有可用的数据源。"
        f"已尝试: {tried or chain}。请检查网络和 API token 配置。"
    )


def resolve_loader_with_fallback(source: str):
    """当指定 source 不可用时，尝试 fallback。"""
    _ensure_registered()

    if source not in LOADER_REGISTRY:
        raise NoAvailableSourceError(f"未知数据源: {source}")

    loader_cls = LOADER_REGISTRY[source]
    try:
        instance = loader_cls()
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Failed to instantiate loader %s: %s", source, e)
        instance = None

    if instance is not None and instance.is_available():
        return loader_cls

    # 不允许降级的 source
    if source in _NO_NETWORK_FALLBACK:
        raise NoAvailableSourceError(
            f"数据源 '{source}' 不可用，且不允许降级到网络源。"
        )

    # 尝试同市场 fallback
    for market in getattr(loader_cls, "markets", []):
        try:
            fallback = resolve_loader(market)
            return type(fallback)
        except NoAvailableSourceError:
            continue

    raise NoAvailableSourceError(
        f"数据源 '{source}' 不可用，且没有找到 fallback。"
    )


def get_loader_or_fallback(source: str):
    """获取 loader，不可用时自动 fallback 到 tushare。

    Unknown sources (not in :data:`LOADER_REGISTRY`) are a hard error:
    silently falling back to tushare for nonsense names like
    ``"xz_abc"`` masks configuration mistakes. Fallback to tushare
    applies only when the requested source is registered but
    temporarily unavailable (e.g. token not set, network down).
    """
    _ensure_registered()

    if source not in LOADER_REGISTRY:
        raise NoAvailableSourceError(f"未知数据源: {source}")

    try:
        return resolve_loader_with_fallback(source)
    except NoAvailableSourceError:
        # 最后手段: 尝试 tushare
        if "tushare" in LOADER_REGISTRY and source != "tushare":
            try:
                instance = LOADER_REGISTRY["tushare"]()
                if instance.is_available():
                    return LOADER_REGISTRY["tushare"]
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug("Tushare fallback failed: %s", e)
        raise
