"""Scheduled Research CLI commands."""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from .cron_parser import next_cron_trigger, validate_cron
from .executor import ScheduledResearchExecutor
from .models import JobStatus, ScheduledResearchJob
from .store import ScheduledResearchStore

_TERMINAL_STATUSES = (
    "complete", "cancelled", "error", "budget_limited",
    "early_stopped", "monitoring",
)


def _parse_metric_targets(spec: str | None) -> list[dict] | None:
    """Parse ``calmar>=0.5,sharpe>=0.3`` → [{name,op,value}, ...].

    Supports ops ``>= <= > < ==``. Returns None for empty/None input.
    """
    if not spec:
        return None
    targets: list[dict] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        op = None
        for candidate in (">=", "<=", "==", ">", "<"):
            if candidate in part:
                op = candidate
                break
        if op is None:
            raise ValueError(f"无效的指标表达式: {part}（支持 >= <= > < ==）")
        name, _, value = part.partition(op)
        try:
            targets.append({"name": name.strip(), "op": op, "value": float(value)})
        except ValueError:
            raise ValueError(f"无效的指标值: {part}")
    return targets or None


def cmd_schedule_create(args) -> int:
    """Create a new scheduled research job (dispatch target: study)."""
    store = ScheduledResearchStore()

    workspace = str(Path(args.workspace).resolve())
    cron_expr = args.cron or ""
    interval_ms = args.interval * 1000 if args.interval else 0

    if not cron_expr and not interval_ms:
        print("错误: 必须指定 --cron 或 --interval")
        return 1

    if cron_expr and not validate_cron(cron_expr):
        print(f"错误: 无效的 cron 表达式: {cron_expr}")
        return 1

    # Compute next_run_at
    if cron_expr:
        next_run = next_cron_trigger(cron_expr)
    else:
        next_run = time.time() + interval_ms / 1000

    config: dict = {}
    try:
        targets = _parse_metric_targets(args.metric)
        if targets:
            config["metric_targets"] = targets
    except ValueError as e:
        print(f"错误: {e}")
        return 1

    if args.budget_turn is not None:
        config["budget_turn"] = args.budget_turn
    if args.budget_token is not None:
        config["budget_token"] = args.budget_token
    if args.budget_time is not None:
        config["budget_time_seconds"] = args.budget_time
    if args.monitor_interval is not None:
        config["monitor_interval_seconds"] = args.monitor_interval
    if args.guidance_file:
        guidance_path = Path(args.guidance_file)
        if not guidance_path.exists():
            print(f"错误: guidance 文件不存在: {guidance_path}")
            return 1
        config["guidance_md"] = guidance_path.read_text(encoding="utf-8")

    job = ScheduledResearchJob(
        workspace=workspace,
        strategy_name=args.strategy,
        prompt=args.prompt or "",
        cron=cron_expr,
        interval_ms=interval_ms,
        next_run_at=next_run,
        max_rounds=args.max_rounds or 1,
        target="study",
    )
    job.config = config

    store.add(job)
    print(f"✓ 已创建定时任务: {job.id}")
    print(f"  工作区: {workspace}")
    print(f"  策略: {job.strategy_name}")
    print("  目标: study（到点创建长程研究任务）")
    if cron_expr:
        print(f"  Cron: {cron_expr}")
    else:
        print(f"  间隔: {args.interval}s")
    print(f"  下次执行: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(next_run))}")
    if job.prompt:
        print(f"  研究目标: {job.prompt[:80]}")
    if config:
        print(f"  附加配置: {', '.join(sorted(config))}")

    return 0


