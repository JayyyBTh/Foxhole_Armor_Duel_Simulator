"""
tank_duel.py
============
Probabilistic simulation of a 1v1 tank duel in Foxhole. Tanks may carry
one or more independently-firing weapons; single-weapon tanks reduce to
the original two-tuple state.

Firing schedule:  t(n) = (n-1)*fire_cooldown + max(0, n-cap)*reload_time
Disable:          HP < disable_threshold * base_health  (default 30%)
State:            (hits1, hits2) where hits1 / hits2 are tuples with one
                  slot per attacking weapon. Single-weapon case collapses
                  to ((h1,), (h2,)).

Naming convention (throughout):
  fires1         →  tuple of tank1-weapon indices that fired this tick
  fires2         →  tuple of tank2-weapon indices that fired this tick
  hits1          →  per-weapon hits absorbed by tank1 (one slot per tank2 weapon)
  hits2          →  per-weapon hits absorbed by tank2 (one slot per tank1 weapon)
  pen_on_t1      →  per-firing-weapon pen chance against tank1 (tank2's weapons)
  pen_on_t2      →  per-firing-weapon pen chance against tank2 (tank1's weapons)
"""

from __future__ import annotations
import argparse
import json
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Weapon:
    """
    name          : label
    damage_health : HP removed from target per penetrating hit
    damage_armor  : armor-points removed per penetrating hit
    pen_mod       : penetration modifier (1.0 = baseline)
    reload_time   : seconds to reload after burst is spent, then after every shot
    fire_cooldown : seconds between shots within a burst (and before reload)
    ammo_capacity : pre-loaded shells at fight start (first-stage size); default 1
    """
    name: str
    damage_health: int
    damage_armor: int
    pen_mod: float
    reload_time: float
    fire_cooldown: float
    ammo_capacity: int = 1


@dataclass
class Tank:
    """
    name            : label
    base_health     : total HP at full health
    base_armor      : total armor-points at full armor
    base_pen_chance : P0 - minimum pen chance at full armor (lower = harder tank)
    pre_bonus_cap   : S0 - ceiling on pre-bonus pen value (lower = harder tank)
    weapons         : list of Weapons; each fires on its own schedule
    """
    name: str
    base_health: int
    base_armor: int
    base_pen_chance: float
    pre_bonus_cap: float
    weapons: List[Weapon]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def range_to_bonus(range_m: float) -> float:
    """Convert range in metres to flat penetration bonus."""
    if range_m <= 5:
        return 0.25
    elif range_m <= 10:
        return 0.20
    elif range_m <= 20:
        return 0.10
    else:
        return 0.0


def compute_pen_chance(defending_tank: Tank, attacking_weapon: Weapon,
                       armor_frac: float, range_bonus: float) -> float:
    """
    P(attacking_weapon penetrates defending_tank).
    basePenChance/preBonusCap: DEFENDING tank. penMod: ATTACKING weapon.

    S = 1 - armor_frac
    pre = max(basePenChance, S)
    capped = min(preBonusCap, pre)
    P = penMod * capped + range_bonus   clamped to [0, 1]
    """
    S = 1.0 - armor_frac
    pre = max(defending_tank.base_pen_chance, S)
    capped = min(defending_tank.pre_bonus_cap, pre)
    return max(0.0, min(1.0, attacking_weapon.pen_mod * capped + range_bonus))


def shot_time(shot_n: int, weapon: Weapon) -> float:
    """t(n) = (n-1)*fire_cooldown + max(0, n-ammo_capacity)*reload_time"""
    return ((shot_n - 1) * weapon.fire_cooldown
            + max(0, shot_n - weapon.ammo_capacity) * weapon.reload_time)


