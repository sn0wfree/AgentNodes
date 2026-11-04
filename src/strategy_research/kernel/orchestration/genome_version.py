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

    def __init__(self, initial: GenomeVersion | None = None) -> None:
        self._lock = threading.RLock()
        base = initial or GenomeVersion(
            version_id="gv-0",
            tier0_frozen={},
            tier1_active={},
            tier2_self_modifiable={},
            activated_at=_now_iso(),
            activated_by_proposal=None,
        )
        self._chain: list[GenomeVersion] = [base]
        self._cursor = 0

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
                    return version_id
        raise KeyError(f"unknown genome version: {version_id!r}")


__all__ = ["GenomeVersion", "GenomeVersionStore"]
