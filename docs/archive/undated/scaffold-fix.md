# Smart Scaffold + import_data Defensive Unwrap

## Background

Session `700dc7f7-95de-45e0-b568-d713fe05065f` (119 messages) accumulated
27 tool errors during a "create a new strategy" workflow. Two root causes
accounted for most failures:

1. **Missing `templates/` in workspace** — The chat system prompt
   (`src/strategy_research/templates/.prompts/chat.md`) advertises a
   `templates/` directory in the workspace tree (lines 22-39, 79, 105,
   118, 134), but **no code path ever creates it**. The LLM obediently
   tries `list_files templates`, `read_file templates/strategy.py`, etc.
   and gets 7 "path not found" / "file not found" errors before giving
   up and falling back to existing strategies.

2. **LLM wraps `data[code]` in `{"item": [...]}`** — `MiniMax-M3` (and
   similar chat models) sometimes wraps per-asset records in a single-key
   object instead of a list. `import_data` then receives
   `{"600519.SH": {"item": [records]}}` instead of the expected
   `{"600519.SH": [records]}` shape, and the unwrap-less code path
   produces a DataFrame with one column named `item` → "no date column
   in data" error. This cascades into "数据为空" errors when
   `run_backtest` then has no data to consume.

## Fixes

### Fix 3b — Smart recursive scaffold (new module + startup hook)

**New file**: `src/strategy_research/core/workspace_setup.py`

Exposes `smart_init_workspace_templates(workspace, *, verbose=False)`.

**Algorithm** (recursive, idempotent, non-overwriting):

1. `mkdir templates/` in workspace (idempotent).
2. Walk `package_templates/` with `Path.rglob("*")` (sorted).
3. Skip top-level dirs in `{"prompts"}` (agent role prompts, not
   workspace content).
4. For each path:
   - Directory → `mkdir(parents=True, exist_ok=True)` in workspace.
   - File present in workspace → record as `skipped`, do **not** copy.
   - File missing in workspace → `shutil.copy2(...)` into place.
5. Return `{"copied": [rel_path...], "skipped": [...], "errors": [...]}`.
6. Log summary: "X new files, Y skipped (existing)" or "up-to-date".

**Wired in**: `src/strategy_research/api/app.py:create_app()`, immediately
after the existing `init_db(workspace_path)` call. Runs on every server
start (including `--reload`). Failures are caught and logged at WARNING
level so a broken scaffold never blocks the server from starting.

**Smart skip semantics**:

- **User customizations are preserved.** If a user edits
  `workspace/templates/strategy.py`, the next start will see it as
  existing and skip the copy. User wins.
- **New package versions auto-propagate.** If a future release adds
  `templates/.skills/new-skill.md`, the next start will see the file is
  missing and copy it. No migration step required.
- **No deletion.** If a user removes a file from workspace, scaffold
  does **not** re-add it (would surprise the user). The user is
  expected to delete the whole `templates/` if they want a clean slate.

**File count on first run** for the current package:

| Path                        | Count |
|-----------------------------|-------|
| Top-level (5 files)         | 5     |
| `.skills/*.md` (27 files)   | 27    |
| **Total scaffolded**        | **32** |

`README.md` is excluded from scaffold (it's a package README, not a
workspace asset). `.prompts/` is excluded by the top-dir filter (the
function is `os.walk`-style but the check is `rel.parts[0] in
_EXCLUDED_TOP_DIRS`).

### Fix 1 — import_data defensive unwrap

**File**: `src/strategy_research/core/agent/builtin_tools/data_tools.py`

**Change 1 — defensive unwrap** (in `ImportDataTool.execute()`):

Before the loop `for code, records in data.items():`, add a per-iteration
unwrap that handles the common LLM hallucination:

```python
_LIST_WRAPPER_KEYS = ("item", "data", "records", "bars", "rows", "ohlcv", "values")

for code, records in data.items():
    if isinstance(records, dict):
        unwrapped = None
        for key in _LIST_WRAPPER_KEYS:
            if key in records and isinstance(records[key], list):
                unwrapped = records[key]
                break
        if unwrapped is None:
            return _err(
                f"data[{code!r}] is a dict (length {len(records)}) but contains no "
                f"list of records. Got keys: {list(records.keys())[:5]}. "
                f"Expected: data[{code!r}] = [{{'trade_date': '...', 'close': ...}}, ...]. "
                f"Fix: call get_market_data(codes=[{code!r}], start_date='...', end_date='...') "
                f"first, then pass result.data as the data argument."
            )
        records = unwrapped
    ...
```

