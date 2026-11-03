"""Migrate historical study agent JSON files to event_log.

Usage:
    python -m strategy_research.scripts.migrate_study_history <study_id> <workspace_path> [--reset]

The session DB is resolved from the workspace path (same file the
backend uses when started with that workspace as cwd):

    <workspace_path>/.quantnodes_strategy_research_session.db

Events are batch-inserted in a single SQLite transaction (the
EventStore's per-event commit is ~28ms on a large DB, which makes a
per-event replay of a few thousand events take minutes; the batched
path below finishes in under a second).

``--reset`` deletes previously-migrated study rows (sessions /
messages / message_parts / event_log) for this study before
re-migrating. Use it to recover from a partial migration.

Example:
    python -m strategy_research.scripts.migrate_study_history study_f48295053041 /home/ll/Public/qn-research --reset
"""

import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from strategy_research.apps.study.engine_common import SESSION_DB_FILENAME

# Event types that are only useful for live streaming, not historical replay
# These are skipped when migrating to event_log since the projector doesn't
# need them (text.ended already contains the complete text)
STREAMING_ONLY_EVENTS = {"text_delta", "thinking_delta", "llm_usage"}


def _resolve_owner_user_id(conn: sqlite3.Connection) -> str:
    """Inherit the most common user_id among existing sessions.

    Study sessions must be owned by the real backend user so the
    chat session API's IDOR check (`_fetch_session_owned`) passes.
    """
    row = conn.execute(
        "SELECT user_id, COUNT(*) AS c FROM sessions "
        "WHERE user_id IS NOT NULL AND user_id != 'system' "
        "GROUP BY user_id ORDER BY c DESC LIMIT 1"
    ).fetchone()
    return row["user_id"] if row else "system"


def _reset_study_rows(conn: sqlite3.Connection, study_id: str) -> None:
    """Delete previously-migrated rows for this study (idempotent re-run)."""
    conn.execute('DELETE FROM message_parts WHERE session_id LIKE ?', (f"study:{study_id}:%",))
    conn.execute('DELETE FROM messages WHERE session_id LIKE ?', (f"study:{study_id}:%",))
    conn.execute('DELETE FROM event_log WHERE aggregate_id LIKE ?', (f"study:{study_id}:%",))
    conn.execute('DELETE FROM sessions WHERE id LIKE ?', (f"study:{study_id}:%",))
    conn.commit()


def migrate_study_history(study_id: str, workspace_path: str, reset: bool = False) -> None:
    workspace = Path(workspace_path).resolve()
    study_dir = workspace / "study" / study_id
    db_path = workspace / SESSION_DB_FILENAME

    if not study_dir.exists():
        print(f"Study directory not found: {study_dir}")
        return
    if not db_path.exists():
        print(f"Session DB not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    if reset:
        print(f"Resetting existing study rows for {study_id}...")
        _reset_study_rows(conn, study_id)

    owner_user_id = _resolve_owner_user_id(conn)
    print(f"Session DB: {db_path}")
    print(f"Owner user: {owner_user_id}")

    now = time.time()
    total_events = 0
    total_agents = 0
    session_rows: list[tuple] = []
    event_rows: list[tuple] = []

    for round_dir in sorted(study_dir.glob("rounds/round_*")):
        round_num = int(round_dir.name.split("_")[1])
        run_dir = round_dir / "run_0001"
        agents_dir = run_dir / "agents"

        if not agents_dir.exists():
            continue

        session_id = f"study:{study_id}:round:{round_num}"

        # Idempotency: skip rounds already migrated
        existing = conn.execute(
            "SELECT COUNT(*) AS c FROM event_log WHERE aggregate_id = ?",
            (session_id,),
        ).fetchone()["c"]
        if existing > 1:
            print(f"{session_id}: already migrated ({existing} events), skipping")
            continue

        session_rows.append((
            session_id,
            f"Study {study_id} Round {round_num}",
            owner_user_id,
            now,
            now,
        ))

        # Per-session seq counter starts after any existing events
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM event_log WHERE aggregate_id = ?",
            (session_id,),
        ).fetchone()["m"]

        def emit(evt_type: str, data: dict) -> None:
            nonlocal seq
            seq += 1
            event_rows.append((
                str(uuid.uuid4()),
                session_id,
                seq,
                evt_type,
                json.dumps(data, ensure_ascii=False),
                now,
                None,
                "main",
            ))

        emit("session.created", {"title": f"Study {study_id} Round {round_num}"})
        print(f"Queueing session: {session_id}")

        for hist_file in sorted(agents_dir.glob("*_history.json")):
            agent_id = hist_file.stem.replace("_history", "")
            message_id = f"study:{study_id}:r{round_num}:{agent_id}"

            try:
                history = json.loads(hist_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                print(f"  Error reading {hist_file}: {e}")
                continue

            agent_events = 0
            for evt in history:
                evt_type = evt.get("type", "")
                data = evt.get("data", {})

                if evt_type in STREAMING_ONLY_EVENTS:
                    continue
                if not isinstance(data, dict):
                    continue

                data["message_id"] = message_id
                data["agent_id"] = agent_id
                emit(evt_type, data)
                agent_events += 1

            # Final agent output → assistant_message boundary event
            agent_json = agents_dir / f"{agent_id}.json"
            if agent_json.exists():
                try:
                    agent_data = json.loads(agent_json.read_text())
                    output = agent_data.get("output", "")
                    if output:
                        emit("assistant_message", {
                            "message_id": message_id,
                            "agent_id": agent_id,
                            "content": output,
                            "message_type": "assistant",
                        })
                        agent_events += 1
                except (json.JSONDecodeError, OSError) as e:
                    print(f"  Error reading {agent_json}: {e}")

            total_events += agent_events
            total_agents += 1
            print(f"  Queued {agent_id}: {agent_events} events")

    # Single-transaction batch write (sessions + events)
    if session_rows:
        print(f"\nWriting {len(session_rows)} sessions + {len(event_rows)} events...")
        conn.execute("BEGIN")
        conn.executemany(
            "INSERT OR IGNORE INTO sessions (id, title, user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            session_rows,
        )
        conn.executemany(
            "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, "
            "time_created, parent_event_id, branch_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            event_rows,
        )
        conn.commit()
        print("Committed.")

    conn.close()

    # Flush projector to materialize messages + message_parts
    if session_rows:
        print("\nFlushing projector...")
        from strategy_research.api.session.projector import Projector
        proj = Projector(db_path)
        for (session_id, *_rest) in session_rows:
            try:
                state, touched = proj.project_incremental(session_id, collect_touched=True)
                proj.flush(state, touched=touched)
                print(f"  Flushed {session_id}: {len(state.messages)} messages")
            except Exception as e:
                print(f"  Error flushing {session_id}: {e}")

    print(f"\nDone: {total_agents} agents, {total_events} events migrated")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    reset = "--reset" in sys.argv
    if len(args) != 2:
        print("Usage: python -m strategy_research.scripts.migrate_study_history <study_id> <workspace_path> [--reset]")
        sys.exit(1)

    migrate_study_history(args[0], args[1], reset=reset)
