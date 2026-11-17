"""Tests for trace.py — TraceWriter JSONL trace writer."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentnodes.kernel.agent.telemetry.trace import TraceWriter, _env_int


class TestEnvInt(unittest.TestCase):

    def test_env_set(self) -> None:
        os.environ["_TEST_TRACE_INT"] = "42"
        self.assertEqual(_env_int("_TEST_TRACE_INT", 10), 42)
        del os.environ["_TEST_TRACE_INT"]

    def test_env_not_set_returns_default(self) -> None:
        self.assertEqual(_env_int("_TEST_TRACE_MISSING", 10), 10)

    def test_env_invalid_returns_default(self) -> None:
        os.environ["_TEST_TRACE_INT"] = "notanumber"
        self.assertEqual(_env_int("_TEST_TRACE_INT", 10), 10)
        del os.environ["_TEST_TRACE_INT"]

    def test_env_non_positive_returns_default(self) -> None:
        os.environ["_TEST_TRACE_INT"] = "0"
        self.assertEqual(_env_int("_TEST_TRACE_INT", 10), 10)
        del os.environ["_TEST_TRACE_INT"]


class TestTraceWriter(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.writer = TraceWriter(self.dir)

    def tearDown(self) -> None:
        self.writer.close()
        self.tmp.cleanup()

    def test_creates_directory(self) -> None:
        self.assertTrue(self.dir.exists())

    def test_creates_trace_jsonl(self) -> None:
        self.assertTrue(self.writer.path.exists())

    def test_write_adds_ts(self) -> None:
        self.writer.write({"type": "test"})
        entries = TraceWriter.read(self.dir)
        self.assertEqual(len(entries), 1)
        self.assertIn("ts", entries[0])
        self.assertEqual(entries[0]["type"], "test")

    def test_write_preserves_existing_ts(self) -> None:
        self.writer.write({"type": "test", "ts": 123.0})
        entries = TraceWriter.read(self.dir)
        self.assertEqual(entries[0]["ts"], 123.0)

    def test_read_empty_dir(self) -> None:
        empty = Path(tempfile.mkdtemp())
        self.assertEqual(TraceWriter.read(empty), [])

    def test_find_trace_dir_defaults_to_agentnodes_package_root(
        self,
    ) -> None:
        import agentnodes as _sr

        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = Path(temp_dir) / "agentnodes"
            with patch.object(
                _sr, "__file__", str(package_root / "__init__.py"),
            ):
                run_trace = package_root / "runs" / "run-1"
                run_trace.mkdir(parents=True)
                (run_trace / "trace.jsonl").touch()
                self.assertEqual(TraceWriter.find_trace_dir("run-1"), run_trace)

                session_trace = package_root / "sessions" / "run-1"
                session_trace.mkdir(parents=True)
                (session_trace / "trace.jsonl").touch()
                self.assertEqual(
                    TraceWriter.find_trace_dir("run-1"), session_trace,
                )

    def test_write_tool_result(self) -> None:
        self.writer.write_tool_result(call_id="c1", result="ok", tool_name="search", status="ok", elapsed_ms=100, iteration=1)
        entries = TraceWriter.read(self.dir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["type"], "tool_result")
        self.assertEqual(entries[0]["tool"], "search")
        self.assertEqual(entries[0]["status"], "ok")

    def test_write_text_entry(self) -> None:
        self.writer.write_text_entry({"type": "llm_call"}, field="content", value="hello world", offload_kind="prompt")
        entries = TraceWriter.read(self.dir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["content"], "hello world")

    def test_offload_large_text(self) -> None:
        large = "x" * 100000
        self.writer.write_text_entry({"type": "large"}, field="content", value=large, offload_kind="prompt", threshold=50000)
        entries = TraceWriter.read(self.dir)
        self.assertIn("content_path", entries[0])
        self.assertEqual(entries[0]["content_size"], 100000)

    def test_resolve_offloads(self) -> None:
        large = "x" * 100000
        self.writer.write_text_entry({"type": "large"}, field="content", value=large, offload_kind="prompt", threshold=50000)
        entries = TraceWriter.read(self.dir, resolve_offloads=True)
        self.assertEqual(len(entries[0]["content"]), 100000)

    def test_resolve_offloads_filtered_fields(self) -> None:
        large = "x" * 100000
        self.writer.write_text_entry({"type": "large"}, field="content", value=large, offload_kind="prompt", threshold=50000)
        entries = TraceWriter.read(self.dir, resolve_offloads=True, resolve_fields={"content"})
        self.assertEqual(len(entries[0]["content"]), 100000)

    def test_read_skips_malformed_lines(self) -> None:
        self.writer.write({"type": "good"})
        with open(self.writer.path, "a") as f:
            f.write("not json" + chr(10))
        entries = TraceWriter.read(self.dir)
        self.assertEqual(len(entries), 1)

    def test_malformed_json_data_skipped(self) -> None:
        with open(self.writer.path, "a") as f:
            f.write("not json" + chr(10))
        entries = TraceWriter.read(self.dir)
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
