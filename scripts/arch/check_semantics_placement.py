# -*- coding: utf-8 -*-
"""check_semantics_placement — 语义轴「构件→物理层」落位门禁。

语义轴（G 基因组 / E 表达 / P 表型 / barrier 屏障 / graph 执行图）的落位
此前只活在框架散文（framework v1.1 §2.2 落位表）里，漂移无人发现。本门禁
把同一张表变成机器可读的 ``semantics_placement.yaml``，逐条断言每个语义
构件的主文件确实落在允许的**物理层**上。

物理层不在这里重算：一律 ``import layer_matrix``（唯一事实源），用
``module_name(path, PKG_ROOT)`` + ``layer_of(mod)`` 求层。所以本门禁报出的
层与 ``tests/test_layer_matrix.py``、``check_dangling_imports.py --layers``
必然一致——三处若各存一张表，迟早在「哪个数字是真的」上打架。

判定口径（与数据文件头部注释逐条对应）：
  · ``.py`` → 实算物理层，∉ allowed_layers 即 FAIL
  · ``allowed_layers == [external]`` → 进程外构件：断言路径**不在**
    src/agentnodes/ 包内；不断言存在（占位路径允许暂缺）
  · 非 ``.py``（如 .jsonl 语料）→ 非代码无层可言，只断言路径存在
  · 路径缺失且 allowed 不含 external → FAIL

全绿 exit 0；任何一条 FAIL exit 1（scripts/arch/check_* 惯例：发现即失败）。
FAIL 时的处理方向写在数据文件头部：先核对是落位表与仓库漂移还是代码搬错，
**不要改数据迁就代码**。

用法：
    python scripts/arch/check_semantics_placement.py             # 判定
    python scripts/arch/check_semantics_placement.py --verbose   # 附每文件明细
    python scripts/arch/check_semantics_placement.py --selftest  # 合成断言自校验

``--selftest`` 用临时合成仓库跑内置断言（一个已知 L3 构件临时标成
allowed=[L4] 必须报 FAIL，同一构件标 [L3] 必须放行），不读真实数据——判定
逻辑坏了不能让真实数据碰巧全绿掩盖过去。

命名规范见 scripts/README.md（arch/ = check_*，发现即失败）。
来源：框架 v1.1 §3.5-D（语义轴静态门禁，W1 批次流C）。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Windows 默认 GBK 控制台兜底（同 sync_architecture_diagram.py）：输出含中文，
# 编码错误不得在打完结果之后把退出码变成 1。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # 仓库根
PKG_ROOT = ROOT / "src" / "agentnodes"
DATA = HERE / "semantics_placement.yaml"

# 物理层唯一事实源；-I 隔离模式下脚本目录不在 sys.path，须显式插入。
sys.path.insert(0, str(HERE))
import layer_matrix  # noqa: E402

VALID_SEMANTIC = {"G", "E", "P", "barrier", "graph"}
VALID_LAYERS = {"L1", "L2", "L3", "L4"}
EXTERNAL = "external"


class Row:
    """单个文件的判定行。

    刻意不用 ``@dataclass``：``tests/test_scripts_arch.py`` 的 ``_load()``
    用 ``exec_module`` 但不注册 ``sys.modules``，dataclass 在那种加载方式下
    会因取不到 ``cls.__module__`` 而抛 AttributeError。
    """

    def __init__(self, ok: bool, label: str, detail: str) -> None:
        self.ok = ok
        self.label = label  # L1-L4 / 未登记 / 缺失 / 非py / external / ?
        self.detail = detail


class Result:
    """单条 entry 的判定结果。"""

    def __init__(self, name: str, semantic: str, allowed: list[str]) -> None:
        self.name = name
        self.semantic = semantic
        self.allowed = allowed
        self.rows: list[Row] = []
        self.problems: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.problems and all(r.ok for r in self.rows)

    @property
    def computed(self) -> str:
        """该 entry 实算层的汇总（多文件去重；无层可算时为 —）。"""
        labels = [r.label for r in self.rows if r.label in VALID_LAYERS]
        return "+".join(dict.fromkeys(labels)) if labels else "—"


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _validate_entry(entry: dict, res: Result) -> bool:
    """数据自检：表坏了同样是 FAIL，不得静默跳过条目。返回 files 是否可用。"""
    semantic = res.semantic
    allowed = res.allowed
    files = entry.get("files")

    if semantic not in VALID_SEMANTIC:
        res.problems.append(
            f"semantic={semantic!r} 不合法（允许 {sorted(VALID_SEMANTIC)}）")
    if not allowed:
        res.problems.append("allowed_layers 为空")
    unknown = [t for t in allowed if t not in VALID_LAYERS and t != EXTERNAL]
    if unknown:
        res.problems.append(f"allowed_layers 含未知层记号 {unknown}")
    if EXTERNAL in allowed and set(allowed) != {EXTERNAL}:
        res.problems.append("external 不得与 L1-L4 混用")
    if not isinstance(files, list) or not files:
        res.problems.append("files 为空或不是列表")
        return False
    return True


def _check_file(
    rel: str,
    allowed: list[str],
    external_only: bool,
    root: Path,
    pkg_root: Path,
) -> Row:
    """判定单个文件项。"""
    if not isinstance(rel, str) or not rel.strip():
        return Row(False, "?", f"非法文件项 {rel!r}")
    path = root / rel
    inside = _inside(path, pkg_root)

    # 进程外构件：只断言「不在包内」，不断言存在（占位路径允许暂缺）。
    if external_only:
        if inside:
            return Row(False, EXTERNAL,
                       f"{rel}（进程外构件却在 src/agentnodes/ 包内）")
        return Row(True, EXTERNAL, rel)

    if not path.exists():
        return Row(False, "缺失", f"{rel}（路径不存在）")
    if path.suffix != ".py":
        # 非代码无层可言：只断言路径存在。
        return Row(True, "非py", f"{rel}（存在性）")
    if not inside:
        return Row(False, "?",
                   f"{rel}（.py 不在 src/agentnodes/ 下，无法算物理层）")

    try:
        mod = layer_matrix.module_name(path.resolve(), pkg_root.resolve())
    except ValueError:  # 兜底：resolve 后仍越界（符号链接等）
        return Row(False, "?", f"{rel}（无法换算模块名）")
    layer = layer_matrix.layer_of(mod)
    if layer is None:
        return Row(False, "未登记", f"{rel}（{mod} 层未登记）")
    if layer not in allowed:
        return Row(False, layer, f"{rel}（实算 {layer} ∉ allowed {allowed}）")
    return Row(True, layer, rel)


def check_entry(
    entry: dict,
    root: Path = ROOT,
    pkg_root: Path | None = None,
) -> Result:
    """判定单条 entry。纯函数（不打印、不退出），供主流程与 selftest 共用。"""
    pkg_root = PKG_ROOT if pkg_root is None else pkg_root
    res = Result(
        name=str(entry.get("name") or "<unnamed>"),
        semantic=str(entry.get("semantic") or ""),
        allowed=list(entry.get("allowed_layers") or []),
    )
    if not _validate_entry(entry, res):
        return res

    external_only = set(res.allowed) == {EXTERNAL}
    for rel in entry["files"]:
        res.rows.append(_check_file(rel, res.allowed, external_only, root, pkg_root))
    return res


def load_entries(data: Path = DATA) -> list[dict]:
    import yaml

    raw = yaml.safe_load(data.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{data}: 顶层必须是 entry 列表，实得 {type(raw).__name__}")
    for i, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"{data}: 第 {i} 条不是映射")
    return raw


def run(data: Path = DATA, verbose: bool = False) -> int:
    if not data.exists():
        print(f"数据文件缺失：{data}", file=sys.stderr)
        return 1
    entries = load_entries(data)
    results = [check_entry(e) for e in entries]
    failed = [r for r in results if not r.ok]

    print(f"check_semantics_placement — {data.name}（{len(results)} 条）")
    print(f"  {'结果':4}  {'构件':32} {'语义':7} {'实算':6} allowed")
    print("  " + "-" * 78)
    for r in results:
        print(f"  {'OK' if r.ok else 'FAIL':4}  {r.name:32} "
              f"{r.semantic or '?':7} {r.computed:6} {r.allowed}")
        for row in r.rows:
            if verbose or not row.ok:
                print(f"        {'·' if row.ok else '!'} {row.label:7} {row.detail}")
        for p in r.problems:
            print(f"        ! 数据  {p}")
    print()

    if failed:
        print(f"FAIL：{len(failed)}/{len(results)} 条落位不符", file=sys.stderr)
        for r in failed:
            print(f"  - {r.name}（实算 {r.computed}，allowed {r.allowed}）",
                  file=sys.stderr)
        print("修复方向：先核对是落位表与仓库漂移还是代码搬错；"
              "不要改数据迁就代码。", file=sys.stderr)
        return 1
    print(f"check_semantics_placement: 0 问题（{len(results)} 条全绿）")
    return 0


# ── selftest ────────────────────────────────────────────────────────


def _selftest() -> int:
    """合成断言：判定逻辑自身必须能报 FAIL，也必须能放行合规项。"""
    checks: list[tuple[str, bool]] = []

    def expect(label: str, got: Result, want_ok: bool) -> None:
        checks.append((f"{label} → 期望 {'OK' if want_ok else 'FAIL'}",
                       got.ok is want_ok))

    with tempfile.TemporaryDirectory(prefix="semantics_selftest_") as td:
        root = Path(td)
        pkg_root = root / "src" / "agentnodes"
        (pkg_root / "kernel").mkdir(parents=True)
        # 已知 L3：layer_matrix.LAYER_MEMBERSHIP 中 agentnodes.kernel = L3。
        (pkg_root / "kernel" / "thing.py").write_text("", encoding="utf-8")
        (pkg_root / "evals").mkdir()
        (pkg_root / "evals" / "golden.jsonl").write_text("", encoding="utf-8")
        # 进程外占位目录（文件故意不存在：external 不断言存在）。
        (root / "scripts" / "arch").mkdir(parents=True)

        base = {
            "name": "synthetic",
            "semantic": "G",
            "files": ["src/agentnodes/kernel/thing.py"],
        }

        # 1. 核心断言：已知 L3 构件临时标成 allowed=[L4] → 必须 FAIL。
        expect("L3 构件标 allowed=[L4]",
               check_entry({**base, "allowed_layers": ["L4"]}, root, pkg_root),
               False)
        # 2. 同一构件 allowed=[L3] → 必须 OK（防「恒 FAIL」假阳性）。
        expect("L3 构件标 allowed=[L3]",
               check_entry({**base, "allowed_layers": ["L3"]}, root, pkg_root),
               True)
        # 3. external 却指向包内 → FAIL。
        expect("external 指向包内 .py",
               check_entry({**base, "allowed_layers": [EXTERNAL]}, root, pkg_root),
               False)
        # 4. external 指向包外不存在的占位路径 → OK。
        expect("external 指向包外占位路径",
               check_entry(
                   {**base, "files": ["scripts/arch/NOT_YET.md"],
                    "allowed_layers": [EXTERNAL]}, root, pkg_root), True)
        # 5. 非 py 路径缺失 → FAIL。
        expect("非 py 路径缺失",
               check_entry(
                   {**base, "files": ["src/agentnodes/evals/none.jsonl"],
                    "allowed_layers": ["L3"]}, root, pkg_root), False)
        # 6. 非 py 路径存在 → OK（无层可算，只断存在）。
        expect("非 py 路径存在",
               check_entry(
                   {**base, "files": ["src/agentnodes/evals/golden.jsonl"],
                    "allowed_layers": ["L3"]}, root, pkg_root), True)
        # 7. external 与 L1-L4 混用 → FAIL（数据模型禁止）。
        expect("external 与 L3 混用",
               check_entry({**base, "allowed_layers": ["L3", EXTERNAL]},
                           root, pkg_root), False)
        # 8. 缺失的 py（allowed 非 external）→ FAIL。
        expect("py 路径缺失",
               check_entry(
                   {**base, "files": ["src/agentnodes/kernel/ghost.py"],
                    "allowed_layers": ["L3"]}, root, pkg_root), False)
        # 9. 包外 .py 且 allowed 非 external → FAIL（不得抛异常）。
        (root / "scripts" / "arch" / "tool.py").write_text("", encoding="utf-8")
        expect("包外 .py 标 [L3]",
               check_entry(
                   {**base, "files": ["scripts/arch/tool.py"],
                    "allowed_layers": ["L3"]}, root, pkg_root), False)

    print("selftest — 合成断言（不读真实数据）")
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = sum(1 for _, ok in checks if ok)
    verdict = "PASS" if passed == len(checks) else "FAIL"
    print(f"selftest: {verdict} ({passed}/{len(checks)})")
    return 0 if verdict == "PASS" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="语义构件→物理层落位门禁")
    ap.add_argument("--selftest", action="store_true",
                    help="跑内置合成断言，不读真实数据")
    ap.add_argument("--verbose", action="store_true",
                    help="逐文件打印判定明细（含通过项）")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    return run(verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
