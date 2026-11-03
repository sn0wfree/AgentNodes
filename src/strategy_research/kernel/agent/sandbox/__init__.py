"""Sandbox subpackage (P0-2 Phase G).

Re-exports ``ExecutionSandbox`` (Protocol) + ``StaticSandbox``
(default implementation) + the legacy ``sandbox`` module's public
names so existing callers (``validate_python_source``,
``PathWhitelist``, ``DEFAULT_*_ROOTS``) keep working.

P0-2 moved the legacy module from ``core.agent.sandbox`` (file) to
``core.agent.sandbox.legacy`` (module under package) so the new
subpackage could expose ``ExecutionSandbox`` and ``StaticSandbox``
without name collisions.
"""

from .legacy import (
    DEFAULT_READ_ROOTS,
    DEFAULT_WRITE_ROOTS,
    ASTValidationError,
    PathValidationError,
    PathWhitelist,
    validate_python_source,
    validate_python_source_or_raise,
)
from .protocol import ExecutionSandbox
from .static_sandbox import StaticSandbox

__all__ = [
    # P0-2 Protocol + default provider
    "ExecutionSandbox",
    "StaticSandbox",
    # Re-exports of legacy public surface (backward compat)
    "ASTValidationError",
    "DEFAULT_READ_ROOTS",
    "DEFAULT_WRITE_ROOTS",
    "PathValidationError",
    "PathWhitelist",
    "validate_python_source",
    "validate_python_source_or_raise",
]
