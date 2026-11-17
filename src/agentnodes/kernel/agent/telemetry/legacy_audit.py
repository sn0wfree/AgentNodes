"""legacy 引擎依赖审计（统一引擎 U6 准备 · R-015 条件 ④）。

**为什么需要**：删 legacy 引擎之前，必须先有**独立于 legacy 本身**的
观测手段——否则删完就没有办法回答「还有谁在依赖它」（批 E 的教训：
「删掉 X」和「删掉能发现 X 的机制」是两件事）。

**三条设计约束**：

1. **只读、零副作用**：不 import 被审计对象、不读环境变量、不写文件。
   纯静态分析（AST + 文本扫描）。
2. **能独立发现依赖**：用 AST 判定「真调用」，而不是 grep 文本——
   注释与文档提及会造成假阳性（实测 21 处提及里只有 1 处是真调用）。
3. **退出码语义**（与批 E-1 的 ``audit-legacy`` 一致）：
   0=干净 / 1=有残留 / 2=审计自身失败。

**审计范围**：

| 类别 | 判定 | 是否阻塞删除 |
|---|---|---|
| legacy 引擎真调用（AST）| ``_run_loop_core(...)`` 的 Call 节点 | **是** |
| legacy 引擎定义 | ``def _run_loop_core`` | **是**（删之前应移除）|
| 引擎选择开关读取 | ``AGENTNODES_ENGINE`` / ``AGENTNODES_SKELETON`` 的读取 | **是** |
| 测试显式依赖 legacy | ``engine="legacy"`` / ``engine='legacy'`` | **是**（遗产网等要一并处理）|
| 注释 / 文档提及 | 文本命中但非代码 | 否（仅计数）|
"""
from __future__ import annotations

import ast
import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import agentnodes as _sr

__all__ = [
    "LEGACY_ENGINE_SYMBOL",
    "LegacyAuditResult",
    "audit_legacy_engine",
    "audit_legacy_engine_text",
]

#: legacy 引擎的核心符号（被审计对象）。
LEGACY_ENGINE_SYMBOL = "_run_loop_core"

#: 引擎选择开关（读取层；U6 时按批 E 先例退役）。
ENGINE_SWITCH_ENV = ("AGENTNODES_ENGINE", "AGENTNODES_SKELETON")

#: 扫描根目录（相对仓库根）。
_SCAN_ROOTS = ("src", "tests")

_SKIP_DIRS = {"__pycache__", "attic", ".git", "node_modules", ".halt",
              "graphify-out"}


@dataclass
class _Finding:
    """一条发现。

    ``kind``:
    - ``call``        —— 真调用（AST 确认）
    - ``definition``  —— 函数定义
    - ``env_read``    —— 开关读取
    - ``test_legacy`` —— 测试显式走 legacy
    - ``mention``     —— 注释/文档提及（不阻塞）
    """

    kind: str
    path: str
    line: int
    text: str
    blocking: bool = True


@dataclass
class LegacyAuditResult:
    """审计结果（机器可读形态）。"""

    findings: list[_Finding] = field(default_factory=list)
    scanned_files: int = 0
    scanned_lines: int = 0
    error: str | None = None

    def by_kind(self, kind: str) -> list[_Finding]:
        return [f for f in self.findings if f.kind == kind]

    @property
    def blocking(self) -> list[_Finding]:
        """阻塞删除 legacy 的发现。"""
        return [f for f in self.findings if f.blocking]

    @property
    def mentions(self) -> list[_Finding]:
        """仅注释/文档提及（不阻塞）。"""
        return [f for f in self.findings if not f.blocking]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_files": self.scanned_files,
            "scanned_lines": self.scanned_lines,
            "blocking_count": len(self.blocking),
            "mention_count": len(self.mentions),
            "findings": [
                {
                    "kind": f.kind,
                    "path": f.path,
                    "line": f.line,
                    "text": f.text,
                    "blocking": f.blocking,
                }
                for f in self.findings
            ],
            "error": self.error,
        }


def _repo_root() -> Path:
    """Return the repository root relative to the installed agentnodes package."""
    return Path(_sr.__file__).resolve().parents[2]


def _is_loop_entry_call(node: ast.Call) -> bool:
    """判断这个 Call 是不是 loop 引擎的入口调用（``arun`` / ``run``）。

    用于收窄 ``engine=`` 关键字参数的审计范围——别的领域也有叫
    ``engine`` 的参数（fence 的 ``SupersedingEngine``、graph 的引擎等），
    与 loop 引擎无关（实测假阳性：``test_r1_fence_wiring.py``）。
    """
    fn = node.func
    name = getattr(fn, "attr", None) or getattr(fn, "id", None)
    return name in ("arun", "run")


