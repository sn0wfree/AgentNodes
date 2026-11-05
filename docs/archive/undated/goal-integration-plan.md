# Goal System Integration Plan

## Date: 2026-07-30

## Background

Session `3a63cdfe` showed that `/goal` in chat doesn't work:
- LLM has no Goal tools → can't create/manage goals via chat
- Chat backend doesn't intercept `/goal` prefix → message sent to LLM as plain text
- Frontend GoalTab receives `goal={null}` → no data source

## Solution: Three-Part Integration

### Part A: Goal Tools for LLM

**New file:** `src/strategy_research/core/agent/builtin_tools/goal_tools.py`

| Tool | Description | GoalStore Method |
|------|-------------|-----------------|
| `create_goal` | Create/replace a research goal | `replace_goal()` |
| `add_evidence` | Append evidence to current goal | `append_evidence()` |
| `complete_goal` | Mark goal complete (lite mode) | `complete_lite()` |
| `get_goal_status` | Get current goal snapshot | `get_current_snapshot()` |
| `list_goals` | List goals with optional filter | `list_goals()` |

Registration: `register_goal_tools(registry)` in `build_default_registry()`.

### Part B: Chat Backend Intercept

**File:** `src/strategy_research/api/routers/chat.py`

Intercept point: `send_async()` before `service.send_message()`

Supported commands:
- `/goal <objective>` → create goal
- `/goal status` → show status
- `/goal evidence <text>` → add evidence
- `/goal complete [recap]` → complete goal
- `/goal cancel [recap]` → cancel goal

### Part C: Frontend Goal Panel

| File | Change |
|------|--------|
| `stores/goal.ts` | **New** Zustand store |
| `api/client.ts` | Add Goal API methods |
| `RightPanel.tsx` | Pass goal data to GoalTab |
| `GoalTab.tsx` | Real-time goal display |
| `useSSE.ts` | Goal event handlers |

### Unit Tests

| Test File | Coverage |
|-----------|----------|
| `tests/test_goal_tools.py` | All 5 goal tools + edge cases |
| `tests/test_goal_chat_intercept.py` | /goal command parsing + execution |
| `tests/test_goal_e2e.py` (extend) | Full lifecycle with tools |
| `tests/test_goal.py` (extend) | Store + context + policy |
