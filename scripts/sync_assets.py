"""Copy tank_duel.py and the tank libraries into docs/assets/.

CI runs this before deploy so the Pyodide-loaded copy never drifts from
the canonical files at the repo root.
"""
from __future__ import annotations
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
SOURCES = ["tank_duel.py", "tanks.json"]
# Copied when present; a stale copy is removed when the source is gone.
OPTIONAL_SOURCES = ["earlywar.json", "pushgun.json"]


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name in SOURCES + OPTIONAL_SOURCES:
        src = ROOT / name
        dst = ASSETS / name
        if name in OPTIONAL_SOURCES and not src.exists():
            if dst.exists():
                dst.unlink()
                print(f"removed stale {dst}")
            continue
        shutil.copyfile(src, dst)
        print(f"copied {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
