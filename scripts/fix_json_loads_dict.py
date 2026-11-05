"""Unwrap json.loads(<step>.execute(...)) where steps already return dict."""
from __future__ import annotations

import re
from pathlib import Path

PATH = Path("src/agentnodes/core/agent/builtin_tools/backtest_tools.py")
# out = json.loads(EXPR)  where EXPR is *_step.execute(...)
PATTERN = re.compile(
    r"out = json\.loads\((\w+_step\.execute\()",
    re.MULTILINE,
)


def find_matching_paren(s: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("no matching paren")


def main() -> None:
    src = PATH.read_text(encoding="utf-8")
    out = []
    pos = 0
    n = 0
    while True:
        m = PATTERN.search(src, pos)
        if not m:
            out.append(src[pos:])
            break
        out.append(src[pos : m.start()])
        # m.group(0) starts at "out = json.loads("
        execute_open = m.start(1) + len(m.group(1)) - 1  # '(' of execute(
        close_loads = find_matching_paren(src, m.start() + len("out = json.loads(") - 1)
        # inner execute(...) is from group(1) start to close_loads-1
        # replace "out = json.loads(" with "out = "
        out.append("out = ")
        out.append(src[m.start(1) : close_loads])  # execute(...) without extra )
        pos = close_loads + 1
        n += 1
    PATH.write_text("".join(out), encoding="utf-8")
    print(f"unwrapped {n} sites")


if __name__ == "__main__":
    main()
