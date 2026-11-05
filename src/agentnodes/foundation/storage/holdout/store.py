"""Holdout 集的持久化（施工方案 §4.2 / §4.9）。

施工方案要求：**走 ⑥ 存储原语，不新建存储通道**。
这里的实现分两层，各用现成的东西：

- 内容（trajectories）→ ``ArtifactChannel``（既有大产物落盘 + 登记通道）
- 元数据（seed / task_signature / digest / 指针）→ 与内容同目录的
  一个小 JSON sidecar，便于审计与按签名索引

**为什么不用 PlatformStore 的 blob 表**：holdout 集是"一次抽样的一份
快照"，需要按 ``(task_signature, seed)`` 可寻址、可复现，而不是按内容
去重。用 artifact 通道得到 sha256 + uri（内容可校验），再用 sidecar 补
元数据，正好把两件事分工清楚。

**只读不变式（H5）**：本模块只提供 ``persist()`` / ``load()`` / ``list_sets()``。
``persist()`` 是**写入一次**（同 key 已存在则拒绝，除非显式 overwrite），
不提供 append/update/delete 单条轨迹的路径——那会把 write-once 快照
变成可变集合，H5 的保证就断了。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: sidecar 的文件名模式（与 holdout 内容同目录）。
_META_SUFFIX = ".holdout.json"

#: 内容目录（相对于 workspace）。
DEFAULT_SUBDIR = Path("holdout")


@dataclass(frozen=True)
class HoldoutPointer:
    """一份已落盘 holdout 集的定位信息。"""

    task_signature: str
    seed: int
    uri: str
    sha256: str
    bytes: int
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_signature": self.task_signature,
            "seed": self.seed,
            "uri": self.uri,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HoldoutPointer":
        return cls(
            task_signature=str(d["task_signature"]),
            seed=int(d["seed"]),
            uri=str(d["uri"]),
            sha256=str(d["sha256"]),
            bytes=int(d["bytes"]),
            count=int(d["count"]),
        )


class HoldoutAlreadyPersisted(RuntimeError):
    """同一 ``(task_signature, seed)`` 已存在；write-once 语义拒绝覆盖。"""


class HoldoutStore:
    """把 ``HoldoutSet`` 落盘为不可变快照。

    :param workspace: 工作区根（内容写在 ``workspace/holdout/``）
    :param channel: ``ArtifactChannel``；None = 只落文件不向 PlatformStore
        登记（测试/无 DB 路径）
    """

    def __init__(self, workspace: str | Path, channel: Any | None = None) -> None:
        self._root = Path(workspace) / DEFAULT_SUBDIR
        self._root.mkdir(parents=True, exist_ok=True)
        self._channel = channel

    # ── 写（write-once） ────────────────────────────────────────────

    def persist(
        self,
        holdout: Any,
        *,
        overwrite: bool = False,
        session_id: str = "",
        run_id: str | None = None,
    ) -> HoldoutPointer:
        """把 ``HoldoutSet`` 写成一份不可变快照，返回指针。

        同一 ``(task_signature, seed)`` 已存在时抛
        ``HoldoutAlreadyPersisted``（除非 ``overwrite=True``）。
        这是 H5 的持久化侧体现：抽样快照一旦定下，不会被悄悄改写。
        """
        sig = str(getattr(holdout, "task_signature", ""))
        seed = int(getattr(holdout, "seed", 0))
        trajectories = list(getattr(holdout, "trajectories", ()))

        payload = {
            "task_signature": sig,
            "seed": seed,
            "schema_v": "1",
            "trajectories": [_traj_to_dict(t) for t in trajectories],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()

        rel_uri = DEFAULT_SUBDIR / f"{sig or 'nosig'}_{seed}.holdout"
        abs_path = self._root / f"{sig or 'nosig'}_{seed}.holdout"
        if abs_path.exists() and not overwrite:
            raise HoldoutAlreadyPersisted(
                f"holdout already persisted for ({sig!r}, {seed}) at {abs_path}"
            )

        abs_path.write_bytes(encoded)

        # 向既有 artifact 通道登记（不新建存储通道）。失败不阻断落盘——
        # 文件已经是权威副本，登记只是让事件侧能引用它。
        if self._channel is not None:
            try:
                self._channel.maybe_artifact(
                    session_id=session_id or "holdout",
                    payload=payload,
                    kind="holdout_set",
                    run_id=run_id,
                    schema_v="1",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("holdout artifact registration failed: %s", exc)

        pointer = HoldoutPointer(
            task_signature=sig,
            seed=seed,
            uri=rel_uri.as_posix(),
            sha256=digest,
            bytes=len(encoded),
            count=len(trajectories),
        )
        self._write_meta(pointer)
        return pointer

    # ── 读 ─────────────────────────────────────────────────────────

    def load_raw(self, task_signature: str, seed: int) -> dict[str, Any]:
        """读回落盘的原始 dict（**不做类型还原**）。

        L4 不得 import apps（H1/H2）：把 dict → ``HoldoutSet`` 的还原
        交给调用方（apps 侧）用 ``HoldoutStore.load()`` 的 ``decoder``。
        """
        abs_path = self._root / f"{task_signature or 'nosig'}_{int(seed)}.holdout"
        return json.loads(abs_path.read_bytes().decode("utf-8"))

    def load(
        self,
        task_signature: str,
        seed: int,
        *,
        decoder: Any | None = None,
    ) -> Any:
        """Load a persisted holdout set.

        :param decoder: ``(raw_dict) -> HoldoutSet``。调用方（apps 侧）提供，
            因为 foundation 是 L4、不能 import L2 的 ``HoldoutSet``。
            未提供时直接返回原始 dict。
        """
        raw = self.load_raw(task_signature, seed)
        if decoder is None:
            return raw
        return decoder(raw)

    def verify(self, pointer: HoldoutPointer) -> bool:
        """内容是否仍与登记的 sha256 一致（防篡改/损坏）。"""
        abs_path = self._root / Path(pointer.uri).name
        if not abs_path.exists():
            return False
        return hashlib.sha256(abs_path.read_bytes()).hexdigest() == pointer.sha256

    def list_sets(self) -> list[HoldoutPointer]:
        """列出已落盘的所有 holdout 集（按文件名稳定排序）。"""
        out: list[HoldoutPointer] = []
        for meta in sorted(self._root.glob(f"*{_META_SUFFIX}")):
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
                out.append(HoldoutPointer.from_dict(d))
            except Exception as exc:  # noqa: BLE001 —— 坏 sidecar 不拖垮枚举
                logger.warning("bad holdout sidecar %s: %s", meta, exc)
        return out

    # ── sidecar ────────────────────────────────────────────────────

    def _write_meta(self, pointer: HoldoutPointer) -> None:
        meta = self._root / f"{pointer.task_signature or 'nosig'}_{pointer.seed}{_META_SUFFIX}"
        meta.write_text(
            json.dumps(pointer.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ── 序列化 ───────────────────────────────────────────────────────────


def _traj_to_dict(t: Any) -> dict[str, Any]:
    """把一条轨迹序列化成纯 dict（不依赖任何 apps 类型）。

    用 ``getattr`` 而非类型标注：存储层对"轨迹对象长什么样"只要求
    这些属性存在，不要求它是某个具体类——这样 L4 不必 import L2。
    """
    return {
        "event_id": getattr(t, "event_id", ""),
        "assignment_id": getattr(t, "assignment_id", ""),
        "ticket_id": getattr(t, "ticket_id", ""),
        "run_id": getattr(t, "run_id", ""),
        "status": getattr(t, "status", ""),
        "ts": getattr(t, "ts", ""),
        "task_signature": getattr(t, "task_signature", ""),
        "payload": dict(getattr(t, "payload", {}) or {}),
    }


__all__ = [
    "DEFAULT_SUBDIR",
    "HoldoutAlreadyPersisted",
    "HoldoutPointer",
    "HoldoutStore",
]
