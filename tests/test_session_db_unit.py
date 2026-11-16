"""``kernel/session/db.py`` 连接缓存的单元测试（批次 D1 引入 ``_get_db_at``）。

背景：D1 把 ``_get_db`` 的连接体拆为 ``_get_db_at(db_path)``（按显式路径取
线程缓存），``_get_db()`` 变薄壳。此前这两个函数只有经由 web/reader 的间接
覆盖，本文件给它们**直达**单测 —— 连接复用、按路径隔离、线程隔离、
patch 语义（B2 教训：tests 靠 monkeypatch ``_get_db_path`` 重定向 DB）。

Windows 注意：线程缓存里的连接持有文件句柄，conftest 的 autouse
``_close_session_services`` 会在每个测试后统一 close + clear（B2 复活的那段）；
本文件内**另起线程**创建的连接必须在线程内自行关闭，否则 tmp_path 清理会撞锁。
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from agentnodes.kernel.session import db as kdb


@pytest.fixture
def tmp_db_path(tmp_path):
    """返回一个 tmp 数据库路径；连接由 conftest autouse 统一回收。"""
    return tmp_path / "unit.db"


class TestGetDbAtCache:
    def test_same_path_returns_same_connection(self, tmp_db_path):
        """同路径两次取 → 同一个连接对象（线程内复用，F1-3 语义）。"""
        c1 = kdb._get_db_at(tmp_db_path)
        c2 = kdb._get_db_at(tmp_db_path)
        assert c1 is c2

    def test_different_paths_get_different_connections(self, tmp_path):
        """不同路径 → 不同连接（缓存按路径隔离，测试多工作区语义）。"""
        c1 = kdb._get_db_at(tmp_path / "a.db")
        c2 = kdb._get_db_at(tmp_path / "b.db")
        assert c1 is not c2

    def test_schema_ensured_on_first_connect(self, tmp_db_path):
        """首次连接即建全量 schema（sessions/messages/message_parts/attempts）。"""
        conn = kdb._get_db_at(tmp_db_path)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"sessions", "messages", "message_parts", "attempts"} <= tables

    def test_wal_mode_applied(self, tmp_db_path):
        """连接默认 WAL + busy_timeout（K4：并发读写不再 database is locked）。"""
        conn = kdb._get_db_at(tmp_db_path)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


class TestGetDbDelegation:
    def test_get_db_resolves_patched_path(self, tmp_path, monkeypatch):
        """_get_db() 读**模块全局** _get_db_path —— tests 的 patch 重定向必须生效。

        这是 B2 教训的钉子：解析发生在 db 模块的全局名上（不能内联进
        _get_db_at），否则 monkeypatch 静默失效、测试写进真实用户库。
        """
        target = tmp_path / "patched.db"
        monkeypatch.setattr(kdb, "_get_db_path", lambda: target)
        conn = kdb._get_db()
        assert target.exists(), "patch 后连接落在 patched 路径"
        # 该连接可写且写入的就是 patched 文件
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES ('px', 'u', 't', 1.0, 1.0)"
        )
        conn.commit()
        check = sqlite3.connect(str(target))
        n = check.execute("SELECT COUNT(*) FROM sessions WHERE id='px'").fetchone()[0]
        check.close()
        assert n == 1

    def test_get_db_is_get_db_at_of_resolved_path(self, tmp_path, monkeypatch):
        """_get_db() ≡ _get_db_at(_get_db_path()) —— 薄壳委托，缓存共享。"""
        target = tmp_path / "same.db"
        monkeypatch.setattr(kdb, "_get_db_path", lambda: target)
        assert kdb._get_db() is kdb._get_db_at(target)


class TestThreadIsolation:
    def test_other_thread_gets_its_own_connection(self, tmp_db_path):
        """线程缓存按线程隔离 —— 另一线程拿到的是不同连接对象。"""
        main_conn = kdb._get_db_at(tmp_db_path)
        seen: dict = {}

        def worker():
            conn = kdb._get_db_at(tmp_db_path)
            seen["is_same"] = conn is main_conn
            seen["closed_ok"] = True
            # 线程内自行关闭（conftest 只清主线程的 thread-local）
            cache = getattr(kdb._db_thread_local, "conns", {})
            for path, c in list(cache.items()):
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
                cache.pop(path, None)

        th = threading.Thread(target=worker)
        th.start()
        th.join(timeout=10)
        assert seen.get("closed_ok") is True
        assert seen["is_same"] is False, "另一线程必须拿到独立连接（thread-local 语义）"


class TestPublicSurface:
    def test_shim_reexports_get_db_at(self):
        """B2 垫片必须转出 _get_db_at（契约测试的语义说明版——单独可读）。"""
        from agentnodes.interfaces.api.session import web_session_db as shim

        assert shim._get_db_at is kdb._get_db_at
