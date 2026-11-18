# -*- coding: utf-8 -*-
"""check_env_access — 环境变量访问门禁（统一读取层的护栏）。

**为什么需要**：`src/agentnodes/` 曾有 56 个 env、71 个读取点，每个模块各自
`os.environ.get(...)`。后果是无法集中校验（拼错静默失效）、无法文档化、
无法做配置快照。批次 A 建立了 `foundation/config/settings.py` 声明表，本门禁确保
**新代码不能再绕过它**。

规则：
1. 读**已登记**的变量（在 `settings.ENV_TABLE` 里）必须经 settings。
2. 读**外部生态变量**（`EXTERNAL_ENV_VARS`：HOME / NO_COLOR / LANGCHAIN_* 等）
   允许直接读——它们不属于本项目配置。
3. 读**未登记**的本项目前缀变量（AN_ / AGENTNODES_ / QNODES_ /
   QUANTNODES_ / SR_）→ 报错：要么登记进表，要么加进 EXTERNAL。

白名单：`foundation/config/settings.py` 自身（它就是读取层）。

用法：
    python scripts/arch/check_env_access.py
    python scripts/arch/check_env_access.py --allow-legacy   # 只报告不失败（迁移期）
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, 'src', 'agentnodes')

sys.path.insert(0, os.path.join(ROOT, 'src'))

#: 读取层自身——它是唯一合法的直接访问点。
EXEMPT_FILES = (
    'foundation/config/settings.py',
    'foundation/config/paths.py',      # env_override / PROFILE_ENV 常量
)

#: 需要写回/删除 env 的场景（测试与 CLI 参数注入），不是"读配置"。
ALLOW_MARKS = (
    'env-access: ok',
    'monkeypatch.setenv',
    'os.environ[',
    'environ.setdefault',
)

READ_PAT = re.compile(
    r'(?:os\.environ\.get|os\.getenv|_os\.environ\.get)\(\s*'
    r'(?:["\']([A-Z_0-9]+)["\']|([A-Za-z_][A-Za-z0-9_]*))'
)

#: 模块级常量形如 ``_HALT_PATH_ENV = "AGENTNODES_HALT_PATH"``。
#: **这是门禁的第一版盲区**（2026-11-09 实测）：原正则只认字面量
#: ``os.environ.get("NAME")``，扫不到 ``os.environ.get(_HALT_PATH_ENV)``——
#: 于是 13 个变量长期逃过登记（其中 ``AGENTNODES_SESSIONS_DB`` 还拼错了：
#: 声明表与 CLI 各写一种拼法）。
#: 现在额外解析同文件内的常量赋值，把真实变量名提取出来。
CONST_PAT = re.compile(
    r'^(_?[A-Z][A-Z0-9_]*)\s*(?::\s*[^=]+)?=\s*["\']([A-Za-z_0-9]+)["\']',
    re.M,
)

PROJECT_PREFIXES = ('AN_', 'AGENTNODES_', 'QNODES_', 'QUANTNODES_', 'SR_',
                    'LLM_', 'OPENAI_')


def _load_tables():
    from agentnodes.foundation.config import settings

    return settings.ENV_TABLE, settings.EXTERNAL_ENV_VARS


def scan() -> list[str]:
    declared, external = _load_tables()
    problems: list[str] = []

    for dp, dn, fn in os.walk(SRC):
        dn[:] = [d for d in dn if d not in ('__pycache__', 'attic')]
        for f in fn:
            if not f.endswith('.py'):
                continue
            path = os.path.join(dp, f)
            rel = os.path.relpath(path, SRC).replace(os.sep, '/')
            if any(rel == x for x in EXEMPT_FILES):
                continue
            try:
                text = io.open(path, encoding='utf-8', errors='replace').read()
            except OSError:
                continue
            # 同文件内的模块级常量（把 _CONST 解析成真实变量名）
            consts = dict(CONST_PAT.findall(text))
            lines = text.splitlines()
            for i, line in enumerate(lines, 1):
                s = line.strip()
                if s.startswith('#'):
                    continue
                if any(m in line for m in ALLOW_MARKS):
                    continue
                for m in READ_PAT.finditer(line):
                    literal, var = m.group(1), m.group(2)
                    if literal is not None:
                        name = literal
                    else:
                        # 常量形式：能解析就检查，不能解析（函数参数）跳过
                        name = consts.get(var)
                        if name is None:
                            continue
                    if name in external:
                        continue          # 生态变量，允许直读
                    if name in declared:
                        problems.append(
                            f'{rel}:{i}: 直接读已登记的 {name} —— '
                            f'应改用 settings.get_xxx("{name}")'
                        )
                    elif name.startswith(PROJECT_PREFIXES):
                        problems.append(
                            f'{rel}:{i}: 读了未登记的项目变量 {name} '
                            f'（经常量 {var or "字面量"}）—— '
                            f'登记进 settings.ENV_TABLE，或加进 EXTERNAL_ENV_VARS'
                        )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--allow-legacy', action='store_true',
                    help='迁移期：只报告不失败')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    problems = scan()
    if not problems:
        if not args.quiet:
            print('check_env_access: 0 问题')
        return 0

    print(f'env 访问问题：{len(problems)} 处', file=sys.stderr)
    for p in problems:
        print('  ' + p, file=sys.stderr)
    if args.allow_legacy:
        print('\n[--allow-legacy] 迁移期：不判失败', file=sys.stderr)
        return 0
    print('\n修复方向：配置读取统一经 foundation.config.settings；'
          '外部生态变量需显式加进 EXTERNAL_ENV_VARS。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
