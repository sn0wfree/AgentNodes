"""阶段三验收 ⑥：holdout 集持久化（施工方案 §4.2「走 ⑥ 存储原语」）。

验证：
- 落盘 / 读回一致（内容 sha256 可校验）
- write-once：同 (task_signature, seed) 二次 persist 被拒（H5 持久化侧）
- 不新建存储通道：登记走既有 ArtifactChannel
- 层方向：foundation 侧不做 apps 类型还原（L4 不 import L2）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.evolution import HoldoutSet, Trajectory
from strategy_research.apps.evolution.holdout import (
    decode_holdout,
    load_persisted_holdout,
    persist_holdout,
)
from strategy_research.foundation.storage.holdout import (
    DEFAULT_SUBDIR,
    HoldoutAlreadyPersisted,
    HoldoutStore,
)


def _set(sig: str = "sig-A", seed: int = 42, n: int = 3) -> HoldoutSet:
    return HoldoutSet(
        trajectories=tuple(
            Trajectory(
                event_id=f"e{i}",
                assignment_id=f"a{i}",
                ticket_id=f"t{i}",
                run_id=f"r{i}",
                status="failed",
                ts=f"2026-01-0{i+1}T00:00:00Z",
                task_signature=sig,
                payload={"reason": f"why-{i}"},
            )
            for i in range(n)
        ),
        seed=seed,
        task_signature=sig,
    )


# ── 落盘 / 读回 ──────────────────────────────────────────────────────


class TestPersistRoundTrip:
    def test_persist_creates_file_under_holdout_dir(self, tmp_path):
        ptr = persist_holdout(_set(), tmp_path)
        p = tmp_path / Path(ptr.uri)
        assert p.exists(), f"未落盘到 {p}"
        assert p.parent.name == DEFAULT_SUBDIR.name

    def test_round_trip_preserves_content(self, tmp_path):
        original = _set()
        ptr = persist_holdout(original, tmp_path)
        back = load_persisted_holdout(tmp_path, "sig-A", 42)
        assert len(back) == len(original)
        assert [t.assignment_id for t in back] == [
            t.assignment_id for t in original
        ]
        assert [t.payload for t in back] == [t.payload for t in original]
        assert back.seed == 42 and back.task_signature == "sig-A"

    def test_pointer_digest_verifies(self, tmp_path):
        store = HoldoutStore(tmp_path)
        ptr = store.persist(_set())
        assert store.verify(ptr) is True

    def test_tampering_breaks_verify(self, tmp_path):
        store = HoldoutStore(tmp_path)
        ptr = store.persist(_set())
        target = tmp_path / Path(ptr.uri)
        target.write_bytes(b'{"tampered": true}')
        assert store.verify(ptr) is False

    def test_count_and_bytes_recorded(self, tmp_path):
        ptr = persist_holdout(_set(n=5), tmp_path)
        assert ptr.count == 5
        assert ptr.bytes > 0


# ── write-once（H5 持久化侧） ────────────────────────────────────────


class TestWriteOnce:
    def test_second_persist_same_key_rejected(self, tmp_path):
        persist_holdout(_set(), tmp_path)
        with pytest.raises(HoldoutAlreadyPersisted, match="already persisted"):
            persist_holdout(_set(), tmp_path)

    def test_overwrite_allowed_explicitly(self, tmp_path):
        persist_holdout(_set(n=3), tmp_path)
        ptr = persist_holdout(_set(n=1), tmp_path, overwrite=True)
        assert ptr.count == 1

    def test_different_seed_is_different_snapshot(self, tmp_path):
        exist = []
        for seed in (1, 2, 3):
            exist.append(persist_holdout(_set(seed=seed), tmp_path))
        assert len({p.uri for p in exist}) == 3

    def test_different_signature_is_different_snapshot(self, tmp_path):
        a = persist_holdout(_set(sig="sig-A"), tmp_path)
        b = persist_holdout(_set(sig="sig-B"), tmp_path)
        assert a.uri != b.uri


# ── 不新建存储通道：走既有 ArtifactChannel ──────────────────────────


class TestUsesExistingChannel:
    def test_registers_through_artifact_channel(self, tmp_path):
        calls = []

        class _Channel:
            def maybe_artifact(self, **kw):
                calls.append(kw)
                return None

        persist_holdout(_set(), tmp_path, channel=_Channel())
        assert len(calls) == 1
        assert calls[0]["kind"] == "holdout_set"
        assert calls[0]["schema_v"] == "1"

    def test_channel_failure_does_not_block_persist(self, tmp_path):
        class _BadChannel:
            def maybe_artifact(self, **kw):
                raise RuntimeError("store down")

        ptr = persist_holdout(_set(), tmp_path, channel=_BadChannel())
        assert (tmp_path / Path(ptr.uri)).exists(), "文件仍是权威副本"

    def test_no_channel_still_persists(self, tmp_path):
        ptr = persist_holdout(_set(), tmp_path, channel=None)
        assert (tmp_path / Path(ptr.uri)).exists()


# ── 枚举与 sidecar ───────────────────────────────────────────────────


class TestMetadata:
    def test_list_sets_returns_pointers(self, tmp_path):
        store = HoldoutStore(tmp_path)
        store.persist(_set(seed=1))
        store.persist(_set(seed=2))
        ptrs = store.list_sets()
        assert len(ptrs) == 2
        assert {p.seed for p in ptrs} == {1, 2}

    def test_bad_sidecar_does_not_break_enumeration(self, tmp_path):
        store = HoldoutStore(tmp_path)
        store.persist(_set(seed=1))
        (tmp_path / DEFAULT_SUBDIR / "broken.holdout.json").write_text(
            "{not json", encoding="utf-8"
        )
        assert len(store.list_sets()) == 1


# ── 层方向：foundation 不做 apps 类型还原 ───────────────────────────


class TestLayerDirection:
    def test_store_module_does_not_import_apps(self):
        """L4 不得 import L2（H1/H2）。用文本断言，防回归。"""
        import strategy_research.foundation.storage.holdout.store as m

        src = Path(m.__file__).read_text(encoding="utf-8")
        # 允许出现于注释/docstring 的提及，但不得有真正的 import 语句
        import_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("from strategy_research.apps", "import strategy_research.apps"))
        ]
        assert import_lines == [], f"foundation 侧出现了 apps import: {import_lines}"

    def test_load_raw_returns_plain_dict(self, tmp_path):
        persist_holdout(_set(), tmp_path)
        raw = HoldoutStore(tmp_path).load_raw("sig-A", 42)
        assert isinstance(raw, dict)
        assert "trajectories" in raw
        assert isinstance(raw["trajectories"][0], dict)

    def test_decoder_lives_in_apps(self, tmp_path):
        """类型还原由 apps 侧 decoder 完成。"""
        persist_holdout(_set(), tmp_path)
        raw = HoldoutStore(tmp_path).load_raw("sig-A", 42)
        hs = decode_holdout(raw)
        assert isinstance(hs, HoldoutSet)
        assert len(hs) == 3
