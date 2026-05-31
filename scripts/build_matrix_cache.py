"""Precompute the cross-faction win-rate matrix at N shared-armor steps.

Rows are Warden tanks; columns are Colonial tanks. For each armor
fraction in `ARMOR_STEPS` and each mode (hp, weapon-disable), simulate
every Warden×Colonial pair at `RANGE_M`. Output a JSON cache file
consumed by the slider widget, plus (for the main library only) one
PNG per step for the slide deck (HP mode only).

Run once per library: main tanks.json -> matrix_cache.json (+ PNGs for
the animation), earlywar.json -> matrix_cache_earlywar.json (cache
only, no PNGs).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tank_duel import (  # noqa: E402
    load_tanks, load_factions, simulate_duel,
    DEFAULT_TANKS_FILE, DEFAULT_EARLYWAR_FILE,
)

ASSETS = ROOT / "docs" / "assets"
MAIN_OUT_JSON = ASSETS / "matrix_cache.json"
EW_OUT_JSON = ASSETS / "matrix_cache_earlywar.json"
PNG_DIR = ASSETS / "matrix_frames"

RANGE_M = 30.0
ARMOR_STEPS = [round(i * 0.05, 4) for i in range(21)]  # 0.00 .. 1.00
MODES = ("hp", "weapon-disable")
ROW_FACTION = "warden"
COL_FACTION = "colonial"


def build_matrices(row_keys, col_keys, library, armor, mode):
    """One sim per (row, col) pair — fills both matrices at once.
      forward[i][j]   = P(row_keys[i] beats col_keys[j])      (row attacker)
      reverse[j][i]   = P(col_keys[j] beats row_keys[i])      (col attacker)
    """
    forward = [[0.0] * len(col_keys) for _ in row_keys]
    reverse = [[0.0] * len(row_keys) for _ in col_keys]
    for i, a_key in enumerate(row_keys):
        for j, b_key in enumerate(col_keys):
            result = simulate_duel(
                tank1=library[a_key], tank2=library[b_key],
                initial_armor_frac1=armor, initial_armor_frac2=armor,
                range_m=RANGE_M, mode=mode,
            )
            forward[i][j] = result["tank1_wins"]
            reverse[j][i] = result["tank2_wins"]
    return forward, reverse


def write_png(matrix, row_keys, row_names, col_keys, col_names, armor, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print(f"  matplotlib unavailable, skipping {path.name}")
        return
    arr = np.array(matrix)
    fig, ax = plt.subplots(figsize=(0.7 * len(col_keys) + 2.5, 0.6 * len(row_keys) + 2))
    im = ax.imshow(arr, vmin=0.0, vmax=1.0, cmap="RdYlGn")
    ax.set_xticks(range(len(col_keys)))
    ax.set_yticks(range(len(row_keys)))
    ax.set_xticklabels(col_names, rotation=45, ha="right")
    ax.set_yticklabels(row_names)
    ax.set_xlabel(f"{COL_FACTION.title()} (defender)")
    ax.set_ylabel(f"{ROW_FACTION.title()} (attacker)")
    ax.set_title(f"P(row beats column) @ armor = {armor:.2f}, range = {int(RANGE_M)} m")
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            ax.text(j, i, f"{arr[i, j]*100:.0f}", ha="center", va="center",
                    color="black", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def build_cache_for_library(library_path: Path, out_json: Path,
                            write_pngs: bool, label: str) -> int:
    """Build one matrix cache from one library file. Returns 0 on success,
    1 if the library lacks both factions (cache write skipped)."""
    print(f"--- {label}: {library_path.name} -> {out_json.name} ---")
    library = load_tanks(library_path)
    factions = load_factions(library_path)
    row_keys = sorted(k for k, f in factions.items() if f == ROW_FACTION)
    col_keys = sorted(k for k, f in factions.items() if f == COL_FACTION)
    if not row_keys or not col_keys:
        print(f"  skipping {out_json.name}: need both factions populated; "
              f"got {ROW_FACTION}={row_keys}, {COL_FACTION}={col_keys}")
        # Tolerate empty/one-faction libraries (e.g. early-war while it's
        # being populated). Delete a stale cache so the website doesn't
        # show outdated data.
        if out_json.exists():
            out_json.unlink()
            print(f"  removed stale {out_json}")
        return 1
    row_names = [library[k].name for k in row_keys]
    col_names = [library[k].name for k in col_keys]
    print(f"  {ROW_FACTION} rows ({len(row_keys)}): {row_keys}")
    print(f"  {COL_FACTION} cols ({len(col_keys)}): {col_keys}")

    matrices: dict[str, dict[str, list]] = {m: {"wc": [], "cw": []} for m in MODES}
    for step_idx, armor in enumerate(ARMOR_STEPS):
        print(f"    step {step_idx+1}/{len(ARMOR_STEPS)}  armor={armor:.2f}")
        for mode in MODES:
            wc, cw = build_matrices(row_keys, col_keys, library, armor, mode)
            matrices[mode]["wc"].append(wc)
            matrices[mode]["cw"].append(cw)
            if write_pngs and mode == "hp":
                write_png(wc, row_keys, row_names, col_keys, col_names,
                          armor, PNG_DIR / f"matrix_{step_idx:02d}.png")

    payload = {
        "range_m": RANGE_M,
        "row_faction": ROW_FACTION,
        "col_faction": COL_FACTION,
        "row_tanks": row_keys,
        "row_names": row_names,
        "col_tanks": col_keys,
        "col_names": col_names,
        "armor_steps": ARMOR_STEPS,
        "modes": list(MODES),
        "matrices": matrices,
    }
    out_json.write_text(json.dumps(payload), encoding="utf-8")
    print(f"  wrote {out_json} ({out_json.stat().st_size} bytes)")
    return 0


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)

    # Main library — PNGs feed the slide-deck animation.
    rc_main = build_cache_for_library(
        DEFAULT_TANKS_FILE, MAIN_OUT_JSON, write_pngs=True, label="main",
    )

    # Early-war library — cache only, no animation. Tolerant of an empty
    # or single-faction file.
    if DEFAULT_EARLYWAR_FILE.exists():
        build_cache_for_library(
            DEFAULT_EARLYWAR_FILE, EW_OUT_JSON, write_pngs=False, label="earlywar",
        )
    else:
        print(f"--- earlywar: {DEFAULT_EARLYWAR_FILE.name} not present, skipping ---")
        if EW_OUT_JSON.exists():
            EW_OUT_JSON.unlink()
            print(f"  removed stale {EW_OUT_JSON}")

    # The main cache is the headline artifact; its absence is fatal.
    return rc_main


if __name__ == "__main__":
    raise SystemExit(main())
