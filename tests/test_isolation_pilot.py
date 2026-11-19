# -*- coding: utf-8 -*-
"""4c B2.2 子批 3c：临时工作区 WriteFileTool 试点（脚本级，不接生产）。

§8.2 四场景：S1 写文件成功 / S2 截止终止 / S3 迟到结果拒收 /
S4 lease 确认后释放。shell 不接入；不改 AgentLoop 生产路径。
Windows 真机跑；POSIX skip-with-reason（3d 做平台矩阵）。

方案：docs/design/isolated-execution-b22-design-20261119.md §8。
"""
from __future__ import annotations

import sys
import time

import pytest

from agentnodes.kernel.agent.tools.isolation import isolate_tool_call

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only：隔离 adapter 依赖 kernel32 Job Object"
           "（POSIX 未验证，3d 做平台矩阵）",
)


def _write_registered() -> bool:
    from agentnodes.kernel.agent.builtin_tools import build_default_registry

    names = {t.name for t in build_default_registry().all_tools()}
    return "write" in names


def test_s1_write_file_in_temp_workspace(tmp_path):
    """S1：真实 write 工具在隔离 worker 内写临时工作区成功。"""
    if not _write_registered():
        pytest.skip("write 工具未注册")
    result = isolate_tool_call(
        "write", {"path": "pilot.txt", "content": "3c-pilot"},
        ctx={"workspace": str(tmp_path)}, timeout_s=30.0,
    )
    assert result.status == "completed"
    assert result.actual_state == "stopped"
    assert (tmp_path / "pilot.txt").read_text(encoding="utf-8") == "3c-pilot"


def test_s2_deadline_terminates_and_confirms():
    """S2：deadline 触发 → 杀 worker → 逐 pid 确认 → terminated/stopped。

    D1：确认先于释放；确认成功才允许记 stopped（4a 词表）。
    """
    t0 = time.perf_counter()
    result = isolate_tool_call("probe_sleep", {"seconds": 8.0}, timeout_s=1.5)
    elapsed = time.perf_counter() - t0
    assert result.status == "terminated"
    assert result.actual_state == "stopped"
    assert elapsed < 6.0, f"截止终止应在 deadline 附近完成，实测 {elapsed:.1f}s"


def test_s3a_late_result_not_attributed():
    """S3(a)：迟到结果不归因——deadline 杀掉 worker 后返回的结果
    payload 不含工具输出（结构性拒收：调用已返回，迟到行无人消费）。"""
    result = isolate_tool_call("probe_sleep", {"seconds": 8.0}, timeout_s=1.5)
    assert result.status == "terminated"
    assert "slept" not in result.payload
    assert result.payload["error"].startswith("deadline exceeded")


def test_s3b_resident_worker_poisoned_after_timeout():
    """S3(b)：常驻 worker 超时 → 毒化关闭，后续调用被拒。

    毒化保证旧任务的迟到结果行不可能被新调用冒名接收（D2）。
    """
    from agentnodes.kernel.agent.tools.isolation import IsolationWorker

    with IsolationWorker() as worker:
        result = worker.call("probe_sleep", {"seconds": 8.0}, timeout_s=1.5)
        assert result.status == "terminated"
        assert result.actual_state == "stopped"
        with pytest.raises(RuntimeError, match="毒化"):
            worker.call("probe_context", {"payload": "late"})


def test_s4_lease_released_after_kill_confirmation():
    """S4：lease 在 kill 确认完成之后才释放（acquire < 确认 < release）。"""
    events: list[str] = []

    class _StubLease:
        def __enter__(self):
            events.append("acquire")
            return self

        def __exit__(self, *exc):
            events.append("release")
            return False

    with _StubLease():
        result = isolate_tool_call("probe_sleep", {"seconds": 8.0}, timeout_s=1.5)
        events.append("confirmed" if result.actual_state == "stopped" else "unconfirmed")
    assert result.status == "terminated"
    assert events == ["acquire", "confirmed", "release"]
