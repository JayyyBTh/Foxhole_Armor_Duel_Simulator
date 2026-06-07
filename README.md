# Foxhole Armor Duel Simulator

A probabilistic 1v1 tank-duel solver for the game [Foxhole](https://www.foxholegame.com/). Computes exact win probabilities by expanding the full probability tree over discrete hit counts — no Monte Carlo, deterministic to the last decimal.

**Live site:** https://jayyybth.github.io/Foxhole_Armor_Duel_Simulator/ — runs entirely in your browser via [Pyodide](https://pyodide.org).

## What it does

Given two tanks, their starting armor fractions, and an engagement range, it produces:

- **Win probabilities** for each side (and the chance of a simultaneous kill).
- **A shot-by-shot timeline** with the per-event pen probability (median and range across the active state distribution) and cumulative win curves.
- **Two win conditions:**
  - `hp` (default): a tank loses when HP drops below `disable_threshold * base_health` (30% by default).
  - `weapon-disable`: a tank also loses if all of its weapons are disabled, in addition to HP. Each penetrating hit rolls the defender's highest-index live weapon's `disable_chance`; within a tick the cascade can chain down to the next weapon.

The math is deterministic state-tree expansion — every state is `(hits1, hits2)` (plus disabled-weapon counts in `weapon-disable` mode), and at each shot event every live state branches by the per-state pen probability. Branches below `prob_cutoff = 1e-12` are pruned.

## Quick start (CLI)

Pure stdlib — no dependencies for the simulator itself. (Building the website's animation and PNG frames needs `matplotlib` + `numpy` + `ffmpeg`/`Pillow`; the simulator works without them.)

```powershell
# Duel: Spatha (full armor) vs Brigand (full armor) at 30 m, HP mode
python tank_duel.py MediumTankOffensiveC 1.0 MediumTank2W 1.0 30

# Weapon-disable mode, longer shot log
python tank_duel.py MediumTankW 0.8 MediumTank2C 0.8 15 --mode weapon-disable --shots 30

# Cross-era duel (early-war Percutio vs main-library Spatha)
python tank_duel.py ArmoredCarOffensiveC 1.0 MediumTankOffensiveC 1.0 30

# List available tanks
python tank_duel.py --help

# Run the sanity-check test suite (deterministic; pipe to file and diff)
python tank_duel.py --tests > tests_now.txt
```

Tank keys are the game's internal codenames (e.g. `MediumTankOffensiveC` = Spatha). `--help`'s epilog lists every key currently loaded.

## Adding a tank

Tanks live in JSON, faction-bucketed, keyed by codename:

```jsonc
{
  "warden": {
    "MediumTank2W": {
      "name": "Brigand",
      "base_health": 2950,
      "base_armor": 11000,
      "base_pen_chance": 0.33,      // P0 — minimum pen at full armor (defender stat)
      "pre_bonus_cap": 0.67,        // S0 — pre-bonus ceiling (defender stat)
      "armor_class": "heavy",       // optional, default "heavy"; "light" for early-war
      "weapon": {                   // OR "weapons": [ {...}, {...} ] for multi-weapon
        "name": "Brigand 30mm",
        "damage_health": 400,       // RAW damage before damage-type multiplier
        "damage_armor": 400,
        "pen_mod": 1.0,             // attacker stat
        "reload_time": 2.5,
        "fire_cooldown": 1.5,
        "ammo_capacity": 3,
        "disable_chance": 0.2,      // DEFENSIVE: P(this weapon disabled on a pen)
        "damage_type": "explosive", // optional, default "ap"
        "disable_mod": 1.0,         // OFFENSIVE: multiplier on defender's dc, default 1.0
        "reload_mode": "per_shot"   // optional, default "per_shot"; "per_magazine" for autocannons
      }
    }
  },
  "colonial": { ... }
}
```

Two library files:
- [`tanks.json`](tanks.json) — main mid/late-war library (15 tanks).
- [`earlywar.json`](earlywar.json) — early-war light vehicles (3 so far). Same shape, optional.

The CLI auto-loads both if both exist. Override with `--tanks-file path1 [path2 ...]`.

### Damage types

Damage values are **raw**; the multiplier table in [`tank_duel.py`](tank_duel.py) (`_GAME_DAMAGE_PCT`) scales them on penetration:

| Damage type  | vs Light | vs Heavy |
|---|---|---|
| `ap` 	       | 100% | 100% |
| `explosive`  | 85%  | 85% |
| `at_kinetic` | 65%  | 15% |

Untagged weapons default to `ap` (no scaling). Add new damage types by extending the table.

### Firing schedules

- `per_shot` (default): every shot after the first burst incurs its own `reload_time`. Burst of `ammo_capacity` shots fires at `fire_cooldown` intervals, then each additional shot costs `fire_cooldown + reload_time`.
- `per_magazine`: empty the whole magazine quickly, then one `reload_time` swaps it. Every shot still incurs `fire_cooldown` after it. Magazine cycle = `ammo_capacity * fire_cooldown + reload_time`. For `ammo_capacity = 1` the two modes coincide.

## Architecture

The whole simulator is one file: [`tank_duel.py`](tank_duel.py). Key invariants documented in the file header — read it before touching the core. Highlights:

- **State** is `(hits1, hits2)` (per-defender per-attacker-weapon hit-count tuples) in HP mode, or `(hits1, hits2, d1, d2)` with disabled-weapon counts in weapon-disable mode.
- **Multiplication order is load-bearing**: the simultaneous-fire branch orders tank2 weapons before tank1 weapons to preserve byte-identical regression against the pre-multi-weapon implementation.
- **`pen_on_t1` / `pen_on_t2`** in the shot log are `(min, median, max)` triples across the active state distribution — display only, computed before outcome resolution.
- **Pen formula** (`compute_pen_chance`) is `min(S0, max(P0, 1 − armor_frac)) * pen_mod + range_bonus`, clamped to [0, 1]. Range bonus is the piecewise step function in `range_to_bonus`.

## The website

Lives in [`docs/`](docs/) and is deployed by GitHub Pages. The static-site pipeline:

```powershell
python scripts/sync_assets.py            # copies tank_duel.py + tanks.json + earlywar.json into docs/assets/
python scripts/build_matrix_cache.py     # rebuilds matrix_cache.json + matrix_cache_earlywar.json + slide PNGs
python scripts/render_animation.py       # rebuilds matrix_evolution.mp4 + GIF (main library only)
git add docs/ && git commit -m "rebuild" && git push
```

The browser page does two things:

1. **Single-duel form** — Pyodide loads `tank_duel.py` and both library JSONs, runs `simulate_duel`, prints the standard summary + shot log into a `<pre>`. Cross-era duels work because the library loader merges both files.
2. **Win-rate matrices** — two precomputed JSON caches (one per library) drive heatmap tables. A shared armor slider (0.00 → 1.00 in 0.05 steps) and an HP/weapon-disable toggle update all visible matrices in sync. Early-war section auto-hides if its cache is missing.

`scripts/build_matrix_cache.py` is the heavy step (294 sims per library at full sweep). It also writes the PNG frames consumed by `render_animation.py`.

## Tests

There's a deterministic sanity-check suite triggered by `--tests`:

```powershell
python tank_duel.py --tests > tests_now.txt
# After any code change:
python tank_duel.py --tests > tests_after.txt
fc tests_now.txt tests_after.txt    # PowerShell built-in diff
```

Scenarios use toy tanks (5 HP, 1-damage weapons, etc.) in [`tanks_test.json`](tanks_test.json) so the numbers are hand-checkable. Designed for byte-identical regression diffing rather than pass/fail assertions.

## Game-data audit

[`_audit_vs_game.py`](_audit_vs_game.py) diffs `tanks.json` against the game's `BPVehicleDynamicData.json` dump (path is hardcoded). It surfaces stat drift after game patches. Mapping conventions and known anomalies (e.g. HTD's phantom subsystem slot) are documented in the script's header.

## File layout

```
tank_duel.py              single-file simulator (stdlib only)
tanks.json                main tank library
earlywar.json             early-war light vehicles (optional)
tanks_test.json           toy tanks for --tests
_audit_vs_game.py         throwaway diff vs the game data
scripts/
  sync_assets.py          copy source files into docs/assets/
  build_matrix_cache.py   precompute the win-rate matrices
  render_animation.py     produce matrix_evolution.mp4/.gif
  dump_full_matrix.py     ad-hoc utility
docs/                     GitHub Pages site (HTML/CSS/JS + assets/)
CLAUDE.md                 working notes for code-assist agents
PLAN.md                   deferred plan document (slide deck, etc.)
```

## License

No license file — this is a personal project. Ask before reusing.
