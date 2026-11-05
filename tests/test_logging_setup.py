"""Tests for the unified logging entry (configure_logging + dual_log).

契约：docs/guides/logging-unified-entry-design-20261102.md §4。
- 策略表：web/mcp → stderr，script → stdout，cli 严格开关
- 幂等：重复调用不重复挂 handler，外来 handler 不被触碰
- 级别/JSON/文件三开关统一解析（AN_LOG_LEVEL / AN_LOG_JSON / AN_LOG_FILE）
- handler 层双写（借鉴 CodersWheel LoggerHelper）：console + file 同挂
- dual_log：有 handler 走 logger 单份，无 handler print 兜底
"""

from __future__ import annotations

import io
import logging
import sqlite3
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from agentnodes.foundation.observability import (
    DEFAULT_TEXT_FORMAT,
    TraceFilter,
    bind_trace,
    configure_logging,
    dual_log,
)
from agentnodes.foundation.observability.logging_setup import (
    LOG_DIR,
    _HANDLER_MARK,
    resolve_level,
)
from agentnodes.foundation.observability.trace import JsonFormatter

_LOG_ENVS = ("AN_LOG_LEVEL", "AN_LOG_JSON", "AN_LOG_FILE")


def _sr_handlers(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]


@pytest.fixture
def clean_root():
    """Save/restore the real root logger around each test.

    隔离修复（2026-11-03）：setup 时先清掉**其他测试泄漏的** sr handler
    （如 create_app → configure_logging("web") 未随用例拆卸），否则
    「cli 严格模式零新增」断言会被历史残留误伤。
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_sr_level = logging.getLogger("agentnodes").level
    yield root
    for h in list(root.handlers):
        root.removeHandler(h)
        if getattr(h, _HANDLER_MARK, False):
            try:
                h.close()
            except Exception:
                pass
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    logging.getLogger("agentnodes").setLevel(saved_sr_level)


@pytest.fixture(autouse=True)
def clean_log_env(monkeypatch):
    for var in _LOG_ENVS:
        monkeypatch.delenv(var, raising=False)


# ── 策略表：console 流定向 ───────────────────────────────────────────


class TestSurfaceStreams:
    def test_web_uses_stderr(self, clean_root, monkeypatch):
        fake = io.StringIO()
        monkeypatch.setattr(sys, "stderr", fake)
        configure_logging("web")
        (h,) = _sr_handlers(clean_root)
        assert h.stream is fake

    def test_mcp_uses_stderr(self, clean_root, monkeypatch):
        fake = io.StringIO()
        monkeypatch.setattr(sys, "stderr", fake)
        configure_logging("mcp")
        (h,) = _sr_handlers(clean_root)
        assert h.stream is fake

    def test_script_uses_stdout(self, clean_root, monkeypatch):
        fake = io.StringIO()
        monkeypatch.setattr(sys, "stdout", fake)
        configure_logging("script")
        (h,) = _sr_handlers(clean_root)
        assert h.stream is fake

    def test_unknown_surface_raises(self, clean_root):
        with pytest.raises(ValueError, match="surface"):
            configure_logging("carrier-pigeon")


# ── cli 严格开关语义 ─────────────────────────────────────────────────


class TestCliStrict:
    def test_default_touches_nothing(self, clean_root):
        before = list(clean_root.handlers)
        configure_logging("cli")
        assert clean_root.handlers == before
        assert _sr_handlers(clean_root) == []

    def test_sr_log_json_enables(self, clean_root, monkeypatch):
        monkeypatch.setenv("AN_LOG_JSON", "1")
        configure_logging("cli")
        (h,) = _sr_handlers(clean_root)
        assert isinstance(h.formatter, JsonFormatter)

    def test_sr_log_file_enables(self, clean_root, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "agentnodes.foundation.observability.logging_setup.LOG_DIR",
            tmp_path / "logs",
        )
        monkeypatch.setenv("AN_LOG_FILE", "cli.log")
        configure_logging("cli")
        assert len(_sr_handlers(clean_root)) == 2  # console + file

    def test_force_enables(self, clean_root):
        configure_logging("cli", force=True)
        (h,) = _sr_handlers(clean_root)
        assert isinstance(h.formatter, logging.Formatter)
        assert not isinstance(h.formatter, JsonFormatter)


# ── 幂等与外来 handler ───────────────────────────────────────────────


class TestIdempotency:
    def test_reconfigure_does_not_duplicate(self, clean_root):
        configure_logging("web")
        configure_logging("web")
        configure_logging("web")
        assert len(_sr_handlers(clean_root)) == 1

    def test_foreign_handlers_preserved(self, clean_root):
        foreign = logging.Handler()
        clean_root.addHandler(foreign)
        configure_logging("web")
        assert foreign in clean_root.handlers
        assert len(_sr_handlers(clean_root)) == 1

    def test_trace_filter_installed_once(self, clean_root):
        before = sum(1 for f in clean_root.filters if isinstance(f, TraceFilter))
        configure_logging("web")
        configure_logging("script")
        after = sum(1 for f in clean_root.filters if isinstance(f, TraceFilter))
        assert after in (before, before + 1)


# ── 开关解析：级别 / JSON / 文件 ─────────────────────────────────────


class TestSwitches:
    def test_resolve_level_explicit(self):
        assert resolve_level(logging.DEBUG) == logging.DEBUG
        assert resolve_level("warning") == logging.WARNING

    def test_resolve_level_env(self, monkeypatch):
        monkeypatch.setenv("AN_LOG_LEVEL", "debug")
        assert resolve_level() == logging.DEBUG

    def test_resolve_level_env_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("AN_LOG_LEVEL", "LOUD")
        assert resolve_level() == logging.INFO

    def test_default_level_info(self, clean_root):
        configure_logging("web")
        assert clean_root.level == logging.INFO

    def test_json_via_env(self, clean_root, monkeypatch):
        monkeypatch.setenv("AN_LOG_JSON", "true")
        configure_logging("web")
        for h in _sr_handlers(clean_root):
            assert isinstance(h.formatter, JsonFormatter)

    def test_json_off_by_default(self, clean_root):
        configure_logging("web")
        for h in _sr_handlers(clean_root):
            assert not isinstance(h.formatter, JsonFormatter)


# ── P0.5 默认文本格式带 trace 字段 ───────────────────────────────────


class TestDefaultTextFormat:
    def test_format_contains_trace_fields(self):
        fmt = logging.Formatter(DEFAULT_TEXT_FORMAT, datefmt="%H:%M:%S")
        tf = TraceFilter()
        rec = logging.LogRecord(
            "mod.test", logging.INFO, "p", 1, "hello %s", ("world",), None,
        )
        with bind_trace(trace_id="fmt-trace", session_id="fmt-sess",
                        study_id="fmt-study", round_num=3):
            tf.filter(rec)
        line = fmt.format(rec)
        assert "t=fmt-trace" in line
        assert "sid=fmt-sess" in line
        assert "st=fmt-study" in line
        assert "r=3" in line

    def test_format_without_context_uses_dashes(self):
        fmt = logging.Formatter(DEFAULT_TEXT_FORMAT, datefmt="%H:%M:%S")
        rec = logging.LogRecord("mod.test", logging.INFO, "p", 1, "m", (), None)
        TraceFilter().filter(rec)
        line = fmt.format(rec)
        assert "t=-" in line
        assert "sid=-" in line

    def test_configured_output_renders_trace(self, clean_root):
        stream = io.StringIO()
        configure_logging("web", stream=stream)
        with bind_trace(trace_id="live-trace"):
            logging.getLogger("mod.live").info("visible")
        out = stream.getvalue()
        assert "live-trace" in out
        assert "visible" in out


# ── P1 文件日志（handler 层双写） ────────────────────────────────────


class TestFileLogging:
    def test_off_by_default_no_dir_created(self, clean_root, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        monkeypatch.setattr(
            "agentnodes.foundation.observability.logging_setup.LOG_DIR", log_dir,
        )
        configure_logging("web")
        assert len(_sr_handlers(clean_root)) == 1  # console only
        assert not log_dir.exists()

    def test_relative_path_lands_in_log_dir(self, clean_root, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        monkeypatch.setattr(
            "agentnodes.foundation.observability.logging_setup.LOG_DIR", log_dir,
        )
        monkeypatch.setenv("AN_LOG_FILE", "sr_run.log")
        configure_logging("web")
        handlers = _sr_handlers(clean_root)
        assert len(handlers) == 2
        file_handlers = [h for h in handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == str(log_dir / "sr_run.log")
        assert (log_dir / "sr_run.log").exists()

    def test_absolute_path_honored(self, clean_root, tmp_path):
        target = tmp_path / "abs" / "sr.log"
        configure_logging("web", log_file=str(target))
        file_handlers = [
            h for h in _sr_handlers(clean_root)
            if isinstance(h, RotatingFileHandler)
        ]
        assert file_handlers[0].baseFilename == str(target)

    def test_dual_write_console_and_file(self, clean_root, tmp_path):
        """CodersWheel 式 handler 双写：一次 logger.info，console + file 各一份。"""
        stream = io.StringIO()
        target = tmp_path / "dual.log"
        configure_logging("web", stream=stream, log_file=str(target))
        logging.getLogger("mod.dual").info("dual-write line %d", 1)
        for h in clean_root.handlers:
            h.flush()
        assert "dual-write line 1" in stream.getvalue()
        assert "dual-write line 1" in target.read_text(encoding="utf-8")

    def test_reconfigure_reopens_file_not_duplicate(self, clean_root, tmp_path):
        target = tmp_path / "re.log"
        configure_logging("web", log_file=str(target))
        configure_logging("web", log_file=str(target))
        file_handlers = [
            h for h in _sr_handlers(clean_root)
            if isinstance(h, RotatingFileHandler)
        ]
        assert len(file_handlers) == 1


# ── dual_log（call-site 双写收敛） ───────────────────────────────────


class TestDualLog:
    def test_print_fallback_without_handlers(self, clean_root, capsys):
        clean_root.handlers = []  # 模拟未装配进程（CLI 默认模式）
        dual_log("runner", "hello %s", "world")
        out = capsys.readouterr().out
        assert "[RUNNER] hello world" in out

    def test_logger_only_with_handlers(self, clean_root, capsys):
        stream = io.StringIO()
        configure_logging("web", stream=stream)
        dual_log("sched", "queued study=%s", "st-1")
        out = capsys.readouterr().out
        assert out == ""  # 有 handler 不再 print（web/mcp 下不再双份）
        assert "[SCHED] queued study=st-1" in stream.getvalue()

    def test_no_args_message(self, clean_root, capsys):
        clean_root.handlers = []
        dual_log("store", "plain message")
        assert "[STORE] plain message" in capsys.readouterr().out


# ── 旧库升级 + trace 列（P0.5 hanging_events） ───────────────────────


class TestHangingEventsTraceColumns:
    @staticmethod
    def _make_legacy_db(path: Path) -> None:
        conn = sqlite3.connect(str(path))
        conn.execute(
            "CREATE TABLE hanging_events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " event_type TEXT NOT NULL, study_id TEXT, session_id TEXT,"
            " detail TEXT, created_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO hanging_events (event_type, study_id, session_id,"
            " detail, created_at) VALUES ('wallclock_timeout', 'old-study',"
            " 'old-sess', 'legacy row', 1700000000.0)"
        )
        conn.commit()
        conn.close()

    def test_fresh_schema_has_columns(self, tmp_path):
        from agentnodes.foundation.observability.hanging_events import (
            HangingEventsStore,
        )

        with HangingEventsStore(tmp_path / "g.db") as store:
            cols = {
                r["name"] for r in store._conn.execute(
                    "PRAGMA table_info(hanging_events)"
                ).fetchall()
            }
            assert {"trace_id", "round_num"} <= cols

    def test_legacy_db_migrated_and_readable(self, tmp_path):
        from agentnodes.foundation.observability.hanging_events import (
            HangingEventsStore,
        )

        db = tmp_path / "legacy.db"
        self._make_legacy_db(db)
        with HangingEventsStore(db) as store:
            rows = store.list_recent(hours=24 * 365 * 10)
            assert len(rows) == 1
            assert rows[0]["study_id"] == "old-study"
            assert rows[0]["trace_id"] is None
            assert rows[0]["round_num"] is None
            # 新写入走新列
            store.record("log_stall", study_id="new-study", detail="d")
            recent = store.list_recent()
            assert recent[0]["study_id"] == "new-study"

    def test_migration_idempotent(self, tmp_path):
        from agentnodes.foundation.observability.hanging_events import (
            HangingEventsStore,
        )

        db = tmp_path / "legacy.db"
        self._make_legacy_db(db)
        with HangingEventsStore(db):
            pass
        with HangingEventsStore(db) as again:
            cols = [
                r["name"] for r in again._conn.execute(
                    "PRAGMA table_info(hanging_events)"
                ).fetchall()
            ]
            assert cols.count("trace_id") == 1
            assert cols.count("round_num") == 1

    def test_record_explicit_trace_fields(self, tmp_path):
        from agentnodes.foundation.observability.hanging_events import (
            EVENT_WATCHDOG,
            HangingEventsStore,
        )

        with HangingEventsStore(tmp_path / "g.db") as store:
            store.record(
                EVENT_WATCHDOG, study_id="st", session_id="se",
                detail="d", trace_id="tr-9", round_num=7,
            )
            (row,) = store.list_recent()
            assert row["trace_id"] == "tr-9"
            assert row["round_num"] == 7
            rep = store.report()
            assert rep["recent"][0]["trace_id"] == "tr-9"
            assert rep["recent"][0]["round_num"] == 7

    def test_record_autofills_from_trace_context(self, tmp_path):
        from agentnodes.foundation.observability.hanging_events import (
            EVENT_NO_PROGRESS,
            HangingEventsStore,
        )

        with HangingEventsStore(tmp_path / "g.db") as store:
            with bind_trace(trace_id="auto-trace", round_num=2):
                store.record(EVENT_NO_PROGRESS, study_id="st")
            (row,) = store.list_recent()
            assert row["trace_id"] == "auto-trace"
            assert row["round_num"] == 2

    def test_record_event_passes_trace_fields(self, tmp_path, monkeypatch):
        import agentnodes.foundation.observability.hanging_events as he

        captured: dict = {}

        class _FakeStore:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def record(self, event_type, **kw):
                captured.update(kw)
                captured["event_type"] = event_type

        monkeypatch.setattr(he, "HangingEventsStore", _FakeStore)
        he.record_event(
            "chat_attempt_stall", study_id="st", detail="d",
            trace_id="conv-trace", round_num=4,
        )
        assert captured["trace_id"] == "conv-trace"
        assert captured["round_num"] == 4
