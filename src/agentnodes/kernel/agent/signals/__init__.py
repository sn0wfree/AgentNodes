"""运行时信号簇（原 kernel/agent 顶层 2 文件，2026-11-17 下沉）。

文件布局：
    pause.py      原 ``pause.py`` —— 挂起/恢复原语（统一引擎 H1，循环级 HITL）
    progress.py  原 ``progress.py`` —— 长时工具的进展发射通道

两者都是**运行时信号**（控制流 / 进度回传），不承载策略判定
（那在 ``policy/``）、也不承载遥测落盘（那在 ``telemetry/``）。

**本包不做 re-export**：所有引用必须指向具体子模块
（``agentnodes.kernel.agent.signals.pause`` 等），理由同其它簇：
re-export 会让 monkeypatch 打在包命名空间而真实模块 global 不变 → 静默失效（R-014 教训）。
"""
