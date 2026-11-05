# Goal Subsystem Design (P3-a)

> Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
> See `docs/archive/undated/vibe-trading-credits.md` for the full attribution list.

## 1. Purpose

A research-only **goal ledger** for tracking finance research objectives,
acceptance criteria, traceable evidence, and completion audits. The ledger
lets an agent (or a human) answer:

- What is the current research goal for this session?
- What acceptance criteria must be satisfied before declaring it complete?
- What evidence backs each criterion, and is it verified?
- Who authorized completion, and what audit trail exists?

This package is **deliberately research-only** — it never executes trades
or places orders. See `policy.py` for the live-execution defense.

## 2. Data Model

All models live in `core/goal/models.py` and are immutable (frozen
dataclass) so they can be safely shared across threads.

### GoalStatus (12 lifecycle values)

| Value | Meaning |
|---|---|
| `active` | Currently being worked on. The default after `replace_goal`. |
| `paused` | User paused; resume via `update_status`. |
| `waiting_user` | Awaiting user input. |
| `needs_refresh` | Evidence may be stale; needs re-collection. |
| `insufficient_evidence` | Cannot proceed without more evidence. |
| `compliance_blocked` | Blocked by a compliance policy. |
| `blocked` | Generic blocker. |
| `budget_limited` | Auto-promoted when token/turn/time budget exceeded. |
| `usage_limited` | Hit a usage limit (separate from budget). |
| `complete` | All required criteria have audit + verified evidence. |
| `cancelled` | User cancelled. |
| `superseded` | Replaced by a newer goal for the same session. |

### RiskTier (4 values — full set per user decision)

| Value | Meaning |
|---|---|
| `research_general` | Default for most research questions. |
| `market_specific_short_term` | Short-horizon market research. |
| `personalized_advice_or_position_sizing` | Personalized recommendations. |
| `live_trading_or_execution` | **Rejected at `replace_goal` — never created.** |

### GoalRecord (frozen, 19 fields)

```python
@dataclass(frozen=True)
class GoalRecord:
    goal_id: str
    session_id: str
    status: GoalStatus
    objective: str
    ui_summary: str
    source: str
    protocol: str
    risk_tier: RiskTier
    token_budget: int | None
    tokens_used: int
    turn_budget: int | None
    turns_used: int
    time_budget_seconds: int | None
    time_used_seconds: int
    budget_wrapup_sent: bool
    created_at: str        # ISO 8601
    updated_at: str        # ISO 8601
    completed_at: str | None
    recap: str | None
```

### Companion models

- `GoalClaim` — research thesis / assumption linked to the goal
- `GoalCriterion` — checklist item with `required` flag and status
- `EvidenceInput` — DTO for adding evidence
- `EvidenceRecord` — persisted evidence with freshness + verification
- `AuditRow` — `(criterion_id, result, evidence_ids, notes)` for completion
- `StaleGoalError` — raised when an agent turn tries to mutate a stale goal

## 3. SQLite Storage

- **Path**: `~/.quantnodes-research/goals.db` (override via
  `QUANTNODES_RESEARCH_GOAL_DB_PATH`).
- **Schema**: 5 tables (`goals`, `goal_claims`, `goal_criteria`,
  `goal_evidence`, `goal_audits`). Created on first open.
- **WAL mode** + `busy_timeout=5000` + `foreign_keys=ON`.
- **Write transactions** use `BEGIN IMMEDIATE` + `RLock` for
  thread/process safety.
- **Uniqueness**: a partial unique index enforces one *current* goal per
  session (any of `active`, `paused`, `waiting_user`, `needs_refresh`,
  `insufficient_evidence`, `compliance_blocked`, `budget_limited`).

### Tables

```sql
CREATE TABLE goals (
    goal_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    objective TEXT NOT NULL,
    ui_summary TEXT NOT NULL,
    source TEXT NOT NULL,
    protocol TEXT NOT NULL,
    risk_tier TEXT NOT NULL,
    token_budget INTEGER,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    turn_budget INTEGER,
    turns_used INTEGER NOT NULL DEFAULT 0,
    time_budget_seconds INTEGER,
    time_used_seconds INTEGER NOT NULL DEFAULT 0,
    budget_wrapup_sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    recap TEXT
);
-- ... claims, criteria, evidence, audits tables
```

## 4. Lifecycle Operations

### `replace_goal(session_id, objective, criteria, ...)`

- Validates objective + criteria (non-empty, no live-execution language).
- Rejects `risk_tier=LIVE_TRADING_OR_EXECUTION`.
- Supersedes any current goal for the session (status → `superseded`).
- Inserts the new goal (status `active`) + 1 `thesis` claim + 1 row per
  criterion (status `pending`).
- Returns the freshly-loaded `GoalRecord`.

### `update_goal(session_id, goal_id, expected_goal_id, ...)`

