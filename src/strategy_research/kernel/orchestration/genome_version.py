"""GenomeVersion — Tier 1 基因组版本（施工方案 §4.6）。

**为什么单独成模块而不进 ``config_hub``**：``config_hub`` 是**启动期一次
性装载**的严格配置骨架（fail-fast、env 覆盖、指纹），它的值在进程生命周期
内不变。基因组版本相反——它会被 validation_gate 递增、回退，是运行期可变
状态。塞进去会破坏 config_hub 的核心不变量（"装载一次，之后只读"）。

**Tier 分层（H3）**：
    tier0_frozen       只读快照；EvolutionAgent 永不可写
    tier1_active       validation_gate 通过后更新（prompt/skill/图形状/...）
    tier2_self_modifiable  研究产物（strategy.py 等），研究域自改

**版本链**：每次升版记一条 ``GenomeVersion``，``rollback()`` 回退一格。
链本身不可变（frozen dataclass），回退是移动游标而不是改历史。
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class GenomeVersion:
    """一个基因组版本快照。不可变。"""

    version_id: str
    tier0_frozen: dict[str, Any] = field(default_factory=dict)
    tier1_active: dict[str, Any] = field(default_factory=dict)
    tier2_self_modifiable: dict[str, Any] = field(default_factory=dict)
    activated_at: str = ""
    activated_by_proposal: str | None = None


class GenomeVersionStore:
    """版本链 + 当前游标。线程安全。

    只提供 ``activate`` / ``rollback`` / ``current`` —— 没有"直接改
    tier1_active"的方法。任何变更都必须经 ``activate``，它要求
    ``proposal_id``，从而保证「每次生效都可追溯到一份提案」。
    """

    def __init__(
        self,
        initial: GenomeVersion | None = None,
        *,
        store: Any | None = None,
    ) -> None:
        """构造版本链。

        :param initial: 初始版本；None = 从 ``store`` 恢复，或建 ``gv-0``
        :param store: ``foundation.storage.genome_store.GenomeStore``；
            None = 纯内存（测试/无持久化路径）。

        **恢复语义**：``store`` 有内容时，链与游标都从磁盘读回——
        进程重启后继续基于上次生效的版本运行，而不是退回 ``gv-0``。
        这正是"基因组必须活过进程"的实现。
        """
        self._lock = threading.RLock()
        self._store = store
        self._chain: list[GenomeVersion] = []
        self._cursor = 0

        if store is not None:
            try:
                restored = store.load_chain()
            except Exception:  # noqa: BLE001 —— 读不出就当空
                logger.warning("genome chain load failed; starting fresh")
                restored = []
            if restored:
                self._chain = [_from_dict(d) for d in restored]
                self._cursor = min(store.load_cursor(), len(self._chain) - 1)
                logger.info(
                    "genome restored: %d versions, cursor=%d",
                    len(self._chain), self._cursor,
                )
                return

        base = initial or GenomeVersion(
            version_id="gv-0",
            tier0_frozen={},
            tier1_active={},
            tier2_self_modifiable={},
            activated_at=_now_iso(),
            activated_by_proposal=None,
        )
        self._chain = [base]
        self._cursor = 0
        self._persist_append(base)
        self._persist_cursor()

    # ── 读 ──────────────────────────────────────────────────────────

    def current(self) -> GenomeVersion:
        with self._lock:
            return self._chain[self._cursor]

    def current_version_id(self) -> str:
        return self.current().version_id

    def activated_at(self) -> str:
        """当前版本的生效时间（holdout 用它排除污染轨迹）。"""
        return self.current().activated_at

    def tier1_active(self) -> dict[str, Any]:
        """供 ``SkillMemoryResolver`` 使用（只读副本）。"""
        return dict(self.current().tier1_active)

    def history(self) -> list[GenomeVersion]:
        with self._lock:
            return list(self._chain[: self._cursor + 1])

    # ── 写（唯一入口） ──────────────────────────────────────────────

    def activate(
        self, proposal_id: str, tier1_active: dict[str, Any]
    ) -> str:
        """按提案升版。返回新 version_id。

        ``tier0_frozen`` 与 ``tier2_self_modifiable`` 从当前版本继承——
        调用方**没有**改它们的入口，这是 H3 的实现层保证。
        """
        with self._lock:
            cur = self.current()
            merged = dict(cur.tier1_active)
            merged.update(tier1_active or {})
            nxt = GenomeVersion(
                version_id=f"gv-{uuid.uuid4().hex[:8]}",
                tier0_frozen=dict(cur.tier0_frozen),
                tier1_active=merged,
                tier2_self_modifiable=dict(cur.tier2_self_modifiable),
                activated_at=_now_iso(),
                activated_by_proposal=proposal_id,
            )
            # 游标之后的历史（已回退过的分支）丢弃 —— 线性化版本链
            self._chain = self._chain[: self._cursor + 1] + [nxt]
            self._cursor = len(self._chain) - 1
            self._persist_append(nxt)
            self._persist_cursor()
            logger.info(
                "genome activated: %s by proposal %s", nxt.version_id, proposal_id
            )
            return nxt.version_id

    def rollback(self) -> str:
        """回退到上一版本。返回回退后的 version_id。

        已在最初版本时原地不动（幂等），返回当前 id。
        """
        with self._lock:
            if self._cursor > 0:
                self._cursor -= 1
                self._persist_cursor()
                logger.warning(
                    "genome rolled back to %s", self.current().version_id
                )
            return self.current().version_id

    def rollback_to(self, version_id: str) -> str:
        """回退到链上某个已知版本。未知 id 抛 KeyError。"""
        with self._lock:
            for idx in range(self._cursor, -1, -1):
                if self._chain[idx].version_id == version_id:
                    self._cursor = idx
                    self._persist_cursor()
                    return version_id
        raise KeyError(f"unknown genome version: {version_id!r}")

    # ── 持久化（内部） ──────────────────────────────────────────────

    def _persist_append(self, version: "GenomeVersion") -> None:
        """追加一个版本到磁盘。无 store 时 no-op。"""
        if self._store is None:
            return
        try:
            self._store.append(_to_dict(version))
        except Exception:  # noqa: BLE001 —— 落盘失败不得中断升版
            logger.warning("genome persist append failed", exc_info=True)

    def _persist_cursor(self) -> None:
        """记录当前游标。无 store 时 no-op。"""
        if self._store is None:
            return
        try:
            self._store.set_cursor(self._cursor)
        except Exception:  # noqa: BLE001
            logger.warning("genome persist cursor failed", exc_info=True)


# ── 序列化（dict ↔ GenomeVersion） ──────────────────────────────────
#
# 放在 kernel 侧而不是 foundation：存储层只做纯 dict 读写，类型还原
# 属于知道 ``GenomeVersion`` 的一侧（与 holdout 的 decode 分工一致）。


def _to_dict(v: "GenomeVersion") -> dict[str, Any]:
    return {
        "version_id": v.version_id,
        "tier0_frozen": dict(v.tier0_frozen),
        "tier1_active": dict(v.tier1_active),
        "tier2_self_modifiable": dict(v.tier2_self_modifiable),
        "activated_at": v.activated_at,
        "activated_by_proposal": v.activated_by_proposal,
    }


def _from_dict(d: dict[str, Any]) -> "GenomeVersion":
    return GenomeVersion(
        version_id=str(d.get("version_id") or ""),
        tier0_frozen=dict(d.get("tier0_frozen") or {}),
        tier1_active=dict(d.get("tier1_active") or {}),
        tier2_self_modifiable=dict(d.get("tier2_self_modifiable") or {}),
        activated_at=str(d.get("activated_at") or ""),
        activated_by_proposal=d.get("activated_by_proposal"),
    )


__all__ = ["GenomeVersion", "GenomeVersionStore"]
