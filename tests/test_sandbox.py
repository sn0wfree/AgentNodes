"""Tests for sandbox: AST guard + path whitelist."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.agent.sandbox import (
    DEFAULT_READ_ROOTS,
    DEFAULT_WRITE_ROOTS,
    ASTValidationError,
    PathValidationError,
    PathWhitelist,
    validate_python_source,
    validate_python_source_or_raise,
)

# ── AST guard ────────────────────────────────────────────────────────


class TestASTGuard:
    """Static checks: dangerous calls and blocked module imports."""

    @pytest.mark.parametrize("safe_code", [
        "x = 1 + 2",
        "import pandas as pd\nimport numpy as np",
        "from scipy import stats",
        "from typing import List, Dict",
        "def foo(a, b):\n    return a + b",
        "class Foo:\n    def __init__(self): self.x = 1",
        "data = {'a': [1, 2, 3]}",
        "[i*i for i in range(10)]",
        "{k: v for k, v in data.items()}",
        "x = obj.__name__\ny = obj.__doc__\nz = obj.__file__",
        "PARAMS = {'top_n': 10}",
        "FACTOR_EXPRS = [{'factor_name': 'mom', 'factor_code': 'ts_mean(close, 20)'}]",
    ])
    def test_safe_code_passes(self, safe_code):
        ok, msg = validate_python_source(safe_code)
        assert ok, f"expected OK, got: {msg}"

    @pytest.mark.parametrize("bad_code,expected_in_msg", [
        ('exec("print(1)")', "exec"),
        ('eval("1+1")', "eval"),
        ('compile("x=1", "<>", "exec")', "compile"),
        ('os = __import__("os")', "__import__"),
        ('breakpoint()', "breakpoint"),
        ('import os', "os"),
        ('import os.path', "os"),
        ('from os import system', "os"),
        ('import subprocess', "subprocess"),
        ('from subprocess import run', "subprocess"),
        ('import shutil', "shutil"),
        ('import socket', "socket"),
        ('from socket import socket', "socket"),
        ('import requests', "requests"),
        ('import urllib.request', "urllib"),
        ('import asyncio', "asyncio"),
        ('import multiprocessing', "multiprocessing"),
        ('import threading', "threading"),
        ('import pickle', "pickle"),
        ('import ctypes', "ctypes"),
        ('import importlib', "importlib"),
        ('x = obj.__class__', "__class__"),
        ('x = obj.__bases__', "__bases__"),
        ('x = obj.__subclasses__', "__subclasses__"),
        ('x = obj.__globals__', "__globals__"),
        ('x = obj.__builtins__', "__builtins__"),
        ('def foo(): return obj.__dict__', "__dict__"),  # this is in safe_dunders so OK
    ])
    def test_dangerous_code_blocked(self, bad_code, expected_in_msg):
        ok, msg = validate_python_source(bad_code)
        if expected_in_msg == "__dict__":
            # __dict__ is allowed by safe_dunders list
            assert ok
        else:
            assert not ok, f"expected block, got OK for: {bad_code}"
            assert expected_in_msg in msg, f"expected '{expected_in_msg}' in: {msg}"

    def test_syntax_error_caught(self):
        ok, msg = validate_python_source("def foo(:")
        assert not ok
        assert "SyntaxError" in msg

    def test_safe_dunders_allowed(self):
        ok, msg = validate_python_source(
            "x = obj.__name__\ny = obj.__doc__\nz = obj.__file__\n"
            "w = obj.__init__\nu = obj.__all__\nv = obj.__version__"
        )
        assert ok

    def test_line_numbers_in_violations(self):
        code = "x = 1\ny = 2\nexec('z')"
        ok, msg = validate_python_source(code)
        assert not ok
        assert "line 3" in msg

    def test_multiple_violations(self):
        code = "import os\nimport subprocess\nx = eval('1')"
        ok, msg = validate_python_source(code)
        assert not ok
        assert "os" in msg
        assert "subprocess" in msg
        assert "eval" in msg

    def test_validate_or_raise_passes(self):
        validate_python_source_or_raise("x = 1")

    def test_validate_or_raise_raises(self):
        with pytest.raises(ASTValidationError, match="exec"):
            validate_python_source_or_raise("exec('x')")

    def test_nested_dangerous_in_function(self):
        ok, msg = validate_python_source(
            "def my_func():\n    return eval('1+1')"
        )
        assert not ok
        assert "line 2" in msg

    def test_method_call_on_blocked_function(self):
        ok, msg = validate_python_source(
            "import os\nresult = os.system('ls')"
        )
        assert not ok
        assert "os" in msg

    def test_realistic_strategy_template(self):
        """The actual templates/strategy.py should pass."""
        from pathlib import Path as P
        template = P("src/agentnodes/templates/strategy.py")
        if template.exists():
            source = template.read_text(encoding="utf-8")
            ok, msg = validate_python_source(source)
            # Note: template uses 'prepare' which we don't block
            assert ok, f"template failed: {msg}"


# ── Path whitelist ───────────────────────────────────────────────────


class TestPathWhitelist:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Build a workspace with default subdirs."""
        for d in DEFAULT_WRITE_ROOTS + ("data", "docs"):
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
        # add a few root files
        (tmp_path / "README.md").write_text("# test")
        (tmp_path / "config.yaml").write_text("a: 1")
        return tmp_path

    @pytest.fixture
    def wl(self, workspace: Path) -> PathWhitelist:
        return PathWhitelist(workspace=workspace)

    # ── Write paths ─────────────────────────────────

    def test_write_strategies(self, wl, workspace):
        p = wl.resolve_write("strategies/foo/strategy.py")
        assert str(p).startswith(str(workspace))
        assert p.name == "strategy.py"

    def test_write_templates(self, wl, workspace):
        p = wl.resolve_write("templates/strategy.py")
        assert p.parent.name == "templates"

    def test_write_memory(self, wl, workspace):
        p = wl.resolve_write("memory/notes.md")
        assert p.parent.name == "memory"

    def test_write_logs(self, wl, workspace):
        p = wl.resolve_write("logs/run.log")
        assert p.parent.name == "logs"

    def test_write_nested_deep(self, wl, workspace):
        p = wl.resolve_write("strategies/sub/deep/file.py")
        # 用 Path.parts 比较，避免 Windows 反斜杠 / POSIX 斜杠差异。
        assert p.parts[-4:] == ("strategies", "sub", "deep", "file.py")

    def test_write_trailing_slash(self, wl, workspace):
        p = wl.resolve_write("strategies/")
        assert p.parent == workspace  # resolves to workspace/strategies

    def test_write_data_blocked(self, wl):
        with pytest.raises(PathValidationError, match="not under any allowed write root"):
            wl.resolve_write("data/foo.parquet")

    def test_write_root_file_blocked(self, wl):
        with pytest.raises(PathValidationError):
            wl.resolve_write("README.md")

    # ── Read paths ──────────────────────────────────

    def test_read_strategies(self, wl):
        p = wl.resolve_read("strategies/foo.py")
        assert p.name == "foo.py"

    def test_read_data(self, wl):
        p = wl.resolve_read("data/foo.parquet")
        assert p.parent.name == "data"

    def test_read_root_files(self, wl):
        p = wl.resolve_read("README.md")
        assert p.name == "README.md"
        p = wl.resolve_read("config.yaml")
        assert p.name == "config.yaml"

    def test_read_docs(self, wl):
        p = wl.resolve_read("docs/llm-config-template.yaml")
        assert p.name == "llm-config-template.yaml"

    # ── Blocked patterns ────────────────────────────

    def test_absolute_path_blocked(self, wl):
        with pytest.raises(PathValidationError, match="absolute"):
            wl.resolve_write("/etc/passwd")

    def test_absolute_path_within_workspace_blocked(self, wl):
        # Even absolute path inside workspace should be blocked
        with pytest.raises(PathValidationError, match="absolute"):
            wl.resolve_write(str((wl.workspace / "strategies" / "x.py").absolute()))

    def test_dotdot_escape_blocked(self, wl):
        # Either "escapes workspace" or "not under any allowed write root" is acceptable
        with pytest.raises(PathValidationError):
            wl.resolve_write("../outside.py")

    def test_deep_dotdot_escape_blocked(self, wl):
        with pytest.raises(PathValidationError):
            wl.resolve_write("strategies/../../escape.py")

    def test_unc_blocked(self, wl):
        with pytest.raises(PathValidationError, match="UNC"):
            wl.resolve_write("\\\\server\\share\\file")

    def test_double_slash_blocked(self, wl):
        with pytest.raises(PathValidationError, match="UNC"):
            wl.resolve_write("//etc/passwd")

    def test_empty_path_blocked(self, wl):
        with pytest.raises(PathValidationError, match="empty"):
            wl.resolve_write("")

    def test_whitespace_path_blocked(self, wl):
        with pytest.raises(PathValidationError, match="empty"):
            wl.resolve_write("   ")

    def test_non_string_path_blocked(self, wl):
        with pytest.raises(PathValidationError, match="must be a string"):
            wl.resolve_write(123)  # type: ignore

    def test_tilde_expansion_rejected(self, wl):
        # ~ expands to home; absolute path → rejected
        with pytest.raises(PathValidationError):
            wl.resolve_write("~/sneaky.py")

    # ── Non-raising variants ────────────────────────

    def test_is_safe_write(self, wl):
        assert wl.is_safe_write("strategies/x.py")
        assert not wl.is_safe_write("data/x.py")
        assert not wl.is_safe_write("/etc/passwd")
        assert not wl.is_safe_write("../escape.py")

    def test_is_safe_read(self, wl):
        assert wl.is_safe_read("data/x.parquet")
        assert wl.is_safe_read("README.md")
        assert not wl.is_safe_read("/etc/passwd")

    # ── Custom roots ────────────────────────────────

    def test_custom_write_roots(self, workspace):
        wl = PathWhitelist(workspace=workspace, write_roots=("strategies",))
        assert wl.is_safe_write("strategies/x.py")
        assert not wl.is_safe_write("templates/x.py")
        assert not wl.is_safe_write("memory/x.md")

    def test_custom_read_roots(self, workspace):
        wl = PathWhitelist(workspace=workspace, read_roots=("strategies",))
        assert wl.is_safe_read("strategies/x.py")
        with pytest.raises(PathValidationError):
            wl.resolve_read("README.md")

    def test_empty_roots_blocks_everything(self, workspace):
        wl = PathWhitelist(workspace=workspace, write_roots=(), read_roots=("strategies",))
        assert not wl.is_safe_write("strategies/x.py")
        assert wl.is_safe_read("strategies/x.py")

    def test_default_roots_are_used_when_none(self, workspace):
        wl = PathWhitelist(workspace=workspace)
        assert wl.write_roots == DEFAULT_WRITE_ROOTS
        assert wl.read_roots == DEFAULT_READ_ROOTS

    # ── Edge cases ──────────────────────────────────

    def test_workspace_resolved(self, tmp_path):
        # workspace with non-canonical path
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        ws = subdir / ".."  # relative path
        wl = PathWhitelist(workspace=ws)
        # workspace should be resolved to absolute canonical
        assert wl.workspace.is_absolute()

    def test_dot_in_path_normalized(self, wl, workspace):
        # "./strategies/x.py" should resolve same as "strategies/x.py"
        p1 = wl.resolve_write("strategies/x.py")
        p2 = wl.resolve_write("./strategies/x.py")
        assert p1 == p2

    def test_write_to_existing_file(self, wl, workspace):
        (workspace / "strategies" / "existing.py").write_text("x = 1")
        p = wl.resolve_write("strategies/existing.py")
        assert p.exists()
        assert p.read_text() == "x = 1"

    def test_write_to_nonexistent_deep_path(self, wl, workspace):
        # Should resolve even if intermediate dirs don't exist
        p = wl.resolve_write("strategies/new/nested/deep/file.py")
        assert not p.exists()  # not created, just resolved
