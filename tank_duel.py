"""
tank_duel.py
============
Probabilistic simulation of a 1v1 tank duel in Foxhole.

Firing schedule:  t(n) = (n-1)*fire_cooldown + max(0, n-cap)*reload_time
Disable:          HP < disable_threshold * base_health  (default 30%)
State:            (h1, h2) — penetrating hits received by each tank

Naming convention (throughout):
  fires1 = True  →  tank1 fires (shoots AT tank2)
  fires2 = True  →  tank2 fires (shoots AT tank1)
  h1             →  hits absorbed by tank1  (from tank2's weapon)
  h2             →  hits absorbed by tank2  (from tank1's weapon)
  pen_on_t1      →  pen chance of tank2's weapon against tank1
  pen_on_t2      →  pen chance of tank1's weapon against tank2
"""

from __future__ import annotations
from dataclasses import dataclass
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
    weapon          : Weapon this tank fires
    """
    name: str
    base_health: int
    base_armor: int
    base_pen_chance: float
    pre_bonus_cap: float
    weapon: Weapon


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


def build_event_sequence(weapon1: Weapon, weapon2: Weapon,
                         max_shots_each: int = 300,
                         time_tol: float = 1e-9) -> List[Tuple[float, bool, bool]]:
    """
    Merge two shot schedules into a chronological list of (time, fires1, fires2).
    fires1=True when weapon1 fires; fires2=True when weapon2 fires.
    Shots within time_tol seconds are treated as simultaneous.
    """
    times1 = [shot_time(n, weapon1) for n in range(1, max_shots_each + 1)]
    times2 = [shot_time(n, weapon2) for n in range(1, max_shots_each + 1)]
    events: Dict[float, Tuple[bool, bool]] = {}

    def add(t: float, f1: bool, f2: bool) -> None:
        for existing in list(events.keys()):
            if abs(existing - t) <= time_tol:
                ef1, ef2 = events[existing]
                events[existing] = (ef1 or f1, ef2 or f2)
                return
        events[t] = (f1, f2)

    for t in times1:
        add(t, True, False)
    for t in times2:
        add(t, False, True)
    return sorted((t, f1, f2) for t, (f1, f2) in events.items())


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
    Probabilistic 1v1 duel with independent firing schedules.

    fires1=True → tank1 fires at tank2 → may increase h2
    fires2=True → tank2 fires at tank1 → may increase h1
    pen_on_t1   → (min, median, max) pen% of tank2.weapon against tank1
    pen_on_t2   → (min, median, max) pen% of tank1.weapon against tank2
    """
    range_bonus = range_to_bonus(range_m)

    # Armor/health lost per penetrating hit on each tank
    armor_loss_on_t1 = (tank2.weapon.damage_armor * velocity_mod) / tank1.base_armor
    armor_loss_on_t2 = (tank1.weapon.damage_armor * velocity_mod) / tank2.base_armor
    health_loss_on_t1 = tank2.weapon.damage_health * velocity_mod
    health_loss_on_t2 = tank1.weapon.damage_health * velocity_mod

    disable_hp1 = tank1.base_health * disable_threshold
    disable_hp2 = tank2.base_health * disable_threshold

    # Current armor fraction given number of hits absorbed
    def af1(h1):
        return max(0.0, initial_armor_frac1 - h1 * armor_loss_on_t1)

    def af2(h2):
        return max(0.0, initial_armor_frac2 - h2 * armor_loss_on_t2)

    def dis1(h1):
        return (tank1.base_health - h1 * health_loss_on_t1) < disable_hp1

    def dis2(h2):
        return (tank2.base_health - h2 * health_loss_on_t2) < disable_hp2

    # fires1=tank1 fires, fires2=tank2 fires
    event_schedule = build_event_sequence(tank1.weapon, tank2.weapon, max_shots_each)

    active: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    p1w = p2w = psim = 0.0
    events_data: List[dict] = []

    for idx, (t, fires1, fires2) in enumerate(event_schedule, 1):
        if not active:
            break

        # --- Pen chance stats across active states (computed before outcomes resolve) ---
        pen_on_t1: Optional[Tuple[float, float, float]] = None  # tank2 hits tank1
        pen_on_t2: Optional[Tuple[float, float, float]] = None  # tank1 hits tank2

        if fires2:  # tank2 fires AT tank1 → pen chance against tank1
            pen_on_t1 = _pen_stats([
                (compute_pen_chance(tank1, tank2.weapon, af1(h1), range_bonus), prob)
                for (h1, _), prob in active.items()
            ])
        if fires1:  # tank1 fires AT tank2 → pen chance against tank2
            pen_on_t2 = _pen_stats([
                (compute_pen_chance(tank2, tank1.weapon, af2(h2), range_bonus), prob)
                for (_, h2), prob in active.items()
            ])

        # --- Resolve outcomes ---
        new_active: Dict[Tuple[int, int], float] = {}
        ev1 = ev2 = evsim = 0.0

        for (h1, h2), prob in active.items():
            if fires1 and fires2:
                # Both fire simultaneously; use pre-shot armor for both rolls
                p_hit_t1 = compute_pen_chance(tank1, tank2.weapon, af1(h1), range_bonus)
                p_hit_t2 = compute_pen_chance(tank2, tank1.weapon, af2(h2), range_bonus)
                outcomes = [
                    (h1+1, h2+1, prob * p_hit_t1 * p_hit_t2),
                    (h1+1, h2,   prob * p_hit_t1 * (1-p_hit_t2)),
                    (h1,   h2+1, prob * (1-p_hit_t1) * p_hit_t2),
                    (h1,   h2,   prob * (1-p_hit_t1) * (1-p_hit_t2)),
                ]
            elif fires1:  # tank1 fires at tank2 → h2 may increase
                p_hit_t2 = compute_pen_chance(tank2, tank1.weapon, af2(h2), range_bonus)
                outcomes = [
                    (h1, h2+1, prob * p_hit_t2),
                    (h1, h2,   prob * (1-p_hit_t2)),
                ]
            else:          # tank2 fires at tank1 → h1 may increase
                p_hit_t1 = compute_pen_chance(tank1, tank2.weapon, af1(h1), range_bonus)
                outcomes = [
                    (h1+1, h2, prob * p_hit_t1),
                    (h1,   h2, prob * (1-p_hit_t1)),
                ]

            for nh1, nh2, p in outcomes:
                if p < prob_cutoff:
                    continue
                d1, d2 = dis1(nh1), dis2(nh2)
                if d1 and d2:
                    evsim += p; psim += p
                elif d1:
                    ev2 += p; p2w += p
                elif d2:
                    ev1 += p; p1w += p
                else:
                    new_active[(nh1, nh2)] = new_active.get((nh1, nh2), 0.0) + p

        active = new_active
        events_data.append({
            "event": idx, "time": t,
            "tank1_fired": fires1, "tank2_fired": fires2,
            "pen_on_t1": pen_on_t1,   # (min, median, max) — tank2 shoots at tank1
            "pen_on_t2": pen_on_t2,   # (min, median, max) — tank1 shoots at tank2
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
            # Both fire: show pen against each target
            pen_str = f"vs {tank1.name}: {_fmt_pen(ev['pen_on_t1'])}  vs {tank2.name}: {_fmt_pen(ev['pen_on_t2'])}"
        elif ev["tank1_fired"]:
            # tank1 fires at tank2
            shooter = tank1.name
            pen_str = f"vs {tank2.name}: {_fmt_pen(ev['pen_on_t2'])}"
        else:
            # tank2 fires at tank1
            shooter = tank2.name
            pen_str = f"vs {tank1.name}: {_fmt_pen(ev['pen_on_t1'])}"

        print(f"  {ev['event']:>3}  {ev['time']:>7.2f}  {shooter:<{w}}  "
              f"{pen_str:<28}  "
              f"{ev['cumulative_tank1_wins']*100:>8.2f}  "
              f"{ev['cumulative_tank2_wins']*100:>8.2f}  "
              f"{ev['remaining_probability']*100:>10.2f}")
    print()


# ---------------------------------------------------------------------------
# Quick usage example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bardiche_gun = Weapon(
        name="Bardiche 68mm",
        damage_health=600,
        damage_armor=600,
        pen_mod=1.5,
        reload_time=4.5,
        fire_cooldown=0.8,
        ammo_capacity=2,
    )
    bardiche = Tank(
        name="Bardiche",
        base_health=4000,
        base_armor=15650,
        base_pen_chance=0.23,
        pre_bonus_cap=0.67,
        weapon=bardiche_gun,
    )

    htd_gun = Weapon(
        name="HTD gun",
        damage_health=1050,
        damage_armor=1050,
        pen_mod=1.5,
        reload_time=4.5,
        fire_cooldown=2.0,
        ammo_capacity=1,
    )
    htd = Tank(
        name="HTD",
        base_health=2200,
        base_armor=17650,
        base_pen_chance=0.17,
        pre_bonus_cap=0.50,
        weapon=htd_gun,
    )
    update_bard_gun = Weapon(
        name="Update Bard 68mm",
        damage_health=600,
        damage_armor=600,
        pen_mod=1.5,
        reload_time=4.5,
        fire_cooldown=0.8,
        ammo_capacity=3,
    )
    update_bard = Tank(
        name="Update Bard",
        base_health=4000,
        base_armor=15650,
        base_pen_chance=0.23,
        pre_bonus_cap=0.67,
        weapon=update_bard_gun,
    )

    update_htd_gun = Weapon(
        name="Update HTD gun",
        damage_health=1050,
        damage_armor=1050,
        pen_mod=1.5,
        reload_time=4.5,
        fire_cooldown=2.0,
        ammo_capacity=1,
    )
    update_htd = Tank(
        name="Update HTD",
        base_health=2200,
        base_armor=17650,
        base_pen_chance=0.22,
        pre_bonus_cap=0.50,
        weapon=update_htd_gun,
    )

    spatha_gun = Weapon(
        name="spatha 40mm",
        damage_health=561,
        damage_armor=561,
        pen_mod=1.0,
        reload_time=3,
        fire_cooldown=1.5,
        ammo_capacity=1,
    )
    spatha = Tank(
        name="spatha",
        base_health=3650,
        base_armor=13550,
        base_pen_chance=0.33,
        pre_bonus_cap=0.67,
        weapon=spatha_gun,
    )

    outlaw_gun = Weapon(
        name="outlaw gun",
        damage_health=612,
        damage_armor=612,
        pen_mod=1.0,
        reload_time=5,
        fire_cooldown=2.0,
        ammo_capacity=1,
    )
    outlaw = Tank(
        name="outlaw",
        base_health=2950,
        base_armor=11000,
        base_pen_chance=0.33,
        pre_bonus_cap=0.67,
        weapon=outlaw_gun,
    )
    update_spatha_gun = Weapon(
        name="Update spatha 40mm",
        damage_health=510,
        damage_armor=510,
        pen_mod=1.0,
        reload_time=4,
        fire_cooldown=1.5,
        ammo_capacity=1,
    )
    update_spatha = Tank(
        name="Update spatha",
        base_health=3650,
        base_armor=10500,
        base_pen_chance=0.33,
        pre_bonus_cap=0.70,
        weapon=update_spatha_gun,
    )

    brigand_gun = Weapon(
        name="brigand 30mm",
        damage_health=340,
        damage_armor=340,
        pen_mod=1.0,
        reload_time=2.5,
        fire_cooldown=1.5,
        ammo_capacity=3,
    )
    
    brigand = Tank(
        name="brigand",
        base_health=2950,
        base_armor=11000,
        base_pen_chance=0.33,
        pre_bonus_cap=0.67,
        weapon=brigand_gun,
    )

    result = simulate_duel(
        tank1=spatha,
        tank2=brigand,
        initial_armor_frac1=0.7,
        initial_armor_frac2=0.7,
        range_m=30.0,
    )

    print_duel_summary(spatha, brigand, result)
    print_shot_log(spatha, brigand, result, n=5)

    result2 = simulate_duel(
        tank1=update_spatha,
        tank2=brigand,
        initial_armor_frac1=0.7,
        initial_armor_frac2=0.7,
        range_m=30.0,
    )

    print_duel_summary(update_spatha, brigand, result2)
    print_shot_log(update_spatha, brigand, result2, n=5)
