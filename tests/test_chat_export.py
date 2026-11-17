"""对话导出格式化（apps/chat/export.py）单元测试——模块此前 0% 覆盖。

只认 duck-typed message（role/content/message_type/created_at/message_id 属性），
测试用 SimpleNamespace 合成标本。
"""
from __future__ import annotations

import json
from types import SimpleNamespace as Msg

from agentnodes.apps.chat.export import (
    FORMATS,
    to_json_rows,
    to_json_text,
    to_markdown,
)


def _samples():
    return [
        Msg(role="user", content="你好", message_type="user",
            created_at=1.0, message_id="m1"),
        Msg(role="assistant", content="你好！需要什么帮助？", message_type="assistant",
            created_at=2.0, message_id="m2"),
        Msg(role="assistant", content="这是摘要", message_type="compaction",
            created_at=3.0, message_id="m3"),
        Msg(role="assistant", content="出错了", message_type="error",
            created_at=4.0, message_id="m4"),
    ]


class TestJsonExport:
    def test_rows_shape(self):
        rows = to_json_rows(_samples())
        assert len(rows) == 4
        assert rows[0] == {
            "role": "user", "content": "你好", "message_type": "user",
            "created_at": 1.0, "message_id": "m1",
        }

    def test_text_is_valid_json_preserving_chinese(self):
        text = to_json_text(_samples()[:1])
        parsed = json.loads(text)
        assert parsed[0]["content"] == "你好"
        assert "你好" in text  # ensure_ascii=False

    def test_empty_messages(self):
        assert to_json_rows([]) == []
        assert to_json_text([]) == "[]"


class TestMarkdownExport:
    def test_user_and_assistant_rendering(self):
        md = to_markdown(_samples()[:2])
        assert "**User:** 你好" in md
        assert "**Assistant:** 你好！需要什么帮助？" in md

    def test_compaction_wrapped_in_divider(self):
        md = to_markdown(_samples()[2:3])
        assert "\n---\n*这是摘要*\n---\n" in md

    def test_error_is_bold(self):
        md = to_markdown(_samples()[3:4])
        assert "**Error:** 出错了" in md

    def test_compaction_truncated_to_200_chars(self):
        long = Msg(role="assistant", content="x" * 500, message_type="compaction",
                   created_at=1.0, message_id="m")
        md = to_markdown([long])
        assert "x" * 200 in md
        assert "x" * 201 not in md


def test_formats_catalog():
    assert FORMATS == frozenset({"markdown", "json"})
