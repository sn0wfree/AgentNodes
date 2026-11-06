# -*- coding: utf-8 -*-
"""split_golden_sets — 把 golden 任务集拆成 held-in / held-out 双集。

**为什么需要拆分**：单集双侧（同一批 golden 上比基线 vs 候选）允许对固定
验证集过拟合——每代挑对该集有利的改动，指标一路合规上升而泛化下降。
评审把它列为 NO-GO 缺口（`docs/design/framework-review-20261106.md` §7.4
C9 / 理念七）。双集里 held-out 是泛化性检验，必须**不在仓库内明文存在**。

**拆分规则（可复现、有依据、非随机）**：

    bucket = int.from_bytes(sha256(task_id)[:8], "big") % 100
    held-in  ⇔  bucket < 70        （≈70%）
    held-out ⇔  bucket >= 70       （≈30%）

规则只依赖 ``task_id``：与文件顺序、随机种子、进程、Python 版本无关。
同一份输入在任何机器上重跑都得到同一份拆分。冲突由 id 哈希裁决，
不由人挑——人挑就能把"难例"从 held-out 挪进 held-in。

**产物**（``--write`` 默认）：

| 路径 | 内容 | 是否进仓库 |
|---|---|---|
| ``src/agentnodes/evals/golden_tasks_heldin.jsonl`` | held-in 明文 | 是 |
| ``src/agentnodes/evals/golden_tasks.jsonl`` | 同 held-in（兼容旧路径） | 是 |
| ``src/agentnodes/evals/golden_sets_manifest.json`` | id + 行指纹 + 计数 | 是 |
| ``~/.agentnodes/eval_heldout/golden_tasks_heldout.jsonl`` | held-out 明文 | **否** |
| ``~/.agentnodes/eval_heldout/golden_tasks_union.jsonl`` | 并集母本（重跑源） | **否** |

**为什么仓库内只有 held-in**：agent 的 ``read`` 工具以工作区为根；
仓库里放 held-out 明文等于把考卷给它看。held-out 只落
``ensure_data_dir()/eval_heldout``（``AN_DATA_DIR`` 可覆盖，
``AN_GOLDEN_HELDOUT_PATH`` 可整路径覆盖）。

**为什么并集母本在仓库外**：拆分完成后仓库内的 ``golden_tasks.jsonl``
只剩 held-in。若以它为源重跑拆分，held-out 会被永久截掉（重跑会"成功"
并静默丢 23 条任务）。母本住外部目录，重跑就从母本重新分桶。

用法：

    python scripts/arch/split_golden_sets.py            # 写产物（含外部导出）
    python scripts/arch/split_golden_sets.py --check    # 只比对，不写；漂移即 exit 1
    python scripts/arch/split_golden_sets.py --verify   # 断言不相交/并集==原集/比例
    python scripts/arch/split_golden_sets.py --verify --write  # 先写后验

退出码：0 通过 · 1 断言失败或与磁盘漂移 · 2 用法/输入错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows GBK 控制台兜底（同 check_semantics_placement.py）：本脚本输出含
# 中文与路径；编码错误不得在打完结果之后把退出码变成 1。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent          # scripts/arch
ROOT = HERE.parents[1]                          # 仓库根
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agentnodes.evals import golden_sets as gs  # noqa: E402

#: 比例断言容差（约 70/30）。桶化抽样有波动，用区间而不是精确值——
#: 精确断言会让「加一条任务」变成必须改脚本的红灯。
HELDIN_RATIO_RANGE = (0.60, 0.80)
HELDOUT_RATIO_RANGE = (0.20, 0.40)


class Failure(Exception):
    """断言失败（区别于用法错误，退出码 1）。"""


# ── 校验 ─────────────────────────────────────────────────────────────


def check_partition(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """纯函数：把源行拆成两集并做全部结构断言。

    断言（对应任务书「验证」§1）：
    1. 两集**不相交**（按 id）
    2. 并集 == 原集（按 id 与按内容指纹两种口径）
    3. 无重复 id（源本身坏了要先报）
    4. 条数比例落在区间内
    5. 分桶与 ``is_heldin`` 逐条一致（防止脚本用了第二套规则）

    返回 ``(heldin_rows, heldout_rows)``。任意一条不成立抛 ``Failure``。
    """
    if not rows:
        raise Failure("source set is empty")

    ids = [str(r.get("id", "")) for r in rows]
    if any(not i for i in ids):
        raise Failure("source has rows without an id")
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise Failure(f"source has duplicate ids: {dupes}")

    heldin, heldout = gs.split_rows(rows)
    hin_ids = {str(r["id"]) for r in heldin}
    hout_ids = {str(r["id"]) for r in heldout}

    overlap = sorted(hin_ids & hout_ids)
    if overlap:
        raise Failure(f"held-in / held-out overlap: {overlap}")

    union = hin_ids | hout_ids
    if union != set(ids):
        missing = sorted(set(ids) - union)
        extra = sorted(union - set(ids))
        raise Failure(f"union != source (missing={missing}, extra={extra})")

    if gs.rows_digest(list(heldin) + list(heldout)) != gs.rows_digest(rows):
        raise Failure("union content fingerprint != source content fingerprint")

    if len(heldin) + len(heldout) != len(rows):
        raise Failure(
            f"count mismatch: {len(heldin)} + {len(heldout)} != {len(rows)}"
        )

    total = len(rows)
    hin_ratio = len(heldin) / total
    hout_ratio = len(heldout) / total
    if not (HELDIN_RATIO_RANGE[0] <= hin_ratio <= HELDIN_RATIO_RANGE[1]):
        raise Failure(
            f"held-in ratio {hin_ratio:.1%} outside {HELDIN_RATIO_RANGE} "
            f"({len(heldin)}/{total})"
        )
    if not (HELDOUT_RATIO_RANGE[0] <= hout_ratio <= HELDOUT_RATIO_RANGE[1]):
        hint = ""
        if not heldout:
            # 最常见的误用：拿**已拆分**的 held-in 副本当源重跑。此时 held-out
            # 恒为空，"拆分成功"会静默丢 30% 任务——必须显式拒绝并指路。
            hint = (
                " — source looks like an already-split held-in file (0 held-out "
                "tasks). Restore the 70-task union master from git, e.g. "
                "`git show <rev>:src/agentnodes/evals/golden_tasks.jsonl > "
                f"{gs.union_master_path()}`, then rerun"
            )
        raise Failure(
            f"held-out ratio {hout_ratio:.1%} outside {HELDOUT_RATIO_RANGE} "
            f"({len(heldout)}/{total}){hint}"
        )

    # 规则一致性：脚本不得使用与库不同的分桶口径
    for row in heldin:
        if not gs.is_heldin(str(row["id"])):
            raise Failure(f"{row['id']} in held-in but bucket says otherwise")
    for row in heldout:
        if gs.is_heldin(str(row["id"])):
            raise Failure(f"{row['id']} in held-out but bucket says otherwise")

    return heldin, heldout


def check_lines(
    lines: list[str],
) -> tuple[list[str], list[str]]:
    """``check_partition`` 的行文本版本（产物逐字节保真的前提）。"""
    rows = [json.loads(line) for line in lines]
    heldin, heldout = check_partition(rows)
    hin_ids = {str(r["id"]) for r in heldin}
    return (
        [line for line in lines if str(json.loads(line)["id"]) in hin_ids],
        [line for line in lines if str(json.loads(line)["id"]) not in hin_ids],
    )


def check_no_leak(heldout_rows: list[dict], heldin_rows: list[dict]) -> None:
    """断言 held-out 明文**不在**仓库内。

    两条口径：
    1. 仓库内任何 ``*heldout*.jsonl`` 文件（除产物目录外的意外副本）
    2. held-in 集里不得出现 held-out 的 id
    """
    hin_ids = {str(r["id"]) for r in heldin_rows}
    leaked = sorted({str(r["id"]) for r in heldout_rows} & hin_ids)
    if leaked:
        raise Failure(f"held-out ids found in held-in set: {leaked}")

    repo_heldout = sorted((ROOT / "src").rglob("*heldout*.jsonl"))
    if repo_heldout:
        raise Failure(
            "held-out plaintext must not live inside the repo; found: "
            + ", ".join(str(p.relative_to(ROOT)) for p in repo_heldout)
        )


def build_manifest(
    heldin_rows: list[dict], heldout_rows: list[dict], source: Path
) -> dict:
    """仓库内清单：**只放计数 + 指纹**，不放 held-out 明文或 id 明文。"""
    return {
        "split_version": gs.SPLIT_VERSION,
        "rule": (
            "sha256(task_id)[:8] as big-endian uint % "
            f"{gs.BUCKET_MODULUS} < {gs.HELDIN_BUCKET_MAX} -> held-in"
        ),
        "source": source.name,
        "total": len(heldin_rows) + len(heldout_rows),
        "heldin": {
            "count": len(heldin_rows),
            "file": gs.HELDIN_FILENAME,
            "ids_digest": gs.ids_digest(str(r["id"]) for r in heldin_rows),
            "rows_digest": gs.rows_digest(heldin_rows),
        },
        "heldout": {
            # 只记计数与聚合指纹：拿到仓库的人无从还原 held-out 内容
            "count": len(heldout_rows),
            "file": gs.HELDOUT_FILENAME,
            "location": "outside-repo (ensure_data_dir()/eval_heldout)",
            "ids_digest": gs.ids_digest(str(r["id"]) for r in heldout_rows),
            "rows_digest": gs.rows_digest(heldout_rows),
        },
        "union_digest": gs.rows_digest(heldin_rows + heldout_rows),
    }


# ── 产物写入 ─────────────────────────────────────────────────────────


def write_outputs(
    heldin_lines: list[str],
    heldout_lines: list[str],
    source: Path,
    *,
    revise_legacy: bool = True,
    write_external: bool = True,
) -> dict:
    """写出全部产物，返回 manifest（调用方打印用）。

    产物一律是**源文件行的子集**（逐字节保真）：``git diff`` 只显示删除，
    不出现全文件重排。

    :param revise_legacy: 是否把仓库内 ``golden_tasks.jsonl`` 改成 held-in
        （保持旧代码路径可用）。``--no-legacy`` 可关闭。
    :param write_external: 是否写外部 held-out + 并集母本。测试用 False
        避免污染真实数据目录。
    """
    gs.heldin_path().write_text(gs.render_lines(heldin_lines), encoding="utf-8")

    if revise_legacy:
        gs.compat_golden_path().write_text(
            gs.render_lines(heldin_lines), encoding="utf-8"
        )

    manifest = build_manifest(
        [json.loads(x) for x in heldin_lines],
        [json.loads(x) for x in heldout_lines],
        source,
    )
    gs.manifest_path().write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if write_external:
        target_dir = gs.heldout_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        gs.heldout_path().write_text(
            gs.render_lines(heldout_lines), encoding="utf-8"
        )
        # 母本用**原始行**（源顺序），保证重跑产物与首次逐哈希一致
        gs.union_master_path().write_text(
            gs.render_lines(_source_order(heldin_lines, heldout_lines, source)),
            encoding="utf-8",
        )
    return manifest


def _source_order(
    heldin_lines: list[str], heldout_lines: list[str], source: Path
) -> list[str]:
    """按**源文件出现顺序**重排并集（母本必须是原文件的行序重排）。

    不能直接 ``heldin + heldout``：那会把两份拼接起来，母本行序与源不同。
    行序本身不影响分桶（哈希只看 id），但母本要能当"原集"用——顺序漂了
    会让下游 ``select_tasks`` 的稳定排序之外的人工比对变得难以复核。
    """
    try:
        positions = {
            line: i for i, line in enumerate(
                gs.read_lines(source)
            )
        }
    except Exception:  # noqa: BLE001
        return heldin_lines + heldout_lines
    combined = heldin_lines + heldout_lines
    return sorted(combined, key=lambda line: positions.get(line, len(positions)))


def compare_disk(heldin_lines: list[str], heldout_lines: list[str]) -> list[str]:
    """把内存结果与磁盘现有产物比对，返回漂移描述（空 = 一致）。"""
    problems: list[str] = []
    expected = gs.rows_digest([json.loads(x) for x in heldin_lines])
    for label, path in (
        ("held-in", gs.heldin_path()),
        ("legacy golden_tasks.jsonl", gs.compat_golden_path()),
    ):
        if not path.exists():
            problems.append(f"{label} missing: {path}")
            continue
        try:
            disk = gs.read_lines(path)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label} unreadable: {exc}")
            continue
        if gs.rows_digest([json.loads(x) for x in disk]) != expected:
            problems.append(
                f"{label} differs from source split "
                f"(disk n={len(disk)}, expected n={len(heldin_lines)})"
            )
    return problems


# ── main ─────────────────────────────────────────────────────────────


def _print_summary(
    heldin_lines: list[str], heldout_lines: list[str], source: Path
) -> None:
    total = len(heldin_lines) + len(heldout_lines)
    print(f"source: {source}")
    print(f"  total   {total}")
    print(
        f"  held-in {len(heldin_lines):3d} ({len(heldin_lines)/total:.1%})  "
        f"-> {gs.heldin_path()}"
    )
    print(
        f"  held-out{len(heldout_lines):3d} ({len(heldout_lines)/total:.1%})  "
        f"-> {gs.heldout_path()}"
    )
    print(f"  union master -> {gs.union_master_path()}")
    print(f"  manifest     -> {gs.manifest_path()}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Split golden tasks into held-in / held-out sets",
    )
    ap.add_argument("--source", default=None,
                    help="source jsonl (default: external union master, "
                         "else in-repo golden_tasks.jsonl)")
    ap.add_argument("--write", action="store_true",
                    help="write products (held-in, legacy, manifest, external)")
    ap.add_argument("--check", action="store_true",
                    help="compare with files on disk; exit 1 on drift (no write)")
    ap.add_argument("--verify", action="store_true",
                    help="assert disjoint / union==source / ratio (read-only)")
    ap.add_argument("--no-legacy", action="store_true",
                    help="do not rewrite in-repo golden_tasks.jsonl")
    ap.add_argument("--no-external", action="store_true",
                    help="do not write the external held-out copy (tests only)")
    args = ap.parse_args(argv)

    source = gs.find_source_path(args.source)
    if source is None:
        print(
            "no source found: pass --source, or create the external union "
            f"master at {gs.union_master_path()}",
            file=sys.stderr,
        )
        return 2

    try:
        lines = gs.read_lines(source)
        heldin_lines, heldout_lines = check_lines(lines)
        check_no_leak(
            [json.loads(x) for x in heldout_lines],
            [json.loads(x) for x in heldin_lines],
        )
    except Failure as exc:
        print(f"split assertion failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 —— 解析失败属输入错误
        print(f"cannot read source {source}: {exc}", file=sys.stderr)
        return 2

    # --write 与 --check 的组合：先查漂移（让人知道自己在改什么），再写。
    # 默认行为（既不 --check 也不 --verify）也写：一次性工具跑一次就该生效。
    do_write = args.write or not (args.check or args.verify)
    if args.check:
        problems = compare_disk(heldin_lines, heldout_lines)
        if problems:
            for p in problems:
                print(f"DRIFT: {p}", file=sys.stderr)
            if not do_write:
                return 1
        else:
            print("check: in-repo sets match the source split")

    if do_write:
        manifest = write_outputs(
            heldin_lines,
            heldout_lines,
            source,
            revise_legacy=not args.no_legacy,
            write_external=not args.no_external,
        )
        print(
            f"wrote held-in n={manifest['heldin']['count']} "
            f"held-out n={manifest['heldout']['count']} "
            f"(split_version={manifest['split_version']})"
        )

    _print_summary(heldin_lines, heldout_lines, source)

    if args.verify:
        # verify 是对**产物**的断言：不只内存里算对了，落到盘上的也一致。
        problems: list[str] = []
        try:
            disk_in = gs.read_lines(gs.heldin_path())
            disk_legacy = gs.read_lines(gs.compat_golden_path())
            disk_out = gs.read_lines(gs.heldout_path())
        except Exception as exc:  # noqa: BLE001
            print(f"verify: cannot read products: {exc}", file=sys.stderr)
            return 1
        try:
            check_partition([json.loads(x) for x in lines])  # 源侧断言（幂等）
            v_in, v_out = check_partition(
                [json.loads(x) for x in disk_in + disk_out]
            )
            check_no_leak(v_out, v_in)
        except Failure as exc:
            print(f"verify failed: {exc}", file=sys.stderr)
            return 1
        if gs.rows_digest([json.loads(x) for x in disk_in]) != gs.rows_digest(
            [json.loads(x) for x in heldin_lines]
        ):
            problems.append("held-in on disk != source split")
        if gs.rows_digest([json.loads(x) for x in disk_legacy]) != gs.rows_digest(
            [json.loads(x) for x in heldin_lines]
        ):
            problems.append("legacy golden_tasks.jsonl != held-in set")
        if gs.rows_digest([json.loads(x) for x in disk_out]) != gs.rows_digest(
            [json.loads(x) for x in heldout_lines]
        ):
            problems.append("held-out on disk != source split")
        if problems:
            for p in problems:
                print(f"verify: {p}", file=sys.stderr)
            return 1
        print(
            "verify: disjoint OK · union == source OK · "
            f"ratio {len(disk_in)}/{len(disk_out)} OK · "
            "no held-out plaintext in repo OK"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
