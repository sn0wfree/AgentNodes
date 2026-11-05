"""Tests for frontmatter.py — YAML-like frontmatter parser."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentnodes.kernel.agent.frontmatter import parse_frontmatter


class TestParseFrontmatter(unittest.TestCase):

    def test_no_frontmatter(self) -> None:
        meta, body = parse_frontmatter("just body text")
        self.assertEqual(meta, {})
        self.assertEqual(body, "just body text")

    def test_empty_string(self) -> None:
        meta, body = parse_frontmatter("")
        self.assertEqual(meta, {})
        self.assertEqual(body, "")

    def test_basic_frontmatter(self) -> None:
        meta, body = parse_frontmatter("---\nkey: value\n---\nbody content")
        self.assertEqual(meta, {"key": "value"})
        self.assertEqual(body, "body content")

    def test_multiple_keys(self) -> None:
        meta, body = parse_frontmatter("---\na: 1\nb: hello\n---\nbody")
        self.assertEqual(meta, {"a": "1", "b": "hello"})

    def test_list_value(self) -> None:
        text = "---\ntags: [a, b, c]\n---\nbody"
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta, {"tags": ["a", "b", "c"]})

    def test_boolean_value_true(self) -> None:
        meta, _ = parse_frontmatter("---\nenabled: true\n---\nbody")
        self.assertEqual(meta, {"enabled": True})

    def test_boolean_value_false(self) -> None:
        meta, _ = parse_frontmatter("---\nenabled: false\n---\nbody")
        self.assertEqual(meta, {"enabled": False})

    def test_line_without_colon_skipped(self) -> None:
        text = "---\nkey: val\nbadline\nother: val2\n---\nbody"
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta, {"key": "val", "other": "val2"})

    def test_only_frontmatter_delimiters(self) -> None:
        meta, body = parse_frontmatter("---\n---\nbody")
        self.assertEqual(meta, {})
        self.assertEqual(body, "---\n---\nbody")

    def test_empty_list_value(self) -> None:
        meta, _ = parse_frontmatter("---\ntags: []\n---\nbody")
        self.assertEqual(meta, {"tags": []})


if __name__ == "__main__":
    unittest.main()
