"""Extracted from cli.py — swarm management commands.

Contains:
- cmd_swarm_list
- cmd_swarm_inspect
- cmd_swarm_run
- cmd_swarm_cancel
"""

from __future__ import annotations

from pathlib import Path


def cmd_swarm_list(args) -> int:
    """列出所有 swarm preset。"""
    from strategy_research.kernel.swarm import list_presets

    import strategy_research.kernel.swarm as _swarm_mod
    presets_dir = Path(_swarm_mod.__file__).parent / "presets"
    presets = list_presets(presets_dir)

    if not presets:
        print("暂无 swarm preset")
        return 0

    print(f"=== Swarm Presets (共 {len(presets)} 个) ===")
    for p in presets:
        agent_count = len(p.agents)
        print(f"  {p.name:30s}  {agent_count} agents  {p.description[:50]}")

    return 0


def cmd_swarm_inspect(args) -> int:
    """显示 swarm preset 结构。"""
    from strategy_research.kernel.swarm import load_preset

    import strategy_research.kernel.swarm as _swarm_mod
    presets_dir = Path(_swarm_mod.__file__).parent / "presets"
    preset = None
    for ext in ("*.yaml", "*.yml"):
        for f in presets_dir.glob(ext):
            p = load_preset(f)
            if p and p.name == args.name:
                preset = p
                break
        if preset:
            break

    if not preset:
        print(f"Preset '{args.name}' 不存在")
        return 1

    print(f"=== {preset.name} ===")
    print(f"描述: {preset.description}")
    print(f"Agents: {len(preset.agents)}")
    print()

    print("DAG 结构:")
    for agent_id, deps in preset.dag.items():
        dep_str = ", ".join(deps) if deps else "(无依赖)"
        print(f"  {agent_id} ← {dep_str}")

    print()
    print("Agent 详情:")
    for a in preset.agents:
        tools_str = ", ".join(a.tools) if a.tools else "(无工具)"
        print(f"  {a.id}: {tools_str}")

    return 0


def cmd_swarm_run(args) -> int:
    """执行 swarm preset。"""
    from strategy_research.kernel.swarm import SwarmRuntime, load_preset

    import strategy_research.kernel.swarm as _swarm_mod
    presets_dir = Path(_swarm_mod.__file__).parent / "presets"
    preset = None
    for ext in ("*.yaml", "*.yml"):
        for f in presets_dir.glob(ext):
            p = load_preset(f)
            if p and p.name == args.name:
                preset = p
                break
        if preset:
            break

    if not preset:
        print(f"Preset '{args.name}' 不存在")
        return 1

    workspace = Path(args.workspace)
    task = args.task or f"执行 swarm preset: {preset.name}"

    runtime = SwarmRuntime()
    result = runtime.execute(preset, workspace, task)

    print("=== Swarm 执行完成 ===")
    print(f"Run ID:  {result.run_id}")
    print(f"Preset:  {result.preset_name}")
    print(f"耗时:    {result.elapsed_s}s")
    print(f"成功:    {'是' if result.success else '否'}")
    print()

    for agent_id, ar in result.agent_results.items():
        status = "✓" if ar.status.value == "completed" else "✗"
        print(f"  {status} {agent_id}: {ar.elapsed_s}s")

    return 0


def cmd_swarm_cancel(args) -> int:
    """取消运行中的 swarm。"""
    from strategy_research.kernel.swarm import SwarmRuntime

    runtime = SwarmRuntime()
    ok = runtime.cancel(args.run_id)
    if ok:
        print(f"已取消 swarm {args.run_id}")
    else:
        print(f"未找到运行中的 swarm {args.run_id}")
        return 1

    return 0
