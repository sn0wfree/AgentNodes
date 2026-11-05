"""MemoryFTS5 — FTS5 全文搜索索引。

存储位置：~/.quantnodes-research/memory/memory_fts5.db
自动 reindex：启动时自动构建索引
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_FTS5_DB = Path.home() / ".quantnodes-research" / "memory" / "memory_fts5.db"

_CREATE_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    path,
    title,
    description,
    body
);
"""

_CREATE_TABLE_RAW = """
CREATE TABLE IF NOT EXISTS memory_raw (
    path TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    body TEXT,
    modified_at REAL
);
"""


class MemoryFTS5:
    """FTS5 全文搜索索引。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else MEMORY_FTS5_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(_CREATE_TABLE_RAW)
            conn.execute(_CREATE_TABLE)

    def reindex(self, entries: list[dict]) -> int:
        """重建索引。

        Args:
            entries: [{"path": str, "title": str, "description": str, "body": str, "modified_at": float}]

        Returns:
            索引的条目数
        """
        with sqlite3.connect(str(self._db_path)) as conn:
            # 清空现有索引
            conn.execute("DELETE FROM memory_raw")
            conn.execute("DELETE FROM memory_fts")

            # 插入新条目
            count = 0
            for entry in entries:
                path = entry.get("path", "")
                title = entry.get("title", "")
                description = entry.get("description", "")
                body = entry.get("body", "")
                modified_at = entry.get("modified_at", 0.0)

                conn.execute(
                    "INSERT INTO memory_raw (path, title, description, body, modified_at) VALUES (?, ?, ?, ?, ?)",
                    (path, title, description, body, modified_at),
                )
                conn.execute(
                    "INSERT INTO memory_fts (path, title, description, body) VALUES (?, ?, ?, ?)",
                    (path, title, description, body),
                )
                count += 1

            return count

    def search(
        self,
        query: str,
        max_results: int = 5,
    ) -> list[dict]:
        """全文搜索。

        Args:
            query: 搜索查询
            max_results: 最大返回数

        Returns:
            [{"path": str, "title": str, "description": str, "score": float}]
        """
        if not query.strip():
            return []

        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT path, title, description,
                           rank AS score
                    FROM memory_fts
                    WHERE memory_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, max_results),
                )
                return [dict(row) for row in cursor]
        except Exception as e:
            logger.warning("FTS5 search failed: %s", e)
            return []

    def get_stats(self) -> dict:
        """获取索引统计。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM memory_raw")
            count = cursor.fetchone()[0]
            return {"count": count, "db_path": str(self._db_path)}
