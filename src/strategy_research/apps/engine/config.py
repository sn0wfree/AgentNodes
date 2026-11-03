"""Backtest config schema — Pydantic 配置验证。"""

from __future__ import annotations

from typing import List, Optional

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError:
    raise ImportError("pydantic is required for config validation: pip install pydantic")


class BacktestConfigSchema(BaseModel):
    """回测配置验证 schema。"""

    codes: List[str] = Field(..., min_length=1, description="回测标的列表")
    start_date: str = Field(..., description="开始日期 (YYYY-MM-DD)")
    end_date: str = Field(..., description="结束日期 (YYYY-MM-DD)")
    interval: str = Field(default="1D", description="K线周期")
    source: str = Field(default="duckdb", description="数据源")
    initial_cash: float = Field(default=1_000_000, gt=0, description="初始资金")
    leverage: float = Field(default=1.0, ge=1.0, description="杠杆倍数")
    engine: str = Field(default="auto", description="引擎类型")
    signal_engine_path: Optional[str] = Field(default=None, description="信号引擎文件路径")
    validation: Optional[dict] = Field(default=None, description="验证配置")
    optimizer: Optional[str] = Field(default=None, description="优化器类型")

    @classmethod
    def validate_config(cls, config: dict) -> list[str]:
        """Validate a config dict and return list of error messages.

        Returns empty list if config is valid.
        """
        try:
            cls(**config)
            return []
        except ValidationError as e:
            return [err["msg"] for err in e.errors()]


__all__ = ["BacktestConfigSchema"]
