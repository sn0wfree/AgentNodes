"""基因组版本链的持久化（L4 存储原语）。

**为什么必须在 L4**：``GenomeVersionStore`` 在 kernel（L3），它要落盘就得
依赖存储层——L3 → L4 是下行，合法。反过来让 foundation import kernel 才是
上行违规。所以这里只做**纯 dict 读写**，不认识 ``GenomeVersion`` 类型；
类型还原由 kernel 侧完成（与 ``foundation/storage/holdout/store.py`` 同一
分工）。

**为什么需要持久化**：当前 ``GenomeVersionStore`` 的链全部在内存里。
进程一重启，所有经 ``ValidationGate`` 验证通过的进化结果归零——回到
``gv-0``。一个自我进化系统若基因组活不过进程，等于没有进化。

**存储形态**：单文件 JSONL（append-only）+ 一个游标文件。

- ``chain.jsonl``：每行一个版本的 dict，只追加，不改写（历史的不可变性
  与内存链一致——回退是移动游标，不是改历史）
- ``cursor.json``：当前游标（整数索引）

为什么不删回退分支：内存实现里 ``activate()`` 会丢弃游标之后的分支。
落盘时**保留**这些行（只追加），但游标指向的位置决定了"当前是哪一版"。
这样即使回退过，历史仍在磁盘上可审计——只是不再参与当前状态。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SUBDIR = "genome"
_CHAIN_FILE = "chain.jsonl"
_CURSOR_FILE = "cursor.json"


class GenomeStore:
    """基因组版本链的纯 dict 持久化。

    :param root: 存储根目录（通常是 ``<workspace>/.quantnodes/genome``）
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._chain_path = self._root / _CHAIN_FILE
        self._cursor_path = self._root / _CURSOR_FILE
        self._lock = threading.RLock()

    # ── 写 ──────────────────────────────────────────────────────────

    def append(self, version: dict[str, Any]) -> None:
        """追加一个版本快照（append-only，不改写既有行）。"""
        line = json.dumps(
            version, ensure_ascii=False, sort_keys=True, default=str
        )
        with self._lock:
            with self._chain_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())

    def set_cursor(self, index: int) -> None:
        """记录当前游标（原子替换，避免半写）。"""
        payload = json.dumps({"cursor": int(index)}, ensure_ascii=False)
        tmp = self._cursor_path.with_suffix(".tmp")
        with self._lock:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._cursor_path)

    # ── 读 ──────────────────────────────────────────────────────────

    def load_chain(self) -> list[dict[str, Any]]:
        """读回全部版本（保持写入顺序）。文件不存在时返回空列表。"""
        if not self._chain_path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self._lock:
            for line in self._chain_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # 半写行（崩溃时可能留下）跳过，不阻断整体加载——
                    # 丢一个尾部残行比整个基因组报错更有价值。
                    logger.warning("genome chain: skipping corrupt line")
        return out

    def load_cursor(self) -> int:
        """读回游标。文件不存在或损坏时返回 0。"""
        if not self._cursor_path.exists():
            return 0
        try:
            data = json.loads(self._cursor_path.read_text(encoding="utf-8"))
            return max(0, int(data.get("cursor", 0)))
        except Exception:  # noqa: BLE001 —— 损坏的游标退回起点
            logger.warning("genome cursor unreadable; falling back to 0")
            return 0

    # ── 维护 ────────────────────────────────────────────────────────

    def exists(self) -> bool:
        return self._chain_path.exists() and self._chain_path.stat().st_size > 0

    def clear(self) -> None:
        """清空存储（测试用）。"""
        with self._lock:
            for p in (self._chain_path, self._cursor_path):
                if p.exists():
                    p.unlink()


__all__ = ["DEFAULT_SUBDIR", "GenomeStore"]