def _code_line_numbers(source: str) -> set[int]:
    """返回「含真实代码 token」的行号集合。

    用 tokenize 精确区分：注释（COMMENT）与字符串字面量（STRING）不计入。
    这样 subprocess 模板里的 docstring 不会被误判成依赖，而模板里的
    **代码行**（模板字符串内部的 Python 代码）仍会被计入——那正是
    我们要抓的（``test_loop_engine_equivalence.py`` 的模板里就有
    ``engine="legacy"``）。

    tokenize 失败（语法错误）时退化为「全部行都算代码」——宁可多算
    mention（不阻塞），不可漏算。
    """
    import tokenize

    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            if tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT, tokenize.ENDMARKER):
                continue
            out.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set(range(1, source.count("\n") + 2))
    return out


def _code_lines_in_templates(source: str) -> set[int]:
    """**赋值给变量的多行字符串**（subprocess 模板）内部的代码行号。

    tokenize 把整个模板字符串当一个 STRING token——内部的 Python 代码
    全被排除，于是模板里的 ``engine="legacy"`` 抓不到。这里把每个
    **赋值右侧**的多行字符串 token 的内容**再 tokenize 一遍**，得到模板内
    真实的代码行号（映射回原文件的行号）。

    **只处理赋值右侧的字符串**：模块/类/函数的 docstring 是**裸表达式**
    位置的字符串，不是模板——否则 docstring 里提到 ``engine="legacy"``
    会被误判成依赖（实测 test_legacy_engine_net.py 的模块 docstring
    就有一处）。
    """
    import tokenize

    out: set[int] = set()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return out

    # 找出「赋值语句右侧的多行字符串」：`NAME = <多行字符串>`
    assign_lines: set[int] = set()
    state = 0   # 0=无 1=刚见 "=" 2=刚见 "=" + 目标名
    for tok in toks:
        if state == 0:
            if tok.type == tokenize.OP and tok.string == "=":
                state = 1
            continue
        if state == 1:
            if tok.type == tokenize.NAME:
                state = 2
            elif tok.type == tokenize.STRING and "\n" in tok.string:
                assign_lines.add(tok.start[0])
                state = 0
            else:
                state = 0
            continue
        # state == 2：等右侧的值
        if tok.type == tokenize.STRING and "\n" in tok.string:
            assign_lines.add(tok.start[0])
        state = 0

    for tok in toks:
        if tok.type != tokenize.STRING or "\n" not in tok.string:
            continue
        if tok.start[0] not in assign_lines:
            continue  # docstring / 裸字符串 —— 不当模板
        start_line = tok.start[0]
        body = tok.string
        # **先去 r/b 前缀，再去三引号定界**（顺序反了会漏：
        # ``r'''...'''`` 先按三引号匹配会失败，因为开头是 ``r'''``）
        if body[:1] in ("r", "R", "b", "B", "u", "U"):
            body = body[1:]
        for q in ('"""', "'''"):
            if body.startswith(q):
                body = body[len(q):]
                if body.endswith(q):
                    body = body[:-len(q)]
                break
        # **行号偏移**：模板字符串的首行（``_INNER = r'''`` 那一行）与
        # 模板内容的第 1 行**同一行**——所以模板内第 N 行对应原文件
        # ``start_line + N - 1``。
        offset = start_line - 1
        try:
            inner = list(tokenize.generate_tokens(io.StringIO(body).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            continue
        for it in inner:
            if it.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            if it.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                           tokenize.DEDENT, tokenize.ENDMARKER):
                continue
            out.add(offset + it.start[0])
    return out


def _iter_source_files(root: Path):
    """遍历待审计的 .py 文件。

    **跳过审计器自身**（``legacy_audit.py``）——否则它的常量与文档
    会成为自指的噪音（实测：审计器自身的 ``LEGACY_ENGINE_SYMBOL``
    与表格里的 ``engine="legacy"`` 都会被命中）。
    """
    self_name = "legacy_audit.py"
    for sub in _SCAN_ROOTS:
        base = root / sub
        if not base.is_dir():
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in _SKIP_DIRS]
            for f in sorted(fn):
                if f.endswith(".py") and f != self_name:
                    yield Path(dp) / f


def _is_comment_or_docstring_line(text: str) -> bool:
    """粗判：整行是注释或落在字符串里。

    docstring 内的行以 ``"`` 结尾或多行字符串续行——用「行内出现
    三引号或行首是引号」粗判。够用：宁可多算 mention（不阻塞），
    不可漏算（会误判为可删除）。
    """
    s = text.strip()
    if s.startswith("#"):
        return True
    # docstring / 多行字符串的续行：以引号开头或结尾
    if s.startswith(('"""', "'''", '"', "'")):
        return True
    if s.endswith(('"""', "'''", '"', "'")):
        return True
    return False


