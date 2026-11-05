"""P0-2 C — ExecutionSandbox Protocol + StaticSandbox tests.

Covers: Protocol runtime_checkable, StaticSandbox delegation equivalence
to legacy functions, future runtime hooks raise NotImplementedError,
backward-compat re-exports.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.sandbox import (
    ExecutionSandbox,
    PathValidationError,
    PathWhitelist,
    StaticSandbox,
    validate_python_source,
)


class TestProtocol:
    def test_static_sandbox_satisfies_protocol(self):
        sb = StaticSandbox("/tmp")
        assert isinstance(sb, ExecutionSandbox)

    def test_protocol_documents_future_methods(self):
        """Protocol surface is the seam: future providers implement all 6."""
        names = {
            "validate_source",
            "resolve_write", "resolve_read",
            "execute_strategy", "allow_network", "get_resource_usage",
        }
        assert names.issubset(set(dir(ExecutionSandbox)))


class TestStaticSandboxDelegation:
    def test_validate_source_matches_legacy(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        legacy = validate_python_source("exec('hi')")
        assert sb.validate_source("exec('hi')") == legacy

    def test_validate_source_blocks_known_dangerous_calls(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        ok, msg = sb.validate_source("import os; os.system('rm -rf /')")
        assert ok is False
        assert "os" in msg

    def test_validate_source_allows_clean_code(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        ok, _ = sb.validate_source(
            "def calc(x):\n    return x * 2\n",
        )
        assert ok is True

    def test_resolve_write_within_workspace(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        resolved = sb.resolve_write("strategies/foo.py")
        assert resolved.is_absolute()
        assert str(resolved).startswith(str(tmp_path.resolve()))

    def test_resolve_read_rejects_absolute(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        with pytest.raises(PathValidationError):
            sb.resolve_read("/etc/passwd")

    def test_resolve_read_rejects_empty(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        with pytest.raises(PathValidationError):
            sb.resolve_read("")

    def test_resolve_write_rejects_parent_traversal(self, tmp_path):
        """``../../etc/foo`` (multi-level) escapes the workspace."""
        sb = StaticSandbox(tmp_path)
        # Set up a sibling dir so the ``..`` resolves to outside.
        sibling = tmp_path.parent / f"{tmp_path.name}-sibling"
        sibling.mkdir()
        try:
            # Write a path that, after ``..`` resolution, lands in sibling.
            with pytest.raises(PathValidationError):
                sb.resolve_write(
                    f"../../{sibling.name}/escape.py",
                )
        finally:
            sibling.rmdir()


class TestRuntimeStubs:
    def test_execute_strategy_raises(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        with pytest.raises(NotImplementedError):
            sb.execute_strategy("def calc(): return 1\n")

    def test_allow_network_raises(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        with pytest.raises(NotImplementedError):
            sb.allow_network()

    def test_get_resource_usage_raises(self, tmp_path):
        sb = StaticSandbox(tmp_path)
        with pytest.raises(NotImplementedError):
            sb.get_resource_usage()


class TestBackwardCompatReexports:
    def test_legacy_imports_still_resolve(self):
        # Existing call sites do
        #   from agentnodes.kernel.agent.sandbox import PathWhitelist
        from agentnodes.kernel.agent.sandbox import (
            DEFAULT_READ_ROOTS,
            DEFAULT_WRITE_ROOTS,
            ASTValidationError,
            PathValidationError,
            validate_python_source,
            validate_python_source_or_raise,
        )
        assert isinstance(DEFAULT_WRITE_ROOTS, tuple)
        assert isinstance(DEFAULT_READ_ROOTS, tuple)
        assert PathWhitelist is not None
        assert callable(validate_python_source)
        assert callable(validate_python_source_or_raise)
        assert issubclass(ASTValidationError, ValueError)
        assert issubclass(PathValidationError, ValueError)

    def test_legacy_module_path_resolves(self):
        """Legacy code that walks into ``sandbox.legacy`` keeps working."""
        from agentnodes.kernel.agent.sandbox.legacy import (
            validate_python_source as legacy_fn,
        )
        ok, _ = legacy_fn("import os")
        assert ok is False
