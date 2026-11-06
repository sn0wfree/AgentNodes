# -*- coding: utf-8 -*-
"""check_capability_requires — capability requires 的 AST 双向差集门禁。

**为什么存在**：W1 给每个业务 kind 加了 ``requires`` 声明（它运行时依赖的其他
kind），装配期缺依赖则整组不注册。但声明是**人手写的**：

- 写漏了（欠声明）→ 把「注册即失败」的幽灵工具放回来
- 写多了（过声明）→ 制造「检查永远绿」的假门禁

本脚本把声明钉到代码上，只用 AST，不 import 目标包：

    actual   = 实现模块集内所有 resolve("字面量") 的字符串实参
    declared = CAPABILITIES 表里声明的 requires

    actual - declared 非空  → 欠声明 FAIL
    declared - actual 非空  → 过声明 FAIL

**CAPABILITIES 的两种值形态**（两种都支持）：

    "kind": _factory                              → declared = empty
    "kind": (_factory, frozenset({"a.b"}))        → declared = {a.b}

**实现模块集**（"factory 执行期闭包"的具体化）：

    factory 定义所在模块
      + 该模块顶层的 agentnodes.apps.* import
      + factory 函数体内的 agentnodes.apps.* import（本仓库的工厂都是惰性 import）
      + 沿这些模块的 import 传递收集的全部 agentnodes.apps.* 模块

只追 ``agentnodes.apps`` 内——kernel / foundation 不注册 kind，不是本表的依赖域。
之所以要追传递闭包：工厂 body 往往只有 ``import + 构造``（``_tool_goal`` 就是），
真正的 resolve 藏在被 import 的工具模块函数里；只扫 factory body 会对全部真实
案例漏报（评审对红队原案的修正）。

**动态 kind**：``resolve(f"tool.{x}")`` 这类静态不可求值。已知的动态注册前缀
（``EXEMPT_DYNAMIC_PREFIXES``，目前只有 evolution.*，它在 kernel 侧注册、不进
CAPABILITIES 表）放行；其余非字面量调用一律违规——"resolve 的 kind 必须是字符串
字面量"是拍板包 C 的明文规范，否则本脚本会静默漏报。

**三种模式**：

1. 默认：发现即 exit 1，逐条打印违规 + ``file:line`` 证据
2. ``--bootstrap``：不判定，输出每个 kind 的 requires 全表 + 证据 +
   可直接粘贴回 capabilities.py 的 python 片段（**片段取自 AST 实算值**）
3. ``--selftest``：临时目录构造合成 mini 包（欠声明 / 过声明 / 动态 kind
   三例，外加传递闭包与裸工厂形态），断言检测命中

用法：
    python scripts/arch/check_capability_requires.py              # 判定
    python scripts/arch/check_capability_requires.py --bootstrap  # 生成声明全表
    python scripts/arch/check_capability_requires.py --selftest   # 校验器自检

命名规范见 scripts/README.md（arch/ = check_*，发现即失败）。
"""
from __future__ import annotations

import argparse
import ast
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PKG = ROOT / "src" / "agentnodes"
CAP_FILE = PKG / "apps" / "capabilities.py"

#: resolve 的宿主模块（registry 只作兼容，当前没有模块级 resolve）。
CAP_MODULES = frozenset({
    "agentnodes.kernel.agent.capability",
    "agentnodes.kernel.agent.capability.registry",
})

APPS_PREFIX = "agentnodes.apps"

#: 静态可识别的动态 kind 前缀（f-string / 拼接的字面量前缀命中即放行）。
#: evolution.* 由 kernel/evolution 内部动态注册，不进 CAPABILITIES 表，
#: 因此不在本校验器的判定域内。
EXEMPT_DYNAMIC_PREFIXES = ("evolution.",)

_COLLECTION_CTORS = frozenset({"frozenset", "set", "list", "tuple"})


# ── 基础设施 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Pkg:
    """一次分析所需的三个路径锚点（selftest 用临时目录替换）。"""

    root: Path
    pkg: Path
    caps: Path

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()


