"""Sandbox: AST guard + path whitelist.

Two layers of safety for code written by the agent:

    1. AST validation — static check that Python source does not use
       exec/eval/compile/__import__ or import dangerous modules
       (os, subprocess, shutil, socket, requests, urllib, http).

    2. Path resolution — ensure file paths stay within the workspace,
       no `..` escape, no absolute paths outside workspace, no UNC paths.

Not in scope (deferred to PR7 if needed):
    - Dynamic code execution sandbox (RestrictedPython / subprocess)
    - Network access blocking (out of scope for strategy.py)
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# ── AST guard ────────────────────────────────────────────────────────


# Built-in functions that allow arbitrary code execution
_DANGEROUS_BUILTINS = {"exec", "eval", "compile", "__import__", "breakpoint"}

# Module names that should never be imported by agent code
_BLOCKED_MODULES = {
    "os", "subprocess", "shutil", "socket", "requests",
    "urllib", "urllib3", "http", "httplib", "ftplib", "smtplib",
    "asyncio", "multiprocessing", "threading", "ctypes",
    "pickle", "shelve", "tempfile",
    "importlib", "pkgutil", "code", "codeop", "ast",
    "pty", "pwd", "spwd", "grp", "resource", "termios", "tty",
    "fcntl", "signal", "mmap",
}


class ASTValidationError(ValueError):
    """Raised when agent-written code fails static AST validation."""


def validate_python_source(source: str) -> tuple[bool, str]:
    """Validate that Python source is safe to execute.

    Checks:
        - Parses as valid Python
        - Does not call exec/eval/compile/__import__/breakpoint
        - Does not import blocked modules (os, subprocess, etc.)

    Args:
        source: Python source code.

    Returns:
        (ok, message). If ok is False, message describes the violation.
    """
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc.msg} (line {exc.lineno})"

    violations: list[str] = []

    for node in ast.walk(tree):
        # Check function calls: exec(...), eval(...), getattr(obj, '__class__'), etc.
        if isinstance(node, ast.Call):
            func_name = _extract_call_name(node.func)
            if func_name in _DANGEROUS_BUILTINS:
                violations.append(
                    f"line {node.lineno}: dangerous call: {func_name}(...)"
                )

        # Check imports
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _BLOCKED_MODULES:
                    violations.append(
                        f"line {node.lineno}: blocked import: {alias.name}"
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in _BLOCKED_MODULES:
                    violations.append(
                        f"line {node.lineno}: blocked import from: {node.module}"
                    )

        # Check attribute access on dunders (e.g., obj.__class__.__mro__...)
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                # Skip common safe dunders; allow: __name__, __doc__, __file__,
                # __all__, __version__, __author__
                safe_dunders = {
                    "__name__", "__doc__", "__file__", "__all__",
                    "__version__", "__author__", "__init__", "__main__",
                    "__dict__", "__slots__", "__annotations__",
                }
                if node.attr not in safe_dunders:
                    violations.append(
                        f"line {node.lineno}: dunder attribute access: {node.attr}"
                    )

    if violations:
        return False, "; ".join(violations)
    return True, "ok"


def _extract_call_name(func: ast.AST) -> str:
    """Extract the function name from a Call node.

    Handles Name, Attribute, and dotted names. Returns "" if not extractable.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


# ── Path whitelist ──────────────────────────────────────────────────


class PathValidationError(ValueError):
    """Raised when a path is outside the allowed workspace scope."""


# Default allowed roots (relative to workspace)
DEFAULT_WRITE_ROOTS: tuple[str, ...] = (
    "strategies",
    "templates",
    "memory",
    "logs",
)

DEFAULT_READ_ROOTS: tuple[str, ...] = (
    "strategies",
    "templates",
    "memory",
    "logs",
    "data",
    "docs",
    ".",  # workspace root files (config.yaml, README.md)
)


