"""Precompute win-rate matrices at N shared-armor steps.

For each armor fraction in `ARMOR_STEPS`, simulate every ordered pair
(tank_a, tank_b) at `RANGE_M`. Output a single JSON file consumed by the
slider widget on the simulator page, plus one PNG per step for the slide
deck's static frames.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tank_duel import load_tanks, simulate_duel  # noqa: E402

ASSETS = ROOT / "docs" / "assets"
OUT_JSON = ASSETS / "matrix_cache.json"
PNG_DIR = ASSETS / "matrix_frames"

RANGE_M = 30.0
ARMOR_STEPS = [round(i * 0.05, 4) for i in range(21)]  # 0.00 .. 1.00


def build_matrix(tank_keys: list[str], library, armor: float) -> list[list[float]]:
    n = len(tank_keys)
    matrix = [[0.0] * n for _ in range(n)]
    for i, a_key in enumerate(tank_keys):
        for j, b_key in enumerate(tank_keys):
            result = simulate_duel(
                tank1=library[a_key], tank2=library[b_key],
                initial_armor_frac1=armor, initial_armor_frac2=armor,
                range_m=RANGE_M,
            )
            matrix[i][j] = result["tank1_wins"]
    return matrix


def write_png(matrix, tank_keys, armor, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print(f"  matplotlib unavailable, skipping {path.name}")
        return
    arr = np.array(matrix)
    fig, ax = plt.subplots(figsize=(0.6 * len(tank_keys) + 2, 0.6 * len(tank_keys) + 1.5))
    im = ax.imshow(arr, vmin=0.0, vmax=1.0, cmap="RdYlGn")
    ax.set_xticks(range(len(tank_keys)))
    ax.set_yticks(range(len(tank_keys)))
    ax.set_xticklabels(tank_keys, rotation=45, ha="right")
    ax.set_yticklabels(tank_keys)
    ax.set_xlabel("opponent")
    ax.set_ylabel("attacker")
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
    tank_keys = sorted(library.keys())
    print(f"tanks ({len(tank_keys)}): {tank_keys}")

    matrices = []
    for step_idx, armor in enumerate(ARMOR_STEPS):
        print(f"  step {step_idx+1}/{len(ARMOR_STEPS)}  armor={armor:.2f}")
        m = build_matrix(tank_keys, library, armor)
        matrices.append(m)
        write_png(m, tank_keys, armor, PNG_DIR / f"matrix_{step_idx:02d}.png")

    payload = {
        "range_m": RANGE_M,
        "tanks": tank_keys,
        "tank_names": [library[k].name for k in tank_keys],
        "armor_steps": ARMOR_STEPS,
        "matrices": matrices,
    }
    OUT_JSON.write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {OUT_JSON} ({OUT_JSON.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
