"""钉住 ``docs/architecture-layered.html`` 的 JS 数据（R-014 F 批回写的固化网）。

该图的 CAPS（28 项能力）/ DEBTS（架构债）/ SESSION_LIFECYCLE（八阶段表）
是 JS 数组，浏览器渲染前无任何校验 —— 手改一条 owner 或状态字符串，
只有打开页面才能发现。本文件把它们当**数据契约**钉住：

- 三组数组必须是合法 JSON（防手改引入语法错误）；
- C8 的实现者含 kernel/session（F3）；
- D1/D2 的处置状态 + D3 保持开放（F4）；
- 生命周期八阶段的 owner/loc 指向 kernel/session 新家，删除阶段标注统一（F4）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "architecture-layered.html"


def _load_array(name: str) -> list:
    """从 HTML 提取 ``var <name> = [...];`` 并解析为 Python 列表。"""
    text = HTML.read_text(encoding="utf-8")
    m = re.search(rf"var {name}\s*=\s*(\[.*?\]);", text, re.S)
    assert m, f"HTML 里找不到 var {name}"
    return json.loads(m.group(1))  # 手改引入语法错误时，这条直接红


@pytest.fixture(scope="module")
def caps() -> list:
    return _load_array("CAPS")


@pytest.fixture(scope="module")
def debts() -> list:
    return _load_array("DEBTS")


@pytest.fixture(scope="module")
def lifecycle() -> list:
    return _load_array("SESSION_LIFECYCLE")


class TestCapsData:
    def test_28_capabilities(self, caps):
        assert len(caps) == 28
        assert [c["id"] for c in caps] == [f"C{i}" for i in range(1, 29)]

    def test_c8_implemented_by_kernel_session(self, caps):
        """F3：历史持久化的实现者 = kernel/session（owner）+ foundation/storage（后端）。"""
        c8 = next(c for c in caps if c["id"] == "C8")
        assert c8["title"] == "历史持久化"
        assert "kernel/session" in c8["providers"]
        assert "foundation/storage" in c8["providers"]


class TestDebtsData:
    def test_three_debts(self, debts):
        assert [d["id"] for d in debts] == ["D1", "D2", "D3"]

    def test_d1_resolved(self, debts):
        d1 = debts[0]
        assert "已处置" in d1["direction"], "D1（生命周期归属）必须标记已处置"
        assert "kernel/session" in d1["direction"]

    def test_d2_resolved_soft_delete_unified(self, debts):
        d2 = debts[1]
        assert "已解决" in d2["direction"], "D2（删除语义相反）必须标记已解决"
        assert "archived" in d2["direction"], "统一后的语义 = archived 软删"

    def test_d3_still_open(self, debts):
        """D3（过期/淘汰）是唯一仍开放的债 —— 不许被顺手标成已解决。"""
        d3 = debts[2]
        assert "已解决" not in d3["direction"]
        assert "已处置" not in d3["direction"]


class TestLifecycleData:
    def test_eight_stages(self, lifecycle):
        assert [s["stage"] for s in lifecycle] == [
            "创建", "运行调度", "消息存储", "状态投影",
            "归档", "删除", "过期 / 淘汰", "只读浏览",
        ]

    def test_domain_owners_live_in_kernel_session(self, lifecycle):
        """R-014 后：创建/存储/投影/浏览的 owner 都在 kernel/session。"""
        by_stage = {s["stage"]: s for s in lifecycle}
        assert by_stage["创建"]["owner"] == "SessionService"
        assert by_stage["创建"]["loc"].startswith("kernel/session/service.py")
        assert by_stage["消息存储"]["loc"].startswith("kernel/session/store.py")
        assert "kernel/session/projector.py" in by_stage["状态投影"]["loc"]
        assert by_stage["只读浏览"]["loc"] == "kernel/session/reader.py"

    def test_delete_stage_marked_unified(self, lifecycle):
        """E1 后删除阶段 = web/CLI 同软删（D2 解决的呈现）。"""
        stage = next(s for s in lifecycle if s["stage"] == "删除")
        assert "统一" in stage["owner"]
        # 两个位置都指向软删实现
        assert "web_session.py" in stage["loc"]
        assert "kernel/session/reader.py" in stage["loc"]

    def test_expiry_stage_still_absent(self, lifecycle):
        """D3 的呈现：过期/淘汰 owner = 无（不许在未实施前谎称有）。"""
        stage = next(s for s in lifecycle if s["stage"] == "过期 / 淘汰")
        assert "无" in stage["owner"], "owner 允许带 <b> 标记，但语义必须是「无」"
        assert "已解决" not in stage["owner"] and "已处置" not in stage["owner"]

    def test_scheduler_untouched(self, lifecycle):
        """运行调度归 gateway（管 run 不管会话）—— R-014 没动它。"""
        stage = next(s for s in lifecycle if s["stage"] == "运行调度")
        assert stage["owner"] == "SessionController"
        assert "kernel/gateway/session_controller.py" in stage["loc"]
