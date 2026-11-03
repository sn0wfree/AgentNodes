"""SignalEngine 抽象基类 — 策略信号生成接口。

所有策略必须实现 SignalEngine 接口：
  class MyStrategy(SignalEngine):
      def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
          ...

generate() 返回 {code: pd.Series}，每个 Series:
  - index = DatetimeIndex (与 data_map[code].index 对齐)
  - values = target weight in [-1.0, 1.0] (正=做多, 负=做空, 0=平仓)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

import pandas as pd


class SignalEngine(ABC):
    """策略信号生成器的抽象基类。

    子类必须实现 generate() 方法，接收 OHLCV 数据并返回目标权重。
    """

    @abstractmethod
    def generate(
        self,
        data_map: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.Series]:
        """生成目标权重信号。

        Args:
            data_map: {code: DataFrame(OHLCV)}，每个 DataFrame 包含
                open/high/low/close/volume 列，index 为 DatetimeIndex。

        Returns:
            {code: pd.Series}，每个 Series 为目标权重，
            values in [-1.0, 1.0]，index 为 DatetimeIndex。
        """
        ...


class ConstantWeightEngine(SignalEngine):
    """最简单的信号引擎：固定权重。"""

    def __init__(self, weights: Dict[str, float]):
        """
        Args:
            weights: {code: weight}，weight in [-1.0, 1.0]
        """
        self.weights = weights

    def generate(
        self,
        data_map: Dict[str, pd.DataFrame],
    ) -> Dict[str, pd.Series]:
        result: Dict[str, pd.Series] = {}
        for code, df in data_map.items():
            w = self.weights.get(code, 0.0)
            result[code] = pd.Series(w, index=df.index, name=code)
        return result


__all__ = ["SignalEngine", "ConstantWeightEngine"]
