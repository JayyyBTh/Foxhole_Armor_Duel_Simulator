"""Render matrix_evolution.mp4 (+ GIF fallback) from matrix_cache.json.

Reads the precomputed armor sweep, builds a matplotlib FuncAnimation,
and writes both MP4 (ffmpeg) and GIF (Pillow) so the slide can fall
back if ffmpeg is unavailable.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
CACHE = ASSETS / "matrix_cache.json"
OUT_MP4 = ASSETS / "matrix_evolution.mp4"
OUT_GIF = ASSETS / "matrix_evolution.gif"

FPS = 2


def main() -> int:
    if not CACHE.exists():
        print(f"missing {CACHE}; run build_matrix_cache.py first", file=sys.stderr)
        return 1

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    import numpy as np

    data = json.loads(CACHE.read_text(encoding="utf-8"))
    steps = data["armor_steps"]
    range_m = data["range_m"]

    # Cross-faction shape: row_tanks × col_tanks. Fall back to legacy
    # symmetric "tanks" key + flat or dict "matrices" for old caches.
    if "row_tanks" in data:
        row_keys = data["row_tanks"]
        col_keys = data["col_tanks"]
        row_labels = data.get("row_names", row_keys)
        col_labels = data.get("col_names", col_keys)
        row_faction = data.get("row_faction", "row")
        col_faction = data.get("col_faction", "col")
    else:
        row_keys = col_keys = data["tanks"]
        row_labels = col_labels = data.get("tank_names", data["tanks"])
        row_faction = col_faction = ""

    # Animation tracks HP / 30pct / Warden-rows ("wc"). Tolerate older shapes:
    #   v4: { mode: { dm: { "wc": [...], "cw": [...] } } }
    #   v3: { mode: { "wc": [...], "cw": [...] } }
    #   v2: { mode: [...] }
    #   v1: [...] (HP-only flat list)
    raw = data["matrices"]
    if isinstance(raw, dict):
        raw = raw["hp"]
    # v4: descend through the disable-mode dimension to the 30% threshold.
    if isinstance(raw, dict) and ("30pct" in raw or "till_death" in raw):
        raw = raw.get("30pct") or raw[next(iter(raw))]
    if isinstance(raw, dict):
        raw = raw["wc"]
    mats = [np.array(m) for m in raw]
    nr, nc = len(row_keys), len(col_keys)

    fig, ax = plt.subplots(figsize=(0.7 * nc + 2.5, 0.6 * nr + 2))
    im = ax.imshow(mats[0], vmin=0.0, vmax=1.0, cmap="RdYlGn")
    ax.set_xticks(range(nc))
    ax.set_yticks(range(nr))
    ax.set_xticklabels(col_labels, rotation=45, ha="right")
    ax.set_yticklabels(row_labels)
    ax.set_xlabel(f"{col_faction.title() or 'opponent'} (defender)")
    ax.set_ylabel(f"{row_faction.title() or 'attacker'} (attacker)")
    title = ax.set_title("")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    texts = [[ax.text(j, i, "", ha="center", va="center", color="black", fontsize=8)
              for j in range(nc)] for i in range(nr)]

    def update(frame):
        m = mats[frame]
        im.set_data(m)
        title.set_text(f"P(row beats column) @ armor = {steps[frame]:.2f}, range = {int(range_m)} m")
        for i in range(nr):
            for j in range(nc):
                texts[i][j].set_text(f"{m[i, j]*100:.0f}")
        return [im, title] + [t for row in texts for t in row]

    fig.tight_layout()
    anim = animation.FuncAnimation(fig, update, frames=len(mats), interval=1000 // FPS, blit=False)

    wrote_mp4 = False
    try:
        anim.save(OUT_MP4, writer=animation.FFMpegWriter(fps=FPS, bitrate=2400))
        print(f"wrote {OUT_MP4}")
        wrote_mp4 = True
    except Exception as exc:
        print(f"ffmpeg unavailable ({exc}); skipping MP4")

    try:
        anim.save(OUT_GIF, writer=animation.PillowWriter(fps=FPS))
        print(f"wrote {OUT_GIF}")
    except Exception as exc:
        print(f"Pillow GIF write failed ({exc})")
        if not wrote_mp4:
            return 2

    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
