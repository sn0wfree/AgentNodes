# Incremental Projector (O(n²) → O(delta))

Date: 2026-08-02
Related: `api/session/projector.py`, `api/session/event_bus_v2.py`,
`docs/archive/undated/compaction-summary-fix.md` (B2/B3/B4 context)

## Problem

Every projector flush is a **full replay**:

```
publish_batch → _flush_projection(session_id)
  → Projector.project(session_id)      # re-reads ALL events from seq 0
  → Projector.flush(state)             # re-UPSERTs ALL messages + parts
```

With N events and M messages in a session, each flush is O(N + M).
Sessions with thousands of messages degrade quadratically: a 10k-message
session re-reads and re-writes everything on every boundary event
(`message_received`, `assistant_message`, `compact.ended`). Each write
also churns the `messages`/`message_parts` FTS triggers.

Secondary issue: `EventBusV2._persist_locked` opens + commits a fresh
sqlite connection per event (including every `text_delta`). Local-file
sqlite connects are cheap (~µs) but the per-event connect/commit is
unnecessary work.

## Design

### 1. In-memory projection cache (the main fix)

`Projector` gains a per-session cache: `session_id → ProjectedSession`.
This is the "live projector" the `Projector.apply` docstring already
anticipated ("Future: live projector that maintains state in memory").

```
Projector.project_incremental(session_id) -> ProjectedSession
  - cache hit:  load_events(session_id, after_seq=cached.last_seq)
                → _apply each into cached state → update cached.last_seq
  - cache miss: project(session_id) (full build) → cache it
  Returns the (mutated) cached state.
```

- `ProjectedSession` remains a plain dataclass; the cache is the only
  new state. `project()` stays a pure function (unchanged semantics,
  used by tests and cold paths).
- `Projector.invalidate(session_id)` drops the cache entry. Called on
  session deletion (`web_session.delete_session` → bus hook).
- Bounded: one state object per active session; sessions are few
  (tabs), each state holds only parsed parts — acceptable memory.

### 2. Delta flush (only write what changed)

`Projector.flush(state)` currently writes all rows. Add a state
signature so we can skip untouched rows:

- Compute a cheap fingerprint per message (id → seq/content-length/part
  count hash) on the previous cached state and the new state.
- Only UPSERT messages whose fingerprint changed, and only upsert/delete
  `message_parts` rows for messages whose part-set changed.
- `flush(..., previous: ProjectedSession | None)` — when `previous` is
  None (cold path), fall back to full flush (current behavior).

Keeps the existing safety properties: single transaction, scoped to
`state.session_id`, deletes parts that no longer exist.

### 3. Connection reuse in `_persist_locked`

Batch the single-event path: `publish()` still appends to event_log
immediately (never batch buffering — events must be durable before SSE
forward), but `_persist_locked` reuses one shared connection instead of
opening per event. Thread safety: the bus lock already serializes all
writes; a single connection guarded by `self._lock` is safe (sqlite
`check_same_thread` is irrelevant because only the lock-holder touches
it). Connection is created lazily and kept for the bus lifetime.

Fallback: on `OperationalError` (db file replaced) close and reconnect
once, then log-and-skip as today.

## Compatibility

- `EventBusV2` public API unchanged (`publish`, `publish_batch`, seq
  semantics). `_flush_projection` switches to
  `project_incremental` + delta flush internally.
- `project()`, `apply()`, `project_to_messages()`, `flush()` keep their
  signatures; `flush` gains an optional `previous` kwarg (default None).
- All existing event-bus / projector tests must stay green: they assert
  messages-table contents after publish sequences, which the delta path
  must reproduce exactly (idempotence preserved).
- Crash-safety argument is unchanged: `event_log` remains the source of
  truth; a stale/empty cache just means a full rebuild on next flush.

## Files

- `src/strategy_research/api/session/projector.py` — cache +
  `project_incremental` + delta flush.
- `src/strategy_research/api/session/event_bus_v2.py` — use incremental
  flush; shared connection in `_persist_locked`; `invalidate` on
  session delete.
- `src/strategy_research/api/routers/web_session.py` — call
  `projector.invalidate` on `delete_session`.
- `tests/test_phase2_data_integrity.py`, event-bus tests — new cases:
  delta flush after incremental publish, cache invalidation on delete,
  cache-hit equivalence with full replay.

## Verification

- Existing suite: `pytest tests/ -k "projector or event_bus or phase2" -q`
- New tests: incremental equivalence (state built incrementally == state
  built from scratch), invalidation, delta-flush row-count assertions.
- Perf smoke (manual): publish 2000 events → flush count of written rows
  ≪ 2000 on the 2nd flush.
