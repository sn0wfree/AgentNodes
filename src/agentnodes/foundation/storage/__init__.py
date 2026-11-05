"""Shared persistence infrastructure (P2).

``sqlite`` — connection/transaction/JSON/id/migration primitives shared by
the SQLite stores (GoalStore, StudyStore, HypothesisStore, ...).

``data_store`` — duck-typed ``DataStore`` Protocol (capability seam).
2026-11-04（W7 收口）：具体提供方 ``duckdb_store`` 与 ``data_store_registry``
移至 ``apps/``——它们包装研究数据门面 ``apps.db``，语义属 L2；协议留本层
供未来注入。此处不再 re-export 具体实现（否则 L4→L2 re-export 本身违规）。
"""

from . import sqlite
from .data_store import DataStore, get_default_data_store, register_default_data_store

__all__ = [
    "sqlite",
    "DataStore",
    "get_default_data_store",
    "register_default_data_store",
]
