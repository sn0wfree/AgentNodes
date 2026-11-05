from agentnodes.kernel.memory.fts5 import MemoryFTS5


class TestMemoryFTS5Init:
    def test_init_creates_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        MemoryFTS5(db_path)
        assert db_path.exists()

    def test_init_default_path(self):
        fts5 = MemoryFTS5()
        assert fts5._db_path.name == "memory_fts5.db"


class TestMemoryFTS5Reindex:
    def test_reindex_empty(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        count = fts5.reindex([])
        assert count == 0

    def test_reindex_single(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        entries = [
            {"path": "test.md", "title": "Test", "description": "A test", "body": "Hello", "modified_at": 0.0},
        ]
        count = fts5.reindex(entries)
        assert count == 1

    def test_reindex_multiple(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        entries = [
            {"path": "a.md", "title": "A", "description": "First", "body": "Alpha", "modified_at": 0.0},
            {"path": "b.md", "title": "B", "description": "Second", "body": "Beta", "modified_at": 1.0},
            {"path": "c.md", "title": "C", "description": "Third", "body": "Gamma", "modified_at": 2.0},
        ]
        count = fts5.reindex(entries)
        assert count == 3

    def test_reindex_clears_old(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        fts5.reindex([
            {"path": "old.md", "title": "Old", "description": "Old", "body": "Old content", "modified_at": 0.0},
        ])
        fts5.reindex([
            {"path": "new.md", "title": "New", "description": "New", "body": "New content", "modified_at": 1.0},
        ])
        results = fts5.search("old")
        assert len(results) == 0


class TestMemoryFTS5Search:
    def test_search_basic(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        fts5.reindex([
            {"path": "test.md", "title": "Momentum", "description": "Factor", "body": "Momentum strategy", "modified_at": 0.0},
        ])
        results = fts5.search("momentum")
        assert len(results) == 1
        assert results[0]["title"] == "Momentum"

    def test_search_empty_query(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        results = fts5.search("")
        assert results == []

    def test_search_whitespace_query(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        results = fts5.search("   ")
        assert results == []

    def test_search_no_results(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        fts5.reindex([
            {"path": "test.md", "title": "Momentum", "description": "Factor", "body": "Momentum", "modified_at": 0.0},
        ])
        results = fts5.search("nonexistent")
        assert results == []

    def test_search_max_results(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        entries = [
            {"path": f"{i}.md", "title": f"Item {i}", "description": "Item", "body": "Common word", "modified_at": float(i)}
            for i in range(10)
        ]
        fts5.reindex(entries)
        results = fts5.search("common", max_results=3)
        assert len(results) <= 3


class TestMemoryFTS5Stats:
    def test_get_stats_empty(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        stats = fts5.get_stats()
        assert stats["count"] == 0

    def test_get_stats_with_entries(self, tmp_path):
        fts5 = MemoryFTS5(tmp_path / "test.db")
        fts5.reindex([
            {"path": "a.md", "title": "A", "description": "A", "body": "A", "modified_at": 0.0},
            {"path": "b.md", "title": "B", "description": "B", "body": "B", "modified_at": 1.0},
        ])
        stats = fts5.get_stats()
        assert stats["count"] == 2
