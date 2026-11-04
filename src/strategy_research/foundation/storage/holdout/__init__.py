"""holdout 集的持久化原语（施工方案 §4.2 / §4.9）。

**不新建存储通道**：内容走既有的 ``ArtifactChannel``（大产物落盘 + 登记），
元数据走同目录 sidecar。本包只额外提供 ``HoldoutStore`` 这一层"按
(task_signature, seed) 可寻址的 write-once 快照"语义。

**H5 在持久化侧的体现**：没有 append / update / delete 单条轨迹的入口。
``persist()`` 是一次性写入，同 key 已存在直接拒绝——把快照变成可变集合，
H5 的保证就断了。
"""

from __future__ import annotations

from .store import (
    DEFAULT_SUBDIR,
    HoldoutAlreadyPersisted,
    HoldoutPointer,
    HoldoutStore,
)

__all__ = [
    "DEFAULT_SUBDIR",
    "HoldoutAlreadyPersisted",
    "HoldoutPointer",
    "HoldoutStore",
]
