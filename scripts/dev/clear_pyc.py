# -*- coding: utf-8 -*-
"""clear_pyc — 清理全仓库 __pycache__（搬迁后陈旧字节码会掩盖真实错误）。

物理分层迁移第 ③ 号陷阱：包搬走后，旧位置的 .pyc 仍可被 import 命中，
使「已修好的 ModuleNotFoundError」以幻影形式复现（W2-12 期间一度把
残留引用误判为缓存问题，浪费一轮排查）。协议固定：**每次搬迁改写后、
验证前先清缓存**。

用法：
    python scripts/dev/clear_pyc.py            # 清 src/ tests/ scripts/ 下全部
    python scripts/dev/clear_pyc.py -n         # 只数数量不删（dry-run）

命名规范见 scripts/README.md（dev/ = 描述式，按需运行）。
"""
from __future__ import annotations

import argparse
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCAN_ROOTS = ("src", "tests", "scripts")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", "--dry-run", action="store_true")
    args = ap.parse_args()
    removed = 0
    for root in SCAN_ROOTS:
        base = os.path.join(ROOT, root)
        if not os.path.isdir(base):
            continue
        for dp, dn, _fn in os.walk(base):
            for d in list(dn):
                if d == "__pycache__":
                    p = os.path.join(dp, d)
                    print(p)
                    removed += 1
                    if not args.dry_run:
                        shutil.rmtree(p, ignore_errors=True)
                    dn.remove(d)
    print(f"{'would remove' if args.dry_run else 'removed'} {removed} __pycache__ dirs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
