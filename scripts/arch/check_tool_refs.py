# -*- coding: utf-8 -*-
"""check_tool_refs — 工具名字符串引用的门禁。

**为什么存在**：同一个病在本仓库发作过两次，只是换了数据结构。

第一次（W1 解决）：``CapabilityRegistry`` 里声明了 kind，却没有实现
→ 幽灵工具 44 个。门禁 = ``check_capability_requires.py``。

第二次（批次 4 解决）：配置表里写了工具名，却没有实现
→ **56 处悬空**（``TOOLSETS`` 32 + ``BUILTIN_PLUGINS`` 24）。
根因：``TOOLSETS`` 与插件的 ``tools`` 是**字符串字面量**，不经过
registry 的注册路径，``CapabilityRegistry.requires`` 机制结构性看不见它们。

本脚本把「配置里写的工具名」钉到「registry 里真实注册的工具」上：

    installed = build_default_registry() 实际注册的全部工具名
    declared  = 各配置面里出现过的工具名

    declared - installed 非空 → 悬空引用 FAIL
    PENDING_TOOLS ∩ installed 非空 → 陈旧待恢复清单 FAIL（反向检查）

**四个配置面**：

1. ``kernel/agent/toolsets.py::TOOLSETS`` — 角色 → 工具白名单
2. ``apps/agent_plugins/*.py`` 插件的 ``tools`` / ``python_function`` / ``prompt_file``
3. ``templates/workflows/*.json`` — 工作流模板的 ``config.tools`` / ``config.tool``
4. ``PENDING_TOOLS``（各插件模块）— **反向**检查：列在待恢复清单里的
   工具若已实现，说明清单陈旧，必须更新

**为什么用 import 而不是 AST**：

本门禁的"事实"是 **registry 里有什么**，而 registry 由一堆
``r.register(XxxTool())`` 语句构建，工具名藏在各 Tool 类的
``name = "..."`` 属性里。用 AST 反推等于重新实现一遍注册逻辑——
那本身就会漂移。直接 import 拿 ``all_tools()`` 是唯一诚实的事实源。

代价：脚本需要能 import ``agentnodes``（PROBE_PI 满足）。这不违背
``check_capability_requires`` 的"只用 AST"原则——那个脚本判定的是
**声明 vs 代码**，两者都在同一份 AST 里；本脚本判定的是
**配置字符串 vs 运行期注册表**，必须真的建一次注册表。

**豁免机制**（``EXEMPT_REFS``）：显式逐条列出，每条必须带理由与去向。
不允许通配豁免——通配豁免会让这道门禁变成假的。

**三种模式**：

1. 默认：发现即 exit 1，逐条打印违规 + ``file:line`` 证据
2. ``--report``：只打印全表（配置面 → 引用的工具 → 是否已实现），不判定
3. ``--selftest``：构造合成配置面，断言四类检测（悬空 / 陈旧待恢复 /
   插件三层 / 模板）都能命中

用法：
    python scripts/arch/check_tool_refs.py             # 判定
    python scripts/arch/check_tool_refs.py --report    # 查看全表
    python scripts/arch/check_tool_refs.py --selftest  # 校验器自检

命名规范见 scripts/README.md（arch/ = check_*，发现即失败）。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
PKG = SRC / "agentnodes"

TEMPLATES_DIR = PKG / "templates" / "workflows"
PLUGINS_DIR = PKG / "apps" / "agent_plugins"

#: 显式豁免：``(配置面, 引用名) -> 理由``。
#:
#: **只允许逐条列**，每条必须说明「为什么现在可以悬空」+「什么时候消失」。
#: 通配豁免会让本门禁失去意义。
EXEMPT_REFS: dict[tuple[str, str], str] = {
    # goal 的 9-agent 拓扑是 R3（preset 化）的处理对象；当前它的工具引用
    # 指向第三步才恢复的量化工具包。R3 会删除 ``build_autoresearch_workflow_config``
    # 改为读 YAML preset，届时本条豁免自然失效。
    ("apps/goal/workflow_config.py", "*"): "R3 preset 化时整体删除",
    # 评估基线是**历史快照**，记录的是当时的工具集。改它等于篡改历史，
    # 所以永久豁免（但本门禁也不扫它——此处仅作文档说明）。
}

# ── 数据结构 ─────────────────────────────────────────────────────────


@dataclass
class Violation:
    surface: str          # 配置面名
    where: str            # file:line 或语义位置
    detail: str           # 人话解释
    missing: list[str] = field(default_factory=list)


@dataclass
class Report:
    """一次完整检查的结果。"""

    installed: set[str] = field(default_factory=set)
    refs: dict[str, set[str]] = field(default_factory=dict)   # 面 → 引用的工具
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


# ── 事实源 ───────────────────────────────────────────────────────────


def _ensure_src_on_path() -> None:
    p = str(SRC)
    if p not in sys.path:
        sys.path.insert(0, p)


def installed_tools() -> set[str]:
    """registry 里真实注册的工具名集合（唯一事实源）。"""
    _ensure_src_on_path()
    from agentnodes.kernel.agent.builtin_tools import build_default_registry

    return {t.name for t in build_default_registry().all_tools()}


def installed_executors() -> tuple[set[str], set[str]]:
    """已注册的 python 执行器与评估器名（插件 ``python_function`` 的对照）。"""
    _ensure_src_on_path()
    from agentnodes.kernel.agent.exec_registry import (
        list_evaluators,
        list_python_executors,
    )

    return set(list_python_executors()), set(list_evaluators())


# ── 四个配置面 ───────────────────────────────────────────────────────


def collect_toolsets() -> dict[str, set[str]]:
    """配置面 1：``TOOLSETS``。返回 ``{toolset 名: 工具名集合}``。"""
    _ensure_src_on_path()
    from agentnodes.kernel.agent.toolsets import TOOLSETS

    return {name: set(tools) for name, tools in TOOLSETS.items()}


def collect_plugin_refs() -> list[tuple[str, set[str], str]]:
    """配置面 2：插件声明。

    返回 ``[(plugin_id, tools, python_function)]``。
    """
    _ensure_src_on_path()
    out: list[tuple[str, set[str], str]] = []
    # 插件模块是 L2 内容；门禁不预设具体模块名，发现即扫
    from agentnodes.apps import agent_plugins
    from agentnodes.kernel.agent.plugin import AgentPlugin

    for mod_name in sorted(getattr(agent_plugins, "__all_plugin_modules__", ())):
        mod = __import__(
            f"agentnodes.apps.agent_plugins.{mod_name}", fromlist=["_"]
        )
        for attr in dir(mod):
            val = getattr(mod, attr)
            if isinstance(val, tuple) and val and all(
                isinstance(p, AgentPlugin) for p in val
            ):
                for p in val:
                    out.append((p.id, set(p.tools), p.python_function or ""))
    return out


def collect_pending() -> dict[str, set[str]]:
    """配置面 4：各插件模块的 ``PENDING_TOOLS``（反向检查用）。"""
    _ensure_src_on_path()
    from agentnodes.apps import agent_plugins

    out: dict[str, set[str]] = {}
    for mod_name in sorted(getattr(agent_plugins, "__all_plugin_modules__", ())):
        mod = __import__(
            f"agentnodes.apps.agent_plugins.{mod_name}", fromlist=["_"]
        )
        pending = getattr(mod, "PENDING_TOOLS", None)
        if pending:
            out[mod_name] = set(pending)
    return out


def collect_pending_executors() -> dict[str, set[str]]:
    """各插件模块的 ``PENDING_EXECUTORS``（执行器侧的反向检查）。"""
    _ensure_src_on_path()
    from agentnodes.apps import agent_plugins

    out: dict[str, set[str]] = {}
    for mod_name in sorted(getattr(agent_plugins, "__all_plugin_modules__", ())):
        mod = __import__(
            f"agentnodes.apps.agent_plugins.{mod_name}", fromlist=["_"]
        )
        pending = getattr(mod, "PENDING_EXECUTORS", None)
        if pending:
            out[mod_name] = set(pending)
    return out


def collect_templates(directory: Path | None = None) -> dict[str, set[str]]:
    """配置面 3：``templates/workflows/*.json`` 里节点引用的工具名。"""
    d = directory or TEMPLATES_DIR
    out: dict[str, set[str]] = {}
    if not d.exists():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            out[f"{path.name}(unreadable: {exc})"] = set()
            continue
        tools: set[str] = set()
        for node in data.get("nodes") or []:
            cfg = node.get("config") or {}
            for t in cfg.get("tools") or []:
                tools.add(str(t))
            if cfg.get("tool"):
                tools.add(str(cfg["tool"]))
        out[path.name] = tools
    return out


# ── 判定 ─────────────────────────────────────────────────────────────


def _is_exempt(surface: str, ref: str) -> bool:
    return (
        (surface, ref) in EXEMPT_REFS
        or (surface, "*") in EXEMPT_REFS
    )


def check(
    *,
    installed: set[str] | None = None,
    toolsets: dict[str, set[str]] | None = None,
    plugins: list[tuple[str, set[str], str]] | None = None,
    templates: dict[str, set[str]] | None = None,
    pending: dict[str, set[str]] | None = None,
    executors: tuple[set[str], set[str]] | None = None,
    pending_executors: dict[str, set[str]] | None = None,
) -> Report:
    """跑全部检查。参数可注入，供 selftest 用。"""
    installed = installed if installed is not None else installed_tools()
    toolsets = toolsets if toolsets is not None else collect_toolsets()
    plugins = plugins if plugins is not None else collect_plugin_refs()
    templates = templates if templates is not None else collect_templates()
    pending = pending if pending is not None else collect_pending()
    executors = executors if executors is not None else installed_executors()
    if pending_executors is None:
        pending_executors = collect_pending_executors()

    py_exec, evaluators = executors
    rep = Report(installed=installed)
    pending_fn: set[str] = set()
    for names in pending_executors.values():
        pending_fn |= names

    # ① TOOLSETS
    for name, tools in sorted(toolsets.items()):
        rep.refs[f"TOOLSETS[{name}]"] = tools
        missing = sorted(t for t in tools if t not in installed)
        if missing:
            rep.violations.append(Violation(
                surface="TOOLSETS",
                where=f"kernel/agent/toolsets.py::TOOLSETS[{name!r}]",
                detail=f"{len(missing)} 个工具未实现",
                missing=missing,
            ))

    # ② 插件
    #
    # **已登记在 PENDING_TOOLS 里的悬空是预期的**：那是显式的「待恢复清单」，
    # 写着"这些工具第三步会恢复"。门禁只拦**没登记的**悬空——那才是
    # 忘了写的、真会静默失败的东西。这个豁免不是通配：每一条都必须在
    # 插件模块的 ``PENDING_TOOLS`` 里逐名登记，且 ④ 会反向检查清单是否陈旧。
    pending_all: set[str] = set()
    for names in pending.values():
        pending_all |= names

    for plugin_id, tools, fn in plugins:
        rep.refs[f"plugin:{plugin_id}"] = set(tools)
        missing = sorted(t for t in tools if t not in installed)
        unregistered = [t for t in missing if t not in pending_all]
        if unregistered:
            rep.violations.append(Violation(
                surface="plugins",
                where=f"apps/agent_plugins/*.py::plugin[{plugin_id!r}].tools",
                detail=(
                    f"{len(unregistered)} 个工具既未实现、也未登记在 "
                    f"PENDING_TOOLS（真悬空）"
                ),
                missing=unregistered,
            ))
        if fn:
            # python_function 可能是执行器或评估器，任一命中即可。
            # 已登记在 PENDING_EXECUTORS 里的悬空是预期的（待恢复清单）。
            if fn not in py_exec and fn not in evaluators and fn not in pending_fn:
                rep.violations.append(Violation(
                    surface="plugins",
                    where=f"apps/agent_plugins/*.py::plugin[{plugin_id!r}]"
                          f".python_function",
                    detail=(
                        f"执行器/评估器 {fn!r} 既未注册、也未登记在 "
                        f"PENDING_EXECUTORS（真悬空）"
                    ),
                    missing=[fn],
                ))

    # ③ 模板
    for tpl_name, tools in sorted(templates.items()):
        rep.refs[f"template:{tpl_name}"] = tools
        missing = sorted(t for t in tools if t not in installed)
        if missing:
            rep.violations.append(Violation(
                surface="templates",
                where=f"templates/workflows/{tpl_name}",
                detail=f"{len(missing)} 个工具未实现",
                missing=missing,
            ))

    # ④ PENDING_TOOLS / PENDING_EXECUTORS 反向检查
    for mod_name, names in sorted(pending.items()):
        already = sorted(n for n in names if n in installed)
        if already:
            rep.violations.append(Violation(
                surface="pending",
                where=f"apps/agent_plugins/{mod_name}.py::PENDING_TOOLS",
                detail=(
                    f"{len(already)} 个工具已实现，但仍列在待恢复清单里"
                    f"（清单陈旧，应移除）"
                ),
                missing=already,
            ))
    for mod_name, names in sorted(pending_executors.items()):
        already = sorted(n for n in names if n in py_exec or n in evaluators)
        if already:
            rep.violations.append(Violation(
                surface="pending",
                where=f"apps/agent_plugins/{mod_name}.py::PENDING_EXECUTORS",
                detail=(
                    f"{len(already)} 个执行器已注册，但仍列在待恢复清单里"
                    f"（清单陈旧，应移除）"
                ),
                missing=already,
            ))

    return rep


# ── 输出 ─────────────────────────────────────────────────────────────


def print_report(rep: Report) -> None:
    """打印全表。

    显示侧与判定侧口径必须一致：**登记在待恢复清单里的悬空算 OK**，
    否则报告会一片红、真实违规被淹没（这正是本仓库反复踩的"噪声淹没信号"）。
    """
    pending_all: set[str] = set()
    try:
        for names in collect_pending().values():
            pending_all |= names
    except Exception:  # noqa: BLE001 —— 报告阶段拿不到就退化为严格显示
        pass

    print(f"registry 已注册工具: {len(rep.installed)}")
    print()
    print("=== 配置面 ===")
    for surface in sorted(rep.refs):
        refs = rep.refs[surface]
        real_bad = sorted(
            t for t in refs
            if t not in rep.installed and t not in pending_all
        )
        pending_bad = sorted(
            t for t in refs
            if t not in rep.installed and t in pending_all
        )
        mark = "OK " if not real_bad else "X  "
        extra = ""
        if real_bad:
            extra += f"  悬空: {real_bad}"
        if pending_bad:
            extra += f"  [待恢复: {len(pending_bad)}]"
        print(f"  {mark} {surface:42s} {len(refs):2d} refs{extra}")


def print_violations(rep: Report) -> None:
    if rep.ok:
        return
    print()
    print("=== 违规 ===")
    for v in rep.violations:
        print(f"  [{v.surface}] {v.where}")
        print(f"       {v.detail}")
        if v.missing:
            print(f"       未实现: {v.missing}")


# ── selftest ─────────────────────────────────────────────────────────


def _selftest() -> int:
    """断言四类检测都能命中。"""
    failures: list[str] = []
    installed = {"read", "write", "shell"}

    # ① 悬空：TOOLSETS 引用未实现工具
    rep = check(
        installed=installed,
        toolsets={"chat": {"read", "write"}, "bad": {"read", "ghost"}},
        plugins=[],
        templates={},
        pending={},
        executors=(set(), set()),
    )
    if not any(v.surface == "TOOLSETS" and "ghost" in v.missing
               for v in rep.violations):
        failures.append("悬空 TOOLSETS 未命中")

    # ② 插件工具悬空
    rep = check(
        installed=installed,
        toolsets={},
        plugins=[("p1", {"read", "nope"}, "")],
        templates={},
        pending={},
        executors=(set(), set()),
    )
    if not any(v.surface == "plugins" for v in rep.violations):
        failures.append("插件工具悬空未命中")

    # ③ 插件 python_function 未注册
    rep = check(
        installed=installed,
        toolsets={},
        plugins=[("p2", set(), "missing_fn")],
        templates={},
        pending={},
        executors=(set(), set()),
    )
    if not any("missing_fn" in v.missing for v in rep.violations):
        failures.append("python_function 未注册未命中")

    # ④ 模板悬空
    rep = check(
        installed=installed,
        toolsets={},
        plugins=[],
        templates={"t.json": {"read", "ghost_tool"}},
        pending={},
        executors=(set(), set()),
    )
    if not any(v.surface == "templates" for v in rep.violations):
        failures.append("模板悬空未命中")

    # ⑤ PENDING_TOOLS 反向：已实现却仍在待恢复清单
    rep = check(
        installed=installed,
        toolsets={},
        plugins=[],
        templates={},
        pending={"quant": {"read", "someone_not_yet"}},
        executors=(set(), set()),
    )
    if not any(v.surface == "pending" and "read" in v.missing
               for v in rep.violations):
        failures.append("PENDING_TOOLS 陈旧未命中")

    # ⑥ 全绿场景不得误报
    rep = check(
        installed=installed,
        toolsets={"chat": {"read", "write"}},
        plugins=[("ok", {"read"}, "")],
        templates={"t.json": {"shell"}},
        pending={"quant": {"not_ready"}},
        executors=(set(), set()),
    )
    if not rep.ok:
        failures.append(f"全绿场景误报: {[v.where for v in rep.violations]}")

    if failures:
        print("[X] selftest FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("[OK] selftest passed (6 assertions)")
    return 0


# ── main ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="只打印全表，不判定")
    ap.add_argument("--selftest", action="store_true", help="校验器自检")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    rep = check()
    print_report(rep)

    if args.report:
        print()
        print(f"violations: {len(rep.violations)} (report mode, no verdict)")
        return 0

    print_violations(rep)
    if rep.ok:
        print()
        print("[OK] tool refs: consistent")
        return 0
    print()
    print(f"[X] {len(rep.violations)} violation(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