The error message includes the actual keys received, the expected shape,
and a concrete fix instruction so the LLM can recover on the next
iteration without trial-and-error.

**Change 2 — schema enrichment** (in `ImportDataTool.parameters`):

```python
"data": {
    "type": "object",
    "description": (
        "OHLCV data dict from get_market_data. "
        "Format: {asset_code: [records]}. "
        "Each record has 'trade_date' (or 'date') + OHLCV fields. "
        "Example: {'600519.SH': [{'trade_date': '2023-12-11', "
        "'close': 1544.555, 'open': 1536.555, 'high': 1550.555, "
        "'low': 1503.555, 'volume': 36831.0}, ...]}"
    ),
    "additionalProperties": {
        "type": "array",
        "items": {"type": "object"},
    },
},
```

This adds an explicit `additionalProperties` constraint that signals to
the LLM that `data[code]` is an array, not an object. Doesn't enforce
strictly (LLM may still wrap), but guides the model toward the right
shape and pairs with the defensive unwrap for safety.

## Tests

### `tests/test_workspace_setup.py` (new, 7 tests)

- `test_empty_workspace_copies_all` — fresh workspace → 32 files copied
- `test_idempotent_no_overwrite` — re-run preserves user customizations
- `test_partial_workspace_only_copies_missing` — partial workspace
- `test_prompts_not_copied` — `.prompts/` filter works
- `test_skills_recursively_copied` — 27 skill files scaffolded
- `test_handles_missing_package_dir` — graceful failure on missing pkg
- `test_recursive_into_subdirs` — future-proof: handles nested dirs

### `tests/test_import_data_robustness.py` (new, 7 tests)

- `test_standard_shape` — backward compat
- `test_unwrap_item_wrapper` — `{"item": [...]}` auto-unwrap
- `test_unwrap_data_wrapper` — `{"data": [...]}` auto-unwrap
- `test_unwrap_records_wrapper` — `{"records": [...]}` auto-unwrap
- `test_dict_no_known_key_clear_error` — actionable error message
- `test_backward_compat_empty_data` — `{"A": []}` still ok
- `test_schema_has_unwrapped_array` — schema declares array

## Files Changed

| File | Type | Description |
|------|------|-------------|
| `src/strategy_research/core/workspace_setup.py` | NEW | Smart scaffold module |
| `src/strategy_research/api/app.py` | MOD | Add `smart_init_workspace_templates` call in `create_app()` |
| `src/strategy_research/core/agent/builtin_tools/data_tools.py` | MOD | `ImportDataTool` defensive unwrap + schema |
| `tests/test_workspace_setup.py` | NEW | 7 scaffold tests |
| `tests/test_import_data_robustness.py` | NEW | 7 import_data tests |
| `docs/archive/undated/scaffold-fix.md` | NEW | This document |

## Backward Compatibility

- **Scaffold**: Idempotent, no existing files modified, no destructive
  operations. Safe to enable on every server start.
- **import_data unwrap**: When `data[code]` is already a list (the
  expected shape), the new code path is a no-op — `isinstance(records,
  dict)` is False, loop body unchanged. 100% backward compatible.
- **Schema enrichment**: Pure documentation; no validation is added
  that could reject previously-accepted calls.

## Verification

After this PR:

1. Start server with empty workspace → `templates/` gets 32 files.
2. Restart server → log shows "up-to-date (32 files present)".
3. Edit `workspace/templates/strategy.py` → restart → edit preserved.
4. LLM calls `import_data` with `{"item": [...]}` → auto-unwrap, ok.
5. LLM calls `import_data` with `{"foo": 1}` → clear actionable error.

## Out of Scope (Follow-up PRs)

- **Fix 2** (full schema strict mode for `import_data`) — would require
  LLM strict tool-call mode; not portable across providers.
- **Fix 4** (actionable "数据为空" error in `config_runner.py:370`) —
  useful but lower priority; the underlying `import_data` failure is
  already fixed.
- **Generic `_safe_get_param` / `_err_actionable` utils** — would
  harden all 33 tools with a shared pattern. Worth doing, but a
  separate PR to keep this one focused.