@dataclass
class KindReport:
    kind: str
    factory: str
    key_line: int
    req_line: int
    declared: set[str]
    actual: dict[str, list[str]]
    closure: list[str] = field(default_factory=list)

    @property
    def under(self) -> list[str]:
        return sorted(set(self.actual) - self.declared)

    @property
    def over(self) -> list[str]:
        return sorted(self.declared - set(self.actual))


def _literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - 兜底，不让输出层拖垮门禁
        return "<expr>"


def _is_type_checking(test: ast.AST) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


# ── 模块名 / import 解析 ─────────────────────────────────────────────


def _path_module(ctx: Pkg, path: Path) -> tuple[str, bool] | None:
    """源文件 → (绝对模块名, 是否包)。不在包根下返回 None。"""
    try:
        rel = path.relative_to(ctx.pkg).as_posix()
    except ValueError:
        return None
    if rel == "__init__.py":
        return "agentnodes", True
    if rel.endswith("/__init__.py"):
        rel = rel[: -len("/__init__.py")]
        return "agentnodes." + rel.replace("/", "."), True
    if rel.endswith(".py"):
        rel = rel[:-3]
        return "agentnodes." + rel.replace("/", "."), False
    return None


def _abs_module(own: str, is_pkg: bool, level: int, module: str | None) -> str:
    """PEP 328 相对导入 → 绝对模块名（level=0 原样返回）。"""
    if level == 0:
        return module or ""
    parts = own.split(".") if is_pkg else own.split(".")[:-1]
    if level > 1:
        cut = level - 1
        if cut > len(parts):
            return ""
        parts = parts[: len(parts) - cut]
    if module:
        parts = parts + module.split(".")
    return ".".join(parts)


def _import_mods(own: str, is_pkg: bool, node: ast.AST) -> set[str]:
    """一条 import 语句可能的绝对模块名。

    ``from pkg import name`` 同时产出 ``pkg`` 与 ``pkg.name`` 两个候选——
    后者命中文件说明 name 是子模块，否则只是包内属性，由调用方查文件过滤。
    """
    if isinstance(node, ast.Import):
        return {a.name for a in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()
    base = _abs_module(own, is_pkg, node.level, node.module)
    if not base:
        return set()
    mods = {base}
    for a in node.names:
        if a.name != "*":
            mods.add(f"{base}.{a.name}")
    return mods


def _iter_module_imports(stmts: list[ast.stmt]):
    """顶层语句里的 import（递归进 if / try，跳过 TYPE_CHECKING 分支）。"""
    for node in stmts:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, ast.If):
            if _is_type_checking(node.test):
                continue
            yield from _iter_module_imports(node.body)
            yield from _iter_module_imports(node.orelse)
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            yield from _iter_module_imports(node.body)
            for handler in node.handlers:
                yield from _iter_module_imports(handler.body)
            yield from _iter_module_imports(node.orelse)
            yield from _iter_module_imports(node.finalbody)


