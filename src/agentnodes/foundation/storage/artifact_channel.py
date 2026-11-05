"""artifact 通道（P-3）—— 大结果落盘 + 登记，事件只带指针。

设计（platform-data-plane §7/§9 + execution-plane §5.3）：
- **大字节永远不进事件流**：> threshold 的结果写成文件（round 目录），
  事件只带 `artifact_id` 指针。
- **小结果留在事件里**：JSON 可序列化且小于阈值 → 直接进事件 data。
- **登记行进全局库**：artifact_id → uri/sha256/bytes/schema_v/血统。

为什么需要它：executor 池并行跑回测时，多个 job 的结果不能同时写一个库
（DuckDB 单写者）。旁挂文件 + 指针登记解决冲突（方案 B）。

URI 根 = workspace（P-5 定案），目录布局 = studies/<study_id>/rounds/R-nnn/（D-4）。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentnodes.foundation.storage.platform_store import Artifact, PlatformStore

# 超过此大小的结果转 artifact（字节）。64KB 与单事件载荷上限一致。
ARTIFACT_THRESHOLD_BYTES = 64 * 1024


@dataclass(frozen=True)
class ArtifactPointer:
    """事件里携带的指针（不是内容本身）。"""

    artifact_id: str
    kind: str
    uri: str
    sha256: str
    bytes: int
    schema_v: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "uri": self.uri,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "schema_v": self.schema_v,
        }


class ArtifactChannel:
    """大结果落盘 + 登记的唯一入口（P-3 机制）。

    与 PlatformStore 分工：
    - PlatformStore：登记行（元数据）+ blob（小二进制去重）
    - ArtifactChannel：文件落盘（大产物）+ 调用 store.register_artifact
    """

    def __init__(self, store: PlatformStore, workspace: str | Path) -> None:
        self._store = store
        self._workspace = Path(workspace)

    def maybe_artifact(
        self,
        *,
        session_id: str,
        payload: Any,
        kind: str,
        run_id: str | None = None,
        parent_id: str | None = None,
        study_id: str | None = None,
        round_id: str | None = None,
        schema_v: str = "1",
    ) -> dict[str, Any] | ArtifactPointer:
        """小于阈值 → 原样返回（进事件）；超过 → 落盘 + 登记 + 返回指针。"""
        encoded = self._encode(payload)
        if len(encoded) <= ARTIFACT_THRESHOLD_BYTES:
            return payload

        artifact_id = self._make_id(run_id, kind)
        uri = self._relative_uri(study_id, round_id, artifact_id)
        abs_path = self._workspace / uri
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(encoded)

        digest = hashlib.sha256(encoded).hexdigest()
        pointer = ArtifactPointer(
            artifact_id=artifact_id,
            kind=kind,
            uri=uri.as_posix(),
            sha256=digest,
            bytes=len(encoded),
            schema_v=schema_v,
        )
        self._store.register_artifact(
            Artifact(
                artifact_id=artifact_id,
                kind=kind,
                uri=pointer.uri,
                sha256=digest,
                bytes=len(encoded),
                schema_v=schema_v,
                status="registered",  # 写盘即 registered（两步可见的第二步）
                created_by_run_id=run_id,
                parent_id=parent_id,
                study_id=study_id,
                round_id=round_id,
            )
        )
        # 顺带进 blob（内容去重 + 快速读取；与文件双份，文件是权威）
        self._store.store_blob(session_id, encoded)
        return pointer

    # ── 读回 ─────────────────────────────────────────────

    def load(self, pointer: ArtifactPointer | dict[str, Any]) -> Any:
        """按指针读回内容（校验 sha256）。"""
        if isinstance(pointer, dict):
            pointer = ArtifactPointer(
                artifact_id=pointer["artifact_id"], kind=pointer["kind"],
                uri=pointer["uri"], sha256=pointer["sha256"],
                bytes=pointer["bytes"], schema_v=pointer.get("schema_v", "1"),
            )
        abs_path = self._workspace / pointer.uri
        if not abs_path.exists():
            raise FileNotFoundError(f"artifact 文件缺失: {pointer.uri}")
        content = abs_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != pointer.sha256:
            raise ValueError(f"artifact 校验失败（内容被改动）: {pointer.uri}")
        return self._decode(content)

    # ── 内部 ─────────────────────────────────────────────

    @staticmethod
    def _encode(payload: Any) -> bytes:
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode("utf-8")
        return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

    @staticmethod
    def _decode(content: bytes) -> Any:
        try:
            return json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return content

    @staticmethod
    def _make_id(run_id: str | None, kind: str) -> str:
        import uuid

        stem = run_id or "anon"
        return f"{stem}:a{uuid.uuid4().hex[:8]}"

    def _relative_uri(self, study_id: str | None, round_id: str | None, artifact_id: str) -> Path:
        """D-4 布局：studies/<study_id>/rounds/<round_id>/artifacts/<id>."""
        safe_id = artifact_id.replace(":", "_")
        if study_id and round_id:
            return Path("studies") / study_id / "rounds" / round_id / "artifacts" / f"{safe_id}.json"
        if study_id:
            return Path("studies") / study_id / "artifacts" / f"{safe_id}.json"
        return Path("files") / f"{safe_id}.json"
