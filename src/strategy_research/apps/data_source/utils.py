"""数据源工具函数。

Token 管理、符号检测、共享工具。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

# ============================================================
# Token 管理
# ============================================================

def load_tokens(workspace_path: Optional[Path] = None) -> dict:
    """从 .env 文件加载 token。

    查找顺序:
    1. workspace_path/.env
    2. ~/.quantnodes/strategy_research/.env
    3. 环境变量
    """
    tokens = {}

    # 从 .env 文件加载
    env_paths = []
    if workspace_path:
        env_paths.append(workspace_path / ".env")
    env_paths.append(Path.home() / ".quantnodes" / "strategy_research" / ".env")

    for env_path in env_paths:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key not in tokens:  # 不覆盖已有的
                        tokens[key] = value

    # 从环境变量补充
    for key in ["TUSHARE_TOKEN", "IFIND_MCP_TOKEN", "FRED_API_KEY"]:
        if key not in tokens:
            env_val = os.environ.get(key)
            if env_val:
                tokens[key] = env_val

    return tokens


def get_token(tokens: dict, key: str) -> Optional[str]:
    """获取 token，返回 None 表示未配置。"""
    value = tokens.get(key, "")
    if not value or value in ("your_token_here", "your_api_key_here", ""):
        return None
    return value


# ============================================================
# 符号检测 (复用自 vibe-trading)
# ============================================================

def is_a_share(code: str) -> bool:
    """检测 A 股代码 (000001.SZ, 600519.SH, 430139.BJ)"""
    return bool(re.match(r"^\d{6}\.(SZ|SH|BJ)$", code))


def is_etf(code: str) -> bool:
    """检测 ETF 代码 (159915.SZ, 518880.SH)"""
    if not is_a_share(code):
        return False
    digits = code.split(".")[0]
    # SH: 50/51/52/56/58, SZ: 15/16
    return (
        (digits.startswith("50") or digits.startswith("51") or
         digits.startswith("52") or digits.startswith("56") or
         digits.startswith("58")) or
        (digits.startswith("15") or digits.startswith("16"))
    )


def is_index(code: str) -> bool:
    """检测指数代码 (000300.SH, 399006.SZ)"""
    if code.endswith(".SH"):
        return code.split(".")[0].startswith("000")
    if code.endswith(".SZ"):
        return code.split(".")[0].startswith("399")
    return False


def is_hk(code: str) -> bool:
    """检测港股代码 (00700.HK)"""
    return bool(re.match(r"^\d{3,5}\.HK$", code))


def is_us(code: str) -> bool:
    """检测美股代码 (AAPL.US)"""
    return bool(re.match(r"^[A-Z]+\.US$", code))


def is_forex(code: str) -> bool:
    """检测外汇代码 (EUR/USD)"""
    return bool(re.match(r"^[A-Z]{3}/[A-Z]{3}$", code))


def is_crypto(code: str) -> bool:
    """检测加密货币代码 (BTC-USDT)"""
    return bool(re.match(r"^[A-Z]+-USDT$", code))


def is_fred_series(code: str) -> bool:
    """检测 FRED 系列 ID (DGS10, CPIAUCSL)"""
    return bool(re.match(r"^[A-Z0-9]{1,20}$", code))


def detect_market(code: str) -> str:
    """自动检测代码所属市场。

    正则单一来源：``core/utils/market_detection.py``（引擎层标签）。
    这里映射回 data_source 层标签（etf/index 细分、us/hk 简写、
    macro），供 LOADER_REGISTRY 选择 loader。
    """
    from strategy_research.core.utils.market_detection import detect_market as _detect

    market = _detect(code)
    if market == "unknown":
        # FRED 系列（如 DGS10, CPIAUCSL）——纯字母数字串但非股票代码。
        # 先于默认值判定；含数字的串不会被 us_equity 吞掉。
        if is_fred_series(code):
            return "macro"
        return "a_share"  # 默认
    if market == "a_share":
        if is_etf(code):
            return "etf"
        if is_index(code):
            return "index"
        return "a_share"
    if market == "us_equity":
        return "us"
    if market == "hk_equity":
        return "hk"
    # crypto / forex / futures / fund / india_equity 标签两层一致
    return market


def normalize_code(code: str, source: str) -> str:
    """将代码标准化为各数据源所需的格式"""
    if source == "tushare":
        return code  # Tushare 使用 000001.SZ 格式
    elif source == "akshare":
        if is_a_share(code) or is_etf(code) or is_index(code):
            return code.split(".")[0]  # 去掉后缀
        return code
    elif source == "tencent":
        if is_a_share(code):
            suffix = "sh" if code.endswith(".SH") else "sz"
            return suffix + code.split(".")[0]
        return code
    elif source == "yfinance":
        if is_us(code):
            return code.split(".")[0]  # AAPL.US -> AAPL
        if is_hk(code):
            return code.split(".")[0].zfill(5) + ".HK"  # 700.HK -> 00700.HK
        return code
    elif source == "fred":
        return code  # FRED 使用系列 ID
    return code
