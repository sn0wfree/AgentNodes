#!/usr/bin/env python3
"""实际使用示例 — Hook + Memory + Session（当前 API · 2026-11-19 更新）。

历史版本使用 ``agentnodes.core.*`` 旧包名与 SessionDB / SessionManager /
RateLimiter / MetricsLogger 等已移除的类。本版按当前真实 API 重写：

- Memory：``kernel.memory.persistent.PersistentMemory``（签名未变）
- Hooks：``kernel.hooks.*``（hook 方法为**同步**，旧版 asyncio.run 调用已移除）
- Session：``kernel.session.state.event_store.EventStore``（事件写入 + 投影）
  + ``kernel.session.store.SessionStore``（读侧）——旧 SessionDB.add_message
  直写已被「事件溯源 → projector 投影」取代
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agentnodes.kernel.hooks.composite import AgentHook, CompositeHook
from agentnodes.kernel.hooks.context import AgentHookContext
from agentnodes.kernel.memory.persistent import PersistentMemory
from agentnodes.kernel.session.projector import Projector
from agentnodes.kernel.session.state.event_store import EventStore
from agentnodes.kernel.session.store import SessionStore


class ResearchLoggerHook(AgentHook):
    """记忆研究发现：每轮结束后把回答写入项目记忆。"""

    def __init__(self, memory: PersistentMemory):
        self.memory = memory
        self.findings: list[str] = []

    def after_iteration(self, ctx: AgentHookContext) -> None:
        if ctx.final_content:
            self.findings.append(ctx.final_content)
            self.memory.add(
                f"finding_{ctx.iteration}", ctx.final_content,
                "project", f"Iteration {ctx.iteration} finding",
            )

    def on_error(self, ctx: AgentHookContext, error: BaseException) -> None:
        self.memory.add("last_error", str(error), "project", "Most recent error")


class PerformanceTrackerHook(AgentHook):
    """统计工具调用次数与累计 token。"""

    def __init__(self):
        self.total_calls = 0
        self.total_tokens = 0

    def before_execute_tools(self, ctx: AgentHookContext) -> None:
        self.total_calls += len(ctx.tool_calls)

    def after_iteration(self, ctx: AgentHookContext) -> None:
        self.total_tokens += sum(ctx.usage.values())

    def get_stats(self) -> dict:
        return {"total_calls": self.total_calls, "total_tokens": self.total_tokens}


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        print("=" * 60)
        print("实际使用示例：Hook + Memory + Session（当前 API）")
        print("=" * 60)

        # 1. Memory
        print("\n1. 初始化 Memory...")
        memory = PersistentMemory(tmp_path / "memory")
        memory.add("strategy_knowledge", "Momentum factor works in large caps.",
                   "reference", "Momentum strategy knowledge")
        memory.add("user_preference", "Low-frequency strategies, max drawdown < 15%.",
                   "user", "User trading preferences")
        print(f"  Memory entries: {len(memory.list_entries())}")

        # 2. Session（事实源 event_log → projector → messages 读侧）
        print("\n2. 初始化 Session...")
        db_path = tmp_path / "sessions.db"
        projector = Projector(db_path)
        event_store = EventStore(
            db_path, flush_to_messages=True, projection_sink=projector,
        )
        session_store = SessionStore(db_path, projector=projector)
        print(f"  SessionStore + EventStore ready at {db_path}")

        # 3. Hooks（同步方法，不再 asyncio.run）
        print("\n3. 初始化 Hooks...")
        research_logger = ResearchLoggerHook(memory)
        performance_tracker = PerformanceTrackerHook()
        composite = CompositeHook([research_logger, performance_tracker])
        print(f"  Registered hooks: {len(composite._hooks)}")

        # 4. 模拟研究会话：hook 生命周期 + 事件写入
        print("\n4. 模拟研究会话...")
        # 先建会话行——否则 assistant_message 投影会因外键缺失被丢弃
        from agentnodes.kernel.session.db import ensure_session_row

        ensure_session_row(db_path, "research_001", "Hook demo")
        for i in range(5):
            ctx = AgentHookContext(iteration=i, usage={"total_tokens": 10})
            ctx.tool_calls = [object()]  # 模拟一次工具调用
            ctx.final_content = f"Iteration {i} complete"
            # CompositeHook 的生命周期方法是 async（AgentHook 基类为同步，
            # CompositeHook 覆写为 async 并行分发）——必须 await
            asyncio.run(composite.before_iteration(ctx))
            asyncio.run(composite.before_execute_tools(ctx))
            asyncio.run(composite.after_iteration(ctx))
            # 当前写入路径：emit 事件 → projector 投影（不直写 messages 表）
            event_store.emit("research_001", "assistant_message", {
                "message_id": f"m-{i}", "role": "assistant",
                "content": ctx.final_content,
            })
        messages = session_store.get_messages("research_001")  # 先读后关
        # 显式释放全部句柄（Windows 临时目录 WinError 32）
        for resource in (event_store, session_store, projector):
            close = getattr(resource, "close", None)
            if callable(close):
                close()

        # 5. 结果
        print("\n5. 查看结果...")
        print(f"  Memory 条目: {len(memory.list_entries())}")
        print(f"  Memory 搜索 'momentum': {len(memory.find_relevant('momentum'))} 条")
        print(f"  Hook 研究发现: {len(research_logger.findings)}")
        print(f"  Hook 工具调用: {performance_tracker.get_stats()['total_calls']}")
        print(f"  Session 消息数: {len(messages)}")
        print("=" * 60)
        print("示例完成！")


if __name__ == "__main__":
    main()
