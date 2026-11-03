"""BaseEngine — bar-by-bar 回测执行引擎。

借鉴自 vibe-trading backtest/engines/base.py，核心执行循环：
  1. _align() — 信号对齐 + 权重归一化
  2. _execute_bars() — 逐 bar 执行 hooks/rebalance/equity
  3. _rebalance() — 开仓/平仓逻辑
  4. _close_position() — 平仓 + PnL 计算
  5. _calc_equity() — 组合权益计算

子类需实现 5 个抽象方法：
  can_execute / round_size / calc_commission / apply_slippage / on_bar
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .market_mixin import MarketMixin
from .models import EquitySnapshot, Position, TradeRecord

logger = logging.getLogger(__name__)


class BaseEngine(ABC, MarketMixin):
    """Bar-by-bar 回测引擎基类。

    继承自 MarketMixin，提供 apply_slippage 和 can_execute 的默认实现。
    子类需实现 2 个抽象方法：round_size / calc_commission
    """

    def __init__(self, config: dict):
        self.config = config
        self.initial_capital: float = config.get("initial_cash", 1_000_000)
        self.default_leverage: float = config.get("leverage", 1.0)
        self.capital: float = self.initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[TradeRecord] = []
        self.equity_snapshots: List[EquitySnapshot] = []
        self._bar_idx: int = 0
        self._active_symbol: str = ""

    # ── 抽象方法（子类必须实现） ──────────────────────

    @abstractmethod
    def round_size(self, raw_size: float, price: float) -> float:
        """整手/精度取整。"""
        ...

    @abstractmethod
    def calc_commission(
        self, size: float, price: float, direction: int, is_open: bool
    ) -> float:
        """佣金计算。is_open=True 为开仓佣金，False 为平仓佣金。"""
        ...

    def on_bar(self, symbol: str, bar: pd.Series, timestamp: pd.Timestamp) -> None:
        """per-bar hooks（funding/liquidation/swap），默认空实现。"""
        pass

    # ── 可选 override（期货等需要） ────────────────────

    def _calc_pnl(
        self, symbol: str, direction: int, size: float, entry_price: float, exit_price: float
    ) -> float:
        """PnL 公式。默认: direction * size * (exit - entry)。"""
        return direction * size * (exit_price - entry_price)

    def _calc_margin(
        self, symbol: str, size: float, price: float, leverage: float
    ) -> float:
        """保证金公式。默认: size * price / leverage。"""
        return size * price / leverage

    def _calc_raw_size(self, symbol: str, target_notional: float, price: float) -> float:
        """原始手数。默认: target_notional / price。"""
        return target_notional / price

    # ── 信号对齐 ──────────────────────────────────────

    @staticmethod
    def _align(
        data_map: Dict[str, pd.DataFrame],
        signal_map: Dict[str, pd.Series],
        codes: List[str],
        optimizer: Optional[Callable] = None,
    ) -> tuple:
        """对齐信号 + 价格 + 收益率。

        Returns:
            (dates, close_df, target_pos, ret_df)
        """
        # 1. 统一日期索引
        all_dates: set = set()
        for c in codes:
            if c in data_map:
                all_dates.update(data_map[c].index)
        dates = pd.DatetimeIndex(sorted(all_dates))

        # 2. close 价格矩阵
        close = pd.DataFrame(index=dates, columns=codes, dtype=float)
        for c in codes:
            if c in data_map:
                close[c] = data_map[c]["close"].reindex(dates)

        # 3. forward-fill (跨市场 limit=10, 单市场 limit=5)
        markets = set()
        for c in codes:
            if c in data_map:
                code_part = c.split(".")[0] if "." in c else c
                markets.add(code_part[:3])
        ffill_limit = 10 if len(markets) > 1 else 5
        close = close.ffill(limit=ffill_limit)

        # 4. 丢弃全 NaN 的列
        all_nan_cols = [c for c in codes if close[c].isna().all()]
        if all_nan_cols:
            codes = [c for c in codes if c not in all_nan_cols]
            close = close[codes]

        # 5. 目标持仓矩阵 — shift 1 bar (next-bar-open)
        pos = pd.DataFrame(0.0, index=dates, columns=codes)
        for c in codes:
            if c in signal_map and c in data_map:
                own_dates = data_map[c].index
                raw = signal_map[c].reindex(own_dates).fillna(0.0).clip(-1.0, 1.0)
                shifted = raw.shift(1).fillna(0.0)
                pos[c] = shifted.reindex(dates).ffill(limit=ffill_limit).fillna(0.0)

        # 6. 收益率矩阵
        ret = close.pct_change().fillna(0.0)

        # 7. 可选优化器
        if optimizer is not None:
            pos = optimizer(ret, pos, dates)

        # 8. 归一化: sum(abs(weights)) <= 1.0
        scale = pos.abs().sum(axis=1).clip(lower=1.0)
        pos = pos.div(scale, axis=0)

        return dates, close, pos, ret

    # ── 逐 bar 执行 ──────────────────────────────────

    def _execute_bars(
        self,
        dates: pd.DatetimeIndex,
        data_map: Dict[str, pd.DataFrame],
        close_df: pd.DataFrame,
        target_pos: pd.DataFrame,
        codes: List[str],
        *,
        progress_every: Optional[int] = None,
    ) -> None:
        """核心执行循环：逐 bar 处理 hooks → rebalance → equity snapshot。"""
        total = max(1, len(dates))
        # keep ≤100 progress lines per backtest regardless of bar count
        every = progress_every if progress_every is not None else 0
        if every == 0:
            every = max(1, total // 100)
        for i, ts in enumerate(dates):
            self._bar_idx = i

            # a. per-bar hooks (funding/liquidation/swap)
            for c in codes:
                if c in data_map and ts in data_map[c].index:
                    self.on_bar(c, data_map[c].loc[ts], ts)

            # b. rebalance
            equity = self._calc_equity(close_df, ts)
            for c in codes:
                try:
                    target_w = float(target_pos.at[ts, c]) if ts in target_pos.index else 0.0
                    df = data_map.get(c)
                    self._rebalance(c, target_w, df, ts, equity)
                except Exception as exc:
                    logger.warning("Rebalance failed for %s at %s: %s", c, ts, exc)
            if progress_every is not None and i % every == 0:
                print(f"[backtest] bar {i + 1}/{total}", flush=True)

            # c. equity snapshot
            snap_equity = self._calc_equity(close_df, ts)
            total_unrealized = 0.0
            if self.positions:
                for p in self.positions.values():
                    cp = self._safe_price(close_df, ts, p.symbol, p.entry_price)
                    total_unrealized += self._calc_pnl(
                        p.symbol, p.direction, p.size, p.entry_price, cp
                    )
            self.equity_snapshots.append(
                EquitySnapshot(
                    timestamp=ts,
                    capital=self.capital,
                    unrealized=total_unrealized,
                    equity=snap_equity,
                    positions=len(self.positions),
                )
            )

        # d. force-close remaining positions
        if len(dates) > 0:
            last_ts = dates[-1]
            for c in list(self.positions.keys()):
                price = self._safe_price(close_df, last_ts, c, self.positions[c].entry_price)
                self._close_position(c, price, last_ts, "end_of_backtest")

    # ── 权益计算 ──────────────────────────────────────

    def _calc_equity(self, close_df: pd.DataFrame, ts: pd.Timestamp) -> float:
        """equity = capital + SUM(margin + unrealized_pnl) per position。"""
        if not self.positions:
            return self.capital

        equity = self.capital
        for sym, pos in self.positions.items():
            cp = self._safe_price(close_df, ts, sym, pos.entry_price)
            margin = self._calc_margin(sym, pos.size, pos.entry_price, pos.leverage)
            unrealized = self._calc_pnl(sym, pos.direction, pos.size, pos.entry_price, cp)
            equity += margin + unrealized
        return equity

    def _safe_price(
        self, close_df: pd.DataFrame, ts: pd.Timestamp, symbol: str, fallback: float
    ) -> float:
        """安全获取当前价格，缺失时用 fallback。"""
        if ts in close_df.index and symbol in close_df.columns:
            val = close_df.at[ts, symbol]
            if pd.notna(val):
                return float(val)
        return fallback

    # ── rebalance 逻辑 ────────────────────────────────

    def _rebalance(
        self,
        symbol: str,
        target_weight: float,
        df: Optional[pd.DataFrame],
        ts: pd.Timestamp,
        equity: float,
    ) -> None:
        """开仓/平仓逻辑。"""
        self._active_symbol = symbol
        target_dir = 1 if target_weight > 1e-9 else (-1 if target_weight < -1e-9 else 0)
        current_pos = self.positions.get(symbol)

        # 无事可做
        if current_pos is None and target_dir == 0:
            return
        if df is None or ts not in df.index:
            return

        bar = df.loc[ts]

        # ── CLOSE if target flat or direction changed ──
        if current_pos is not None:
            need_close = target_dir == 0 or target_dir != current_pos.direction
            if need_close:
                if self.can_execute(symbol, 0, bar):
                    open_price = float(bar.get("open", bar.get("close", 0)))
                    price = self.apply_slippage(open_price, -current_pos.direction)
                    self._close_position(symbol, price, ts, "signal")
                else:
                    return

        # ── OPEN new position ──
        if target_dir != 0 and symbol not in self.positions:
            if not self.can_execute(symbol, target_dir, bar):
                return

            open_price = float(bar.get("open", bar.get("close", 0)))
            if open_price <= 0:
                return

            slipped = self.apply_slippage(open_price, target_dir)
            leverage = self.default_leverage
            target_notional = abs(target_weight) * equity * leverage
            raw_size = self._calc_raw_size(symbol, target_notional, slipped)
            size = self.round_size(raw_size, slipped)
            if size <= 0:
                return

            margin = self._calc_margin(symbol, size, slipped, leverage)
            comm = self.calc_commission(size, slipped, target_dir, is_open=True)

            # capital guard
            if margin + comm > self.capital:
                available = self.capital - comm
                if available <= 0:
                    return
                size = self.round_size(
                    self._calc_raw_size(symbol, available * leverage, slipped), slipped
                )
                if size <= 0:
                    return
                margin = self._calc_margin(symbol, size, slipped, leverage)

            self.capital -= margin + comm
            self.positions[symbol] = Position(
                symbol=symbol,
                direction=target_dir,
                entry_price=slipped,
                entry_time=ts,
                size=size,
                leverage=leverage,
                entry_bar_idx=self._bar_idx,
                entry_commission=comm,
            )

    # ── 平仓逻辑 ──────────────────────────────────────

    def _close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_time: pd.Timestamp,
        reason: str,
    ) -> None:
        """平仓 + 记录 TradeRecord。"""
        self._active_symbol = symbol
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return

        pnl = self._calc_pnl(symbol, pos.direction, pos.size, pos.entry_price, exit_price)
        margin = self._calc_margin(symbol, pos.size, pos.entry_price, pos.leverage)
        pnl_pct = pnl / margin * 100 if margin > 1e-9 else 0.0
        exit_comm = self.calc_commission(pos.size, exit_price, pos.direction, is_open=False)

        self.capital += margin + pnl - exit_comm

        holding_bars = max(self._bar_idx - pos.entry_bar_idx, 0)

        self.trades.append(
            TradeRecord(
                symbol=symbol,
                direction=pos.direction,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                entry_time=pos.entry_time,
                exit_time=exit_time,
                size=pos.size,
                leverage=pos.leverage,
                pnl=pnl,
                pnl_pct=pnl_pct,
                exit_reason=reason,
                holding_bars=holding_bars,
                commission=pos.entry_commission + exit_comm,
            )
        )

    # ── 主入口 ────────────────────────────────────────

    def run_backtest(
        self,
        data_map: Dict[str, pd.DataFrame],
        signal_map: Dict[str, pd.Series],
        codes: List[str],
        bars_per_year: int = 252,
        bench_ret: Optional[pd.Series] = None,
        optimizer: Optional[Callable] = None,
        progress_every: Optional[int] = None,
    ) -> Dict[str, Any]:
        """运行完整回测。

        Args:
            data_map: {code: DataFrame(OHLCV)}
            signal_map: {code: pd.Series(weights)} — from SignalEngine.generate()
            codes: 参与回测的标的列表
            bars_per_year: 年化 bar 数
            bench_ret: 基准日收益率 (optional)
            optimizer: 权重优化器 (optional)
            progress_every: 进度行频率 — 每 N bar 向 stdout 打一行
                ``[backtest] bar i/total``（含 align/metrics 阶段标记）。
                None = 零输出（默认，CLI/工具链行为不变）。
                后台化场景（run.log 捕获 stdout）用它驱动日志停滞判定。

        Returns:
            metrics dict (17 keys)
        """
        from strategy_research.foundation.utils.backtest_metrics import calc_metrics

        def _progress(msg: str) -> None:
            if progress_every is not None:
                print(msg, flush=True)

        # 1. reset state
        self.capital = self.initial_capital
        self.positions.clear()
        self.trades.clear()
        self.equity_snapshots.clear()
        self._bar_idx = 0

        # 2. align
        dates, close_df, target_pos, ret_df = self._align(
            data_map, signal_map, codes, optimizer
        )
        valid_codes = [c for c in codes if c in target_pos.columns]
        _progress(
            f"[backtest] align done: {len(dates)} bars, {len(valid_codes)} codes"
        )

        # 3. execute bars
        self._execute_bars(
            dates, data_map, close_df, target_pos, valid_codes,
            progress_every=progress_every,
        )

        # 4. build equity series
        equity_series = pd.Series(
            [s.equity for s in self.equity_snapshots],
            index=[s.timestamp for s in self.equity_snapshots],
        )

        # 5. metrics
        m = calc_metrics(
            equity_series, self.trades, self.initial_capital, bars_per_year, bench_ret
        )
        _progress("[backtest] metrics done")
        return m


__all__ = ["BaseEngine"]
