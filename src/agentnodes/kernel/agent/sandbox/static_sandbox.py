"""StaticSandbox — default ``ExecutionSandbox`` provider.

Wraps the existing ``core.agent.sandbox`` module functions:
- ``validate_source`` → ``validate_python_source``
- ``resolve_write`` / ``resolve_read`` → ``PathWhitelist`` instances
  (one per workspace, cached on the sandbox)

The three future methods (execute_strategy, allow_network,
get_resource_usage) are explicit ``NotImplementedError`` — v0.1 callers
must use the existing ``backtest.py`` subprocess path; v0.2 will
replace them with a proper subprocess-with-timeout implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import legacy as _legacy


class StaticSandbox:
    """Default ``ExecutionSandbox`` implementation."""

    def __init__(
        self,
        workspace: Path,
        *,
        write_roots: list[str] | None = None,
        read_roots: list[str] | None = None,
    ):
        self._workspace = Path(workspace).resolve()
        self._write_roots = write_roots
        self._read_roots = read_roots
        # Cache PathWhitelist instances so PathValidationError raised by
        # the legacy module propagates untouched to consumers.
        self._write_wl: _legacy.PathWhitelist | None = None
        self._read_wl: _legacy.PathWhitelist | None = None

    # ── Static analysis ─────────────────────────────────────

    def validate_source(self, source: str) -> tuple[bool, str]:
        return _legacy.validate_python_source(source)

    # ── Path safety ─────────────────────────────────────────

    def _get_write_wl(self) -> _legacy.PathWhitelist:
        if self._write_wl is None:
            self._write_wl = _legacy.PathWhitelist(
                workspace=self._workspace,
                write_roots=self._write_roots,
            )
        return self._write_wl

    def _get_read_wl(self) -> _legacy.PathWhitelist:
        if self._read_wl is None:
            self._read_wl = _legacy.PathWhitelist(
                workspace=self._workspace,
                read_roots=self._read_roots,
            )
        return self._read_wl

    def resolve_write(self, rel_path: str) -> Path:
        return self._get_write_wl().resolve_write(rel_path)

    def resolve_read(self, rel_path: str) -> Path:
        return self._get_read_wl().resolve_read(rel_path)

    # ── Runtime hooks (v0.1 not implemented) ────────────────

    def execute_strategy(
        self,
        source: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
        memory_limit_mb: int = 512,
    ) -> Any:
        raise NotImplementedError(
            "StaticSandbox.execute_strategy is a v0.1 stub; use the "
            "existing backtest.py subprocess path until v0.2 lands the "
            "sandbox-backed runner."
        )

    def allow_network(self, hosts: list[str] | None = None) -> None:
        raise NotImplementedError(
            "StaticSandbox.allow_network is a v0.1 stub; the legacy "
            "backtest.py env whitelist stays in effect."
        )

    def get_resource_usage(self) -> dict[str, Any]:
        raise NotImplementedError(
            "StaticSandbox.get_resource_usage is a v0.1 stub."
        )


__all__ = ["StaticSandbox"]
