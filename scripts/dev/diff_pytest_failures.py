# -*- coding: utf-8 -*-
"""diff_pytest_failures — 对比两份 pytest 全量日志的失败集（差分验证）。

物理分层迁移期间确立的「差分验证制度」的固化版：凡疑似回归，一律对
两个时点跑同命令同文件集，失败集必须逐字对账——新增的逐项归因
（迁移引入 / 既有缺陷 / 顺序敏感摇摆），消失的记录为修复。

日志来源约定（与仓库既有习惯一致）：
    pytest tests -q -n 4 --dist loadgroup -p no:randomly > tmp/full_<tag>.log

用法：
    python scripts/dev/diff_pytest_failures.py tmp/full_before.log tmp/full_after.log
    python scripts/dev/diff_pytest_failures.py a.log b.log --group-by-file

输出：
  NEW   n  （后一时点新增——需逐项归因）
  FIXED n  （前一时点有、现无——记录为修复）
  STILL n  （两时点皆有——基线承续）

命名规范见 scripts/README.md（dev/ = 描述式）。

来源：物理分层迁移 W2-12 沉淀（tmp/diff_fails.py + classify 系列合并固化，
覆盖迁移期间每一次波末差分）。
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from collections import Counter

FAIL_RE = re.compile(r"FAILED (tests/\S+)")


def load(path: str) -> set[str]:
    text = io.open(path, encoding="utf-8", errors="replace").read()
    return set(FAIL_RE.findall(text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--group-by-file", action="store_true",
                    help="NEW 集按测试文件聚合计数")
    args = ap.parse_args()
    old, new = load(args.before), load(args.after)

    print(f"before: {len(old)} failures  ({args.before})")
    print(f"after : {len(new)} failures  ({args.after})")
    print(f"STILL {len(old & new)} | FIXED {len(old - new)} | NEW {len(new - old)}")

    if args.group_by_file and new - old:
        print("\nNEW by file:")
        c = Counter(f.split("::")[0] for f in new - old)
        for k, v in c.most_common():
            print(f"  {v:4d}  {k}")
    elif new - old:
        print("\nNEW:")
        for f in sorted(new - old):
            print("  +", f)
    if old - new:
        print("\nFIXED:")
        for f in sorted(old - new):
            print("  -", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
