"""Permission ruleset engine (Tier 1 - A1).

Ported from opencode ``packages/opencode/src/permission/index.ts``:
    Rule = { permission: "edit" | "bash" | ...,
             pattern:   "<glob>",
             action:    "allow" | "ask" | "deny" }
    Ruleset = Rule[]

Resolution: last-match-wins with glob matching. When a tool call is
evaluated:
- ``deny``   -> raise ``PermissionDeniedError`` immediately
- ``allow``  -> execute
- ``ask``    -> emit SSE ``permission_request`` + block on an asyncio
              Deferred until the user responds once / always / reject

Persistence: user-curated rules live at
``~/.agentnodes/permissions.yaml``. Always responses append a
new rule; reject-always appends a deny rule.
"""

from agentnodes.kernel.permission.approvals import PermissionGateway
from agentnodes.kernel.permission.evaluator import PermissionDeniedError, PermissionEvaluator
from agentnodes.kernel.permission.rules_io import DEFAULT_RULES_PATH, load_rules, save_rule
from agentnodes.kernel.permission.schema import (
    DEFAULT_HARD_DENY,
    MODE_PRESETS,
    HardDenyRule,
    Permission,
    PermissionAction,
    PermissionDecision,
    PermissionMode,
    PermissionResponse,
    PermissionRule,
)

__all__ = [
    "Permission",
    "PermissionAction",
    "PermissionDecision",
    "PermissionResponse",
    "PermissionRule",
    "HardDenyRule",
    "DEFAULT_HARD_DENY",
    "PermissionMode",
    "MODE_PRESETS",
    "PermissionEvaluator",
    "PermissionDeniedError",
    "PermissionGateway",
    "DEFAULT_RULES_PATH",
    "load_rules",
    "save_rule",
]
