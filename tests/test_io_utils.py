"""Unit tests for utils.io_utils — atomic writes and dotenv parsing."""
from __future__ import annotations

import json
import os
from pathlib import Path

from agentnodes.foundation.utils.io_utils import (
    atomic_write_env,
    atomic_write_json,
    atomic_write_text,
    read_dotenv,
)


class TestAtomicWriteText:
    def test_creates_file_and_parent(self, tmp_path: Path):
        p = tmp_path / "a" / "b.txt"
        out = atomic_write_text(p, "hello")
        assert out == p
        assert p.read_text(encoding="utf-8") == "hello"

    def test_overwrites_existing(self, tmp_path: Path):
        p = tmp_path / "f.txt"
        atomic_write_text(p, "v1")
        atomic_write_text(p, "v2")
        assert p.read_text(encoding="utf-8") == "v2"

    def test_no_temp_leftovers(self, tmp_path: Path):
        p = tmp_path / "f.txt"
        atomic_write_text(p, "x")
        leftovers = [f for f in tmp_path.iterdir() if f.name != "f.txt"]
        assert leftovers == []

    def test_returns_path(self, tmp_path: Path):
        p = tmp_path / "f.txt"
        assert atomic_write_text(p, "x") == p

    def test_write_failure_cleans_temp(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "f.txt"
        real_replace = os.replace

        def boom(src, dst):
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", boom)
        try:
            atomic_write_text(p, "x")
            raised = False
        except OSError:
            raised = True
        monkeypatch.setattr(os, "replace", real_replace)
        assert raised
        assert list(tmp_path.iterdir()) == []


class TestAtomicWriteJson:
    def test_roundtrip(self, tmp_path: Path):
        p = tmp_path / "d.json"
        payload = {"a": 1, "b": "中文"}
        atomic_write_json(p, payload)
        assert json.loads(p.read_text(encoding="utf-8")) == payload

    def test_trailing_newline(self, tmp_path: Path):
        p = tmp_path / "d.json"
        atomic_write_json(p, {"x": 1})
        assert p.read_text(encoding="utf-8").endswith("\n")


class TestAtomicWriteEnv:
    def test_writes_key_values(self, tmp_path: Path):
        p = tmp_path / ".env"
        atomic_write_env(p, {"A": "1", "B": "two"})
        text = p.read_text(encoding="utf-8")
        assert "A=1" in text
        assert "B=two" in text

    def test_empty_value_kept(self, tmp_path: Path):
        p = tmp_path / ".env"
        atomic_write_env(p, {"EMPTY": ""})
        assert "EMPTY=" in p.read_text(encoding="utf-8")

    def test_roundtrip_with_read_dotenv(self, tmp_path: Path):
        p = tmp_path / ".env"
        atomic_write_env(p, {"K1": "v1", "K2": ""})
        assert read_dotenv(p) == {"K1": "v1", "K2": ""}


class TestReadDotenv:
    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert read_dotenv(tmp_path / "nope.env") == {}

    def test_skips_comments_and_blanks(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text("# comment\n\nA=1\n  \nB=2\n", encoding="utf-8")
        assert read_dotenv(p) == {"A": "1", "B": "2"}

    def test_skips_lines_without_equals(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text("NOEQ\nA=1\n", encoding="utf-8")
        assert read_dotenv(p) == {"A": "1"}

    def test_value_keeps_inner_equals(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text("URL=http://x?y=1\n", encoding="utf-8")
        assert read_dotenv(p)["URL"] == "http://x?y=1"

    def test_strips_key_whitespace_only(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text("  KEY  =value\n", encoding="utf-8")
        assert read_dotenv(p)["KEY"] == "value"

    def test_values_not_quote_stripped(self, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text('A="quoted"\n', encoding="utf-8")
        assert read_dotenv(p)["A"] == '"quoted"'
