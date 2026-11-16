"""会话生命周期下沉 —— 双入口同源契约测试（批次 A / B1 的固化网）。

**为什么需要这份测试**：迁移期间新旧两条 import 路径并存。
如果哪天有人「顺手」把垫片改成复制一份实现（而不是 re-export），
两个路径就会返回**不同的类对象** —— 类型检查失效、isinstance 判断失败、
且不会有任何现有测试报警。这份测试钉住「同源」这件事。

对应方案：docs/design/session-lifecycle-kernel-migration-20261116.md §4 批次 A / B1
"""

from __future__ import annotations

import importlib
import inspect

import pytest

#: 已下沉的模块 → 该模块的公开符号（新增符号时同步这里）
SUNK_MODULES = {
    # ── 批次 A：零依赖词汇表 ──
    "models": [
        "Attempt", "AttemptId", "AttemptStatus", "Message",
        "MessageId", "Session", "SessionId", "SessionStatus", "_utc_now_iso",
    ],
    "projection_models": ["ProjectedPart", "ProjectedMessage", "ProjectedSession"],
    "part_accumulate": [
        "_accumulate_part", "_accumulate_text_started", "_accumulate_text_delta",
        "_accumulate_text_ended", "_build_tool_call_part", "_accumulate_tool_result",
        "_accumulate_thinking_delta",
    ],
    # ── 批次 B1：叶子下沉（2026-11-16）──
    "history_convert": [
        "_build_tool_indexes", "_convert_one_history_message", "_decide_kept_compactions",
        "_defer_or_drop_tool_message", "_extract_tool_calls", "_find_compaction_indices",
        "MAX_HISTORY_CHARS", "_HistoryConvertContext",
    ],
    "projection_handlers": [
        "apply_event", "HANDLERS", "_on_message_received", "_on_tool_result",
        "_on_compact", "_on_goal_updated",
    ],
    "projection_store": ["ProjectionStore"],
    "trace_projection": ["TraceProjection"],
    "backfill_event_log": [
        # `main` 是 argparse CLI 入口，不进包级公共 API（垫片仍转出它）
        "backfill_event_log", "_generate_events_for_session",
    ],
    # ── 批次 B2：会话库（2026-11-16，带改名）──
    "db": [
        "_get_db_path", "_get_db_at", "_ensure_schema", "_run_schema_migrations",
        "_ensure_fts_table", "_ensure_fts_table_impl", "_add_column", "_has_column",
        "_validate_identifier", "_drop_column", "_migrate_text_part_ids",
        "_migrate_message_types", "_db_thread_local", "_get_db",
        "_row_to_session", "_row_to_message", "ensure_session_row",
        # 注意：`_fetch_session_owned` **不在**这里 —— 它是 HTTP 鉴权语义，
        # 批次 B2 决策 2b 已把它上移到 routers/web_session.py。
    ],
    # ── 批次 B3：相对依赖簇（2026-11-16，同 commit 整簇搬）──
    "service": [
        # 顶层定义
        "SessionService", "_QUEUE_LIMIT", "_CHAT_ATTEMPT_TIMEOUT",
        "_friendly_error_text", "_utc_now_iso", "MAX_HISTORY_CHARS",
        # W-split 门面 re-export（消费方从 service 旧路径取 loop_events /
        # history_convert / part_accumulate 的名字，垫片必须继续转出）
        "_bootstrap_workspace", "_build_attempt_metrics", "_LoopEventForwarder",
        "_plan_mode_allowed_tools", "_resolve_loop_prompt", "_thinking_instructions",
        "_accumulate_part",
    ],
    "store": ["SessionStore", "_row_to_attempt", "_utc_now_iso"],
    "projector": [
        "Projector",
        # test_display_tools / test_goal_events / test_projector_snapshot
        # 从 projector 路径取 projection_models 的名字
        "ProjectedMessage", "ProjectedPart", "ProjectedSession",
    ],
    "loop_events": [
        "_build_attempt_metrics", "_LoopEventForwarder", "_bootstrap_workspace",
        "_PLAN_READONLY_TOOLS", "_plan_mode_allowed_tools", "_thinking_instructions",
        "_resolve_loop_prompt",
    ],
}

