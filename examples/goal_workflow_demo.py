"""Phase 4 v0.5.4 — Goal Workflow Demo.

Demonstrates end-to-end goal workflow execution via the Python API.

Usage::

    python examples/goal_workflow_demo.py

Requires:
    - ``quantnodes-strategy-research`` installed (``pip install -e .``)
    - PyYAML (``pip install pyyaml``)

This demo loads the ``goal_market_analysis`` preset, executes the DAG
with a stub agent, and prints the evidence collected.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path


def main() -> None:
    # Ensure a fresh DB for the demo
    demo_db = Path("./demo_goal_workflow.db")
    if demo_db.exists():
        demo_db.unlink()
    os.environ["STRATEGY_RESEARCH_GOAL_DB"] = str(demo_db)

    from agentnodes.core.goal.workflow_config import (
        load_goal_workflow,
        list_goal_workflows,
    )
    from agentnodes.core.goal.workflow import GoalWorkflowRunner
    from agentnodes.core.goal.store import GoalStore
    from agentnodes.core.goal.event_bus import CollectingObserver

    # List available workflows
    workflows = list_goal_workflows()
    print("Available workflows:")
    for wf in workflows:
        print(f"  - {wf['name']}: {wf['description']}")
    print()

    # Load a preset
    config = load_goal_workflow("goal_market_analysis")
    print(f"Loaded workflow: {config.name}")
    print(f"  Agents: {[a.id for a in config.agents]}")
    print(f"  Criteria: {len(config.goal.default_criteria)}")
    print(f"  Completion: {config.completion.mode}")
    print()

    # Create runner with event collector
    store = GoalStore(db_path=str(demo_db))
    runner = GoalWorkflowRunner(
        config=config,
        session_id="demo",
        store=store,
    )
    observer = CollectingObserver()
    runner.subscribe(observer)

    # Execute
    print("Executing workflow...")
    goal_id = asyncio.run(runner.start("分析 2024 年 Q3 市场状态"))
    print(f"\nDone! goal_id={goal_id}")

    # Show results
    progress = runner.get_progress()
    print(f"  Status: {progress['status']}")
    print(f"  Agents completed: {progress['agents_completed']}/{progress['agents_total']}")
    print(f"  Evidence collected: {progress['evidence_count']}")
    print(f"  Events emitted: {len(observer.events)}")

    for event_name, event_data in observer.events:
        print(f"    - {event_name}: {event_data}")

    # Cleanup
    if demo_db.exists():
        demo_db.unlink()
    print("\nDemo complete!")


if __name__ == "__main__":
    main()