def build_event_sequence(
    weapons1: List[Weapon], weapons2: List[Weapon],
    max_shots_each: int = 300,
    time_tol: float = 1e-9,
) -> List[Tuple[float, Tuple[int, ...], Tuple[int, ...]]]:
    """
    Merge per-weapon shot schedules into a chronological list of
    (time, fires1_indices, fires2_indices). The index tuples list which
    weapons on each side fired at that tick (empty = none). Shots within
    time_tol seconds are treated as simultaneous, across all weapons on
    either side.
    """
    events: Dict[float, Tuple[List[int], List[int]]] = {}

    def add(t: float, side: int, wi: int) -> None:
        for existing in list(events.keys()):
            if abs(existing - t) <= time_tol:
                f1, f2 = events[existing]
                (f1 if side == 1 else f2).append(wi)
                return
        events[t] = ([wi], []) if side == 1 else ([], [wi])

    for wi, w in enumerate(weapons1):
        for n in range(1, max_shots_each + 1):
            add(shot_time(n, w), 1, wi)
    for wi, w in enumerate(weapons2):
        for n in range(1, max_shots_each + 1):
            add(shot_time(n, w), 2, wi)

    return sorted((t, tuple(f1), tuple(f2)) for t, (f1, f2) in events.items())


def _pen_stats(pen_prob_pairs: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    """
    Given (pen_chance, probability_weight) pairs, return (min, median, max).
    Median is probability-weighted.
    """
    if not pen_prob_pairs:
        return (0.0, 0.0, 0.0)
    sorted_pairs = sorted(pen_prob_pairs, key=lambda x: x[0])
    total = sum(p for _, p in sorted_pairs)
    lo = sorted_pairs[0][0]
    hi = sorted_pairs[-1][0]
    cumul = 0.0
    median = lo
    for val, p in sorted_pairs:
        cumul += p
        if cumul >= total * 0.5:
            median = val
            break
    return lo, median, hi


# ---------------------------------------------------------------------------
# Core duel simulator
# ---------------------------------------------------------------------------

def simulate_duel(tank1: Tank, tank2: Tank,
                  initial_armor_frac1: float = 1.0,
                  initial_armor_frac2: float = 1.0,
                  range_m: float = 30.0,
                  velocity_mod: float = 1.0,
                  disable_threshold: float = 0.30,
                  prob_cutoff: float = 1e-12,
                  max_shots_each: int = 300) -> dict:
    """
    Probabilistic 1v1 duel with independent firing schedules across all
    weapons on both tanks.

    fires1 = tuple of tank1-weapon indices firing this tick (empty = none)
    fires2 = tuple of tank2-weapon indices firing this tick
    pen_on_t1 = list of (weapon_name, (min, median, max)) per tank2-weapon
                that fired at tank1 this tick
    pen_on_t2 = same for tank1-weapons firing at tank2
    """
    range_bonus = range_to_bonus(range_m)

    # Per-hit losses indexed by attacker-weapon index
    armor_loss_on_t1 = [w.damage_armor * velocity_mod / tank1.base_armor for w in tank2.weapons]
    armor_loss_on_t2 = [w.damage_armor * velocity_mod / tank2.base_armor for w in tank1.weapons]
    health_loss_on_t1 = [w.damage_health * velocity_mod for w in tank2.weapons]
    health_loss_on_t2 = [w.damage_health * velocity_mod for w in tank1.weapons]

    disable_hp1 = tank1.base_health * disable_threshold
    disable_hp2 = tank2.base_health * disable_threshold

    def af1(hits1: Tuple[int, ...]) -> float:
        loss = sum(h * armor_loss_on_t1[i] for i, h in enumerate(hits1))
        return max(0.0, initial_armor_frac1 - loss)

    def af2(hits2: Tuple[int, ...]) -> float:
        loss = sum(h * armor_loss_on_t2[i] for i, h in enumerate(hits2))
        return max(0.0, initial_armor_frac2 - loss)

    def dis1(hits1: Tuple[int, ...]) -> bool:
        hp_loss = sum(h * health_loss_on_t1[i] for i, h in enumerate(hits1))
        return (tank1.base_health - hp_loss) < disable_hp1

    def dis2(hits2: Tuple[int, ...]) -> bool:
        hp_loss = sum(h * health_loss_on_t2[i] for i, h in enumerate(hits2))
        return (tank2.base_health - hp_loss) < disable_hp2

    event_schedule = build_event_sequence(tank1.weapons, tank2.weapons, max_shots_each)

    initial_state: Tuple[Tuple[int, ...], Tuple[int, ...]] = (
        (0,) * len(tank2.weapons),
        (0,) * len(tank1.weapons),
    )
    active: Dict[Tuple[Tuple[int, ...], Tuple[int, ...]], float] = {initial_state: 1.0}
    p1w = p2w = psim = 0.0
    events_data: List[dict] = []

    for idx, (t, fires1, fires2) in enumerate(event_schedule, 1):
        if not active:
            break

        # --- Per-firing-weapon pen stats across active states (display only) ---
        pen_on_t1: Optional[List[Tuple[str, Tuple[float, float, float]]]] = None
        pen_on_t2: Optional[List[Tuple[str, Tuple[float, float, float]]]] = None

        if fires2:
            pen_on_t1 = []
            for wi in fires2:
                w = tank2.weapons[wi]
                stats = _pen_stats([
                    (compute_pen_chance(tank1, w, af1(hits1), range_bonus), prob)
                    for (hits1, _), prob in active.items()
                ])
                pen_on_t1.append((w.name, stats))
        if fires1:
            pen_on_t2 = []
            for wi in fires1:
                w = tank1.weapons[wi]
                stats = _pen_stats([
                    (compute_pen_chance(tank2, w, af2(hits2), range_bonus), prob)
                    for (_, hits2), prob in active.items()
                ])
                pen_on_t2.append((w.name, stats))

        # --- Resolve outcomes ---
        # Order tank2-weapons before tank1-weapons so single-weapon both-fire
        # multiplication order matches the original implementation
        # (prob * p_hit_t1 * p_hit_t2).
        firing: List[Tuple[int, int]] = []  # (side, weapon_index)
        for wi in fires2:
            firing.append((2, wi))
        for wi in fires1:
            firing.append((1, wi))

        new_active: Dict[Tuple[Tuple[int, ...], Tuple[int, ...]], float] = {}
        ev1 = ev2 = evsim = 0.0

        for (hits1, hits2), prob in active.items():
            pen_probs: List[float] = []
            for side, wi in firing:
                if side == 1:
                    pen_probs.append(compute_pen_chance(
                        tank2, tank1.weapons[wi], af2(hits2), range_bonus))
                else:
                    pen_probs.append(compute_pen_chance(
                        tank1, tank2.weapons[wi], af1(hits1), range_bonus))

            for outcome_bits in product((False, True), repeat=len(firing)):
                p_branch = prob
                nh1 = list(hits1)
                nh2 = list(hits2)
                for (side, wi), pen, hit in zip(firing, pen_probs, outcome_bits):
                    if hit:
                        p_branch *= pen
                        if side == 1:
                            nh2[wi] += 1
                        else:
                            nh1[wi] += 1
                    else:
                        p_branch *= (1.0 - pen)

                if p_branch < prob_cutoff:
                    continue

                nh1_t = tuple(nh1)
                nh2_t = tuple(nh2)
                d1 = dis1(nh1_t)
                d2 = dis2(nh2_t)
                if d1 and d2:
                    evsim += p_branch; psim += p_branch
                elif d1:
                    ev2 += p_branch; p2w += p_branch
                elif d2:
                    ev1 += p_branch; p1w += p_branch
                else:
                    key = (nh1_t, nh2_t)
                    new_active[key] = new_active.get(key, 0.0) + p_branch

        active = new_active
        events_data.append({
            "event": idx, "time": t,
            "tank1_fired": bool(fires1), "tank2_fired": bool(fires2),
            "pen_on_t1": pen_on_t1,
            "pen_on_t2": pen_on_t2,
            "tank1_wins_this_event": ev1, "tank2_wins_this_event": ev2,
            "simultaneous_this_event": evsim,
            "cumulative_tank1_wins": p1w, "cumulative_tank2_wins": p2w,
            "cumulative_simultaneous": psim,
            "remaining_probability": sum(active.values()),
            "active_states": len(active),
        })

    return {
        "tank1_wins": p1w, "tank2_wins": p2w, "simultaneous": psim,
        "unresolved": sum(active.values()),
        "events": events_data,
        "range_m": range_m,
        "initial_armor_frac1": initial_armor_frac1,
        "initial_armor_frac2": initial_armor_frac2,
    }


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def print_duel_summary(tank1: Tank, tank2: Tank, result: dict) -> None:
    total = result["tank1_wins"] + result["tank2_wins"] + result["simultaneous"]
    sep = "=" * 58
    print(f"\n{sep}")
    print(f"  DUEL: {tank1.name}  vs  {tank2.name}  @ {result['range_m']} m")
    print(sep)
    print(f"  {tank1.name:<22} wins : {result['tank1_wins']*100:6.2f} %")
    print(f"  {tank2.name:<22} wins : {result['tank2_wins']*100:6.2f} %")
    print(f"  Simultaneous disable       : {result['simultaneous']*100:6.2f} %")
    if result["unresolved"] > 1e-6:
        print(f"  Unresolved (> max shots)   : {result['unresolved']*100:6.2f} %")
    print(f"  Total resolved             : {total*100:6.2f} %")
    print(sep)
    for ev in result["events"]:
        c = (ev["cumulative_tank1_wins"] + ev["cumulative_tank2_wins"]
             + ev["cumulative_simultaneous"])
        if c >= 0.95:
            print(f"  95% resolved by: {ev['time']:.2f} s  (event {ev['event']})")
            break
    print()


def _fmt_pen(stats: Optional[Tuple[float, float, float]]) -> str:
    """Format (min, median, max) as 'med% [min-max]', or just 'med%' if no spread."""
    if stats is None:
        return "-"
    lo, med, hi = stats
    if abs(hi - lo) < 0.0005:
        return f"{med*100:.1f}%"
    return f"{med*100:.1f}% [{lo*100:.1f}-{hi*100:.1f}]"


def _fmt_pen_list(plist: Optional[List[Tuple[str, Tuple[float, float, float]]]]) -> str:
    """Format a list of (weapon_name, stats). Single entry omits the weapon name
    so single-weapon output matches the original layout."""
    if not plist:
        return "-"
    if len(plist) == 1:
        return _fmt_pen(plist[0][1])
    return ", ".join(f"{name}: {_fmt_pen(stats)}" for name, stats in plist)


def print_shot_log(tank1: Tank, tank2: Tank, result: dict, n: int = 15) -> None:
    """
    Print the first n shot events: time, shooter, pen% (median [min-max]
    across the active state distribution), cumulative win%.
    """
    w = max(len(tank1.name), len(tank2.name), 4)
    print(f"  First {n} shot events  ({result['range_m']} m  |  pen = median [min-max] across active states):")
    print(f"  {'#':>3}  {'Time(s)':>7}  {'Shooter':<{w}}  "
          f"{'Pen% on target':<28}  {'T1 win%':>8}  {'T2 win%':>8}  {'Undecided%':>10}")
    print("  " + "-" * (3+2+7+2+w+2+28+2+8+2+8+2+10))

    for ev in result["events"][:n]:
        if ev["tank1_fired"] and ev["tank2_fired"]:
            shooter = "BOTH"
            pen_str = f"vs {tank1.name}: {_fmt_pen_list(ev['pen_on_t1'])}  vs {tank2.name}: {_fmt_pen_list(ev['pen_on_t2'])}"
        elif ev["tank1_fired"]:
            shooter = tank1.name
            pen_str = f"vs {tank2.name}: {_fmt_pen_list(ev['pen_on_t2'])}"
        else:
            shooter = tank2.name
            pen_str = f"vs {tank1.name}: {_fmt_pen_list(ev['pen_on_t1'])}"

        print(f"  {ev['event']:>3}  {ev['time']:>7.2f}  {shooter:<{w}}  "
              f"{pen_str:<28}  "
              f"{ev['cumulative_tank1_wins']*100:>8.2f}  "
              f"{ev['cumulative_tank2_wins']*100:>8.2f}  "
              f"{ev['remaining_probability']*100:>10.2f}")
    print()


# ---------------------------------------------------------------------------
# Tank library loader
# ---------------------------------------------------------------------------

DEFAULT_TANKS_FILE = Path(__file__).with_name("tanks.json")


def _make_weapon(w: dict) -> Weapon:
    return Weapon(
        name=w["name"],
        damage_health=w["damage_health"],
        damage_armor=w["damage_armor"],
        pen_mod=w["pen_mod"],
        reload_time=w["reload_time"],
        fire_cooldown=w["fire_cooldown"],
        ammo_capacity=w.get("ammo_capacity", 1),
    )


def load_tanks(path: Path = DEFAULT_TANKS_FILE) -> Dict[str, Tank]:
    """Load tank definitions from a JSON file. Keys are lookup IDs.

    Each entry must provide either a single "weapon" dict or a "weapons"
    list. Single-weapon tanks may use either form.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tanks: Dict[str, Tank] = {}
    for key, entry in data.items():
        if "weapons" in entry:
            weapons = [_make_weapon(w) for w in entry["weapons"]]
        else:
            weapons = [_make_weapon(entry["weapon"])]
        tanks[key] = Tank(
            name=entry["name"],
            base_health=entry["base_health"],
            base_armor=entry["base_armor"],
            base_pen_chance=entry["base_pen_chance"],
            pre_bonus_cap=entry["pre_bonus_cap"],
            weapons=weapons,
        )
    return tanks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    library = load_tanks()
    tank_keys = sorted(library.keys())

    parser = argparse.ArgumentParser(
        description="Simulate a 1v1 Foxhole tank duel.",
        epilog=f"Available tanks: {', '.join(tank_keys)}",
    )
    parser.add_argument("tank1", help="lookup key of tank 1 (see list below)")
    parser.add_argument("armor1", type=float, help="tank 1 initial armor fraction (0.0-1.0)")
    parser.add_argument("tank2", help="lookup key of tank 2")
    parser.add_argument("armor2", type=float, help="tank 2 initial armor fraction (0.0-1.0)")
    parser.add_argument("range_m", type=float, help="engagement range in metres")
    parser.add_argument("--shots", type=int, default=15, help="shot-log length (default 15)")
    parser.add_argument("--tanks-file", type=Path, default=DEFAULT_TANKS_FILE,
                        help=f"path to tanks JSON (default {DEFAULT_TANKS_FILE.name})")
    args = parser.parse_args(argv)

    if args.tanks_file != DEFAULT_TANKS_FILE:
        library = load_tanks(args.tanks_file)

    for key in (args.tank1, args.tank2):
        if key not in library:
            parser.error(f"unknown tank '{key}'. Available: {', '.join(sorted(library))}")

    t1 = library[args.tank1]
    t2 = library[args.tank2]

    result = simulate_duel(
        tank1=t1,
        tank2=t2,
        initial_armor_frac1=args.armor1,
        initial_armor_frac2=args.armor2,
        range_m=args.range_m,
    )
    print_duel_summary(t1, t2, result)
    print_shot_log(t1, t2, result, n=args.shots)
    return 0


if __name__ == "__main__":
    sys.exit(main())
