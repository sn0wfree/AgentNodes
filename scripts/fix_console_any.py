"""Change console: Optional[Console] = None -> console: Any = None in CLI slash commands.

Reason: rich.Console has no type stubs, so Optional[Console] becomes
Unknown | None and pyright never narrows after _resolve_console().
Using Any keeps the call sites unchanged and clears 70+ false Optional
errors. Run with --check to list files without applying.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("src/agentnodes/cli/commands")
OLD_PARAM = "console: Optional[Console] = None"
NEW_PARAM = "console: Any = None"
OLD_RESOLVE = "def _resolve_console(console: Optional[Console] = None) -> Console:"
NEW_RESOLVE = "def _resolve_console(console: Any = None) -> Any:"


def main() -> int:
    check = "--check" in sys.argv
    changed = 0
    for path in sorted(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        new = text.replace(OLD_PARAM, NEW_PARAM).replace(OLD_RESOLVE, NEW_RESOLVE)
        if new == text:
            continue
        # ensure Any is imported
        if "from typing import" in new and "Any" not in new.split("from typing import", 1)[1].split("\n", 1)[0]:
            new = new.replace(
                "from typing import Optional",
                "from typing import Any, Optional",
                1,
            )
            if "from typing import Any, Optional" not in new:
                new = new.replace(
                    "from typing import Optional,",
                    "from typing import Any, Optional,",
                    1,
                )
        if "from typing import Any" not in new and "Any," not in new:
            # fallback: add import after __future__
            new = new.replace(
                "from __future__ import annotations\n",
                "from __future__ import annotations\n\nfrom typing import Any\n",
                1,
            )
        if check:
            print(f"would change {path}")
        else:
            path.write_text(new, encoding="utf-8")
            print(f"updated {path}")
        changed += 1
    print(f"files: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
