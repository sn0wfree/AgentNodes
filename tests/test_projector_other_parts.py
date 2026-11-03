"""Tests for the Projector's file_edit / table / chart / image handlers.

B7-2 (defense-in-depth): the backend's AgentLoop does NOT currently
emit these event types — they're declared in ``event_v2.py`` and
registered in the frontend's SSE listener table, but the runtime
emit sites don't exist. Wiring the projector handlers now means a
future contributor can flip on the emit sites without writing
persistence code. This file pins the projector contract for those
parts.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from strategy_research.api.routers.web_session import _ensure_schema
from strategy_research.kernel.events.event_v2 import EventType, EventV2
from strategy_research.api.session.projector import Projector


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(str(db)) as conn:
        _ensure_schema(conn)
        conn.commit()
    return db


def _make_event(seq: int, event_type: str, message_id: str, **data) -> EventV2:
    return EventV2(
        id=f"evt_{seq}",
        aggregate_id="sess_test",
        seq=seq,
        type=event_type,
        data={"message_id": message_id, **data},
        time_created=1700000000.0 + seq,
    )


def _project(db_path: Path, message_id: str, events: list[EventV2]) -> dict:
    proj = Projector(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        for ev in events:
            conn.execute(
                "INSERT INTO event_log (id, aggregate_id, seq, type, "
                "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
                (ev.id, ev.aggregate_id, ev.seq, ev.type,
                 json.dumps(ev.data, ensure_ascii=False), ev.time_created),
            )
        conn.commit()
    state = proj.project("sess_test")
    parts = {}
    if message_id in state.messages:
        for part_id, part in state.messages[message_id].parts.items():
            parts[part_id] = (part.type, part.data)
    return parts


class TestFileEditHandler:
    def test_basic_persistence(self, db_path: Path) -> None:
        mid = "msg_file_edit"
        events = [
            _make_event(1, EventType.FILE_EDIT, mid, id="fe-1",
                        file_path="/tmp/x.py", old_content="a",
                        new_content="b"),
        ]
        parts = _project(db_path, mid, events)
        assert len(parts) == 1
        part_type, part_data = next(iter(parts.values()))
        assert part_type == "file_edit"
        assert part_data["file_path"] == "/tmp/x.py"
        assert part_data["old_content"] == "a"
        assert part_data["new_content"] == "b"

    def test_id_fallback_to_event_seq(self, db_path: Path) -> None:
        """If the event omits ``id`` we fall back to ``file_edit_<seq>``
        so concurrent edits in the same message don't collide."""
        mid = "msg_file_edit_2"
        events = [
            _make_event(2, EventType.FILE_EDIT, mid,
                        file_path="/a", old_content="x", new_content="y"),
            _make_event(3, EventType.FILE_EDIT, mid,
                        file_path="/b", old_content="p", new_content="q"),
        ]
        parts = _project(db_path, mid, events)
        assert len(parts) == 2
        paths = {d["file_path"] for _, d in parts.values()}
        assert paths == {"/a", "/b"}


class TestTableHandler:
    def test_basic_persistence(self, db_path: Path) -> None:
        mid = "msg_table"
        events = [
            _make_event(1, EventType.TABLE, mid, id="t-1",
                        headers=["col1", "col2"],
                        rows=[["a", "b"], ["c", "d"]],
                        caption="test table"),
        ]
        parts = _project(db_path, mid, events)
        assert len(parts) == 1
        part_type, part_data = next(iter(parts.values()))
        assert part_type == "table"
        assert part_data["headers"] == ["col1", "col2"]
        assert part_data["rows"] == [["a", "b"], ["c", "d"]]
        assert part_data["caption"] == "test table"

    def test_empty_table(self, db_path: Path) -> None:
        """Defensive: a table with empty rows/headers still persists."""
        mid = "msg_table_empty"
        events = [
            _make_event(1, EventType.TABLE, mid, id="t-empty",
                        headers=[], rows=[]),
        ]
        parts = _project(db_path, mid, events)
        assert len(parts) == 1
        _, part_data = next(iter(parts.values()))
        assert part_data["headers"] == []
        assert part_data["rows"] == []


class TestChartHandler:
    def test_basic_persistence(self, db_path: Path) -> None:
        mid = "msg_chart"
        events = [
            _make_event(1, EventType.CHART, mid, id="c-1",
                        chart_type="bar",
                        data=[{"x": 1, "y": 2}, {"x": 3, "y": 4}],
                        title="revenue"),
        ]
        parts = _project(db_path, mid, events)
        assert len(parts) == 1
        part_type, part_data = next(iter(parts.values()))
        assert part_type == "chart"
        assert part_data["chart_type"] == "bar"
        assert part_data["data"] == [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        assert part_data["title"] == "revenue"

    def test_default_chart_type(self, db_path: Path) -> None:
        """If the event omits ``chart_type`` we default to ``bar`` so
        the frontend renderer never crashes on undefined."""
        mid = "msg_chart_default"
        events = [
            _make_event(1, EventType.CHART, mid, id="c-2", data=[]),
        ]
        parts = _project(db_path, mid, events)
        _, part_data = next(iter(parts.values()))
        assert part_data["chart_type"] == "bar"


class TestImageHandler:
    def test_basic_persistence(self, db_path: Path) -> None:
        mid = "msg_image"
        events = [
            _make_event(1, EventType.IMAGE, mid, id="img-1",
                        url="https://example.com/x.png",
                        alt="example"),
        ]
        parts = _project(db_path, mid, events)
        assert len(parts) == 1
        part_type, part_data = next(iter(parts.values()))
        assert part_type == "image"
        assert part_data["url"] == "https://example.com/x.png"
        assert part_data["alt"] == "example"

    def test_no_alt(self, db_path: Path) -> None:
        mid = "msg_image_no_alt"
        events = [
            _make_event(1, EventType.IMAGE, mid, id="img-2",
                        url="https://example.com/y.png"),
        ]
        parts = _project(db_path, mid, events)
        _, part_data = next(iter(parts.values()))
        assert part_data["alt"] is None


# ── Mixed types don't collide (id namespacing) ──────────────────


class TestMixedPartIdNamespacing:
    def test_all_four_types_in_one_message(self, db_path: Path) -> None:
        """Different event types must produce different part types and
        not collide on id. Each type's id falls back to its own
        prefix when the event omits ``id``."""
        mid = "msg_all_four"
        events = [
            _make_event(1, EventType.FILE_EDIT, mid,
                        file_path="/x", old_content="", new_content=""),
            _make_event(2, EventType.TABLE, mid, headers=[], rows=[]),
            _make_event(3, EventType.CHART, mid, data=[]),
            _make_event(4, EventType.IMAGE, mid, url="x"),
        ]
        parts = _project(db_path, mid, events)
        assert len(parts) == 4
        types = {t for t, _ in parts.values()}
        assert types == {"file_edit", "table", "chart", "image"}
        # Each id namespace is distinct (file_edit_1, table_2, chart_3, image_4).
        assert set(parts.keys()) == {"file_edit_1", "table_2", "chart_3", "image_4"}
