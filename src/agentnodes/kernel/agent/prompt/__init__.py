"""提示词构造簇（原 kernel/agent 顶层 2 文件，2026-11-17 下沉）。

文件布局：
    builder.py          原 ``prompt_builder.py`` —— PromptBuilderFactory / ChatPromptBuilder /
                        StaticFilePromptBuilder（auto-discovery + harness 解析）
    schema_extractor.py 原 ``prompt_schema_extractor.py`` —— 提示词 schema 抽取

**本包不做 re-export**（docs/design/agent-dir-structuring-plan-20261117.md §3.3）：
所有引用必须指向具体子模块（``agentnodes.kernel.agent.prompt.builder`` 等）。
理由同 compact 包：re-export 会让 ``monkeypatch.setattr("...prompt.X", ...)`` 打在
包命名空间而真实模块 global 不变 → patch 静默失效（R-014 教训）。

包级 import 形态的正确写法（保持绑定名）::

    from agentnodes.kernel.agent.prompt.builder import builder as prompt_builder
    from agentnodes.kernel.agent.prompt.builder import schema_extractor as prompt_schema_extractor
"""