class PathWhitelist:
    """Resolve paths safely within a workspace.

    Usage:
        wl = PathWhitelist(workspace=Path("/my/ws"))
        resolved = wl.resolve_write("strategies/foo/strategy.py")
        # → Path("/my/ws/strategies/foo/strategy.py")
        wl.resolve_read("README.md")
        wl.resolve_read("../outside.py")  # raises PathValidationError
    """

    def __init__(
        self,
        workspace: Path,
        write_roots: Iterable[str] | None = None,
        read_roots: Iterable[str] | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.write_roots = tuple(write_roots) if write_roots is not None else DEFAULT_WRITE_ROOTS
        self.read_roots = tuple(read_roots) if read_roots is not None else DEFAULT_READ_ROOTS

    # ── Public API ───────────────────────────────

    def resolve_write(self, rel_path: str) -> Path:
        """Resolve a relative write path inside the workspace.

        Raises:
            PathValidationError: If path is absolute, contains `..`,
                starts with `\\\\`, or lands outside an allowed write root.
        """
        self._validate_basic(rel_path)
        resolved = self._resolve_under_workspace(rel_path)
        if not self._is_in_any_root(resolved, self.write_roots):
            allowed = ", ".join(self.write_roots)
            raise PathValidationError(
                f"write path '{rel_path}' is not under any allowed write root "
                f"({allowed})"
            )
        return resolved

    def resolve_read(self, rel_path: str) -> Path:
        """Resolve a relative read path inside the workspace.

        Raises:
            PathValidationError: Same rules as resolve_write, plus UNC check.
        """
        self._validate_basic(rel_path)
        resolved = self._resolve_under_workspace(rel_path)
        if not self._is_in_any_root(resolved, self.read_roots):
            allowed = ", ".join(self.read_roots)
            raise PathValidationError(
                f"read path '{rel_path}' is not under any allowed read root "
                f"({allowed})"
            )
        return resolved

    def is_safe_write(self, rel_path: str) -> bool:
        """Non-raising variant of resolve_write."""
        try:
            self.resolve_write(rel_path)
            return True
        except PathValidationError:
            return False

    def is_safe_read(self, rel_path: str) -> bool:
        """Non-raising variant of resolve_read."""
        try:
            self.resolve_read(rel_path)
            return True
        except PathValidationError:
            return False

    # ── Internal helpers ─────────────────────────

    def _validate_basic(self, rel_path: str) -> None:
        """Reject absolute, empty, UNC paths."""
        if not isinstance(rel_path, str):
            raise PathValidationError(
                f"path must be a string, got {type(rel_path).__name__}"
            )
        if not rel_path or not rel_path.strip():
            raise PathValidationError("path is empty")
        if rel_path.startswith(("\\\\", "//")):
            raise PathValidationError(f"UNC paths are not allowed: {rel_path!r}")
        if os.path.isabs(rel_path):
            raise PathValidationError(
                f"absolute paths are not allowed: {rel_path!r}"
            )

    def _resolve_under_workspace(self, rel_path: str) -> Path:
        """Resolve a relative path under workspace; reject escape via '..'."""
        # Normalize: strip leading ./, collapse //, expand ~
        cleaned = rel_path.strip().lstrip("./")
        candidate = (self.workspace / cleaned).resolve()
        # Verify resolved stays inside workspace
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise PathValidationError(
                f"path '{rel_path}' escapes workspace root"
            ) from exc
        return candidate

    def _is_in_any_root(self, resolved: Path, roots: Iterable[str]) -> bool:
        """Check if a resolved path is under any of the allowed roots."""
        for root in roots:
            root_path = (self.workspace / root).resolve()
            try:
                resolved.relative_to(root_path)
                return True
            except ValueError:
                continue
        # "." root always matches workspace
        if "." in roots:
            try:
                resolved.relative_to(self.workspace)
                return True
            except ValueError:
                pass
        return False


# ── Convenience functions (module-level) ────────────────────────────


def validate_python_source_or_raise(source: str) -> None:
    """Validate Python source; raise ASTValidationError on violation."""
    ok, msg = validate_python_source(source)
    if not ok:
        raise ASTValidationError(msg)