#: kernel 模块名 → L1 垫片模块名（默认同名；B2 的 db 是改名下沉）
SHIM_ALIASES = {
    "db": "web_session_db",
}


def _pair(module: str):
    """返回 (kernel 路径模块, L1 垫片模块)。

    ``SHIM_ALIASES`` 处理改名下沉（B2 的 ``db`` ← ``web_session_db``）。
    """
    new = importlib.import_module(f"agentnodes.kernel.session.{module}")
    old = importlib.import_module(
        f"agentnodes.interfaces.api.session.{SHIM_ALIASES.get(module, module)}"
    )
    return new, old


@pytest.mark.parametrize("module", sorted(SUNK_MODULES))
def test_shim_reexports_same_objects(module: str):
    """垫片必须是 re-export，不是复制 —— 每个符号都是同一个对象。"""
    new, old = _pair(module)
    for name in SUNK_MODULES[module]:
        assert hasattr(new, name), f"kernel 侧缺符号 {name}"
        assert hasattr(old, name), f"L1 垫片缺符号 {name}"
        assert getattr(new, name) is getattr(old, name), (
            f"{module}.{name} 在两个路径上不是同一对象 —— "
            f"垫片被改成了复制，双入口同源已破裂"
        )


@pytest.mark.parametrize("module", sorted(SUNK_MODULES))
def test_symbols_belong_to_kernel_module(module: str):
    """可调用符号的 __module__ 必须指向 kernel —— 证明实现真的在 L3。

    只查类/函数：常量（如 ``MAX_HISTORY_CHARS`` / ``HANDLERS``）没有
    ``__module__`` 属性，查它们会得到 None。常量的归属由身份同一性
    （``test_shim_reexports_same_objects``）覆盖。
    """
    new, _ = _pair(module)
    for name in SUNK_MODULES[module]:
        obj = getattr(new, name)
        if not (inspect.isclass(obj) or inspect.isfunction(obj)
                or inspect.isbuiltin(obj)):
            continue
        owner = getattr(obj, "__module__", None)
        assert owner is not None, f"{module}.{name} 没有 __module__"
        assert owner.startswith("agentnodes.kernel.session"), (
            f"{module}.{name}.__module__ = {owner!r}，实现不在 kernel"
        )


