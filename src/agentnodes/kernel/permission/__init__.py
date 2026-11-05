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
``~/.quantnodes-research/permissions.yaml``. Always responses append a
new rule; reject-always appends a deny rule.
"""

from .approvals import PermissionGateway
from .evaluator import PermissionDeniedError, PermissionEvaluator
from .rules_io import DEFAULT_RULES_PATH, load_rules, save_rule
from .schema import (
    Permission,
    PermissionAction,
    PermissionDecision,
    PermissionResponse,
    PermissionRule,
)

__all__ = [
    "Permission",
    "PermissionAction",
    "PermissionDecision",
    "PermissionResponse",
    "PermissionRule",
    "PermissionEvaluator",
    "PermissionDeniedError",
    "PermissionGateway",
    "DEFAULT_RULES_PATH",
    "load_rules",
    "save_rule",
]
