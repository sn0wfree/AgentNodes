"""决策与模式簇（原 kernel/agent 顶层 3 文件，2026-11-17 下沉）。

文件布局：
    deciders.py       原 ``deciders.py`` —— 停止/继续判定的可配置对象（批次 10 · B2）
    modes.py          原 ``modes.py`` —— Agent 模式注册表（批次 10 · B2）
    step_adapters.py  原 ``step_adapters.py`` —— 流式 handler 适配器（T11 后只剩 FnStreamingHandler）

三者是**循环的策略层**：什么时候停、用哪种模式、step 怎么执行。
共享的协议类型（``_core_types``）仍留在 ``agent/`` 顶层 —— 它有 11 个簇外依赖方，
下沉会制造「共享协议藏在某簇里」的耦合（设计文档 §12.2）。

**本包不做 re-export**：所有引用必须指向具体子模块
（``agentnodes.kernel.agent.policy.deciders`` 等），理由同其它簇。
"""
