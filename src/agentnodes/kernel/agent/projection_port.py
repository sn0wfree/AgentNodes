# -*- coding: utf-8 -*-
"""投影端口（写侧拥有的窄接口）— 依赖倒置的 kernel 侧定义。

**为什么存在**：``EventStore``（L3 内核）原先在 ``_get_projector()`` 里
``from agentnodes.interfaces.api.session.projector import Projector`` ——
内核反向依赖接口层（L3→L1），把模块图闭成了 SCC[3] 环。这违反 Clean
Architecture 的 Dependency Rule（内层不得点名外层符号），也是本项目
``docs/design/layered-skeleton-design-20261103.md`` §1.2 簇 C 登记过的
「依赖倒置缺失」条目。

**修法**（业界惯例，见 Axon / Marten / Spring Modulith）：
写侧声明**自己需要的窄抽象**，实现由组合根注入。事件溯源社区的三条硬
规则在本文件全部满足：

1. 依赖朝内：本 Protocol 在 kernel，实现（Projector）在 L1，方向 L1→L3。
2. 事件类型是唯一共享契约：端口只描述「怎么应用投影」，不描述「投影是什么」。
3. 构造权归组合根：``EventStore`` 只接收实例，绝不自己 ``new``。

**为什么不直接 import Projector**：即便将来 ``api/session`` 下沉到
``kernel/session``（同层），「谁构造」的问题依然存在——同层不代表可以在
构造函数外偷偷 new 一个带状态的协作者。端口注入解决的是**所有权**问题，
不是**方向**问题，两者独立。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProjectionSink(Protocol):
    """写侧对读侧的最小要求：把新事件折叠进投影并落库。

    **刻意只暴露一个方法**。原实现让 ``EventStore`` 直接调用
    ``project_incremental()`` + ``flush()`` 两段式，等于强迫写侧知道
    投影内部「先算增量、再落盘」的实现细节。那是 Control-Freak 的另一面：
    调用方知道了不该知道的步骤。

    实现应在内部完成「增量推进 → 落库 → 更新 checkpoint」全流程；
    失败向上抛，由调用方决定记日志还是降级。
    """

    def apply_projection(self, session_id: str) -> None:
        """把 ``session_id`` 的未投影事件折叠进读模型并持久化。

        **命名**：刻意不叫 ``apply``——实现类（``Projector``）内部已有
        ``apply(event, state)`` 作为单事件 reducer，同名会覆盖。

        :param session_id: 会话标识
        :raises Exception: 投影失败时抛出（调用方决定降级策略）
        """
        ...


@runtime_checkable
class ProjectionInterest(Protocol):
    """可选能力：读侧自报「关心哪些事件类型」。

    若实现提供本方法，``EventStore`` 用它替代内置白名单——**读模型策略
    必须住在读侧**（Marten 的 ``ProjectionLifecycle`` 注册在组合根，
    Spring Modulith 的监听器住在消费方模块）。没有它时写侧退化为
    「每个事件都通知」，由读侧自己短路。
    """

    def interested_in(self, event_type: str) -> bool:
        """Return True iff this sink wants to be notified for ``event_type``."""
        ...


__all__ = ["ProjectionInterest", "ProjectionSink"]
