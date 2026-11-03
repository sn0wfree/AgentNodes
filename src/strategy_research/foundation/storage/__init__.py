"""Shared persistence infrastructure (P2).

``sqlite`` — connection/transaction/JSON/id/migration primitives shared by
the SQLite stores (GoalStore, StudyStore, HypothesisStore, ...).

``data_store`` + ``duckdb_store`` + ``data_store_registry`` — P0-2
capability seam: a duck-typed ``DataStore`` Protocol with a DuckDB
default and a Registry so new providers (in-memory, SQLite) can plug in
without rewriting call sites.
"""

from . import data_store_registry, sqlite
from .data_store_registry import (
    DataStoreRegistry,
    get_store,
    register_store,
)

__all__ = [
    "data_store_registry",
    "sqlite",
    "DataStoreRegistry",
    "get_store",
    "register_store",
]
