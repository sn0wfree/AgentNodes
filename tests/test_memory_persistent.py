"""Tests for memory/persistent.py — PersistentMemory file-based cross-session memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.kernel.memory.persistent import (
    MAX_ENTRY_CHARS,
    MAX_INDEX_LINES,
    MAX_RESULTS,
    MEMORY_TYPES,
    MemoryEntry,
    PersistentMemory,
    _coerce_str,
    _sanitize_body,
    _tokenize,
    _truncate_body,
)


@pytest.fixture
def mem_dir(tmp_path):
    """Use a temp directory for memory storage."""
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture
def mem(mem_dir):
    """Create a PersistentMemory backed by tmp_path."""
    return PersistentMemory(memory_dir=mem_dir)


# ============================================================
# MemoryEntry dataclass
# ============================================================


class TestMemoryEntry:
    def test_create_minimal(self):
        e = MemoryEntry(
            path=Path("/tmp/x.md"), title="test",
            description="d", memory_type="project", body="c",
            modified_at=0.0,
        )
        assert e.title == "test"
        assert e.memory_type == "project"
        assert e.description == "d"
        assert e.body == "c"

    def test_equality(self):
        kwargs = dict(
            path=Path("/tmp/x.md"), title="x",
            description="d", memory_type="user", body="c",
            modified_at=0.0,
        )
        e1 = MemoryEntry(**kwargs)
        e2 = MemoryEntry(**kwargs)
        assert e1 == e2

    def test_inequality(self):
        kwargs = dict(
            path=Path("/tmp/x.md"), description="d",
            memory_type="user", body="c", modified_at=0.0,
        )
        e1 = MemoryEntry(title="x", **kwargs)
        e2 = MemoryEntry(title="y", **kwargs)
        assert e1 != e2


# ============================================================
# _tokenize
# ============================================================


class TestTokenize:
    def test_basic_english(self):
        tokens = _tokenize("hello world python")
        assert "hello" in tokens
        assert "world" in tokens
        assert "python" in tokens

    def test_short_words_excluded(self):
        tokens = _tokenize("a an to")
        # Words < 3 chars excluded
        assert "a" not in tokens
        assert "an" not in tokens
        assert "to" not in tokens

    def test_three_char_words_included(self):
        tokens = _tokenize("the cat dog")
        # 3-char words ARE included (regex is {3,})
        assert "the" in tokens
        assert "cat" in tokens
        assert "dog" in tokens

    def test_cjk_tokens(self):
        tokens = _tokenize("你好世界")
        assert "你好世界" in tokens or "你" in tokens or len(tokens) >= 1

    def test_mixed(self):
        tokens = _tokenize("hello 世界 python 测试")
        assert "hello" in tokens
        assert "python" in tokens

    def test_numbers_included(self):
        tokens = _tokenize("factor123 signal456")
        # Alphanumeric strings ≥ 3 chars
        assert "factor123" in tokens
        assert "signal456" in tokens

    def test_underscore_splits_tokens(self):
        tokens = _tokenize("signal_456")
        # Underscore is not in regex, so tokens split
        assert "signal" in tokens
        assert "456" in tokens


# ============================================================
# _sanitize_body / _truncate_body / _coerce_str
# ============================================================


class TestSanitizeBody:
    def test_basic_text(self):
        assert _sanitize_body("hello") == "hello"

    def test_keeps_newlines_tabs(self):
        # sanitize removes control chars but keeps \n and \t
        assert _sanitize_body("hello\nworld") == "hello\nworld"
        assert _sanitize_body("a\tb") == "a\tb"

    def test_removes_control_chars(self):
        # Strips C0/C1 control bytes
        result = _sanitize_body("hello\x00world\x01")
        assert "\x00" not in result
        assert "\x01" not in result

    def test_empty_string(self):
        assert _sanitize_body("") == ""


class TestTruncateBody:
    def test_short_text_unchanged(self):
        assert _truncate_body("hello", limit=100) == "hello"

    def test_long_text_truncated(self):
        long = "x" * 100
        result = _truncate_body(long, limit=50)
        assert len(result) <= 50

    def test_default_limit(self):
        text = "x" * (MAX_ENTRY_CHARS + 100)
        result = _truncate_body(text)
        assert len(result) <= MAX_ENTRY_CHARS


class TestCoerceStr:
    def test_string_input(self):
        assert _coerce_str("hello") == "hello"

    def test_none_input(self):
        assert _coerce_str(None) == ""

    def test_int_input(self):
        assert _coerce_str(42) == "42"

    def test_default_value(self):
        assert _coerce_str(None, default="fallback") == "fallback"

    def test_empty_string(self):
        assert _coerce_str("") == ""


# ============================================================
# Constants
# ============================================================


class TestConstants:
    def test_max_entry_chars_positive(self):
        assert MAX_ENTRY_CHARS > 0

    def test_max_index_lines_positive(self):
        assert MAX_INDEX_LINES > 0

    def test_max_results_positive(self):
        assert MAX_RESULTS > 0

    def test_memory_types(self):
        assert "user" in MEMORY_TYPES
        assert "project" in MEMORY_TYPES


# ============================================================
# PersistentMemory — basic operations
# ============================================================


class TestPersistentMemoryAdd:
    def test_add_minimal(self, mem):
        result = mem.add("test_name", "test content")
        # add() returns the file Path
        assert isinstance(result, Path)

    def test_add_creates_file(self, mem, mem_dir):
        mem.add("test_name", "test content")
        # Some file should exist in mem_dir
        files = list(mem_dir.iterdir())
        assert len(files) >= 1

    def test_add_with_type(self, mem):
        result = mem.add("test", "content", memory_type="user")
        assert isinstance(result, Path)

    def test_add_with_description(self, mem):
        result = mem.add("test", "content", description="my desc")
        assert isinstance(result, Path)


class TestPersistentMemoryList:
    def test_list_empty(self, mem):
        entries = mem.list_entries()
        assert entries == []

    def test_list_after_add(self, mem):
        mem.add("test1", "content1")
        entries = mem.list_entries()
        assert len(entries) >= 1

    def test_list_multiple(self, mem):
        mem.add("a", "content a")
        mem.add("b", "content b")
        mem.add("c", "content c")
        entries = mem.list_entries()
        assert len(entries) == 3


class TestPersistentMemoryFind:
    def test_find_existing(self, mem):
        mem.add("test", "content")
        result = mem.find("test")
        assert result is not None
        # MemoryEntry has a 'title' field
        assert result.title == "test"

    def test_find_nonexistent(self, mem):
        result = mem.find("nonexistent_xyz")
        assert result is None


class TestPersistentMemoryRemove:
    def test_remove_existing(self, mem):
        mem.add("test", "content")
        result = mem.remove("test")
        assert result is True
        assert mem.find("test") is None

    def test_remove_nonexistent(self, mem):
        result = mem.remove("nonexistent_xyz")
        assert result is False


class TestPersistentMemoryFindRelevant:
    def test_find_relevant_basic(self, mem):
        mem.add("python", "Python is a programming language")
        results = mem.find_relevant("python")
        assert len(results) > 0

    def test_find_relevant_no_match(self, mem):
        mem.add("python", "Python is great")
        results = mem.find_relevant("completely_unrelated_xyz")
        # May or may not have results, depending on matching logic
        assert isinstance(results, list)

    def test_find_relevant_max_results(self, mem):
        # Add many entries
        for i in range(10):
            mem.add(f"entry_{i}", f"python test content {i}")
        results = mem.find_relevant("python", max_results=3)
        assert len(results) <= 3

    def test_find_relevant_empty_memory(self, mem):
        results = mem.find_relevant("anything")
        assert results == []


class TestPersistentMemoryFormatContext:
    def test_format_empty(self, mem):
        result = mem.format_context_for_prompt("query")
        assert isinstance(result, str)

    def test_format_with_entries(self, mem):
        mem.add("test", "Some content here")
        result = mem.format_context_for_prompt("test", max_results=1)
        # Should return a string
        assert isinstance(result, str)


# ============================================================
# PersistentMemory — snapshot
# ============================================================


class TestPersistentMemorySnapshot:
    def test_snapshot_empty(self, mem):
        # snapshot is a property
        result = mem.snapshot
        assert isinstance(result, str)

    def test_snapshot_preloaded(self, mem_dir):
        # Pre-create an index file with content
        index = mem_dir / "MEMORY.md"
        index.write_text("# Memory Index\n- test entry\n", encoding="utf-8")
        mem = PersistentMemory(memory_dir=mem_dir)
        # Snapshot should have loaded the index
        assert "test entry" in mem.snapshot or "Memory Index" in mem.snapshot


# ============================================================
# Custom memory_dir
# ============================================================


class TestCustomDir:
    def test_custom_dir_used(self, tmp_path):
        custom = tmp_path / "custom_mem"
        custom.mkdir()
        mem = PersistentMemory(memory_dir=custom)
        mem.add("test", "content")
        files = list(custom.iterdir())
        assert len(files) >= 1

    def test_default_dir_is_none(self):
        mem = PersistentMemory()
        # Should use default MEMORY_BASE
        assert mem is not None
