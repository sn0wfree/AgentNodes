"""Type strategy-step host binds as Any (duck-typed AgentLoop)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path("src/agentnodes/core/agent/strategy/steps")
REPLACES = [
    ("self._loop: object | None = None", "self._loop: Any = None"),
    ("def bind_agent_loop(self, agent_loop: object) -> None:", "def bind_agent_loop(self, agent_loop: Any) -> None:"),
]


def main() -> None:
    for path in sorted(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        new = text
        for old, repl in REPLACES:
            new = new.replace(old, repl)
        if new == text:
            continue
        if "from typing import" in new and "Any" not in new.split("from typing import", 1)[1].split("\n", 1)[0]:
            new = new.replace("from typing import", "from typing import Any, ", 1)
        elif "from typing import Any" not in new:
            new = new.replace(
                "from __future__ import annotations\n",
                "from __future__ import annotations\n\nfrom typing import Any\n",
                1,
            )
        path.write_text(new, encoding="utf-8")
        print("updated", path.name)


if __name__ == "__main__":
    main()
