import time

from strategy_research.kernel.memory.persistent import PersistentMemory


class TestMemoryFormatContext:
    def test_format_context_for_prompt_with_results(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        memory.add("momentum", "Momentum works", "feedback", "Momentum feedback")
        context = memory.format_context_for_prompt("momentum")
        assert "<recalled-memories>" in context
        assert "momentum" in context
        assert "</recalled-memories>" in context

    def test_format_context_for_prompt_no_results(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        context = memory.format_context_for_prompt("nonexistent")
        assert context == ""

    def test_format_context_for_prompt_max_results(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        for i in range(10):
            memory.add(f"item_{i}", f"Item {i} content", "project", f"Item {i}")
        context = memory.format_context_for_prompt("item", max_results=3)
        # Should contain at most 3 items
        lines = [l for l in context.split("\n") if l.startswith("- [")]
        assert len(lines) <= 3


class TestMemoryWriteDedup:
    def test_dedup_same_content(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        path1 = memory.add("test", "Same content", "project", "Test")
        path2 = memory.add("test", "Same content", "project", "Test")
        assert path1 == path2

    def test_dedup_different_content(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        path1 = memory.add("test", "Content A", "project", "Test A")
        path2 = memory.add("test", "Content B", "project", "Test B")
        # Same name, different content - overwrites
        assert path1 == path2

    def test_dedup_hash_in_body(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        memory.add("test", "Test content", "project", "Test")
        for entry in memory.list_entries():
            if entry.title == "test":
                assert entry.body.startswith("[hash:")


class TestMemoryRecencyBoost:
    def test_recency_boost_newer_higher(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        memory.add("old", "Old memory content", "project", description="Old")
        # Set old modification time
        for entry in memory.list_entries():
            if entry.title == "old":
                old_time = time.time() - 86400 * 30
                import os
                os.utime(entry.path, (old_time, old_time))
        memory.add("new", "New memory content", "project", description="New")
        results = memory.find_relevant("memory content")
        if len(results) >= 2:
            assert results[0].title == "new"

    def test_recency_boost_with_query(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        memory.add("factor", "Factor analysis results", "project", description="Factor")
        memory.add("strategy", "Strategy backtest results", "project", description="Strategy")
        results = memory.find_relevant("results")
        assert len(results) >= 1


class TestMemoryEdgeCases:
    def test_empty_memory(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        assert memory.find_relevant("test") == []
        assert memory.format_context_for_prompt("test") == ""

    def test_special_characters(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        memory.add("test with spaces", "Content with special chars: @#$%", "project", "Special")
        results = memory.find_relevant("special")
        assert len(results) == 1

    def test_unicode_content(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        memory.add("unicode", "中文内容测试", "project", "Unicode")
        results = memory.find_relevant("中文")
        assert len(results) == 1

    def test_long_content(self, tmp_path):
        memory = PersistentMemory(tmp_path)
        long_content = "A" * 10000
        memory.add("long", long_content, "project", "Long")
        results = memory.find_relevant("long")
        assert len(results) == 1
        assert len(results[0].body) <= 8000  # Truncated