def _defined_names(module) -> set[str]:
    """模块**自己定义**的顶层名字（AST 推导，不含 import 进来的名字）。

    ``dir()`` 会包含 ``Optional`` / ``json`` / ``logging`` 这类被 import 的名字，
    用它当基线会让断言永远失败。
    """
    import ast
    from pathlib import Path

    src = Path(module.__file__).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Name) and tg.id != "__all__":
                    names.add(tg.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
    return names


def test_kernel_session_package_exports_public_names():
    """kernel.session 包的导出面能取到全部已下沉符号的**公有**名。

    只查公有：私有名（``_convert_one_history_message`` 等）属实现细节，
    不是包的公共 API；它们由 ``test_every_top_level_name_is_reexported``
    在模块级保证（垫片必须转出）。
    """
    pkg = importlib.import_module("agentnodes.kernel.session")
    missing = []
    for module, names in SUNK_MODULES.items():
        for name in names:
            if name.startswith("_"):
                continue
            if not hasattr(pkg, name):
                missing.append(f"{module}.{name}")
    assert not missing, f"agentnodes.kernel.session 未导出: {missing}"


@pytest.mark.parametrize(
    "consumer",
    [
        "agentnodes.interfaces.api.session.store",
        "agentnodes.interfaces.api.session.service",
        "agentnodes.interfaces.api.session.projector",
        # B3 后 L1 只剩垫片 + 词汇表；真实实现全在 kernel
        "agentnodes.kernel.session.store",
        "agentnodes.kernel.session.service",
        "agentnodes.kernel.session.projector",
        "agentnodes.kernel.session.loop_events",
        # K1 已消除：kernel/session 里对 routers 的真实 import = 0（注释除外）
    ],
)
def test_l1_consumers_still_import(consumer: str):
    """依赖旧路径的 L1 行为模块仍能 import（迁移期间不能断链）。"""
    importlib.import_module(consumer)


def test_kernel_session_has_no_routers_import():
    """K1 终态断言：kernel/session 里对 ``routers`` 的 import 必须为 0。

    方案 K1：``service.py``/``store.py`` 曾反向 import ``routers.web_session``
    （`_get_db` / `_row_to_message` / `auto_title_session`）。B2 把前两者下沉
    ``db.py``，B3 把 ``auto_title_session`` 下沉 ``service.py`` —— 全部变为
    同包引用。用 AST 判定（注释里的旧代码字样不算 import）。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "agentnodes" / "kernel" / "session"
    hits = []
    for p in root.glob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.ImportFrom) and n.module:
                mods.append("." * n.level + n.module)
            elif isinstance(n, ast.Import):
                mods.extend(a.name for a in n.names)
            if any("routers" in m for m in mods):
                hits.append(f"{p.name}:{n.lineno}")
    assert not hits, f"kernel/session 仍 import routers（K1 破裂）: {hits}"


@pytest.mark.parametrize("module", sorted(SUNK_MODULES))
def test_every_top_level_name_is_reexported(module: str):
    """垫片必须转出 kernel 模块的**全部**顶层名字（含私有）。

    SUNK_MODULES 是「消费方用到的核心符号」清单；这条更严 —— 它按 kernel 模块的
    实际内容推导，防的是「新增符号忘了加进垫片」这种静默漂移。
    """
    new, old = _pair(module)
    defined = _defined_names(new)
    missing = []
    for name in defined:
        if not hasattr(old, name):
            missing.append(name)
        elif getattr(new, name) is not getattr(old, name):
            missing.append(f"{name}(非同一对象)")
    assert not missing, (
        f"{module} 的顶层符号未被垫片完整转出: {sorted(missing)}"
    )


def _fastapi_importers(root) -> list[str]:
    """扫描目录下所有 .py，返回真的 import 了 fastapi 的文件相对路径。

    用 AST 而非字符串匹配：注释里写「原先是 fastapi」不算导入
    （本仓有过把注释误判成 import 的教训，见方案 §1.3 的修正）。
    """
    import ast
    from pathlib import Path

    hits = []
    for p in Path(root).rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.ImportFrom) and n.module:
                mods.append(n.module)
            elif isinstance(n, ast.Import):
                mods.extend(a.name for a in n.names)
            for m in mods:
                if m == "fastapi" or m.startswith("fastapi."):
                    hits.append(str(p))
                    break
            else:
                continue
            break
    return hits


@pytest.mark.parametrize("layer", ["kernel", "foundation"])
def test_kernel_and_foundation_never_import_fastapi(layer: str):
    """L3/L4 对 fastapi 的导入数必须恒为 **0**。

    这是批次 B2 决策 2b 的立足点：``web_session_db.py`` 曾有 3 行 fastapi 依赖
    （L19 + 两处 ``HTTPException``），全部在 ``_fetch_session_owned`` 里。那 11 行
    是 HTTP 鉴权语义，留在了 L1 路由层 —— 内核因此零 web 框架依赖。

    若哪天有人又把 ``HTTPException`` 引进 kernel，会话库就会把 web 框架拖进
    每一个纯内核场景（CLI / TUI / MCP / 嵌入式 / 单元测试）。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "agentnodes" / layer
    hits = _fastapi_importers(root)
    assert not hits, (
        f"{layer}/ 层出现 fastapi 导入（必须为 0）：\n  "
        + "\n  ".join(hits)
        + "\nHTTP 语义（状态码/Request/Response）应留在 L1 接口层。"
    )
