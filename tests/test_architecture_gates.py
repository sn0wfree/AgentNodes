"""阶段 4 门禁 —— 依赖方向 / 不变量 / 门退役契约。

这些是**结构性**测试：不验行为，验架构约束不被悄悄破坏。
失败信息指向具体文件与 import 行，便于定位。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "strategy_research"
# 接缝加固（docs/seam-hardening-design-20261030.md）：llm/memory 入严格集。
# agent 不进——builtin_tools 是能力适配器，合法包装域工具（组合根职责）。
PLATFORM_PKGS = (
    "gateway", "orchestration", "runtimes", "storage", "context",
    "llm", "memory",
)
# 研究域全家（v6.3 图 ⑤ 层）：backtest/autoresearch 是产品 IP 的一部分。
RESEARCH_DOMAIN_PKGS = (
    "study", "goal", "hypothesis", "backtest", "autoresearch",
)
# 研究域→平台触点白名单（门禁实测，非估算：9 个文件）。
# 新增触点 = 有意决策，须改此表并在 docs/seam-contract-platform-research-20261030.md
# 登记理由。`study/_archived/` 豁免。
STUDY_PLATFORM_TOUCHPOINTS: dict[str, set[str]] = {
    "graph_preset.py": {"runtimes"},        # GraphEngine 公共 API（阶段图）
    "phase_engine.py": {"agent"},            # build_agent_loop 能力工厂
    "dag_planner.py": {"agent"},             # AgentPlugin/Registry/DAG 配置
    "monitor.py": {"observability"},         # dual_log 统一双写（2026-11-03 日志轮·ADR-000）
    "runner.py": {"agent", "observability", "utils"},
    "runner_knowledge.py": {"agent"},
    "scheduler.py": {"gateway", "orchestration", "utils", "observability"},
    "scenario_router.py": {"llm"},           # 场景路由自建 provider 客户端
    "state_utils.py": {"observability"},     # trace 上下文
    "store.py": {"storage", "observability"},  # sqlite 工具 + dual_log（ADR-000）
}


def _iter_py(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imports(path: Path) -> list[tuple[str, int]]:
    """Return [(absolute dotted module, lineno)] for every import.

    相对导入按文件所在包解析为绝对名（AST 的 ImportFrom.node.module 不含
    前导点——``from ..agent import x`` 的 module 是 ``agent``，不解析就会
    静默绕过所有前缀门禁）。``from pkg import name`` 的 name 可能是子模块，
    一并发射 ``pkg.name`` 候选。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.relative_to(SRC).parts
    pkg = ("strategy_research",) + rel[:-1]
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((a.name, node.lineno) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = pkg[: len(pkg) - (node.level - 1)]
                base = ".".join(anchor)
                if node.module:
                    mods = [f"{base}.{node.module}"]
                    mods += [f"{base}.{node.module}.{a.name}" for a in node.names]
                else:
                    mods = [f"{base}.{a.name}" for a in node.names]
            else:
                if node.module:
                    mods = [node.module]
                    mods += [f"{node.module}.{a.name}" for a in node.names]
                else:
                    mods = [a.name for a in node.names]
            found.extend((m, node.lineno) for m in mods if m)
    return found


def _matches(module: str, prefix: str) -> bool:
    """边界感知前缀匹配（裸 startswith 会把 core.agentx 误判为 core.agent）。"""
    return module == prefix or module.startswith(prefix + ".")


def _violations(root: Path, prefixes: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for path in _iter_py(root):
        for module, lineno in _imports(path):
            if any(_matches(module, p) for p in prefixes):
                out.append(f"{path.relative_to(SRC)}:{lineno} -> {module}")
    return out


# ── 依赖方向：平台 → 域 单向 ────────────────────────────


def test_platform_never_imports_study_domain():
    """平台严格子层不得 import 研究域全家（study/goal/hypothesis/backtest/autoresearch）。

    接缝加固（docs/seam-hardening-design-20261030.md）：v6.3 图 ⑤ 研究域
    独立成层，方向必须是 域→平台。llm/memory 随本刀入严格集（实测零引用，
    只锁不修）。（相对导入由 _imports 解析为绝对名后匹配，无绕过空间。）
    """
    banned = tuple(
        f"strategy_research.core.{p}" for p in RESEARCH_DOMAIN_PKGS
    ) + tuple(f"core.{p}" for p in RESEARCH_DOMAIN_PKGS)
    all_v: list[str] = []
    for pkg in PLATFORM_PKGS:
        all_v.extend(_violations(SRC / "core" / pkg, banned))
    assert not all_v, "平台层反向依赖研究域:\n" + "\n".join(all_v)


def test_study_platform_touchpoints_are_whitelisted():
    """研究域→平台的耦合面锁定为白名单（拆分就绪的看门人）。

    方向合法（域→平台），但新触点必须是有意的：加文件 = 改此表 +
    在 docs/seam-contract-platform-research-20261030.md 登记理由。
    `study/_archived/` 是隔离区，不参与。
    """
    root = SRC / "core" / "study"
    allowed_sublayers = tuple(f"strategy_research.core.{s}" for s in (
        "agent", "llm", "runtimes", "gateway", "orchestration",
        "context", "storage", "memory", "utils", "observability",
        "permission", "strategy_acceptance",
    ))
    violations: list[str] = []
    for path in _iter_py(root):
        rel = path.relative_to(root)
        if "_archived" in rel.parts or rel.name == "__init__.py":
            continue
        # D1：method/ 子包由专属门禁管辖（见 test_method_never_imports_engine）
        if "method" in rel.parts:
            continue
        allowed = STUDY_PLATFORM_TOUCHPOINTS.get(rel.name, set())
        for module, lineno in _imports(path):
            for m in _matches_all(module, allowed_sublayers):
                sub = m.split(".")[2]
                if sub not in allowed:
                    violations.append(
                        f"study/{rel}:{lineno} -> {module} "
                        f"(子层 {sub!r} 不在白名单；如属有意，更新 "
                        f"STUDY_PLATFORM_TOUCHPOINTS 并登记接缝契约)"
                    )
    assert not violations, "研究域→平台出现未登记触点:\n" + "\n".join(violations)


def _matches_all(module: str, prefixes: tuple[str, ...]) -> list[str]:
    """返回 module 命中的全部前缀（边界感知）。"""
    return [p for p in prefixes if _matches(module, p)]


def test_context_hub_never_imports_producers():
    """core/context 不得 import 任何生产方域（agent/goal/study）。

    ContextHub 三条硬约束之一（docs/context-hub-discussion.md §3）：
    「生产方注册进中枢，中枢从不 import 生产方」——否则它就是下一个
    上帝对象。生产方适配器住在 core/agent/context_providers.py，
    方向是 域→平台。（相对导入由 _imports 解析为绝对名后匹配。）

    已知豁免（平台→生产方，有意接线，非本门禁管辖）：
    gateway/singleton.py import core.agent.builtin_tools（组合根装配）。
    """
    violations = _violations(
        SRC / "core" / "context",
        tuple(f"strategy_research.core.{p}" for p in ("agent", "goal", "study"))
        + tuple(f"core.{p}" for p in ("agent", "goal", "study")),
    )
    assert not violations, "ContextHub 反向依赖生产方:\n" + "\n".join(violations)


def test_platform_never_imports_fastapi():
    """core/gateway 契约：零 FastAPI import（挂载式宿主的前提）。

    见 gateway/__init__.py 落位规则与 v6 宿主决策。
    """
    violations: list[str] = []
    for path in _iter_py(SRC / "core" / "gateway"):
        for module, lineno in _imports(path):
            if module.startswith(("fastapi", "starlette")):
                violations.append(f"{path.relative_to(SRC)}:{lineno} -> {module}")
    assert not violations, "gateway 依赖了 FastAPI:\n" + "\n".join(violations)


# ── 不变量：可执行登记表全绿 ────────────────────────────


def test_invariants_verify_all_passes():
    from strategy_research.kernel.gateway.invariants import verify_all

    verify_all()


def test_invariant_registry_covers_documented_set():
    """INV-1/3/5/8/9 必须在登记表里（防悄悄删条目）。"""
    from strategy_research.kernel.gateway.invariants import invariants

    ids = {inv.id.split(":")[0].strip() for inv in invariants()}
    assert {"INV-1", "INV-3", "INV-5", "INV-8", "INV-9"} <= ids


# ── 选项 B：编排层 / 执行原语的分工 ─────────────────────


def test_domain_layers_use_the_agent_factory_not_raw_agentloop():
    """域层不直接 new AgentLoop，但**可以**经 role_factory 用它（选项 B）。

    分工（2026-10-28 决策，见 docs/stage-c1-wp-agent-node-design-20261028.md）：
      - GraphEngine = 编排层（节点/边/中断/预算树），唯一编排 runtime
      - AgentLoop  = agent 执行原语（单 turn），**不是**第二套控制流

    所以「域层出现 AgentLoop」本身不是违规——违规的是**绕过工厂**直接构造：
    那样会跳过角色提示装配、工具白名单、预算与权限接线。

    允许的构造点：
      - core/agent/role_factory.py  → build_agent_loop（唯一构造点）
      - core/agent/chat_loop.py     → chat 专用，P5-1c 待删
      - core/agent/executor.py      → swarm 专用
    """
    violations: list[str] = []
    for domain in ("study", "workflow", "swarm"):
        for path in _iter_py(SRC / "core" / domain):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "AgentLoop"
                ):
                    violations.append(
                        f"{path.relative_to(SRC)}:{node.lineno} -> AgentLoop(...)"
                    )
    assert not violations, (
        "域层绕过 role_factory 直接构造了 AgentLoop"
        "（应用 run_agent_via_llm / agent_node）:\n"
        + "\n".join(violations)
    )


def test_graph_engine_is_the_only_orchestration_runtime():
    """除 GraphEngine 外，不得再出现第二套「按图跑节点」的编排器。

    选项 B 下 AgentLoop 是执行原语，可以存在；但**编排**只能有一个。
    判据：core/ 里 import StateGraph 的文件只能是 GraphEngine 本身。
    """
    violators: list[str] = []
    for path in _iter_py(SRC / "core"):
        if path.name == "graph_engine.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "from langgraph.graph import" in text and "StateGraph" in text:
            violators.append(str(path.relative_to(SRC)))
    assert not violators, "出现了第二个状态图编排器:\n" + "\n".join(violators)


def test_agentloop_capability_premise_holds():
    """选项 B 的前提必须持续成立：AgentLoop 独有四项能力仍然在。

    选项 B 保留 AgentLoop 作为执行原语的理由，是它无条件提供四项
    react 节点没有的能力。若未来任何一项被移除，选项 B 的前提即消失，
    应重新评估架构决策而不是继续沿用。

    见 docs/stage-c1-wp-agent-node-design-20261028.md §1.1。
    """
    loop_tools = (SRC / "core" / "agent" / "loop_tools.py").read_text(encoding="utf-8")
    assert "_TOOL_MAX_RETRIES" in loop_tools, "瞬时工具重试"
    assert "_TOOL_RESULT_HISTORY_MAX" in loop_tools, "30k 工具结果截断"
    assert "check_guards" in loop_tools, "单调 deny guard"
    assert "_execute_tool_batch" in loop_tools, "只读工具并行"


# ── 门退役契约（阶段 4）──────────────────────────────


def test_research_graph_gate_is_retired():
    """门已退役：恒 True，环境变量无效。"""
    import os

    from strategy_research.kernel.gateway import research_graph_enabled

    assert research_graph_enabled() is True
    for value in ("0", "1", "false"):
        os.environ["QNODES_RESEARCH_GRAPH"] = value
        try:
            assert research_graph_enabled() is True
        finally:
            os.environ.pop("QNODES_RESEARCH_GRAPH", None)


def test_phase_engine_has_no_gate_import():
    """phase_engine 不再 import research_graph_enabled（门接线已移除）。"""
    path = SRC / "core" / "study" / "phase_engine.py"
    source = path.read_text(encoding="utf-8")
    # 允许文档字符串提及，但不得有实际调用
    assert "research_graph_enabled()" not in source


# ── 数据面：messages 单一写入者（S3）─────────────────────


def test_messages_table_written_only_by_projection_or_guarded_persist():
    """`INSERT INTO messages` 白名单（S3 数据面收敛契约）。

    统一库 messages 表的设计写入者只有两个：
    - ``api/session/projection_store.py``：projector（事件 fold）；
    - ``api/routers/web_session.py``：``persist_message`` 兼容直写
      （带 message_id 幂等防护，服务 compaction/slash 回调）。

    B4 Step 1/4-5（2026-10-30）后两条已知遗留都已消失：MemoryManager
    改为发事件经 projector（不再直写），旧会话栈整体删除。白名单回到
    只有 projector + persist_message——这是这条门禁设计时的目标状态。

    新增任何写者即失败；这份白名单只能缩短，不能变长。
    """
    allowed = {
        "api/session/projection_store.py",
        "api/routers/web_session.py",
    }
    # 精确匹配表名 messages（不要把 messages_fts 索引写入算进来）
    import re as _re

    pattern = _re.compile(
        r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+messages\s*\(",
        _re.IGNORECASE,
    )
    violations: list[str] = []
    for path in _iter_py(SRC):
        source = path.read_text(encoding="utf-8", errors="ignore")
        if not pattern.search(source):
            continue
        rel = path.relative_to(SRC).as_posix()
        if rel not in allowed:
            violations.append(rel)
    assert not violations, (
        "messages 表出现未授权写入者（S3 白名单只允许 projector + "
        "persist_message + 两个已知遗留）:\n" + "\n".join(violations)
    )


# B4 Step 4-5 (2026-10-30): ``test_legacy_session_db_stays_separate_from_unified_db``
# was removed together with ``core/session/db.py``. It pinned the rule "the
# legacy stack must not point at the unified file because the schemas are
# incompatible" -- once the legacy stack is gone there is nothing to keep
# apart. Keeping it would have required keeping the file.


# ── 消费归属：kind 过滤不得丢失工单 ────────────────────


def test_mailbox_kind_filter_never_drops_work():
    from strategy_research.kernel.gateway import scheduler_request
    from strategy_research.kernel.orchestration import EntryPoint, WorkKind

    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(scheduler_request("s", {"study_id": "x"}))
    mail = ep.mailbox_for("automation:s")

    # 反复用错误的 kind 过滤，工单必须一直在
    for _ in range(5):
        assert mail.poll_due({WorkKind.CHAT}) is None
        assert mail.peek_depth() == 1
    assert mail.poll_due({WorkKind.AUTOMATION}) is not None


def test_chat_kind_filter_never_drops_chat_work():
    from strategy_research.kernel.gateway import api_chat_request
    from strategy_research.kernel.orchestration import EntryPoint, WorkKind

    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(api_chat_request("s", "hi"))
    mail = ep.mailbox_for("chat:s")

    assert mail.poll_due({WorkKind.AUTOMATION}) is None
    assert mail.peek_depth() == 1
    assert mail.poll_due({WorkKind.CHAT}) is not None


# ── 用户主目录沙箱（阶段 4 测试隔离）───────────────────


def test_user_home_is_sandboxed_during_tests():
    """整个测试进程的 home 解析必须落在沙箱。

    背景：曾有测试经 Windows Path.home()（读 USERPROFILE 而非 HOME）长期
    覆写宿主 ~/.quantnodes/jwt_secret。conftest 顶部重定向后，任何测试里
    的 Path.home()/expanduser("~") 都不得再指向真实主目录。
    """
    import os
    from pathlib import Path

    import _home_sandbox

    assert _home_sandbox.sandboxed(), "conftest 未重定向 USERPROFILE"
    assert Path.home() == _home_sandbox.SANDBOX_HOME
    assert os.path.expanduser("~") == str(_home_sandbox.SANDBOX_HOME)
    # 模块级常量（import 时求值）也在沙箱里
    from strategy_research.core.llm import quantnodes_bridge

    assert str(_home_sandbox.SANDBOX_HOME) in str(quantnodes_bridge.CONFIG_PATH)


def test_sandbox_home_is_outside_real_home():
    """沙箱必须与真实主目录物理隔离（不能是它的子目录）。"""
    import _home_sandbox

    sandbox = _home_sandbox.SANDBOX_HOME.resolve()
    real = _home_sandbox.REAL_HOME.resolve()
    assert real not in sandbox.parents
