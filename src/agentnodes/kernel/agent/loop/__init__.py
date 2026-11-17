"""Agent Loop 引擎簇（原 kernel/agent 顶层 11 文件，2026-11-17 下沉，方案 A 全去重前缀）。

文件布局：
    core.py           原 ``loop.py`` —— AgentLoop 主体（1,580 行，引擎核心）
    assembly.py       原 ``loop_assembly.py`` —— LoopSpec → 可执行体装配
    spec.py           原 ``loop_spec.py`` —— LoopSpec 数据结构
    engine.py         原 ``loop_engine.py`` —— 骨架引擎
    engine_adapters.py 原 ``loop_engine_adapters.py`` —— 引擎适配（→ core._make_strategy_ctx）
    lifecycle.py      原 ``loop_lifecycle.py`` —— 收尾与 metrics 归集
    endings.py        原 ``loop_endings.py`` —— 终止路径判定
    progress.py       原 ``loop_progress.py`` —— 进展检测
    stream.py         原 ``loop_stream.py`` —— 流式迭代
    telemetry.py      原 ``loop_telemetry.py`` —— 遥测钩子
    tools.py          原 ``loop_tools.py`` —— loop 侧工具调用编排

**命名注意**：``agentnodes.kernel.agent.loop.tools``（本包，loop 侧工具编排）
与 ``agentnodes.kernel.agent.tools``（工具注册表层，阶段 3 建的）是**两个不同的包**，
路径唯一但视觉相近 —— 导入时务必写全路径。

**本包不做 re-export**（docs/design/agent-dir-structuring-plan-20261117.md §3.3）：
所有引用必须指向具体子模块（``agentnodes.kernel.agent.loop.core`` 等）。
理由同前三簇：re-export 会让 monkeypatch 打在包命名空间而真实模块 global 不变 →
静默失效（R-014 教训）。本簇有 2 处这类 patch（``loop.git_commit``）。

包级 import 形态的正确写法（保持绑定名）::

    from agentnodes.kernel.agent.loop import core as loop
    from agentnodes.kernel.agent.loop import endings as loop_endings
"""
