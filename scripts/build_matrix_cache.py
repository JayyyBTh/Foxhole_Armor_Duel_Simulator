"""Precompute the cross-faction win-rate matrix at N shared-armor steps.

Rows are Warden tanks; columns are Colonial tanks. For each armor
fraction in `ARMOR_STEPS` and each mode (hp, weapon-disable), simulate
every Warden×Colonial pair at `RANGE_M`. Output a single JSON file
consumed by the slider widget, plus one PNG per step for the slide deck
(HP mode only).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tank_duel import load_tanks, load_factions, simulate_duel  # noqa: E402

ASSETS = ROOT / "docs" / "assets"
OUT_JSON = ASSETS / "matrix_cache.json"
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


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)

    library = load_tanks()
    factions = load_factions()
    row_keys = sorted(k for k, f in factions.items() if f == ROW_FACTION)
    col_keys = sorted(k for k, f in factions.items() if f == COL_FACTION)
    if not row_keys or not col_keys:
        print(f"need both factions populated; got {ROW_FACTION}={row_keys}, "
              f"{COL_FACTION}={col_keys}", file=sys.stderr)
        return 1
    row_names = [library[k].name for k in row_keys]
    col_names = [library[k].name for k in col_keys]
    print(f"{ROW_FACTION} rows ({len(row_keys)}): {row_keys}")
    print(f"{COL_FACTION} cols ({len(col_keys)}): {col_keys}")

    # matrices[mode]["wc"][step][i][j] = P(warden_i beats colonial_j)
    # matrices[mode]["cw"][step][j][i] = P(colonial_j beats warden_i)
    matrices: dict[str, dict[str, list]] = {m: {"wc": [], "cw": []} for m in MODES}
    for step_idx, armor in enumerate(ARMOR_STEPS):
        print(f"  step {step_idx+1}/{len(ARMOR_STEPS)}  armor={armor:.2f}")
        for mode in MODES:
            wc, cw = build_matrices(row_keys, col_keys, library, armor, mode)
            matrices[mode]["wc"].append(wc)
            matrices[mode]["cw"].append(cw)
            if mode == "hp":
                # PNG frames + animation track the headline Warden-rows matrix.
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
    OUT_JSON.write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {OUT_JSON} ({OUT_JSON.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
