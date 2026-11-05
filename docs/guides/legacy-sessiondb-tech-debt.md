# Legacy SessionDB Technical Debt

> **Status**: deferred. Not migrated to event-sourcing. Independent of
> webui event-sourcing migration (B-series).
>
> **Risk**: low. Legacy SessionDB is a separate DB file used by TUI/CLI
> only. Does not affect webui runtime.
>
> **Migration plan**: see [Migration Roadmap](#migration-roadmap) below.

## Background

The legacy `core.session.db.SessionDB` is a SQLite store used by the
TUI, CLI, and MCP server. It is **completely separate** from the
webui DB (`quantnodes_strategy_research_user.db`):

| Aspect | Legacy SessionDB | Webui DB |
|--------|------------------|----------|
| Path | `~/.quantnodes-research/sessions.db` | `~/.quantnodes/quantnodes_strategy_research_user.db` |
| Schema | `id INTEGER`, `role`, `content`, `timestamp` | `id TEXT`, `role`, `content`, `created_at`, `seq`, `message_type`, `message_parts` |
| Message parts | No | Yes (Phase 2) |
| Message type | No | Yes (user/assistant/tool/compaction) |
| FTS5 | Yes (auto-trigger) | Yes (auto-trigger) |
| Event-sourced | No | Yes (Phase 3 B5) |

## Call sites

Production read sites (read-only):
- `api/routers/session.py` (webui router) — list/search sessions
- `core/mcp/server.py` (MCP server) — list/search messages

Production write sites:
- `core/hooks/bundled/session_memory.py` — `SessionMemoryHook.archive_session()`
  writes to SessionDB when archiving a TUI/CLI session
- `cli/commands/session.py` — CLI `session delete` (deletes all messages)
- `cli/commands/slash_session.py` — CLI slash commands

None of these paths are exercised by the webui chat flow (which uses
the modern webui DB + event-sourcing).

## Why not migrated

The webui event-sourcing migration (B-series) is **complete** for the
primary chat product. The legacy SessionDB is used by:

1. **TUI** (terminal UI) — separate product, not deployed to users
2. **CLI** (command-line tools) — used by developers, not users
3. **MCP server** — read-only access to legacy data
4. **session_memory hook** — automatic session archiving for TUI/CLI

Migrating would require:
- Schema migration: add `event_log` table to `sessions.db`
- Wire `EventBusV2` into `SessionManager` and all CLI/TUI entry points
- Backfill: convert existing `sessions.db` rows to events
- Testing: all TUI/CLI flows still work
- Time: ~1-2 days of focused work

The webui migration is a higher priority (active user product) and is
now stable. Legacy migration is a separate project.

## Migration Roadmap

When ready to migrate (post-B-series):

### Phase 1: Schema + event_log
1. Add `event_log` table to `sessions.db` (same schema as webui)
2. Update `SessionDB._init_db()` to create event_log

### Phase 2: EventBusV2 wiring
1. Create `EventBusV2` for legacy path
2. Update `SessionDB.add_message()` to use events (similar to B2)
3. Update `SessionDB.add_message_batch()` similarly
4. Hooks into `SessionMemoryHook.archive_session()` to use events

### Phase 3: Backfill
1. Add backfill tool: `core.session.db.add_message` → event_log
2. Run backfill on existing `sessions.db`

### Phase 4: Read path
1. Add `SessionDB.get_messages_via_projector()` (event_log read)
2. Default: use projector (like webui B3)

### Phase 5: Cleanup
1. Remove direct `INSERT INTO messages`
2. Document the new architecture
3. Add comprehensive tests

### Estimated effort
- Phase 1: 0.5 day
- Phase 2: 1 day
- Phase 3: 0.5 day
- Phase 4: 0.5 day
- Phase 5: 0.5 day
- **Total: ~3 days**

## Verification of non-impact

The legacy SessionDB does **not** affect webui because:
1. Different DB files (sessions.db vs quantnodes_strategy_research_user.db)
2. Different schema (no shared IDs)
3. Different runtime paths (TUI/CLI never invoked from webui)
4. Webui's `SessionStore` uses the webui DB exclusively

If users have data in `sessions.db` from old TUI/CLI usage, it
remains intact and queryable through the MCP server. No data loss.

## Related files

- `src/strategy_research/core/session/db.py` — legacy SessionDB
- `src/strategy_research/core/session/manager.py` — SessionManager wrapper
- `src/strategy_research/core/hooks/bundled/session_memory.py` — archiver
- `src/strategy_research/api/routers/session.py` — read-only webui use
- `src/strategy_research/cli/tui/app.py` — TUI use
- `src/strategy_research/cli/commands/session.py` — CLI use
- `src/strategy_research/cli/commands/slash_session.py` — CLI use
- `src/strategy_research/core/mcp/server.py` — MCP server use