def _scan_file(path: Path, root: Path, result: LegacyAuditResult) -> None:
    rel = path.relative_to(root).as_posix()
    try:
        source = io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return
    lines = source.splitlines()
    result.scanned_files += 1
    result.scanned_lines += len(lines)
    in_test = rel.startswith("tests/")

    # ── AST 层：真调用 / 定义 ──
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None

    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None)
                if name == LEGACY_ENGINE_SYMBOL:
                    result.findings.append(_Finding(
                        kind="call", path=rel, line=node.lineno,
                        text=_snippet(lines, node.lineno),
                    ))
                # engine="legacy" —— 关键字参数（AST 判定，避开注释/docstring）
                for kw in node.keywords:
                    if kw.arg != "engine":
                        continue
                    # **只关心 loop 引擎入口**（arun / run）——
                    # 别的领域也有 `engine=` 参数（fence 的 SupersedingEngine
                    # 等），它们与 loop 引擎无关（实测 test_r1_fence_wiring
                    # 的假阳性）。
                    if not _is_loop_entry_call(node):
                        continue
                    if isinstance(kw.value, ast.Constant) and \
                            kw.value.value == "legacy":
                        result.findings.append(_Finding(
                            kind="test_legacy" if in_test else "call",
                            path=rel, line=kw.value.lineno,
                            text=_snippet(lines, kw.value.lineno),
                        ))
                    elif isinstance(kw.value, (ast.Name, ast.Call)):
                        # engine=<变量> —— **静态不可判定**（值来自
                        # 参数/字典/命令行）。这类必须人工确认，
                        # 列为需确认项（不阻塞，但必须看见）。
                        result.findings.append(_Finding(
                            kind="engine_var", path=rel, line=kw.value.lineno,
                            text=_snippet(lines, kw.value.lineno),
                            blocking=False,
                        ))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name == LEGACY_ENGINE_SYMBOL:
                result.findings.append(_Finding(
                    kind="definition", path=rel, line=node.lineno,
                    text=_snippet(lines, node.lineno),
                ))
            elif isinstance(node, ast.Attribute) and \
                    node.attr in ENGINE_SWITCH_ENV:
                result.findings.append(_Finding(
                    kind="env_read", path=rel, line=node.lineno,
                    text=_snippet(lines, node.lineno),
                ))

    # ── 文本层：引擎字面量 + 开关字符串 + 注释提及 ──
    #
    # **为什么还需要文本层**：subprocess 模板（``_INNER = r'''...'''``）
    # 里的代码是**字符串**，AST 看不到——但那里也可能有真实的
    # ``engine="legacy"`` 调用（实测 test_loop_engine_equivalence.py:91
    # 就在模板里）。
    #
    # **模板内代码怎么算「代码行」**：模板字符串整体是一个 STRING token，
    # tokenize 会把它的所有行都排除。所以对「多行字符串 token」做二次
    # 扫描——把它内部的文本再 tokenize 一遍，得到**模板内真实的代码行**。
    code_lines = _code_line_numbers(source)
    code_lines |= _code_lines_in_templates(source)

    seen_test_legacy: set[tuple[str, int]] = {
        (f.path, f.line) for f in result.findings if f.kind == "test_legacy"
    }
    seen_engine_var: set[tuple[str, int]] = {
        (f.path, f.line) for f in result.findings if f.kind == "engine_var"
    }
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        in_code = i in code_lines
        # engine="legacy" / engine='legacy'（真实代码行）
        if in_test and in_code and re.search(
            r"""engine\s*=\s*['"]legacy['"]""", line
        ):
            if (rel, i) not in seen_test_legacy:
                seen_test_legacy.add((rel, i))
                result.findings.append(_Finding(
                    kind="test_legacy", path=rel, line=i,
                    text=_snippet(lines, i),
                ))
            continue
        # engine=<变量>（真实代码行，静态不可判定）
        if in_test and in_code and re.search(
            r"""\bengine\s*=\s*engine\b""", line
        ):
            if (rel, i) not in seen_engine_var:
                seen_engine_var.add((rel, i))
                result.findings.append(_Finding(
                    kind="engine_var", path=rel, line=i,
                    text=_snippet(lines, i), blocking=False,
                ))
            continue
        # 开关名的字符串出现（settings 声明表 / 文档）
        for env in ENGINE_SWITCH_ENV:
            if env in line:
                # AST 已抓到的读取点不重复记
                already = any(
                    f.kind == "env_read" and f.path == rel and f.line == i
                    for f in result.findings
                )
                if already:
                    continue
                is_comment = not in_code
                result.findings.append(_Finding(
                    kind="mention" if is_comment else "env_read",
                    path=rel, line=i, text=_snippet(lines, i),
                    blocking=not is_comment,
                ))
                break
        # legacy 引擎符号的注释/文档提及
        if LEGACY_ENGINE_SYMBOL in line and not in_code:
            already = any(
                f.kind in ("call", "definition") and f.path == rel and f.line == i
                for f in result.findings
            )
            if not already:
                result.findings.append(_Finding(
                    kind="mention", path=rel, line=i,
                    text=_snippet(lines, i), blocking=False,
                ))


