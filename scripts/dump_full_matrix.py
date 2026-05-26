"""Dump the full N x N win-rate matrix to CSV files for offline analysis.

Produces one CSV per (armor, mode) combination under
``output/full_matrix/``. Each CSV has the row tank names as the leftmost
column and the column tank names as the header row; cells are
P(row tank beats column tank). Both factions appear on both axes, so the
file contains warden-vs-warden, colonial-vs-colonial, and both
cross-faction quadrants in a single square matrix.

The website does *not* consume these files. This is a side channel for
ad-hoc analysis (Excel, pandas, plotting, etc.).

Usage:
    python scripts/dump_full_matrix.py
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tank_duel import load_tanks, load_factions, simulate_duel  # noqa: E402

OUT_DIR = ROOT / "output" / "full_matrix"

RANGE_M = 30.0
ARMOR_STEPS = [round(i * 0.05, 4) for i in range(21)]  # 0.00 .. 1.00
MODES = ("hp", "weapon-disable")

# Row/column ordering: Wardens first, then Colonials, alphabetical within
# each faction. Any tank with no faction (e.g. test fixtures) goes last.
FACTION_ORDER = ("warden", "colonial")


def ordered_tank_keys(library, factions):
    by_faction: dict[str, list[str]] = {f: [] for f in FACTION_ORDER}
    for key in library:
        f = factions.get(key, "")
        by_faction.setdefault(f, []).append(key)
    ordered: list[str] = []
    for f in FACTION_ORDER:
        ordered.extend(sorted(by_faction.get(f, [])))
    for f, ks in by_faction.items():
        if f not in FACTION_ORDER and ks:
            ordered.extend(sorted(ks))
    return ordered


def build_full_matrix(keys, library, armor, mode):
    """N x N win matrix. Each off-diagonal pair (i, j) needs only one
    sim; both M[i][j] and M[j][i] are filled from tank1_wins / tank2_wins.
    Diagonals (self-mirror) get one sim each."""
    n = len(keys)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            result = simulate_duel(
                tank1=library[keys[i]], tank2=library[keys[j]],
                initial_armor_frac1=armor, initial_armor_frac2=armor,
                range_m=RANGE_M, mode=mode,
            )
            m[i][j] = result["tank1_wins"]
            if i != j:
                m[j][i] = result["tank2_wins"]
    return m


def write_csv(matrix, keys, names, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([""] + names)
        for i, key in enumerate(keys):
            row_label = names[i] if names[i] else key
            w.writerow([row_label] + [f"{matrix[i][j]:.6f}" for j in range(len(keys))])


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    library = load_tanks()
    factions = load_factions()
    keys = ordered_tank_keys(library, factions)
    names = [library[k].name for k in keys]
    print(f"tanks ({len(keys)}): {keys}")
    print(f"output dir: {OUT_DIR}")

    pair_count = len(keys) * (len(keys) + 1) // 2
    total_sims = pair_count * len(ARMOR_STEPS) * len(MODES)
    print(f"~{total_sims} simulations across {len(ARMOR_STEPS)} armor steps "
          f"x {len(MODES)} modes\n")

    for armor in ARMOR_STEPS:
        for mode in MODES:
            matrix = build_full_matrix(keys, library, armor, mode)
            fname = f"full_matrix_armor{armor:.2f}_{mode}.csv"
            write_csv(matrix, keys, names, OUT_DIR / fname)
            print(f"  wrote {fname}")
    print(f"\nDone. {len(ARMOR_STEPS) * len(MODES)} files in {OUT_DIR}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