- Stale-write guard: `expected_goal_id` must equal `goal_id`.
- Goal must be current (one of the active statuses) for the session.
- Updates `objective` + `ui_summary` and the linked `thesis` claim.

### `append_evidence(session_id, goal_id, expected_goal_id, evidence)`

- Same stale-write guard.
- Verifies `criterion_id` (if given) belongs to the goal.
- Sets `verification_status`:
  - `"verified"` if `artifact_path` exists, has matching SHA-256, OR
    `run_id` resolves to an existing run directory.
  - `"unverified"` otherwise.
- Auto-marks the linked criterion as `covered` (only if it was
  `pending` / `open` / `unsatisfied`).

### `update_status(session_id, goal_id, expected_goal_id, status, audit, recap)`

- For `status=COMPLETE`, validates every required criterion has:
  - A matching audit row
  - A result in `{satisfied, satisfied_with_caveat, not_applicable_user_accepted}`
  - At least one verified evidence
  - For `not_applicable_user_accepted`: non-empty `notes`
- Sets `completed_at` for terminal statuses.

### `account_usage(...)`

- Tracks token / turn / time usage.
- Auto-promotes to `BUDGET_LIMITED` if any budget is exceeded.

## 5. Live-Execution Defense (policy.py)

```python
_EXECUTION_PATTERNS = (
    re.compile(r"\b(place|submit|execute|send)\b.{0,40}\b(order|trade)\b", re.I),
    re.compile(
        r"\b(buy|sell|short|long)\b.{0,40}\b(now|immediately|market order|limit order|"
        r"shares?|contracts?|btc|eth|usdt)\b",
        re.I,
    ),
    re.compile(r"(下单|市价单|限价单|马上买|立即买|现在买|马上卖|立即卖|现在卖)"),
)
```

Both English and Chinese execution phrases are blocked at:
1. `replace_goal` objective / criteria
2. `update_goal` objective
3. `replace_goal` with `risk_tier=LIVE_TRADING_OR_EXECUTION`

## 6. Context Injection

The `context` module produces XML-ish blocks for the AgentLoop's user
message:

```xml
<current-research-goal>
goal_id: goal_abc
expected_goal_id: goal_abc
status: active
objective: Investigate momentum factor
risk_tier: research_general
evidence_count: 3
criteria:
- 1. [covered] crit_001: Define the thesis (evidence=1)
- 2. [pending] crit_002: Collect evidence (evidence=0)
- 3. [pending] crit_003: Record caveats (evidence=0)
instructions:
- Continue this goal unless the user explicitly replaces or cancels it.
- ...
</current-research-goal>
```

The continuation prompt variant is used to drive the next model turn
from ledger state rather than just the objective.

## 7. CLI

```
quantnodes-research goal start --session-id S --objective "..." \
    [--criterion "..." ...] [--risk-tier ...] [--token-budget N ...]
quantnodes-research goal status --session-id S
quantnodes-research goal status --goal-id G
quantnodes-research goal evidence --session-id S --text "..." \
    [--criterion-id C] [--artifact PATH --artifact-hash sha256:...]
quantnodes-research goal audit --session-id S --criterion-id C \
    --result satisfied [--evidence EV_ID ...] [--notes "..."]
quantnodes-research goal complete --session-id S \
    [--audit-file audit.json | --criterion-id C --result R]
quantnodes-research goal list --session-id S
quantnodes-research goal cancel --session-id S
```

## 8. Integration Points (P3-d, planned)

- `AgentLoop.run()` — inject `format_goal_context()` at top of user message
- `WorkflowController` — Layer 0 can call `get_current_goal_context()`
- `cmd_autoresearch` — wrap each round in a goal-aware prompt
- `HypothesisRegistry` — `link_backtest()` can cite a goal

## 9. Limitations / Future Work

- Single-process SQLite — no remote backup. Use `litestream` if needed.
- No PII redaction on `objective` / `evidence.text` (handled at agent
  layer via `redaction.py`).
- No versioning — once a goal is superseded, its audit trail is frozen.
- No goal templates — `default_goal_criteria()` returns the 3-criterion
  standard; custom protocols must supply their own criterion list.

## 10. Testing

| Test file | Count | Focus |
|---|---|---|
| `test_goal_models.py` | 26 | dataclass invariants, enum values, freeze |
| `test_goal_policy.py` | 34 | normalize + EN/CN live-execution reject |
| `test_goal_store.py` | 32 | SQLite CRUD, stale-guard, completion audit |
| `test_goal_context.py` | 32 | format helpers + continuation prompt |
| `test_goal_cli.py` | 26 | 7 subcommands + argparse wiring |
| `test_goal_e2e.py` | 12 | full lifecycle + isolation + JSON round-trip |
| **Total** | **162** | |

Run:

```bash
pytest tests/test_goal_*.py -v
```