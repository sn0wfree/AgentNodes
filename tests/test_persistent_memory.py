"""PersistentMemory 单元测试——文件式持久记忆的增删查/索引/检索。

覆盖 docs/archive/2026-10/coverage-90-plan-20261030.md §3.2：`memory/persistent.py` 原
79.5%，此前**无任何测试文件**。全部操作都是纯文件 IO（markdown +
frontmatter 索引），用 tmp_path 即可完整覆盖。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.memory.persistent import (
    PersistentMemory,
    _coerce_str,
    _sanitize_body,
    _tokenize,
    _truncate_body,
)


@pytest.fixture()
def mem(tmp_path: Path) -> PersistentMemory:
    return PersistentMemory(memory_dir=tmp_path / "memory")


def _entry_text(name="factor_notes", mtype="project", description="动量因子笔记",
                body="动量因子回测记录。") -> str:
    return (
        f"---\nname: {name}\ntype: {mtype}\n"
        f"description: {description}\n---\n{body}\n"
    )


# ── 纯函数 ───────────────────────────────────────────────


def test_tokenize_lowercases_and_splits():
    assert _tokenize("Hello World momentum") == {"hello", "world", "momentum"}


def test_sanitize_body_strips_control_chars_keeps_newline():
    """真实契约：剔除 C0/C1 控制字节，保留 \\n 与 \\t（不改 markdown）。"""
    dirty = "# Title\x00\x1b\n---\nbody\x07"
    assert _sanitize_body(dirty) == "# Title\n---\nbody"


def test_sanitize_body_keeps_tab():
    assert "\t" in _sanitize_body("col1\tcol2")


def test_truncate_body_limits():
    assert len(_truncate_body("x" * 500, limit=100)) <= 100


def test_truncate_body_short_passthrough():
    assert _truncate_body("short", limit=100) == "short"


def test_truncate_body_includes_marker():
    out = _truncate_body("x" * 500, limit=100)
    assert "truncated" in out


def test_coerce_str():
    assert _coerce_str(123) == "123"
    assert _coerce_str(None, default="fb") == "fb"
    assert _coerce_str(True) == "true"
    assert _coerce_str(["a", "b"]) == "a, b"
    assert _coerce_str("kept as-is") == "kept as-is"


# ── add / list / find ────────────────────────────────────


def test_add_creates_markdown_and_index(mem):
    mem.add("factor_notes", "动量因子回测记录。", memory_type="project")
    files = [p.name for p in mem._dir.glob("*.md")]
    assert any("factor_notes" in f for f in files)
    assert (mem._dir / "MEMORY.md").exists()


def test_list_entries_parses_frontmatter(mem):
    mem.add("factor_notes", "动量因子回测记录。", memory_type="project",
            description="动量因子笔记")
    entries = mem.list_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e.title == "factor_notes"
    assert e.memory_type == "project"
    assert "动量" in e.body


def test_find_by_title(mem):
    mem.add("factor_notes", "动量因子回测记录。")
    assert mem.find("factor_notes") is not None
    assert mem.find("nonexistent") is None


def test_find_by_stem_suffix(mem):
    """索引里的名字是 {type}_{slug} 形式 → 按 slug 后缀也能找到。"""
    mem.add("project_factor_notes", "内容内容。")
    assert mem.find("factor_notes") is not None


def test_find_empty_name_returns_none(mem):
    assert mem.find("") is None
    assert mem.find("   ") is None


# ── snapshot ─────────────────────────────────────────────


def test_snapshot_is_frozen_at_init(tmp_path: Path):
    mem = PersistentMemory(memory_dir=tmp_path)
    frozen = mem.snapshot
    mem.add("later", "后写入的内容。")
    # 快照不随 add 更新（设计：下一会话才生效）
    assert mem.snapshot == frozen


def test_snapshot_loaded_from_existing_index(tmp_path: Path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "MEMORY.md").write_text("# Memory Index\n- [old](old.md)\n" + "~" * 10,
                                 encoding="utf-8")
    mem = PersistentMemory(memory_dir=d)
    assert "old" in mem.snapshot


# ── remove ───────────────────────────────────────────────


def test_remove_deletes_file_and_rebuilds_index(mem):
    mem.add("factor_notes", "内容。")
    entry = mem.find("factor_notes")
    assert mem.remove_entry(entry) is True
    assert mem.find("factor_notes") is None


def test_remove_missing_file_still_rebuilds_index(mem):
    """unlink(missing_ok=True) → 幽灵文件也算删除成功，索引仍重建。"""
    entry = SimpleNamespace(path=mem._dir / "ghost.md")
    assert mem.remove_entry(entry) is True


def test_remove_by_name(mem):
    mem.add("factor_notes", "内容。")
    assert mem.remove("factor_notes") is True
    assert mem.remove("factor_notes") is False  # 已删


from types import SimpleNamespace  # noqa: E402


# ── find_relevant（关键词回退；semantic 依赖缺省跳过）────


def test_find_relevant_keyword_match(mem):
    mem.add("momentum_notes", "动量因子 momentum 回测。")
    mem.add("value_notes", "价值因子 value 研究。")
    hits = mem.find_relevant("momentum 动量", max_results=2)
    assert hits and hits[0].title == "momentum_notes"


def test_find_relevant_empty_dir(mem):
    assert mem.find_relevant("anything") == []


def test_find_relevant_max_results(mem):
    for i in range(5):
        mem.add(f"note{i}", f"动量 momentum 主题 {i}。")
    hits = mem.find_relevant("动量", max_results=2)
    assert len(hits) <= 2


def test_format_context_for_prompt(mem):
    mem.add("momentum_notes", "动量因子回测记录，包含 IC 与换手率。")
    text = mem.format_context_for_prompt("动量")
    assert isinstance(text, str)
    assert "momentum_notes" in text


def test_format_context_for_prompt_empty(mem):
    assert mem.format_context_for_prompt("动量") == ""