def cmd_schedule_list(args) -> int:
    """List scheduled research jobs."""
    store = ScheduledResearchStore()
    # workspace 在 job 里存的是绝对路径（create 时做了 resolve），所以这里的
    # 过滤值也必须 resolve。"." 与 "/abs/path/ws" 做字符串比较永远不相等，
    # 会让 `schedule list`（默认 workspace="."）永远显示「暂无定时任务」。
    raw = getattr(args, "workspace", None)
    workspace = str(Path(raw).resolve()) if raw else None
    jobs = store.list_jobs(workspace=workspace)

    if not jobs:
        print("暂无定时任务")
        return 0

    print(f"=== 定时任务 (共 {len(jobs)} 个) ===")
    for j in jobs:
        status_icon = {
            "pending": "⏳",
            "running": "🔄",
            "completed": "✓",
            "failed": "✗",
            "cancelled": "🚫",
        }.get(j.status.value, "?")

        schedule = j.cron if j.cron else f"every {j.interval_ms // 1000}s"
        next_run = time.strftime("%m-%d %H:%M", time.localtime(j.next_run_at)) if j.next_run_at else "—"

        print(f"  {status_icon} {j.id[:16]:16s}  {schedule:20s}  next: {next_run}  {j.strategy_name}")

    return 0


def cmd_schedule_show(args) -> int:
    """Show details of a scheduled job."""
    store = ScheduledResearchStore()
    job = store.get(args.job_id)

    if not job:
        print(f"任务 '{args.job_id}' 不存在")
        return 1

    print("=== 定时任务详情 ===")
    print(f"  ID:        {job.id}")
    print(f"  工作区:    {job.workspace}")
    print(f"  策略:      {job.strategy_name}")
    print(f"  目标:      {job.target}")
    print(f"  状态:      {job.status.value}")
    if job.cron:
        print(f"  Cron:      {job.cron}")
    if job.interval_ms:
        print(f"  间隔:      {job.interval_ms / 1000}s")
    if job.prompt:
        print(f"  研究目标:  {job.prompt}")
    print(f"  最大轮数:  {job.max_rounds}")
    print(f"  创建时间:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job.created_at))}")
    if job.last_run_at:
        print(f"  上次执行:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job.last_run_at))}")
    if job.next_run_at:
        print(f"  下次执行:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job.next_run_at))}")
    if job.last_run_id:
        print(f"  上次Study: {job.last_run_id}")
    if job.config:
        print(f"  配置:      {job.config}")
    if job.config.get("last_error"):
        print(f"  最后错误:  {job.config['last_error'][:200]}")

    return 0


def cmd_schedule_cancel(args) -> int:
    """Cancel a scheduled job."""
    store = ScheduledResearchStore()
    job = store.get(args.job_id)

    if not job:
        print(f"任务 '{args.job_id}' 不存在")
        return 1

    job.status = JobStatus.CANCELLED
    store.update(job)
    print(f"✓ 已取消任务 {job.id}")
    return 0


def cmd_schedule_delete(args) -> int:
    """Delete a scheduled job."""
    store = ScheduledResearchStore()
    ok = store.delete(args.job_id)

    if ok:
        print(f"✓ 已删除任务 {args.job_id}")
    else:
        print(f"任务 '{args.job_id}' 不存在")
        return 1

    return 0


def _build_study_scheduler():
    """In-process StudyScheduler (NullEmitter — no SSE in CLI context)."""
    from ...study import StudyScheduler, StudyStore

    return StudyScheduler(StudyStore(), session_service=None)


async def _run_job_and_wait(job_id: str) -> int:
    """Dispatch a job once and wait for its study to reach a terminal state."""
    from ...study import StudyStatus, StudyStore

    store = ScheduledResearchStore()
    job = store.get(job_id)
    if job is None:
        print(f"任务 '{job_id}' 不存在")
        return 1

    scheduler = _build_study_scheduler()
    executor = ScheduledResearchExecutor(store, scheduler=scheduler)
    await executor.run_once_async(job_id)
    print(f"✓ 已触发任务 {job.id}")

    refreshed = store.get(job_id)
    study_id = refreshed.last_run_id if refreshed else None
    if not study_id:
        print("  任务未产生 study（检查上方错误）")
        return 1

    print(f"  等待 study {study_id} 完成（Ctrl+C 中断等待，任务在后台继续）...")
    terminal = {s.value for s in StudyStatus if s.value in _TERMINAL_STATUSES}
    with StudyStore() as sstore:
        while True:
            record = sstore.get_study(study_id)
            if record is None or record.execution_status.value in terminal:
                break
            await asyncio.sleep(1)
        final = sstore.get_study(study_id)
    status = final.execution_status.value if final else "gone"
    print(f"  Study {study_id} 终态: {status}"
          + (f"（rounds={final.current_round}）" if final else ""))
    if final and final.last_metrics:
        print(f"  指标: {final.last_metrics}")
    if final and final.last_error:
        print(f"  错误: {final.last_error}")
    return 0


