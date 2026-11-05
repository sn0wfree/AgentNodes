#!/usr/bin/env python3
"""实际使用示例 — 展示 Hook + Memory + Session 的完整用法。"""

import tempfile
from pathlib import Path
from agentnodes.core.hooks import AgentHook, CompositeHook, AgentHookContext
from agentnodes.core.memory import PersistentMemory
from agentnodes.core.session import SessionDB, SessionManager, RateLimiter, MetricsLogger


# ============================================================
# 1. 定义自定义 Hook
# ============================================================

class ResearchLoggerHook(AgentHook):
    """研究日志 Hook — 记录每次迭代的研究发现。"""
    
    name = "research_logger"
    
    def __init__(self, memory: PersistentMemory):
        self.memory = memory
        self.findings = []
    
    def after_iteration(self, ctx: AgentHookContext):
        """迭代结束后记录发现。"""
        iteration = ctx.iteration
        # 模拟从 context 中提取研究发现
        finding = f"Iteration {iteration}: Analyzed strategy performance"
        self.findings.append(finding)
        
        # 保存到 memory
        self.memory.add(
            name=f"finding_{iteration}",
            content=finding,
            memory_type="feedback",
            description=f"Research finding from iteration {iteration}"
        )
    
    def on_error(self, ctx: AgentHookContext, error: BaseException):
        """错误时记录。"""
        self.memory.add(
            name=f"error_{ctx.iteration}",
            content=f"Error in iteration {ctx.iteration}: {error}",
            memory_type="feedback",
            description=f"Error from iteration {ctx.iteration}"
        )


class PerformanceTrackerHook(AgentHook):
    """性能跟踪 Hook — 跟踪工具执行性能。"""
    
    name = "performance_tracker"
    
    def __init__(self):
        self.tool_calls = []
        self.total_time = 0.0
    
    def before_execute_tools(self, ctx: AgentHookContext):
        """工具执行前记录时间。"""
        import time
        self._start_time = time.time()
    
    def after_tool_executed(self, ctx: AgentHookContext, tool_call, result):
        """工具执行后记录耗时。"""
        import time
        duration = time.time() - self._start_time
        tool_name = getattr(tool_call, 'name', 'unknown')
        self.tool_calls.append({
            "tool": tool_name,
            "duration": duration,
            "success": True
        })
        self.total_time += duration
    
    def get_stats(self):
        """获取统计信息。"""
        return {
            "total_calls": len(self.tool_calls),
            "total_time": self.total_time,
            "avg_time": self.total_time / len(self.tool_calls) if self.tool_calls else 0,
            "tools": list(set(t["tool"] for t in self.tool_calls))
        }


# ============================================================
# 2. 实际使用示例
# ============================================================

def main():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        
        print("=" * 60)
        print("实际使用示例：Hook + Memory + Session")
        print("=" * 60)
        
        # --------------------------------------------------
        # 初始化 Memory
        # --------------------------------------------------
        print("\n1. 初始化 Memory...")
        memory = PersistentMemory(tmp_path / "memory")
        
        # 添加一些初始知识
        memory.add("strategy_knowledge", """
            Momentum factor works well in large cap stocks.
            - IC mean > 0.03 is considered good
            - IR mean > 0.5 is acceptable
            - Max drawdown should be < 20%
        """, "reference", "Momentum strategy knowledge")
        
        memory.add("user_preference", """
            Preference: Low frequency strategies (1-5 day holding)
            Risk tolerance: Max drawdown < 15%
            Market: A-shares + Hong Kong
        """, "user", "User trading preferences")
        
        print(f"  Memory entries: {len(memory.list_entries())}")
        
        # --------------------------------------------------
        # 初始化 Session
        # --------------------------------------------------
        print("\n2. 初始化 Session...")
        db = SessionDB(tmp_path / "sessions.db")
        session = db.create_session("research_001", workspace=str(tmp_path))
        print(f"  Session ID: {session.id}")
        
        # --------------------------------------------------
        # 初始化 Hooks
        # --------------------------------------------------
        print("\n3. 初始化 Hooks...")
        research_logger = ResearchLoggerHook(memory)
        performance_tracker = PerformanceTrackerHook()
        
        composite = CompositeHook([research_logger, performance_tracker])
        print(f"  Registered hooks: {[h.name for h in composite._hooks]}")
        
        # --------------------------------------------------
        # 模拟研究会话
        # --------------------------------------------------
        print("\n4. 模拟研究会话...")
        
        # 模拟多轮迭代
        for i in range(5):
            ctx = AgentHookContext(iteration=i)
            
            # 触发 hooks
            import asyncio
            asyncio.run(composite.before_iteration(ctx))
            asyncio.run(composite.before_execute_tools(ctx))
            asyncio.run(composite.after_iteration(ctx))
            
            # 记录消息到 session
            db.add_message("research_001", "user", f"Analyze iteration {i}")
            db.add_message("research_001", "assistant", f"Iteration {i} complete")
        
        # --------------------------------------------------
        # 查看结果
        # --------------------------------------------------
        print("\n5. 查看结果...")
        
        # Memory 统计
        print(f"\n  Memory 统计:")
        print(f"    - 总条目: {len(memory.list_entries())}")
        
        # Memory 搜索
        print(f"\n  Memory 搜索 'momentum':")
        results = memory.find_relevant("momentum")
        for r in results:
            print(f"    - {r.title}: {r.description[:50]}...")
        
        # Session 统计
        print(f"\n  Session 统计:")
        messages = db.get_messages("research_001")
        print(f"    - 消息数: {len(messages)}")
        
        # Hook 统计
        print(f"\n  Hook 统计:")
        print(f"    - 研究发现: {len(research_logger.findings)}")
        print(f"    - 工具调用: {performance_tracker.get_stats()['total_calls']}")
        
        # Metrics 统计
        print(f"\n  Metrics 统计:")
        stats = db.metrics_logger.get_stats()
        print(f"    - 总写入: {stats.get('total_writes', 0)}")
        print(f"    - 成功率: {stats.get('success_rate', 0):.1%}")
        
        # --------------------------------------------------
        # CLI 命令演示
        # --------------------------------------------------
        print("\n6. CLI 命令演示:")
        print("  $ quantnodes-research session stats")
        print("  $ quantnodes-research session list")
        
        print("\n" + "=" * 60)
        print("示例完成！")
        print("=" * 60)


if __name__ == "__main__":
    main()
