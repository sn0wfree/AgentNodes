"""Canonical ``DataStore`` Protocol (P0-2 Phase E).

P0-2 separates the "data layer" into:
- ``DataStore`` Protocol (this file) — duck-typed facade every consumer
  targets. Add new providers (SQLite, in-memory) without rewriting callers.
- ``DuckDBDataStore`` (``duckdb_store.py``) — wraps the existing
  ``db.py`` functions as methods so the same code path is exercised.
- ``DataStoreRegistry`` (``data_store_registry.py``) — runtime lookup
  mirroring the LLM provider pattern.

P0-2.A scope:
- Protocol defined with all the surfaces the 5 high-frequency callers
  (``config_runner``, ``data_import``, ``data_readiness``,
  ``factor_tools``, ``data_tools``) actually use.
- ``DuckDBDataStore`` wraps ``db.py`` so the default behaviour is
  unchanged (the existing 195+ tests are the equivalence oracle).
- Existing module-level ``db.py`` functions remain (v0.1 backward-compat).

P0-2.B/C/D (later phases) add ``BacktestEngine`` / ``ExecutionSandbox``
Protocols and broaden DI; this file stays focused on persistence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class DataStore(Protocol):
    """Duck-typed persistence facade.

    Mirrors the surface area of ``core/db.py`` so a ``DuckDBDataStore``
    can be a thin shim around it. New providers (SQLite, in-memory)
    implement the same Protocol — consumers stay unchanged.
    """

    # Lifecycle
    def init(self, workspace: Path) -> None:
        """Idempotent table creation (mirrors ``init_db``)."""

    def get_connection(self, workspace: Path, *, read_only: bool = False) -> Any:
        """Return a backend-native connection. For DuckDB this is a
        ``duckdb.DuckDBPyConnection``; for in-memory tests it could be
        a context-manager mock. Callers using raw SQL are aware of the
        backend type; consumers should prefer the typed methods below.
        """

    # Price data
    def save_ohlcv(
        self,
        workspace: Path,
        data_map: dict[str, pd.DataFrame],
        *,
        strategy_name: str = "default",
    ) -> int:
        ...

    def load_price_data(
        self,
        workspace: Path,
        strategy_name: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        ...

    def load_ohlcv_data(
        self,
        workspace: Path,
        strategy_name: str,
        *,
        codes: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        ...

    # Factor data + registry
    def save_factor_data(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        values: pd.DataFrame,
    ) -> bool:
        ...

    def load_factor_data(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        ...

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
        ...

    def list_factors(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        ...

    def remove_factor(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
    ) -> bool:
        ...

    # Backtest results
    def save_backtest_result(
        self,
        workspace: Path,
        *,
        run_id: str,
        strategy_name: str,
        result: dict,
    ) -> bool:
        ...

    def list_backtest_results(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        ...

    def get_backtest_result(
        self,
        workspace: Path,
        run_id: str,
    ) -> Optional[dict]:
        ...

    # Weight / NAV history
    def save_weight_history(
        self,
        workspace: Path,
        *,
        run_id: str,
        weights: pd.DataFrame,
    ) -> bool:
        ...

    def load_weight_history(
        self,
        workspace: Path,
        run_id: str,
    ) -> pd.DataFrame:
        ...

    def save_nav_history(
        self,
        workspace: Path,
        *,
        run_id: str,
        nav: pd.Series,
    ) -> bool:
        ...

    def load_nav_history(
        self,
        workspace: Path,
        run_id: str,
    ) -> pd.Series:
        ...

    # Validation cache
    def cache_validation(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        factor_name: str,
        metrics: dict,
        ttl_seconds: int = 7 * 24 * 3600,
    ) -> bool:
        ...

    def list_validation_cache(
        self,
        workspace: Path,
        *,
        strategy_name: str | None = None,
        factor_name: str | None = None,
    ) -> list[dict]:
        ...

    # Import metadata + fingerprint
    def get_last_import_date(
        self,
        workspace: Path,
        *,
        codes: list[str] | None = None,
    ) -> Optional[float]:
        ...

    def update_import_meta(
        self,
        workspace: Path,
        *,
        codes: list[str],
        import_date: float,
        source: str = "",
        row_count: int = 0,
    ) -> bool:
        ...

    def update_data_fingerprint(
        self,
        workspace: Path,
        *,
        strategy_name: str,
        fingerprint: dict,
    ) -> bool:
        ...

    def get_data_fingerprint(
        self,
        workspace: Path,
        strategy_name: str,
    ) -> Optional[dict]:
        ...


__all__ = ["DataStore"]
