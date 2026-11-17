"""工具层簇（原 kernel/agent 顶层 5 文件，2026-11-17 下沉）。

文件布局：
    core.py       原 ``tools.py`` —— ToolRegistry / BaseTool / ToolError / ToolGuard
    result.py     原 ``tool_result.py`` —— 工具结果裁剪与序列化
    context.py    原 ``tool_context.py`` —— ToolContext（工具执行上下文）
    capability.py 原 ``tools_capability.py`` —— 工具能力声明
    toolsets.py   原 ``toolsets.py`` —— TOOLSETS：角色 → 工具白名单

簇内依赖（实测）：``toolsets`` → ``core``；其余互不依赖。

**本包不做 re-export**（docs/design/agent-dir-structuring-plan-20261117.md §3.3）：
所有引用必须指向具体子模块（``agentnodes.kernel.agent.tools.core`` 等）——
std``logging`` 层级链上 ``agentnodes.kernel.agent.tools.core`` 现在是包，
沿用旧路径会拿到包而非模块。理由同 compact / prompt / context 三簇：
re-export 会让 monkeypatch 打在包命名空间而真实模块 global 不变 → 静默失效（R-014 教训）。

包级 import 形态的正确写法（保持绑定名）::

    from agentnodes.kernel.agent.tools import core as tools_mod
    from agentnodes.kernel.agent.tools import toolsets
"""
