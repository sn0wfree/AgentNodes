"""上下文注入簇（原 kernel/agent 顶层 3 文件，2026-11-17 下沉）。

文件布局：
    core.py       原 ``context.py`` —— ContextBuilder / estimate_tokens（消息组装核心）
    injector.py   原 ``context_injector.py`` —— ContextInjector 协议 + Goal/Todos/Memory 注入器
    providers.py  原 ``context_providers.py`` —— pre-run 上下文生产方桥接（→ ContextHub）

簇内依赖（实测）：``providers`` → ``injector``；其余互不依赖。

**注意命名区分**：本包是 ``agentnodes.kernel.agent.context.core``（agent 的上下文注入），
与 L3 的 ``agentnodes.kernel.context``（ContextHub / policy）是**两个不同的包**。

**本包不做 re-export**（docs/design/agent-dir-structuring-plan-20261117.md §3.3）：
所有引用必须指向具体子模块（``agentnodes.kernel.agent.context.core`` 等）。
理由同 compact 包：re-export 会让 ``monkeypatch.setattr("...context.X", ...)`` 打在
包命名空间而真实模块 global 不变 → patch 静默失效（R-014 教训）。

包级 import 形态的正确写法（保持绑定名）::

    from agentnodes.kernel.agent.context.core import core as context
    from agentnodes.kernel.agent.context.core import injector as context_injector
"""
