# -*- coding: utf-8 -*-
"""SessionExecutionLease —— 会话级执行互斥原语（裁决 2，B1）。

设计：docs/design/goal-gateway-session-lease-design-20261118.md §2。

## 为什么需要独立原语（不复用现有机制）

- ``SessionFence.claim()`` 是「新 run 顶替旧 run」的写栅栏语义，不是
  「空闲时原子获取、完成后释放」的互斥；
- ``chat_session_state()`` 是状态快照，拿快照当锁是 TOCTOU；
- legacy ``SessionService._processing_sessions`` 在聊天入口迁 Gateway 后
  已空转（chat 侧永不写入），Gateway 路径零处可读。

## 语义（与设计文档一致，一处实施期微调）

- ``acquire`` **恒为非阻塞**（设计稿的 ``wait`` 参数移除：asyncio 内同步
  阻塞等待是反模式；调用方需要等待语义时自旋 ``await asyncio.sleep``
  后重试——GoalWorkflow 迁移（B3）正是保持现状的 0.25s 自旋形态）；
- **原子性**：本方法为纯同步、无 ``await``，asyncio 单线程内
  check-then-set 天然原子——**实现不得引入 await/让出点**，此约束由
  测试 ``test_acquire_is_synchronous`` 钉住（源码扫描 async 痕迹）；
- **ownership token**：``acquire`` 返回不透明 token；``release`` 必须
  校验 token——防「异常清理误释放他人租约」与「双重释放」；
- 不自动顶替：已占用时 ``acquire`` 返回 ``None``，持有者不变。

## B1 范围

零接线：本模块不 import Gateway/SessionService，也不被任何生产代码
引用。挂钩（chat run 启止 / GoalWorkflow）是 B2/B3 批次。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

__all__ = ["LeaseRecord", "SessionExecutionLease"]


@dataclass(frozen=True)
class LeaseRecord:
    """一笔已授予的租约（holder 观测用）。"""

    session_id: str
    owner: str
    token: str


class SessionExecutionLease:
    """Per-session 执行互斥：原子获取 / 按 token 释放 / 只读持有者观测。

    线程说明：主要消费方在 asyncio 单线程内（check-then-set 无 await 即
    原子）；``threading.Lock`` 仅为跨线程调用（如未来 API 线程直接触碰）
    提供保底，锁内无 IO，不构成阻塞风险。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[str, LeaseRecord] = {}

    def acquire(self, session_id: str, owner: str) -> str | None:
        """原子获取 ``session_id`` 的执行租约。

        Args:
            session_id: 互斥域（物理会话 id）。
            owner: 持有者标识（约定 ``goal:<goal_id>`` / ``chat:<run_id>``），
                仅用于观测与诊断，不参与判定。

        Returns:
            新租约的 ownership token；``session_id`` 已被占用时返回
            ``None``（不顶替、不等待）。
        """
        # 原子性契约：本方法必须保持纯同步——不得引入 await/让出点。
        with self._lock:
            if session_id in self._leases:
                return None
            token = uuid.uuid4().hex
            self._leases[session_id] = LeaseRecord(
                session_id=session_id, owner=owner, token=token,
            )
            return token

    def release(self, session_id: str, token: str) -> bool:
        """按 token 释放租约。

        Returns:
            token 匹配且成功释放 → ``True``；session 无租约、或 token
            不匹配（他人持有）→ ``False``，**状态不变**。
        """
        with self._lock:
            record = self._leases.get(session_id)
            if record is None or record.token != token:
                return False
            del self._leases[session_id]
            return True

    def holder(self, session_id: str) -> str | None:
        """当前持有者 owner（只读观测，非互斥原语）。

        快照语义：返回值只用于展示/诊断；**不得**用于互斥判定
        （TOCTOU——见设计文档 §2.1 的两条禁止）。
        """
        with self._lock:
            record = self._leases.get(session_id)
            return record.owner if record is not None else None

    def holding(self, session_id: str) -> bool:
        """是否有任何持有者（观测便捷方法；同样不得当锁用）。"""
        with self._lock:
            return session_id in self._leases
