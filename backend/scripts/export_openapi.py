"""Write the OpenAPI contract to backend/openapi.json (BE-1.7).

The file is committed and is the source for frontend type generation. CI re-exports it
and fails if it differs from the committed copy (contract drift check, D-05).

Usage (from backend/):  python -m scripts.export_openapi [--check]
"""

import json
import sys
from pathlib import Path

from app.core.config import Settings
from app.main import create_app

OUT = Path(__file__).resolve().parent.parent / "openapi.json"


def render() -> str:
    spec = create_app(Settings(ENV="test")).openapi()
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print("openapi.json is out of date: run `python -m scripts.export_openapi`")
            return 1
        print("openapi.json is up to date")
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
