"""Export the FastAPI OpenAPI schema to a JSON file.

Usage:
    python scripts/export_openapi.py [output_path]

Default output: ``webui/frontend/openapi.json`` — committed so the
frontend's ``gen:types`` step can regenerate ``types.gen.ts`` without a
running server. CI re-runs this and diffs to catch stale schemas.

The export does not start the app or its background tasks (only builds
the schema object), so it is safe to run offline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def build_openapi() -> dict:
    from agentnodes.api.app import create_app

    app = create_app()
    schema = app.openapi()
    return schema


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        REPO_ROOT / "webui" / "frontend" / "openapi.json"
    )
    schema = build_openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote openapi schema ({len(schema.get('paths', {}))} paths) → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
