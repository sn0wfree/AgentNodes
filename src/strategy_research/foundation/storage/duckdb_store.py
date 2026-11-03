"""DuckDBDataStore — default ``DataStore`` provider.

Wraps the existing ``core.db.py`` module-level functions so every call
site that already does ``from .db import save_ohlcv_to_db`` keeps working
unmodified. The wrapper is intentionally a no-op layer — each method
just delegates to the legacy function — so the 195+ existing tests
remain the equivalence oracle.

Why a wrapper instead of rewriting ``db.py``:
- The legacy module is 1165 lines with 30+ call sites. A wholesale
  rewrite would explode this PR's blast radius.
- v0.1 goal: stand up the Protocol + a working provider. Future phases
  can migrate individual methods to native SQL once we have DI tooling
  (P0-2.D) and tests for non-DuckDB backends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, cast

import pandas as pd

# 绝对路径（2026-11-03 W4c）：本模块原在 core/storage/，用 `from ...core import db`
# 依赖所在深度；搬入 foundation/storage/ 后该相对路径只是「巧合正确」。
# db 属 apps（W4d 搬迁），此处先用旧路径，届时前缀改写追平。
from strategy_research.apps import db as _db


class DuckDBDataStore:
    """Default DataStore that delegates to ``core.db`` functions.

    All methods take ``workspace`` as the first positional argument (the
    Protocol's contract). ``init_db`` runs lazily on first write; reads
    no-op if the workspace is uninitialised.
    """

    # Lifecycle
    def init(self, workspace: Path) -> None:
        _db.init_db(workspace)

    def get_connection(self, workspace: Path, *, read_only: bool = False) -> Any:
        return _db.get_connection(workspace, read_only=read_only)

    # Price data
    def save_ohlcv(
        self,
        workspace: Path,
        data_map: dict[str, pd.DataFrame],
        *,
        strategy_name: str = "default",
    ) -> int:
        return _db.save_ohlcv_to_db(
            workspace,
            data_map,
            strategy_name=strategy_name,
        )

    def load_price_data(
        self,
        workspace: Path,
        strategy_name: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return _db.load_price_data(
            workspace,
            strategy_name,
            start_date=start_date,
            end_date=end_date,
        )

    def load_ohlcv_data(
        self,
        workspace: Path,
        strategy_name: str,
        *,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        # ``load_ohlcv_data`` legacy signature is positional + uses
        # ``codes`` as the third arg (after start_date). Pass through
        # so callers that migrated to the Protocol get the same shape.
        return _db.load_ohlcv_data(
            workspace,
            strategy_name,
            codes=codes,
            start_date=start_date,
            end_date=end_date,
        )

    # Factor data + registry
    def save_factor_data(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        values: pd.DataFrame,
    ) -> bool:
        return _db.save_factor_data(workspace, strategy_name, factor_name, values)

    def load_factor_data(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return _db.load_factor_data(
            workspace,
            strategy_name=strategy_name,
            factor_name=factor_name,
            start_date=start_date,
            end_date=end_date,
        )

    def register_factor(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        formula: str,
        description: str = "",
        tags: list[str] | None = None,
        info_channel: str = "",
        parent_hypothesis_id: str = "",
    ) -> bool:
        # Legacy signature has ``factor_code`` (the formula body) plus
        # ``source``; ``description`` and ``tags`` are not in the legacy
        # schema, so we fold them into ``source`` for parity.
        legacy_source = description
        if tags:
            legacy_source = (legacy_source + " " if legacy_source else "") + \
                "tags:" + ",".join(tags)
        return _db.register_factor(
            workspace,
            strategy_name=strategy_name,
            factor_name=factor_name,
            factor_code=formula,
            source=legacy_source,
            info_channel=info_channel,
            parent_hypothesis_id=parent_hypothesis_id,
        )

    def list_factors(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        # The legacy ``get_factors`` only filters by strategy_name and
        # has no ``tags`` column; ``register_factor`` encodes tags into
        # ``source`` as ``tags:t1,t2`` (parse that here).
        result = (
            _db.get_factors(workspace, strategy_name)
            if strategy_name
            else _db.get_factors(workspace, "")
        )
        if tags:
            tag_set = set(tags)
            filtered: list[dict] = []
            for f in result:
                source = f.get("source", "")
                parsed_tags: set[str] = set()
                for token in source.split():
                    if token.startswith("tags:"):
                        parsed_tags = set(token[len("tags:"):].split(","))
                        break
                if tag_set.issubset(parsed_tags):
                    filtered.append(f)
            result = filtered
        return result

    def remove_factor(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
    ) -> bool:
        return _db.remove_factor(workspace, strategy_name, factor_name)

    # Backtest results
    def save_backtest_result(
        self,
        workspace: Path,
        *,
        run_id: str,
        strategy_name: str,
        result: dict,
    ) -> bool:
        r = result or {}
        return _db.save_backtest_result(
            workspace,
            strategy_name,
            run_id,
            commit_hash=str(r.get("commit_hash", "")),
            action=str(r.get("action", "")),
            goal_metric=float(r.get("goal_metric", 0.0) or 0.0),
            calmar=float(r.get("calmar", 0.0) or 0.0),
            sharpe=float(r.get("sharpe", 0.0) or 0.0),
            max_dd=float(r.get("max_dd", 0.0) or 0.0),
            ann_return=float(r.get("ann_return", 0.0) or 0.0),
            ann_vol=float(r.get("ann_vol", 0.0) or 0.0),
            sortino=float(r.get("sortino", 0.0) or 0.0),
            turnover=float(r.get("turnover", 0.0) or 0.0),
            factors_added=int(r.get("factors_added", 0) or 0),
            factors_removed=int(r.get("factors_removed", 0) or 0),
            params_changed=int(r.get("params_changed", 0) or 0),
            status=str(r.get("status", "pending") or "pending"),
            description=str(r.get("description", "") or ""),
        )

    def list_backtest_results(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        results = _db.get_backtest_results(
            workspace, strategy_name or "", limit if limit is not None else 100
        )
        if status is not None:
            results = [r for r in results if r.get("status") == status]
        return results

    def get_backtest_result(
        self,
        workspace: Path,
        run_id: str,
    ) -> Optional[dict]:
        # Legacy ``get_best_backtest`` is per-strategy; lookup-by-run_id
        # is approximated by scanning list_backtest_results. When this
        # becomes a hot path, add a dedicated ``get_backtest_result``
        # function to ``db.py``.
        for r in self.list_backtest_results(workspace):
            if r.get("run_id") == run_id:
                return r
        return None

    # Weight / NAV history
    def save_weight_history(
        self,
        workspace: Path,
        *,
        run_id: str,
        weights: "pd.DataFrame | list",
        strategy_name: str = "",
    ) -> bool:
        # Accept both the Protocol's DataFrame and db.py's
        # list[tuple[Timestamp, dict[str, float]]].
        if isinstance(weights, pd.DataFrame):
            history = [
                (cast(pd.Timestamp, pd.Timestamp(str(ts))),
                 {str(k): float(v) for k, v in row.items() if pd.notna(v)})
                for ts, row in weights.iterrows()
            ]
        else:
            history = list(weights)
        return _db.save_weight_history(workspace, strategy_name, run_id, history)

    def load_weight_history(
        self,
        workspace: Path,
        run_id: str,
        *,
        strategy_name: str = "",
    ) -> pd.DataFrame:
        return _db.load_weight_history(workspace, strategy_name, run_id)

    def save_nav_history(
        self,
        workspace: Path,
        *,
        run_id: str,
        nav: pd.Series,
        strategy_name: str = "",
    ) -> bool:
        return _db.save_nav_history(workspace, strategy_name, run_id, nav)

    def load_nav_history(
        self,
        workspace: Path,
        run_id: str,
        *,
        strategy_name: str = "",
    ) -> pd.Series:
        return _db.load_nav_history(workspace, strategy_name, run_id)

    # Validation cache
    def cache_validation(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        metrics: dict,
        ttl_seconds: int = 7 * 24 * 3600,  # noqa: ARG002 — legacy schema has no TTL
    ) -> bool:
        m = metrics or {}
        scores = m.get("scores") if isinstance(m.get("scores"), dict) else {}
        return _db.cache_validation(
            workspace,
            strategy_name,
            factor_name,
            factor_code=str(m.get("factor_code", factor_name)),
            ic_mean=float(m.get("ic_mean", 0.0) or 0.0),
            ic_std=float(m.get("ic_std", 0.0) or 0.0),
            ir=float(m.get("ir", 0.0) or 0.0),
            rank_ic_mean=float(m.get("rank_ic_mean", 0.0) or 0.0),
            ic_decay_1d=float(m.get("ic_decay_1d", 0.0) or 0.0),
            ic_decay_5d=float(m.get("ic_decay_5d", 0.0) or 0.0),
            ic_decay_20d=float(m.get("ic_decay_20d", 0.0) or 0.0),
            scores=scores,
            overall_score=float(m.get("overall_score", 0.0) or 0.0),
            is_valid=bool(m.get("is_valid", False)),
            fail_reasons=str(m.get("fail_reasons", "") or ""),
            source=str(m.get("source", "") or ""),
            data_fingerprint=str(m.get("data_fingerprint", "") or ""),
        )

    def list_validation_cache(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        factor_name: str | None = None,
    ) -> list[dict]:
        if strategy_name is None:
            return []
        results = _db.get_validation_cache(workspace, strategy_name)
        if factor_name is not None:
            results = [r for r in results if r.get("factor_name") == factor_name]
        return results

    # Import metadata + fingerprint
    def get_last_import_date(
        self,
        workspace: Path,
        *,
        codes: list[str] | None = None,
        strategy_name: str = "",
    ) -> Optional[float]:
        if not codes:
            return None
        # Legacy API is per-asset; take the earliest last_date across codes.
        dates = []
        for code in codes:
            d = _db.get_last_import_date(workspace, strategy_name, code)
            if d is not None:
                dates.append(d)
        return min(dates) if dates else None

    def update_import_meta(
        self,
        workspace: Path,
        *,
        codes: list[str],
        import_date: float,
        source: str = "",
        row_count: int = 0,  # noqa: ARG002 — legacy schema has no row_count
        strategy_name: str = "",
    ) -> bool:
        ok = True
        last_date = str(import_date)
        for code in codes or []:
            ok = _db.update_import_meta(
                workspace, strategy_name, code, last_date, source
            ) and ok
        return ok

    def update_data_fingerprint(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        fingerprint: dict,
        table_name: str = "ohlcv",
        row_count: int = 0,
    ) -> bool:
        import json as _json

        fp = fingerprint or {}
        # db.py stores fingerprint as a string column.
        fp_str = fp if isinstance(fp, str) else _json.dumps(fp, sort_keys=True, default=str)
        return _db.update_data_fingerprint(
            workspace, strategy_name, table_name, fp_str, row_count
        )

    def get_data_fingerprint(
        self,
        workspace: Path,
        strategy_name: str,
    ) -> Optional[dict]:
        return _db.get_data_fingerprint(workspace, strategy_name)


__all__ = ["DuckDBDataStore"]
