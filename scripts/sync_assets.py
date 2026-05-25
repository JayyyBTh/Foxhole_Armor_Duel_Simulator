"""Copy tank_duel.py and tanks.json into docs/assets/.

CI runs this before deploy so the Pyodide-loaded copy never drifts from
the canonical files at the repo root.
"""
from __future__ import annotations
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
SOURCES = ["tank_duel.py", "tanks.json"]


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        src = ROOT / name
        dst = ASSETS / name
        shutil.copyfile(src, dst)
        print(f"copied {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
