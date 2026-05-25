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
    tanks = data["tanks"]
    steps = data["armor_steps"]
    mats = [np.array(m) for m in data["matrices"]]
    range_m = data["range_m"]
    n = len(tanks)

    fig, ax = plt.subplots(figsize=(0.6 * n + 2, 0.6 * n + 1.5))
    im = ax.imshow(mats[0], vmin=0.0, vmax=1.0, cmap="RdYlGn")
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(tanks, rotation=45, ha="right")
    ax.set_yticklabels(tanks)
    ax.set_xlabel("opponent")
    ax.set_ylabel("attacker")
    title = ax.set_title("")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    # Cell labels updated each frame.
    texts = [[ax.text(j, i, "", ha="center", va="center", color="black", fontsize=8)
              for j in range(n)] for i in range(n)]

    def update(frame):
        m = mats[frame]
        im.set_data(m)
        title.set_text(f"P(row beats column) @ armor = {steps[frame]:.2f}, range = {int(range_m)} m")
        for i in range(n):
            for j in range(n):
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
