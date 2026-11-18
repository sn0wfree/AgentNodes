# -*- coding: utf-8 -*-
"""循环层异常类型（顺序⑤ 缺口①：attempt 级超时）。

单独成模块而非塞进 ``core.py``：异常类型被调用方按类型捕获，
放独立模块便于 apps/kernel 侧 ``except AttemptTimeoutError`` 精确处理，
也避免 core.py 继续膨胀。
"""
from __future__ import annotations

__all__ = ["AttemptTimeoutError"]


class AttemptTimeoutError(RuntimeError):
    """整轮 attempt 超时（``AGENTNODES_CHAT_ATTEMPT_TIMEOUT``）。

    与**迭代级**超时的区别：迭代级由 ``_with_iteration_timeout``
    （async 走 ``asyncio.wait_for`` 真取消 / sync 走事后测量）处理，
    终态是 ``LoopResult.finished_reason="timeout"``（不抛异常，循环正常
    退出）；attempt 级是**整轮**预算耗尽，必须**中止执行**——抛本异常，
    由调用链（GraphEngine → SessionController）走既有失败路径：

    - ``run.failed``（error 含 "attempt timed out after Ns"）
    - ``run.settled{ok: False}``
    - Assignment settle("failed") + fence release（controller 既有 finally）

    语义保证（2026-11-18 裁决 3 缺口①）：
    - 计时**从实际执行开始**（不含排队）；
    - 检查点发现到期后不再进入下一轮；
    - 可协作的异步模型调用可取消；同步工具、线程及远端操作不能因此
      假定已中断或回滚。
    """