def _snippet(lines: list[str], lineno: int, width: int = 90) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()[:width]
    return ""


def audit_legacy_engine(root: Path | None = None) -> LegacyAuditResult:
    """审计 legacy 引擎依赖（只读）。

    :param root: 仓库根；默认按本文件位置推导。
    :returns: :class:`LegacyAuditResult`（含 findings / 计数）
    """
    result = LegacyAuditResult()
    try:
        base = Path(root) if root else _repo_root()
        # **容忍部分结构**：只要 src/ 或 tests/ 有一个即可审计。
        # （严格要 src/ 会让「只注入 tests/ 的最小仓库」无法审计——
        #   而注入法正是验证审计器有效性的主要手段，R-019。）
        if not (base / "src").is_dir() and not (base / "tests").is_dir():
            result.error = f"不是仓库根（缺 src/ 与 tests/）：{base}"
            return result
        for path in _iter_source_files(base):
            _scan_file(path, base, result)
    except Exception as exc:  # noqa: BLE001 —— 审计失败要能被 CI 看见
        result.error = str(exc)
    return result


def audit_legacy_engine_text(result: LegacyAuditResult | None = None) -> tuple[str, int]:
    """人读版审计报告。返回 (文本, 退出码)。

    退出码：0=干净 / 1=有阻塞残留 / 2=审计失败。
    """
    if result is None:
        result = audit_legacy_engine()
    if result.error:
        return f"审计失败: {result.error}", 2

    lines: list[str] = []
    lines.append("=== 1. legacy 引擎代码（运行时残留）===")
    calls = result.by_kind("call")
    defs = result.by_kind("definition")
    if not calls and not defs:
        lines.append("  （无）")
    else:
        for f in defs:
            lines.append(f"  [定义] {f.path}:{f.line}: {f.text}")
        for f in calls:
            lines.append(f"  [调用] {f.path}:{f.line}: {f.text}")

    lines.append("")
    lines.append("=== 2. 引擎选择开关 ===")
    envs = result.by_kind("env_read")
    if not envs:
        lines.append("  （无）")
    else:
        for f in envs:
            lines.append(f"  {f.path}:{f.line}: {f.text}")

    lines.append("")
    lines.append("=== 3. 测试显式依赖 legacy ===")
    tests = result.by_kind("test_legacy")
    if not tests:
        lines.append("  （无）")
    else:
        for f in tests:
            lines.append(f"  {f.path}:{f.line}: {f.text}")

    lines.append("")
    lines.append("=== 3b. engine=<变量>（静态不可判定，需人工确认）===")
    varf = result.by_kind("engine_var")
    if not varf:
        lines.append("  （无）")
    else:
        for f in varf:
            lines.append(f"  {f.path}:{f.line}: {f.text}")
        lines.append(
            "  ⚠ 这些调用把 engine 值来自参数/字典/命令行——静态不可判定。"
            "删除 legacy 前必须人工确认它们不会传 'legacy'。"
        )

    lines.append("")
    lines.append("=== 4. 注释 / 文档提及（不阻塞删除）===")
    mentions = result.mentions
    lines.append(f"  {len(mentions)} 处")
    for f in mentions[:10]:
        lines.append(f"    {f.path}:{f.line}: {f.text}")
    if len(mentions) > 10:
        lines.append(f"    ... 还有 {len(mentions) - 10} 处")

    lines.append("")
    lines.append(
        f"扫描 {result.scanned_files} 个文件 / {result.scanned_lines} 行；"
        f"阻塞项 {len(result.blocking)} 处"
    )
    if result.blocking:
        lines.append(
            "→ **尚不可删除 legacy**：先清掉上述阻塞项（调用 / 定义 / 开关 / 测试依赖）"
        )
    else:
        lines.append("→ **legacy 已无残留依赖**，可以进入删除流程")

    return "\n".join(lines), (1 if result.blocking else 0)
