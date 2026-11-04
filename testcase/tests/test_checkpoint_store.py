"""Tests for checkpoint_store.py — workflow state persistence."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.goal.checkpoint_store import CheckpointStore


class TestCheckpointStore(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = CheckpointStore(base_dir=self.base)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_creates_directory(self) -> None:
        cp = self.store.save("s1", "g1", {"status": "running"}, {"a": 1}, "wf1")
        self.assertTrue(cp.exists())
        self.assertTrue((cp / "state.json").exists())

    def test_save_writes_state_json(self) -> None:
        self.store.save("s1", "g1", {"status": "running"}, {}, "wf1")
        state = json.loads((self.base / "s1" / "g1" / "state.json").read_text())
        self.assertEqual(state["status"], "running")

    def test_save_writes_layer_results(self) -> None:
        self.store.save("s1", "g1", {}, {"sharpe": 1.5}, "wf1")
        lr = json.loads((self.base / "s1" / "g1" / "layer_results.json").read_text())
        self.assertEqual(lr["sharpe"], 1.5)

    def test_save_writes_meta(self) -> None:
        self.store.save("s1", "g1", {}, {}, "my-workflow")
        meta = json.loads((self.base / "s1" / "g1" / "meta.json").read_text())
        self.assertEqual(meta["workflow_name"], "my-workflow")
        self.assertIn("created_at", meta)
        self.assertIn("checkpoint_version", meta)

    def test_save_returns_path(self) -> None:
        cp = self.store.save("s1", "g1", {}, {}, "wf1")
        self.assertEqual(cp, self.base / "s1" / "g1")

    def test_session_isolation(self) -> None:
        self.store.save("s1", "g1", {}, {}, "wf1")
        self.store.save("s2", "g1", {}, {}, "wf1")
        self.assertTrue((self.base / "s1" / "g1").exists())
        self.assertTrue((self.base / "s2" / "g1").exists())

    def test_load_returns_none_for_missing(self) -> None:
        self.assertIsNone(self.store.load("nonexistent", "g1"))

    def test_load_returns_state(self) -> None:
        self.store.save("s1", "g1", {"status": "done"}, {"ic": 0.5}, "wf1")
        result = self.store.load("s1", "g1")
        self.assertIsNotNone(result)
        self.assertEqual(result["state"]["status"], "done")
        self.assertEqual(result["layer_results"]["ic"], 0.5)
        self.assertEqual(result["meta"]["workflow_name"], "wf1")

    def test_load_incomplete_returns_none(self) -> None:
        cp = self.base / "s1" / "g1"
        cp.mkdir(parents=True)
        (cp / "state.json").write_text("{}")
        result = self.store.load("s1", "g1")
        self.assertIsNone(result)

    def test_delete_removes_checkpoint(self) -> None:
        self.store.save("s1", "g1", {}, {}, "wf1")
        self.assertTrue(self.store.delete("s1", "g1"))
        self.assertFalse((self.base / "s1" / "g1").exists())

    def test_delete_nonexistent_returns_false(self) -> None:
        self.assertFalse(self.store.delete("s1", "g1"))

    def test_list_checkpoints(self) -> None:
        self.store.save("s1", "g1", {}, {}, "wf1")
        self.store.save("s1", "g2", {}, {}, "wf2")
        checkpoints = self.store.list_checkpoints("s1")
        self.assertEqual(len(checkpoints), 2)
        names = {c["workflow_name"] for c in checkpoints}
        self.assertIn("wf1", names)
        self.assertIn("wf2", names)

    def test_list_checkpoints_empty(self) -> None:
        self.assertEqual(self.store.list_checkpoints("s1"), [])

    def test_list_checkpoints_all(self) -> None:
        self.store.save("s1", "g1", {}, {}, "wf1")
        checkpoints = self.store.list_checkpoints("s1")
        self.assertEqual(len(checkpoints), 1)


if __name__ == "__main__":
    unittest.main()