def _module_file(ctx: Pkg, modname: str) -> Path | None:
    """绝对模块名 → 源文件（.py 或包 __init__.py）。"""
    if not modname.startswith("agentnodes."):
        return None
    rel = modname[len("agentnodes."):].replace(".", "/")
    for cand in (ctx.pkg / (rel + ".py"), ctx.pkg / rel / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _is_apps_mod(modname: str) -> bool:
    return modname == APPS_PREFIX or modname.startswith(APPS_PREFIX + ".")


def _is_apps_file(ctx: Pkg, path: Path) -> bool:
    try:
        parts = path.relative_to(ctx.pkg).parts
    except ValueError:
        return False
    return bool(parts) and parts[0] == "apps"


def _module_imports(ctx: Pkg, path: Path, *, whole_file: bool = False) -> set[str]:
    """该文件 import 的绝对模块名集合。

    默认只取**模块顶层** import（``whole_file=False``）——`capabilities.py`
    必须按顶层看，否则会把 6 个工厂函数体内的 import 全算成它的闭包，
    每个 kind 都拿到全部工具模块（闭包污染）。

    ``whole_file=True`` 时连函数体内的惰性 import 一并收集（用于工具模块：
    它们可能把 apps 内 import 藏在函数里，那也是 factory 执行期的可达路径）。
    """
    mod = _path_module(ctx, path)
    if mod is None:
        return set()
    own, is_pkg = mod
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    nodes = (
        (n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
        if whole_file
        else _iter_module_imports(tree.body)
    )
    out: set[str] = set()
    for node in nodes:
        out |= _import_mods(own, is_pkg, node)
    return out


def _closure(ctx: Pkg, seeds: set[Path]) -> set[Path]:
    """factory 起点 + apps 内 import 传递闭包的全部源文件。"""
    seen: set[Path] = set()
    queue = list(seeds)
    while queue:
        cur = queue.pop()
        if cur in seen:
            continue
        # 非 apps 的种子只作为起点保留，不继续追（kernel / foundation 不在域内）
        if cur not in seeds and not _is_apps_file(ctx, cur):
            continue
        seen.add(cur)
        # capabilities.py 只看顶层（工厂体由 _factory_seeds 逐 kind 收窄）
        whole_file = cur != ctx.caps
        for modname in _module_imports(ctx, cur, whole_file=whole_file):
            if not _is_apps_mod(modname):
                continue
            target = _module_file(ctx, modname)
            if target is not None and target not in seen:
                queue.append(target)
    return seen


# ── CAPABILITIES 表解析 ─────────────────────────────────────────────


def _declared_from(
    node: ast.AST, kind: str, caps_rel: str, problems: list[str]
) -> set[str]:
    """从 requires 节点取字符串字面量集合（frozenset({...}) / {...} / [...]）。"""
    out: set[str] = set()

    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id in _COLLECTION_CTORS):
            problems.append(
                f"{caps_rel}:{node.lineno}: {kind!r} requires must be a literal "
                f"collection, got {_unparse(node)}"
            )
            return out
        if not node.args:
            return out
        if len(node.args) != 1 or node.keywords:
            problems.append(
                f"{caps_rel}:{node.lineno}: {kind!r} requires constructor must take "
                "exactly one positional argument"
            )
            return out
        node = node.args[0]

    if isinstance(node, ast.Dict) and not node.keys:
        return out  # frozenset({}) —— 空集惯用写法
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                problems.append(
                    f"{caps_rel}:{elt.lineno}: {kind!r} requires must not use * unpacking"
                )
                continue
            value = _literal_str(elt)
            if value is None:
                problems.append(
                    f"{caps_rel}:{elt.lineno}: {kind!r} requires element must be a "
                    f"string literal, got {_unparse(elt)}"
                )
            else:
                out.add(value)
        return out

    problems.append(
        f"{caps_rel}:{node.lineno}: {kind!r} requires must be a literal collection "
        f"(e.g. frozenset({{...}})), got {_unparse(node)}"
    )
    return out


def parse_capabilities(ctx: Pkg) -> tuple[list[KindReport], list[str]]:
    """解析 CAPABILITIES 表 → KindReport 骨架（actual/closure 尚未填充）。"""
    caps_rel = ctx.rel(ctx.caps)
    problems: list[str] = []
    try:
        tree = ast.parse(ctx.caps.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return [], [f"{caps_rel}: cannot parse capabilities file: {exc}"]

    table: ast.AST | None = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "CAPABILITIES" for t in stmt.targets):
                table = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == "CAPABILITIES":
                table = stmt.value
    if not isinstance(table, ast.Dict):
        return [], [f"{caps_rel}: module-level CAPABILITIES dict literal not found"]

    reports: list[KindReport] = []
    seen: set[str] = set()
    for key_node, value_node in zip(table.keys, table.values):
        if key_node is None:  # {**mapping} —— 键无法静态枚举
            problems.append(f"{caps_rel}: CAPABILITIES must not use ** unpacking")
            continue
        kind = _literal_str(key_node)
        if kind is None:
            problems.append(
                f"{caps_rel}:{key_node.lineno}: CAPABILITIES key must be a string "
                f"literal, got {_unparse(key_node)}"
            )
            continue
        if kind in seen:
            problems.append(f"{caps_rel}:{key_node.lineno}: duplicate kind {kind!r}")
        seen.add(kind)

        req_line = key_node.lineno
        if isinstance(value_node, (ast.Tuple, ast.List)):
            if len(value_node.elts) != 2:
                problems.append(
                    f"{caps_rel}:{value_node.lineno}: {kind!r} value must be "
                    "`factory` or `(factory, requires)`, got "
                    f"{len(value_node.elts)} elements"
                )
                continue
            factory_node, req_node = value_node.elts
            req_line = getattr(req_node, "lineno", key_node.lineno)
        else:
            factory_node, req_node = value_node, None

        if not isinstance(factory_node, ast.Name):
            problems.append(
                f"{caps_rel}:{factory_node.lineno}: factory for {kind!r} must be a "
                f"bare name, got {_unparse(factory_node)}"
            )
            continue

        declared = (
            _declared_from(req_node, kind, caps_rel, problems) if req_node is not None else set()
        )
        reports.append(
            KindReport(
                kind=kind,
                factory=factory_node.id,
                key_line=key_node.lineno,
                req_line=req_line,
                declared=declared,
                actual={},
            )
        )
    return reports, problems


# ── 实现模块集起点 ─────────────────────────────────────────────────


def _local_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return stmt
    return None


def _imported_binding_file(
    ctx: Pkg, tree: ast.Module, name: str
) -> Path | None:
    """``from <apps 模块> import <name> [as name]`` 形式的 factory 绑定 → 源文件。"""
    mod = _path_module(ctx, ctx.caps)
    own, is_pkg = mod if mod is not None else ("agentnodes.apps.capabilities", False)
    for node in _iter_module_imports(tree.body):
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _abs_module(own, is_pkg, node.level, node.module)
        if not _is_apps_mod(base):
            continue
        for alias in node.names:
            if (alias.asname or alias.name) == name:
                target = _module_file(ctx, f"{base}.{alias.name}")
                if target is not None:
                    return target
    return None


def _factory_seeds(
    ctx: Pkg, report: KindReport, problems: list[str], caps_tree: ast.Module | None
) -> set[Path]:
    """factory 所在模块 + factory 函数体内的 apps import 源文件（闭包真起点）。"""
    seeds: set[Path] = {ctx.caps}
    if caps_tree is None:
        return seeds

    mod = _path_module(ctx, ctx.caps)
    own, is_pkg = mod if mod is not None else ("agentnodes.apps.capabilities", False)

    fn_node = _local_function(caps_tree, report.factory)
    if fn_node is None:
        # factory 定义在别的 apps 模块（从本文件 import 进来的形态）
        target = _imported_binding_file(ctx, caps_tree, report.factory)
        if target is None:
            problems.append(
                f"{ctx.rel(ctx.caps)}:{report.key_line}: factory {report.factory!r} "
                "is neither defined in nor app-imported by capabilities.py"
            )
            return seeds
        seeds.discard(ctx.caps)
        seeds.add(target)
        try:
            other_tree = ast.parse(target.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return seeds
        fn_node = _local_function(other_tree, report.factory)
        if fn_node is None:
            return seeds
        own, is_pkg = _path_module(ctx, target) or (own, is_pkg)

    for node in ast.walk(fn_node):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for modname in _import_mods(own, is_pkg, node):
                if not _is_apps_mod(modname):
                    continue
                imported = _module_file(ctx, modname)
                if imported is not None:
                    seeds.add(imported)
    return seeds


# ── resolve 调用提取 ────────────────────────────────────────────────


def _resolve_refs(tree: ast.Module, own: str, is_pkg: bool) -> tuple[set[str], set[str]]:
    """本文件里 capability.resolve 的本地函数名与模块别名。"""
    funcs: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = _abs_module(own, is_pkg, node.level, node.module)
            if base not in CAP_MODULES:
                continue
            for alias in node.names:
                if alias.name == "resolve":
                    funcs.add(alias.asname or "resolve")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in CAP_MODULES and alias.asname:
                    modules.add(alias.asname)
    return funcs, modules


def _is_resolve_call(func: ast.AST, funcs: set[str], modules: set[str]) -> bool:
    if isinstance(func, ast.Name):
        return func.id in funcs
    if isinstance(func, ast.Attribute) and func.attr == "resolve":
        return isinstance(func.value, ast.Name) and func.value.id in modules
    return False


def _dynamic_prefix(node: ast.AST) -> str:
    """非字面量实参的**静态前缀**（f-string 首段 / 字符串拼接最左常量）。"""
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return ""
    left: ast.AST = node
    if isinstance(left, ast.BinOp) and isinstance(left.op, ast.Add):
        while isinstance(left, ast.BinOp) and isinstance(left.op, ast.Add):
            left = left.left
        if isinstance(left, ast.Constant) and isinstance(left.value, str):
            return left.value
    return ""


def _extract_resolves(ctx: Pkg, path: Path) -> tuple[dict[str, list[str]], list[str]]:
    """→ ({kind: [file:line, ...]}, dynamic_violations)。"""
    actual: dict[str, list[str]] = {}
    dynamic: list[str] = []

    mod = _path_module(ctx, path)
    if mod is None:
        return actual, dynamic
    own, is_pkg = mod
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return actual, dynamic  # 语法错误由编译门禁负责

    funcs, modules = _resolve_refs(tree, own, is_pkg)
    if not funcs and not modules:
        return actual, dynamic

    rel = ctx.rel(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_resolve_call(node.func, funcs, modules):
            continue
        arg: ast.AST | None = node.args[0] if node.args else None
        if arg is None:
            for kw in node.keywords:
                if kw.arg in ("kind", "kind_name"):
                    arg = kw.value
                    break
        if arg is None:
            dynamic.append(
                f"{rel}:{node.lineno}: resolve() is missing the kind argument"
            )
            continue
        literal = _literal_str(arg)
        if literal is not None:
            actual.setdefault(literal, []).append(f"{rel}:{node.lineno}")
            continue
        prefix = _dynamic_prefix(arg)
        if prefix and any(prefix.startswith(p) for p in EXEMPT_DYNAMIC_PREFIXES):
            continue
        dynamic.append(
            f"{rel}:{node.lineno}: non-literal resolve({_unparse(arg)[:80]})"
        )
    return actual, dynamic


# ── 判定 ────────────────────────────────────────────────────────────


def evaluate(ctx: Pkg) -> tuple[list[KindReport], list[str], list[str]]:
    """→ (per_kind, parse_problems, dynamic_violations)。"""
    reports, problems = parse_capabilities(ctx)
    try:
        caps_tree = ast.parse(ctx.caps.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        caps_tree = None

    cache: dict[Path, tuple[dict[str, list[str]], list[str]]] = {}
    dynamic_all: set[str] = set()

    for report in reports:
        seeds = _factory_seeds(ctx, report, problems, caps_tree)
        files = _closure(ctx, seeds)
        actual: dict[str, list[str]] = {}
        for path in sorted(files):
            if path not in cache:
                cache[path] = _extract_resolves(ctx, path)
            file_actual, file_dynamic = cache[path]
            for kind, evidence in file_actual.items():
                actual.setdefault(kind, []).extend(evidence)
            dynamic_all.update(file_dynamic)
        report.actual = actual
        report.closure = sorted(ctx.rel(p) for p in files)

    return reports, problems, sorted(dynamic_all)


# ── 输出 ────────────────────────────────────────────────────────────


def _snippet(report: KindReport) -> str:
    """按 AST 实算值生成可粘贴片段（空集回退到裸工厂形态）。"""
    deps = sorted(report.actual)
    if not deps:
        return f'    "{report.kind}": {report.factory},'
    if len(deps) <= 2:
        body = "frozenset({" + ", ".join(f'"{d}"' for d in deps) + "})"
        return (
            f'    "{report.kind}": (\n'
            f"        {report.factory},\n"
            f"        {body},\n"
            f"    ),"
        )
    lines = [
        f'    "{report.kind}": (',
        f"        {report.factory},",
        "        frozenset({",
    ]
    lines.extend(f'            "{d}",' for d in deps)
    lines.extend(["        }),", "    ),"])
    return "\n".join(lines)


def run_check(ctx: Pkg) -> int:
    reports, problems, dynamic = evaluate(ctx)
    drift = [r for r in reports if r.under or r.over]

    if not problems and not drift and not dynamic:
        print(f"check_capability_requires: 0 problems ({len(reports)} kinds)")
        return 0

    print(
        f"check_capability_requires: FAIL "
        f"({len(reports)} kinds, {len(problems)} parse problems, "
        f"{len(drift)} kinds with drift, {len(dynamic)} dynamic resolve)",
        file=sys.stderr,
    )
    for problem in problems:
        print("  [PARSE] " + problem, file=sys.stderr)

    for report in drift:
        if report.under:
            print(
                f"  [UNDER] {report.kind}: {len(report.under)} kind(s) resolved by the "
                "implementation but not declared",
                file=sys.stderr,
            )
            for dep in report.under:
                print(f"      {dep}", file=sys.stderr)
                for evidence in report.actual[dep]:
                    print(f"          {evidence}", file=sys.stderr)
        if report.over:
            print(
                f"  [OVER] {report.kind}: {len(report.over)} kind(s) declared but "
                "never resolved",
                file=sys.stderr,
            )
            where = f"{ctx.rel(ctx.caps)}:{report.req_line}"
            for dep in report.over:
                print(f"      {dep}  (declared at {where})", file=sys.stderr)

    for item in dynamic:
        print("  [DYNAMIC] " + item, file=sys.stderr)

    print(
        "\n修复方向：跑 --bootstrap 看每个 kind 的实算 requires（带调用点证据），"
        "把声明补/删到与代码一致；动态 kind 请改用字符串字面量或登记豁免前缀。",
        file=sys.stderr,
    )
    return 1


def run_bootstrap(ctx: Pkg) -> int:
    reports, problems, dynamic = evaluate(ctx)
    caps_rel = ctx.rel(ctx.caps)

    print("=" * 78)
    print("requires bootstrap -- paste the snippets back into apps/capabilities.py")
    print("=" * 78)

    for report in reports:
        print()
        print(
            f"# {report.kind}  (factory={report.factory}, "
            f"declared at {caps_rel}:{report.key_line})"
        )
        print("#   closure:")
        for item in report.closure:
            print(f"#     {item}")
        if report.actual:
            print("#   requires (AST actual):")
            for dep in sorted(report.actual):
                print(f"#     {dep:<28} <- {', '.join(report.actual[dep])}")
        else:
            print("#   requires (AST actual): (none)")
        print(
            "#   declared: "
            + (", ".join(sorted(report.declared)) if report.declared else "(none)")
        )
        if report.under:
            print("#   drift: UNDER " + ", ".join(report.under))
        if report.over:
            print("#   drift: OVER  " + ", ".join(report.over))
        print("#   --- paste:")
        print(_snippet(report))

    if problems:
        print("\n# parse problems (fix the table before pasting):")
        for problem in problems:
            print(f"#   {problem}")
    if dynamic:
        print("\n# dynamic (non-literal) resolve calls -- literalize or exempt:")
        for item in dynamic:
            print(f"#   {item}")

    print()
    print(f"bootstrap: {len(reports)} kinds, {len(dynamic)} dynamic resolve call(s)")
    return 0


# ── 自检 ────────────────────────────────────────────────────────────


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def selftest() -> int:
    """临时目录构造合成 mini 包，验证欠声明 / 过声明 / 动态 kind 都命中。"""
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pkg = root / "src" / "agentnodes"
        apps = pkg / "apps"
        impl = apps / "impl"

        _write(pkg / "__init__.py", "")
        _write(apps / "__init__.py", "")
        _write(impl / "__init__.py", "")

        # 欠声明的实现：resolve("a.dep")
        _write(
            impl / "under.py",
            """
            from agentnodes.kernel.agent.capability import resolve


            def run(reg):
                return resolve("a.dep")
            """,
        )
        # 过声明的实现：没有任何 resolve
        _write(
            impl / "over.py",
            """
            def run(reg):
                return []
            """,
        )
        # 动态 kind：f-string 实参
        _write(
            impl / "dynamic.py",
            """
            from agentnodes.kernel.agent.capability import resolve as _r


            def run(reg):
                name = "x"
                return _r(f"dynamic.{name}")
            """,
        )
        # 传递闭包：mid -> leaf，resolve 只在 leaf 里
        _write(
            impl / "mid.py",
            """
            from .leaf import helper


            def run(reg):
                return helper(reg)
            """,
        )
        _write(
            impl / "leaf.py",
            """
            from agentnodes.kernel.agent.capability import resolve


            def helper(reg):
                return resolve("deep.dep")
            """,
        )
        _write(
            apps / "capabilities.py",
            """
            CAPABILITIES = {
                "kind.under": _f_under,
                "kind.over": (_f_over, frozenset({"a.dep"})),
                "kind.dynamic": (_f_dynamic, frozenset()),
                "kind.transitive": _f_transitive,
                "kind.clean": (_f_clean, frozenset({"a.dep"})),
            }


            def _f_under(reg):
                from agentnodes.apps.impl.under import run
                return run(reg)


            def _f_over(reg):
                from agentnodes.apps.impl.over import run
                return run(reg)


            def _f_dynamic(reg):
                from agentnodes.apps.impl.dynamic import run
                return run(reg)


            def _f_transitive(reg):
                from agentnodes.apps.impl.mid import run
                return run(reg)


            def _f_clean(reg):
                from agentnodes.apps.impl.under import run
                return run(reg)
            """,
        )

        ctx = Pkg(root=root, pkg=pkg, caps=apps / "capabilities.py")
        reports, problems, dynamic = evaluate(ctx)
        by_kind = {r.kind: r for r in reports}

        under = by_kind.get("kind.under")
        over = by_kind.get("kind.over")
        transitive = by_kind.get("kind.transitive")
        clean = by_kind.get("kind.clean")

        checks.append(("table parses without problems", not problems))
        checks.append(
            (
                "under-declaration detected (bare factory form)",
                under is not None and under.under == ["a.dep"] and not under.declared,
            )
        )
        checks.append(
            (
                "under-declaration carries file:line evidence",
                under is not None
                and any("impl/under.py:" in ev for ev in under.actual.get("a.dep", [])),
            )
        )
        checks.append(
            (
                "over-declaration detected",
                over is not None and over.over == ["a.dep"] and not over.under,
            )
        )
        checks.append(
            (
                "dynamic resolve detected",
                any("impl/dynamic.py:" in item and "non-literal resolve(" in item
                    for item in dynamic),
            )
        )
        checks.append(
            (
                "transitive apps closure followed",
                transitive is not None and transitive.under == ["deep.dep"],
            )
        )
        checks.append(
            (
                "clean kind has no complaint",
                clean is not None and not clean.under and not clean.over,
            )
        )
        checks.append(
            (
                "bootstrap snippet uses AST actual, not declared",
                under is not None
                and '"a.dep"' in _snippet(under)
                and over is not None
                and "frozenset" not in _snippet(over),
            )
        )

    ok = True
    for label, hit in checks:
        print(f"  {'PASS' if hit else 'FAIL'} {label}")
        ok = ok and hit
    print(f"selftest: {'PASS' if ok else 'FAIL'} ({sum(1 for _, h in checks if h)}/{len(checks)})")
    return 0 if ok else 1


# ── 入口 ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--bootstrap",
        action="store_true",
        help="不判定，输出每个 kind 的 requires 全表 + 证据 + 可粘贴片段",
    )
    mode.add_argument(
        "--selftest",
        action="store_true",
        help="临时目录构造合成 mini 包，验证三种检测都命中",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    ctx = Pkg(root=ROOT, pkg=PKG, caps=CAP_FILE)
    if not ctx.caps.is_file():
        print(f"check_capability_requires: capabilities file not found: {ctx.caps}",
              file=sys.stderr)
        return 1
    if args.bootstrap:
        return run_bootstrap(ctx)
    return run_check(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
