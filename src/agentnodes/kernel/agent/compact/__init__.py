"""上下文压缩簇（原 kernel/agent 顶层 4 文件，2026-11-17 下沉）。

文件布局：
    core.py             原 ``compact.py`` —— 压缩核心（CompactConfig / compact_messages）
    message.py          原 ``compaction_message.py`` —— 消息 → 压缩提示词
    runtime.py          原 ``compaction_runtime.py`` —— 运行期压缩器（Compactor）
    loop_compaction.py  原 ``kernel/agent/loop_compaction.py`` —— loop 的压缩 mixin

**本包不做 re-export**（docs/design/agent-dir-structuring-plan-20261117.md §3.3）：
所有引用必须指向具体子模块（``agentnodes.kernel.agent.compact.core`` 等）。
理由：R-014 实测教训 —— 若 ``__init__`` 转出 ``compact_messages``，
``monkeypatch.setattr("...compact.compact_messages", ...)`` 会打在**包命名空间**，
而 ``core`` 模块内部调用走自己的 global → **patch 静默失效**。
测试里有 8 处这样的 patch，必须直达 ``compact.core``。

包级 import 形态的正确写法（保持绑定名，后续属性访问 / reload / patch 零改动）::

    from agentnodes.kernel.agent.compact import core as compact
    from agentnodes.kernel.agent.compact import runtime as cr
"""