def cmd_schedule_run(args) -> int:
    """Immediately run a scheduled job once (in-process study)."""
    return asyncio.run(_run_job_and_wait(args.job_id))


def cmd_schedule_start(args) -> int:
    """Start the scheduler (single asyncio loop, in-process studies)."""

    async def _main() -> None:
        store = ScheduledResearchStore()
        scheduler = _build_study_scheduler()
        executor = ScheduledResearchExecutor(
            store, scheduler=scheduler, tick_interval=args.tick,
        )
        loop = asyncio.get_running_loop()
        for sig in ("SIGINT", "SIGTERM"):
            try:
                loop.add_signal_handler(
                    getattr(__import__("signal"), sig),
                    executor.stop,
                )
            except NotImplementedError:
                pass  # Windows — Ctrl+C path below still works

        recovered = store.recover_stale_running()
        if recovered:
            print(f"恢复了 {recovered} 个中断的任务")
        jobs = store.list_jobs(status=JobStatus.PENDING)
        print("=== 启动调度器 ===")
        print(f"  待执行任务: {len(jobs)} 个")
        print(f"  Tick 间隔:  {args.tick}s")
        print("  并发上限:   SR_STUDY_MAX_CONCURRENT（默认 3）")
        print("  按 Ctrl+C 停止")
        print()
        executor.start(loop=loop)
        try:
            while executor._running:
                await asyncio.sleep(1)
        finally:
            executor.stop()
            await scheduler.shutdown()
            print("\n调度器已停止")

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    return 0


def add_schedule_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Add schedule subcommands to the main CLI parser."""
    schedule_parser = subparsers.add_parser("schedule", help="定时研究")
    schedule_sub = schedule_parser.add_subparsers(dest="schedule_command", help="定时任务命令")

    # schedule create
    create_p = schedule_sub.add_parser("create", help="创建定时任务")
    create_p.add_argument("--workspace", "-w", required=True, help="工作区路径")
    create_p.add_argument("--strategy", "-s", required=True, help="策略名称")
    create_p.add_argument("--cron", "-c", help="Cron 表达式 (5字段)")
    create_p.add_argument("--interval", "-i", type=int, help="间隔秒数")
    create_p.add_argument("--prompt", "-p", help="研究目标（映射 study objective）")
    create_p.add_argument("--max-rounds", "-m", type=int, default=1, help="每次最大轮数")
    create_p.add_argument("--metric", help="验收指标: calmar>=0.5,sharpe>=0.3")
    create_p.add_argument("--budget-turn", type=int, help="轮数预算")
    create_p.add_argument("--budget-token", type=int, help="token 预算")
    create_p.add_argument("--budget-time", type=int, help="时间预算（秒）")
    create_p.add_argument("--monitor-interval", type=int,
                          help="达标后监控间隔（秒）；不传则完成即止")
    create_p.add_argument("--guidance-file", help="研究指引文件路径（guidance.md 覆盖）")

    # schedule list
    list_p = schedule_sub.add_parser("list", help="列出定时任务")
    list_p.add_argument("--workspace", "-w", help="按工作区筛选")

    # schedule show
    show_p = schedule_sub.add_parser("show", help="显示任务详情")
    show_p.add_argument("job_id", help="任务 ID")

    # schedule cancel
    cancel_p = schedule_sub.add_parser("cancel", help="取消任务")
    cancel_p.add_argument("job_id", help="任务 ID")

    # schedule delete
    delete_p = schedule_sub.add_parser("delete", help="删除任务")
    delete_p.add_argument("job_id", help="任务 ID")

    # schedule run
    run_p = schedule_sub.add_parser("run", help="立即执行一次")
    run_p.add_argument("job_id", help="任务 ID")

    # schedule start
    start_p = schedule_sub.add_parser("start", help="启动调度器")
    start_p.add_argument("--tick", type=int, default=60, help="检查间隔 (秒, 默认60)")
