"""Wrap json.loads(step.execute(...)) with cast(str, ...) after tool_errors wrapper."""
from __future__ import annotations

from pathlib import Path

PATH = Path("src/agentnodes/core/agent/builtin_tools/backtest_tools.py")
OLD = "json.loads("
NEW = "json.loads(cast(str, "


def matching_paren(s: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced")


def main() -> None:
    src = PATH.read_text(encoding="utf-8")
    out = []
    pos = 0
    n = 0
    while True:
        i = src.find(OLD, pos)
        if i < 0:
            out.append(src[pos:])
            break
        # skip if already cast
        if src[i : i + len("json.loads(cast(str, ")] == "json.loads(cast(str, ":
            out.append(src[pos : i + len(OLD)])
            pos = i + len(OLD)
            continue
        open_idx = i + len(OLD) - 1  # '(' of loads
        close_idx = matching_paren(src, open_idx)
        inner = src[open_idx + 1 : close_idx]
        out.append(src[pos:i])
        out.append(NEW + inner + ")")
        pos = close_idx + 1
        n += 1
    text = "".join(out)
    if "from typing import" in text:
        if "cast" not in text.split("from typing import", 1)[1].split("\n", 1)[0]:
            text = text.replace("from typing import", "from typing import cast, ", 1)
    elif "import cast" not in text:
        text = text.replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n\nfrom typing import cast\n",
            1,
        )
    PATH.write_text(text, encoding="utf-8")
    print(f"wrapped {n}")


if __name__ == "__main__":
    main()
