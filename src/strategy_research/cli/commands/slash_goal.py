"""``/goal`` slash-command shims.

Thin command wrappers around :class:`GoalStore` for the interactive REPL:

* :func:`cmd_status` (``/goal`` or ``/goal status``) — render current snapshot.
* :func:`cmd_start` (``/goal start <objective>``) — create a new goal.
* :func:`cmd_evidence` (``/goal evidence <idx> <note>``) — append evidence.
* :func:`cmd_complete` (``/goal complete [recap]``) — mark complete.
* :func:`cmd_cancel` (``/goal cancel [recap]``) — cancel.
* :func:`cmd_workflows` (``/goal workflows [list|show|path]``) — enumerate workflow presets.
* :func:`cmd_checkpoint` (``/goal checkpoint [save|list|resume|delete]``) — checkpoint control.
* :func:`cmd_help` (``/goal help``) — usage panel.

Phase 4 v0.5.1 added workflow + checkpoint subcommands.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from strategy_research.cli.theme import get_console


def _resolve_db_path() -> Path:
    """Return the goal DB path. Default to ``<cwd>/goals.db``."""
    raw = os.environ.get("STRATEGY_RESEARCH_GOAL_DB")
    if raw:
        return Path(raw).expanduser()
    return Path.cwd() / "goals.db"


def _resolve_console(console: Any = None) -> Console:
    return console or get_console()


def _store():
    from strategy_research.core.goal import GoalStore
    return GoalStore(db_path=_resolve_db_path())


def _user_workflows_dir() -> Path:
    """Return the user-scope workflows directory.

    Defaults to ``~/.quantnodes-research/workflows/``. Exposed for
    monkeypatching in tests.
    """
    raw = os.environ.get("STRATEGY_RESEARCH_WORKFLOWS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".quantnodes-research" / "workflows"


def _checkpoint_base_dir() -> Path:
    """Return the checkpoint base directory.

    Defaults to ``~/.quantnodes-research/checkpoints/``. Exposed for
    monkeypatching in tests.
    """
    raw = os.environ.get("STRATEGY_RESEARCH_CHECKPOINT_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".quantnodes-research" / "checkpoints"


def cmd_status(*, console: Any = None, session_id: str = "cli") -> int:
    """``/goal`` — render current goal snapshot."""
    console = _resolve_console(console)
    try:
        store = _store()
        snapshot = store.get_current_snapshot(session_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal status failed:[/red] {exc}")
        return 1

    if not snapshot:
        console.print("[yellow]No active goal. Use /goal start <objective>.[/yellow]")
        return 0

    goal = snapshot.get("goal", {})
    title = goal.get("objective", "(no objective)")
    console.print(Panel(
        f"[bold]{title}[/bold]\n"
        f"status: {goal.get('status')}\n"
        f"protocol: {goal.get('protocol')}\n"
        f"created: {goal.get('created_at', '')[:19]}",
        title=f"Goal {goal.get('goal_id', '')[:12]}",
        border_style="blue",
    ))

    criteria = snapshot.get("criteria", [])
    if criteria:
        table = Table(show_header=True, header_style="bold")
        table.add_column("#")
        table.add_column("Criterion")
        table.add_column("Status")
        for i, c in enumerate(criteria, 1):
            table.add_row(str(i), str(c.get("text", ""))[:60], str(c.get("status", "")))
        console.print(table)
    return 0


def cmd_start(objective: str, *, console: Any = None,
              session_id: str = "cli", template_key: str = "",
              workflow_name: str = "") -> int:
    """``/goal start <objective> [--template <key>] [--workflow <name>]`` — create a new goal.

    Phase 4 v0.5.1: ``--workflow <name>`` loads a YAML workflow preset and
    runs the DAG synchronously, recording evidence and auto-completing per
    the workflow's completion config. CLI standalone mode is best-effort:
    it executes the workflow in-process and reports the final state.

    If both ``--workflow`` and ``--template`` are provided, ``--workflow``
    takes precedence and ``--template`` is ignored.
    """
    console = _resolve_console(console)
    if not objective.strip():
        console.print(
            "[red]Usage:[/red] "
            "/goal start <objective> [--template <key>] [--workflow <name>]"
        )
        return 1
    try:
        store = _store()

        # ── Workflow path (Phase 4 v0.5.1) ──
        if workflow_name:
            return _start_workflow(
                objective=objective,
                workflow_name=workflow_name,
                session_id=session_id,
                console=console,
                store=store,
            )

        # ── Legacy / template path ──
        from strategy_research.core.goal.context import default_goal_criteria
        from strategy_research.core.goal.models import RiskTier
        from strategy_research.core.goal.templates import get_template

        criteria = default_goal_criteria()
        risk_tier = RiskTier.RESEARCH_GENERAL
        if template_key:
            tmpl = get_template(template_key)
            if tmpl is None:
                console.print(f"[red]Unknown template:[/red] {template_key}")
                console.print("[dim]Available: " + ", ".join(
                    sorted(getattr(__import__('strategy_research.core.goal.templates',
                                              fromlist=['TEMPLATES']), 'TEMPLATES').keys())
                ) + "[/dim]")
                return 1
            criteria = tmpl.criteria
            # tmpl.risk_tier may be a string — coerce to RiskTier enum.
            tier_value = getattr(tmpl, "risk_tier", "research_general")
            risk_tier = (
                tier_value
                if isinstance(tier_value, RiskTier)
                else RiskTier(tier_value)
            )

        goal = store.replace_goal(
            session_id=session_id,
            objective=objective,
            criteria=criteria,
            source="cli",
            protocol="thesis_review",
            risk_tier=risk_tier,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal start failed:[/red] {exc}")
        return 1
    console.print(f"[green]Started goal:[/green] {goal.goal_id}")
    return 0


def _start_workflow(
    *,
    objective: str,
    workflow_name: str,
    session_id: str,
    console: Console,
    store: Any,
) -> int:
    """Load a YAML workflow and execute it synchronously (CLI standalone).

    Creates a goal row with ``workflow_id`` set, then runs the DAG in-process
    and reports evidence / completion status. Returns 0 on success, 1 on
    workflow-load or execution failure.
    """
    from strategy_research.core.goal.workflow import GoalWorkflowRunner
    from strategy_research.core.goal.workflow_config import load_goal_workflow

    try:
        config = load_goal_workflow(workflow_name)
    except FileNotFoundError as exc:
        console.print(f"[red]Workflow not found:[/red] {workflow_name}")
        console.print(f"[dim]{exc}[/dim]")
        return 1
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to load workflow:[/red] {exc}")
        return 1

    runner = GoalWorkflowRunner(
        config=config,
        session_id=session_id,
        store=store,
        workspace=Path.cwd(),
    )

    console.print(
        f"[cyan]Running workflow:[/cyan] {config.name} "
        f"({len(config.agents)} agents)"
    )
    try:
        goal_id = asyncio.run(runner.start(objective))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Workflow execution failed:[/red] {exc}")
        return 1

    progress = runner.get_progress()
    status = progress.get("status", "unknown")
    evidence = progress.get("evidence_count", 0)
    console.print(
        f"[green]Workflow finished:[/green] goal={goal_id[:12]} "
        f"status={status} evidence={evidence}"
    )
    return 0


def cmd_evidence(criterion_ref: str, note: str, *,
                 console: Any = None,
                 session_id: str = "cli") -> int:
    """``/goal evidence <idx-or-id> <note>``."""
    console = _resolve_console(console)
    if not criterion_ref or not note.strip():
        console.print("[red]Usage:[/red] /goal evidence <idx-or-id> <note>")
        return 1
    try:
        from strategy_research.core.goal.models import EvidenceInput

        store = _store()
        snapshot = store.get_current_snapshot(session_id)
        if not snapshot:
            console.print("[yellow]No active goal.[/yellow]")
            return 0

        goal_id = snapshot["goal"]["goal_id"]
        criteria = snapshot.get("criteria", [])
        # Resolve criterion_ref — 1-based index or exact id or prefix
        criterion_id = None
        try:
            idx = int(criterion_ref) - 1
            if 0 <= idx < len(criteria):
                criterion_id = criteria[idx].get("criterion_id")
        except ValueError:
            pass
        if criterion_id is None:
            for c in criteria:
                cid = str(c.get("criterion_id", ""))
                if cid == criterion_ref or cid.startswith(criterion_ref):
                    criterion_id = cid
                    break
        if criterion_id is None:
            console.print(f"[red]Unknown criterion:[/red] {criterion_ref}")
            return 1

        store.append_evidence(
            session_id=session_id,
            goal_id=goal_id,
            expected_goal_id=goal_id,
            evidence=EvidenceInput(
                criterion_id=criterion_id,
                text=note,
                source_provider="cli",
                source_type="manual_note",
            ),
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal evidence failed:[/red] {exc}")
        return 1
    console.print("[green]Evidence recorded.[/green]")
    return 0


def cmd_complete(recap: str = "", *, console: Any = None,
                 session_id: str = "cli", lite: bool = False) -> int:
    """``/goal complete [recap] [--lite]`` — mark the goal complete.

    With ``--lite``, only verifies evidence coverage (no audit required).
    Without ``--lite``, requires audit rows for all required criteria.
    """
    console = _resolve_console(console)
    try:
        from strategy_research.core.goal.models import AuditRow, GoalStatus

        store = _store()
        snapshot = store.get_current_snapshot(session_id)
        if not snapshot:
            console.print("[yellow]No active goal.[/yellow]")
            return 0
        goal_id = snapshot["goal"]["goal_id"]
        criteria = snapshot.get("criteria", [])

        if lite:
            # Lite mode: just verify all required criteria have evidence
            store.complete_lite(
                session_id=session_id,
                goal_id=goal_id,
                expected_goal_id=goal_id,
                recap=recap or "(lite completion)",
            )
        else:
            all_covered = snapshot.get("all_covered", False)
            if criteria and not all_covered:
                console.print("[red]Cannot complete:[/red] not all criteria have evidence.")
                return 1
            evidence_ids = [e.get("evidence_id", "") for e in snapshot.get("evidence", [])]
            audit_rows = [
                AuditRow(
                    criterion_id=str(c.get("criterion_id", "")),
                    result="satisfied",
                    evidence_ids=evidence_ids,
                    notes=recap or "",
                )
                for c in criteria
            ]
            store.update_status(
                session_id=session_id,
                goal_id=goal_id,
                expected_goal_id=goal_id,
                status=GoalStatus.COMPLETE,
                audit=audit_rows,
                recap=recap or None,
            )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal complete failed:[/red] {exc}")
        return 1
    mode = " (lite)" if lite else ""
    console.print(f"[green]Goal completed{mode}.[/green]")
    return 0


def cmd_cancel(recap: str = "", *, console: Any = None,
               session_id: str = "cli") -> int:
    """``/goal cancel [recap]``."""
    console = _resolve_console(console)
    try:
        from strategy_research.core.goal.models import GoalStatus

        store = _store()
        snapshot = store.get_current_snapshot(session_id)
        if not snapshot:
            console.print("[yellow]No active goal.[/yellow]")
            return 0
        goal_id = snapshot["goal"]["goal_id"]
        store.update_status(
            session_id=session_id,
            goal_id=goal_id,
            expected_goal_id=goal_id,
            status=GoalStatus.CANCELLED,
            recap=recap,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/goal cancel failed:[/red] {exc}")
        return 1
    console.print("[yellow]Goal cancelled.[/yellow]")
    return 0


def cmd_help(*, console: Any = None) -> int:
    """``/goal help`` — usage panel."""
    console = _resolve_console(console)
    body = (
        "/goal status                                  — show current goal\n"
        "/goal start <obj> [--template T] [--workflow W] — create a new goal\n"
        "/goal evidence <idx-or-id> <note>             — record evidence\n"
        "/goal complete [recap] [--lite]               — mark complete\n"
        "/goal cancel [recap]                          — cancel\n"
        "/goal templates                               — list templates (+ workflows)\n"
        "/goal workflows [list|show <n>|path <n>]      — enumerate workflow presets\n"
        "/goal checkpoint save|list|resume|delete ...  — checkpoint control\n"
    )
    console.print(Panel(body, title="/goal", border_style="dim"))
    return 0


def cmd_templates(*, console: Any = None) -> int:
    """``/goal templates`` — list available goal templates."""
    from strategy_research.core.goal.templates import list_templates
    console = _resolve_console(console)
    templates = list_templates()
    if not templates:
        console.print("[dim]No templates registered.[/dim]")
    else:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Key")
        table.add_column("Name")
        table.add_column("Description")
        table.add_column("Criteria", justify="right")
        for key, tmpl in sorted(templates.items()):
            table.add_row(key, tmpl.name, tmpl.description, str(len(tmpl.criteria)))
        console.print(table)

    # Phase 4 v0.5.1: also list workflows under templates view
    console.print("\n[dim]Workflow presets:[/dim]")
    workflows = _list_workflows_for_display()
    if workflows:
        for wf in workflows:
            console.print(
                f"  [cyan]{wf['name']}[/cyan]  {wf['description'][:60]}"
            )
    else:
        console.print("  [dim](none found)[/dim]")
    return 0


# ── Phase 4 v0.5.1 — workflow enumeration ─────────────────────────────


def _list_workflows_for_display() -> list[dict[str, str]]:
    """Read all built-in + user workflow presets for display.

    Wraps :func:`list_goal_workflows` but always returns dicts (never raises).
    Used by ``/goal templates`` and ``/goal workflows list``.
    """
    try:
        from strategy_research.core.goal.workflow_config import list_goal_workflows
        return list_goal_workflows()
    except Exception:  # noqa: BLE001
        return []


def _resolve_workflow_path(name_or_path: str) -> Path | None:
    """Resolve a workflow name or explicit path to a YAML file.

    Mirrors the search logic in :mod:`workflow_config` but returns the path
    directly for CLI display purposes (no parsing needed).
    """
    from strategy_research.core.goal import workflow_config

    path = Path(name_or_path)
    if path.exists() and path.suffix in (".yaml", ".yml"):
        return path

    candidates = [
        workflow_config._PRESETS_DIR / f"goal_{name_or_path}.yaml",
        workflow_config._PRESETS_DIR / f"{name_or_path}.yaml",
        _user_workflows_dir() / f"{name_or_path}.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def cmd_workflows(*args: str, console: Any = None) -> int:
    """``/goal workflows [list|show <name>|path <name>]`` — enumerate presets.

    Subcommands:
      * ``list`` (default) — table of name / description / source / agents
      * ``show <name>`` — render YAML path + first 30 lines
      * ``path <name>`` — print absolute YAML path
    """
    console = _resolve_console(console)

    if not args or args[0] == "list":
        return _cmd_workflows_list(console)
    if args[0] == "show" and len(args) >= 2:
        return _cmd_workflows_show(args[1], console)
    if args[0] == "path" and len(args) >= 2:
        return _cmd_workflows_path(args[1], console)

    console.print(
        "[red]Usage:[/red] /goal workflows [list|show <name>|path <name>]"
    )
    return 1


def _cmd_workflows_list(console: Console) -> int:
    workflows = _list_workflows_for_display()
    if not workflows:
        console.print("[yellow]No workflows found.[/yellow]")
        console.print(
            f"[dim]Searched: "
            f"{_user_workflows_dir()} "
            f"(and built-in presets)[/dim]"
        )
        return 0

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Source")
    for wf in workflows:
        path = wf.get("path", "")
        source = "user" if "workflows" in path else "built-in"
        table.add_row(
            wf.get("name", ""),
            wf.get("description", "")[:60],
            source,
        )
    console.print(table)
    return 0


def _cmd_workflows_show(name: str, console: Console) -> int:
    path = _resolve_workflow_path(name)
    if path is None:
        console.print(f"[red]Workflow not found:[/red] {name}")
        return 1

    try:
        import yaml
    except ImportError:
        console.print("[red]PyYAML required for show.[/red]")
        return 1

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    agents = data.get("agents", [])
    dag = data.get("dag", {})

    body = (
        f"[bold]{path.name}[/bold]\n"
        f"description: {data.get('description', '')}\n"
        f"version: {data.get('version', '1.0')}\n"
        f"agents: {len(agents)} | "
        f"dag nodes: {len(dag)} | "
        f"completion: {data.get('completion', {}).get('mode', 'auto')}\n"
        f"\n"
        f"[dim]path: {path}[/dim]"
    )
    console.print(Panel(body, title="Workflow", border_style="cyan"))
    return 0


def _cmd_workflows_path(name: str, console: Console) -> int:
    path = _resolve_workflow_path(name)
    if path is None:
        console.print(f"[red]Workflow not found:[/red] {name}")
        return 1
    console.print(str(path))
    return 0


# ── Phase 4 v0.5.1 — checkpoint control ──────────────────────────────


def cmd_checkpoint(*args, console: Any = None,
                   session_id: str = "cli") -> int:
    """``/goal checkpoint [save|list|resume|delete] [args]``.

    Subcommands:
      * ``save`` — checkpoint the current goal's workflow state (CLI mode
        prints state summary; full save requires active runner — currently
        returns 1 with explanation if no runner).
      * ``list [session_id]`` — list all checkpoints on disk.
      * ``resume [goal_id]`` — restore latest (or specified) checkpoint.
      * ``delete <goal_id>`` — remove a checkpoint directory.
    """
    console = _resolve_console(console)
    if not args:
        console.print(
            "[red]Usage:[/red] "
            "/goal checkpoint save|list|resume|delete [goal_id]"
        )
        return 1

    sub = args[0]
    rest = list(args[1:])

    if sub == "save":
        return _cmd_checkpoint_save(console)
    if sub == "list":
        sid = rest[0] if rest else None
        return _cmd_checkpoint_list(console, sid)
    if sub == "resume":
        goal_id = rest[0] if rest else ""
        return _cmd_checkpoint_resume(console, session_id, goal_id)
    if sub == "delete":
        if not rest:
            console.print("[red]Usage:[/red] /goal checkpoint delete <goal_id>")
            return 1
        return _cmd_checkpoint_delete(console, session_id, rest[0])

    console.print(
        "[red]Unknown checkpoint subcommand:[/red] " + sub + "\n"
        "Use save|list|resume|delete."
    )
    return 1


def _cmd_checkpoint_save(console: Console) -> int:
    """Checkpoint the active goal.

    CLI standalone mode: there is no live ``GoalWorkflowRunner`` attached
    to the CLI session — the workflow ran to completion in the same call
    as ``cmd_start --workflow``. We therefore persist a "summary" snapshot
    of the current goal state (post-completion), which lets ``resume``
    later restore context even though the DAG itself has finished.

    For long-running workflows (TUI mode), checkpointing is driven by the
    ``WorkflowWorker`` (Phase 4 v0.5.2).
    """
    try:
        store = _store()
        snapshot = store.get_current_snapshot("cli")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to access store:[/red] {exc}")
        return 1

    if not snapshot:
        console.print("[yellow]No active goal. Nothing to checkpoint.[/yellow]")
        return 1

    goal = snapshot.get("goal", {})
    goal_id = goal.get("goal_id", "")
    if not goal_id:
        console.print("[yellow]No active goal.[/yellow]")
        return 1

    try:
        from strategy_research.core.goal.checkpoint_store import CheckpointStore
    except ImportError as exc:
        console.print(f"[red]CheckpointStore unavailable:[/red] {exc}")
        return 1

    state = {
        "goal_status": goal.get("status"),
        "objective": goal.get("objective"),
        "protocol": goal.get("protocol"),
        "risk_tier": goal.get("risk_tier"),
        "checkpoint_kind": "summary",
    }
    layer_results = {
        "criteria": snapshot.get("criteria", []),
        "evidence": snapshot.get("evidence", []),
    }
    cp = CheckpointStore(base_dir=_checkpoint_base_dir())
    cp.save(
        session_id="cli",
        goal_id=goal_id,
        state=state,
        layer_results=layer_results,
        workflow_name=goal.get("protocol", ""),
    )
    console.print(
        f"[green]Checkpoint saved:[/green] {goal_id[:12]}"
    )
    return 0


def _cmd_checkpoint_list(console: Console, session_id: Optional[str]) -> int:
    try:
        from strategy_research.core.goal.checkpoint_store import CheckpointStore
    except ImportError as exc:
        console.print(f"[red]CheckpointStore unavailable:[/red] {exc}")
        return 1

    cp = CheckpointStore(base_dir=_checkpoint_base_dir())
    items = cp.list_checkpoints(session_id=session_id)
    if not items:
        scope = f"session '{session_id}'" if session_id else "any session"
        console.print(f"[dim]No checkpoints in {scope}.[/dim]")
        return 0

    table = Table(show_header=True, header_style="bold")
    table.add_column("Goal ID")
    table.add_column("Workflow")
    table.add_column("Session")
    table.add_column("Created")
    for meta in items:
        table.add_row(
            meta.get("goal_id", "")[:12],
            meta.get("workflow_name", ""),
            meta.get("session_id", ""),
            meta.get("created_at", "")[:19],
        )
    console.print(table)
    return 0


def _cmd_checkpoint_resume(console: Console, session_id: str,
                           goal_id: str) -> int:
    """Resume from a checkpoint and continue execution.

    深度统一 B2（docs/deep-unification-graph-engine-design-20261031.md
    决定 E）：装载 workflow 配置 → ``GoalWorkflowRunner.resume_from_checkpoint``
    → ``resume_and_continue()``——pre_completed + ``run(start=layer_k)``
    从断点层续跑，已完成 agent 不重跑。workflow 名缺失或装载失败时
    回退为检视模式（v0.5.1 行为：还原元数据供检查）。
    """
    try:
        from strategy_research.core.goal.checkpoint_store import CheckpointStore
    except ImportError as exc:
        console.print(f"[red]CheckpointStore unavailable:[/red] {exc}")
        return 1

    cp = CheckpointStore(base_dir=_checkpoint_base_dir())

    # Resolve goal_id: latest if blank
    if not goal_id:
        items = cp.list_checkpoints(session_id=session_id)
        if not items:
            console.print(
                f"[yellow]No checkpoints to resume for "
                f"session '{session_id}'.[/yellow]"
            )
            return 0
        goal_id = items[-1]["goal_id"]

    data = cp.load(session_id=session_id, goal_id=goal_id)
    if data is None:
        console.print(
            f"[red]Checkpoint not found:[/red] session='{session_id}' "
            f"goal='{goal_id}'"
        )
        return 1

    state = data.get("state", {})
    layer_results = data.get("layer_results", {})
    meta = data.get("meta", {})

    def _show(note: str, is_error: bool = False) -> int:
        body = (
            f"goal_id: {goal_id[:12]}\n"
            f"workflow: {meta.get('workflow_name', '')}\n"
            f"created: {meta.get('created_at', '')[:19]}\n"
            f"status: {state.get('goal_status', '?')}\n"
            f"protocol: {state.get('protocol', '?')}\n"
            f"\n"
            f"[dim]criteria: {len(layer_results.get('criteria', []))} | "
            f"evidence: {len(layer_results.get('evidence', []))}[/dim]\n"
            f"\n{'[red]' if is_error else '[yellow]'}{note}"
            f"{'[/red]' if is_error else '[/yellow]'}"
        )
        console.print(Panel(body, title="Checkpoint", border_style="cyan"))
        return 1 if is_error else 0

    workflow_name = str(meta.get("workflow_name", "") or "")
    if not workflow_name:
        return _show(
            "checkpoint 未记录 workflow 名，无法续跑；"
            "请用 /goal start --workflow 重跑。"
        )

    from strategy_research.core.goal.workflow import GoalWorkflowRunner
    from strategy_research.core.goal.workflow_config import load_goal_workflow

    try:
        config = load_goal_workflow(workflow_name)
    except Exception as exc:  # noqa: BLE001
        return _show(
            f"workflow '{workflow_name}' 装载失败（{exc}）；已保留检视模式。",
            is_error=True,
        )

    runner = GoalWorkflowRunner.resume_from_checkpoint(
        session_id=session_id,
        goal_id=goal_id,
        config=config,
        workspace=Path.cwd(),
    )
    if runner is None:
        return _show("checkpoint 状态还原失败。", is_error=True)

    console.print(
        f"[cyan]Resuming workflow:[/cyan] {config.name} "
        f"from layer {state.get('current_layer', 0)} "
        f"({len(layer_results)} captured results)"
    )
    try:
        resumed_goal = asyncio.run(runner.resume_and_continue())
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Resume failed:[/red] {exc}")
        return 1

    progress = runner.get_progress()
    console.print(
        f"[green]Resumed + finished:[/green] goal={resumed_goal[:12]} "
        f"status={progress.get('status', 'unknown')} "
        f"evidence={progress.get('evidence_count', 0)}"
    )
    return 0


def _cmd_checkpoint_delete(console: Console, session_id: str,
                           goal_id: str) -> int:
    try:
        from strategy_research.core.goal.checkpoint_store import CheckpointStore
    except ImportError as exc:
        console.print(f"[red]CheckpointStore unavailable:[/red] {exc}")
        return 1

    cp = CheckpointStore(base_dir=_checkpoint_base_dir())
    deleted = cp.delete(session_id=session_id, goal_id=goal_id)
    if deleted:
        console.print(
            f"[green]Checkpoint deleted:[/green] {goal_id[:12]}"
        )
    else:
        console.print(
            f"[yellow]No checkpoint to delete for {goal_id[:12]}.[/yellow]"
        )
    return 0


# Slash-router entrypoint
def run(ctx: Any = None, *args: str) -> int:
    """Router for ``/goal`` subcommands."""
    if not args:
        return cmd_status()
    sub = args[0]
    rest = list(args[1:])
    if sub == "status":
        return cmd_status()
    if sub == "help":
        return cmd_help()
    if sub == "templates":
        return cmd_templates()
    if sub == "workflows":
        return cmd_workflows(*rest)
    if sub == "checkpoint":
        return cmd_checkpoint(*rest)
    if sub == "start":
        # Parse --template and --workflow options (Phase 4 v0.5.1)
        template_key = ""
        workflow_name = ""
        objective_parts = []
        i = 0
        while i < len(rest):
            if rest[i] == "--template" and i + 1 < len(rest):
                template_key = rest[i + 1]
                i += 2
            elif rest[i] == "--workflow" and i + 1 < len(rest):
                workflow_name = rest[i + 1]
                i += 2
            else:
                objective_parts.append(rest[i])
                i += 1
        return cmd_start(
            " ".join(objective_parts),
            template_key=template_key,
            workflow_name=workflow_name,
        )
    if sub == "evidence" and rest:
        # Next tokens: "<idx-or-id>" "<note words...>"
        if len(rest) >= 2:
            return cmd_evidence(rest[0], " ".join(rest[1:]))
        return cmd_evidence(rest[0], "")
    if sub == "complete":
        # Parse --lite option
        lite = "--lite" in rest
        rest = [r for r in rest if r != "--lite"]
        return cmd_complete(" ".join(rest), lite=lite)
    if sub == "cancel":
        return cmd_cancel(" ".join(rest))
    return cmd_help()


__all__ = [
    "cmd_status",
    "cmd_start",
    "cmd_evidence",
    "cmd_complete",
    "cmd_cancel",
    "cmd_help",
    "cmd_templates",
    "cmd_workflows",
    "cmd_checkpoint",
    "run",
    "_user_workflows_dir",
    "_checkpoint_base_dir",
]
