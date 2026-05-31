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
    name           : label
    damage_health  : HP removed from target per penetrating hit
    damage_armor   : armor-points removed per penetrating hit
    pen_mod        : penetration modifier (1.0 = baseline)
    reload_time    : seconds to reload after burst is spent, then after every shot
    fire_cooldown  : seconds between shots within a burst (and before reload)
    ammo_capacity  : pre-loaded shells at fight start (first-stage size); default 1
    disable_chance : DEFENSIVE stat. P(this weapon is disabled when its tank
                     takes a penetrating hit and this weapon is the current
                     last-to-first disable target). Ignored in HP mode.
    disable_mod    : OFFENSIVE multiplier applied to the defender weapon's
                     disable_chance on this weapon's penetrating hits
                     (default 1.0). Affects only the disable roll — pen
                     probability is unchanged (use pen_mod for that).
                     Effective dc = clamp(defender.disable_chance *
                     attacker.disable_mod, 0, 1). Ignored in HP mode.
    damage_type    : shell type for armor-class damage scaling. Looked up in
                     _GAME_DAMAGE_PCT — known values: "ap", "explosive",
                     "at_kinetic". Default "ap" (the no-op type: 1.0 against
                     both armor classes). The looked-up percentage scales
                     both damage_armor and damage_health on penetration; it
                     does NOT affect pen probability or the disable roll.
                     damage_health/damage_armor are RAW values (pre-multiplier).
    reload_mode    : firing schedule semantics — "per_shot" (default) reloads
                     between every shot after the first burst; "per_magazine"
                     fires all `ammo_capacity` shots quickly, then reloads the
                     whole magazine at once. Irrelevant when ammo_capacity=1.
    """
    name: str
    damage_health: int
    damage_armor: int
    pen_mod: float
    reload_time: float
    fire_cooldown: float
    disable_chance: float
    ammo_capacity: int = 1
    disable_mod: float = 1.0
    damage_type: str = "ap"
    reload_mode: str = "per_shot"


@dataclass
class Tank:
    """
    name            : label
    base_health     : total HP at full health
    base_armor      : total armor-points at full armor
    base_pen_chance : P0 - minimum pen chance at full armor (lower = harder tank)
    pre_bonus_cap   : S0 - ceiling on pre-bonus pen value (lower = harder tank)
    weapons         : list of Weapons; each fires on its own schedule
    armor_class     : "heavy" (default) or "light". Selects which column of
                      _GAME_DAMAGE_PCT scales incoming damage. Does not
                      affect pen probability.
    """
    name: str
    base_health: int
    base_armor: int
    base_pen_chance: float
    pre_bonus_cap: float
    weapons: List[Weapon]
    armor_class: str = "heavy"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

# Game-published damage percentage by (damage_type, armor_class).
# Applied to raw damage_armor and damage_health on a penetrating hit; does NOT
# affect pen probability or the disable roll. Missing entries fall back to 1.0
# (no scaling) — keep this table the single source of truth and add new entries
# rather than scattering multipliers elsewhere.
_GAME_DAMAGE_PCT: Dict[Tuple[str, str], float] = {
    ("ap",         "light"): 1.00, ("ap",         "heavy"): 1.00,
    ("explosive",  "light"): 0.85, ("explosive",  "heavy"): 0.85,
    ("at_kinetic", "light"): 0.65, ("at_kinetic", "heavy"): 0.15,
}


def damage_multiplier(damage_type: str, armor_class: str) -> float:
    """Game-published damage percentage for (damage_type, armor_class).
    Falls back to 1.0 for unknown combinations so untagged weapons or new
    armor classes behave as no-ops until the table is extended."""
    return _GAME_DAMAGE_PCT.get((damage_type, armor_class), 1.0)


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
    """Time at which shot n fires.

    per_shot (default):
        t(n) = (n-1)*fire_cooldown + max(0, n-ammo_capacity)*reload_time
        Each shot beyond the initial burst incurs its own reload_time.

    per_magazine:
        Empty the whole magazine, then one reload swaps it. Every shot
        (including the magazine's last) still incurs fire_cooldown before
        anything else can happen, so the reload only begins fc seconds after
        the last shot of the magazine.
        mag_idx     = (n-1) // ammo_capacity
        shot_in_mag = (n-1) %  ammo_capacity
        t(n) = mag_idx * (ammo_capacity*fire_cooldown + reload_time)
               + shot_in_mag * fire_cooldown
    """
    if weapon.reload_mode == "per_magazine":
        cap = weapon.ammo_capacity
        mag_idx = (shot_n - 1) // cap
        shot_in_mag = (shot_n - 1) % cap
        return (mag_idx * (cap * weapon.fire_cooldown + weapon.reload_time)
                + shot_in_mag * weapon.fire_cooldown)
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
                  max_shots_each: int = 300,
                  mode: str = "hp") -> dict:
    """
    Probabilistic 1v1 duel with independent firing schedules across all
    weapons on both tanks.

    mode = "hp"             : a tank loses when its HP drops below
                              disable_threshold * base_health (original behavior).
    mode = "weapon-disable" : a tank loses when EITHER its HP falls below
                              threshold OR all of its weapons are disabled.
                              On every penetrating hit, the DEFENDER's
                              highest-index live weapon rolls its own
                              `disable_chance`; on success that weapon is
                              disabled. Within one tick, sequential pens
                              cascade — once weapon[n-d-1] falls, the next
                              pen targets weapon[n-d-2].

    fires1 = tuple of tank1-weapon indices firing this tick (empty = none)
    fires2 = tuple of tank2-weapon indices firing this tick
    pen_on_t1 = list of (weapon_name, (min, median, max)) per tank2-weapon
                that fired at tank1 this tick
    pen_on_t2 = same for tank1-weapons firing at tank2
    """
    if mode not in ("hp", "weapon-disable"):
        raise ValueError(f"unknown mode '{mode}'; expected 'hp' or 'weapon-disable'")
    range_bonus = range_to_bonus(range_m)

    # Per-hit losses indexed by attacker-weapon index. damage_type × armor_class
    # multiplier scales raw weapon damage to the effective damage actually
    # dealt to this defender.
    mult_on_t1 = [damage_multiplier(w.damage_type, tank1.armor_class) for w in tank2.weapons]
    mult_on_t2 = [damage_multiplier(w.damage_type, tank2.armor_class) for w in tank1.weapons]
    armor_loss_on_t1 = [w.damage_armor * velocity_mod * mult_on_t1[i] / tank1.base_armor
                        for i, w in enumerate(tank2.weapons)]
    armor_loss_on_t2 = [w.damage_armor * velocity_mod * mult_on_t2[i] / tank2.base_armor
                        for i, w in enumerate(tank1.weapons)]
    health_loss_on_t1 = [w.damage_health * velocity_mod * mult_on_t1[i]
                         for i, w in enumerate(tank2.weapons)]
    health_loss_on_t2 = [w.damage_health * velocity_mod * mult_on_t2[i]
                         for i, w in enumerate(tank1.weapons)]

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

    if mode == "hp":
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
            "tank1_wins_by_hp": p1w, "tank1_wins_by_disable": 0.0,
            "tank2_wins_by_hp": p2w, "tank2_wins_by_disable": 0.0,
            "unresolved": sum(active.values()),
            "events": events_data,
            "range_m": range_m,
            "initial_armor_frac1": initial_armor_frac1,
            "initial_armor_frac2": initial_armor_frac2,
            "mode": mode,
        }

    # -----------------------------------------------------------------------
    # Weapon-disable mode (parallel implementation; state carries d1, d2)
    # -----------------------------------------------------------------------
    n1 = len(tank1.weapons)
    n2 = len(tank2.weapons)
    # Disable_chance is a DEFENDER-weapon property: tank X's own weapons each
    # have a disable_chance that rolls when tank X takes a penetrating hit.
    dc_t1_weapons = [w.disable_chance for w in tank1.weapons]
    dc_t2_weapons = [w.disable_chance for w in tank2.weapons]

    initial_state_wd: Tuple[Tuple[int, ...], Tuple[int, ...], int, int] = (
        (0,) * n2,
        (0,) * n1,
        0,
        0,
    )
    active_wd: Dict[Tuple[Tuple[int, ...], Tuple[int, ...], int, int], float] = {
        initial_state_wd: 1.0
    }
    p1w = p2w = psim = 0.0
    p1w_hp = p1w_dis = p2w_hp = p2w_dis = 0.0
    events_data_wd: List[dict] = []

    for idx, (t, fires1, fires2) in enumerate(event_schedule, 1):
        if not active_wd:
            break

        # Pen stats restricted to states where the firing weapon is still live
        pen_on_t1: Optional[List[Tuple[str, Tuple[float, float, float]]]] = None
        pen_on_t2: Optional[List[Tuple[str, Tuple[float, float, float]]]] = None
        if fires2:
            pen_on_t1 = []
            for wi in fires2:
                w = tank2.weapons[wi]
                pairs = [
                    (compute_pen_chance(tank1, w, af1(hits1), range_bonus), prob)
                    for (hits1, _, _, d2_s), prob in active_wd.items()
                    if wi < n2 - d2_s
                ]
                pen_on_t1.append((w.name, _pen_stats(pairs)))
        if fires1:
            pen_on_t2 = []
            for wi in fires1:
                w = tank1.weapons[wi]
                pairs = [
                    (compute_pen_chance(tank2, w, af2(hits2), range_bonus), prob)
                    for (_, hits2, d1_s, _), prob in active_wd.items()
                    if wi < n1 - d1_s
                ]
                pen_on_t2.append((w.name, _pen_stats(pairs)))

        # tank2-then-tank1 firing order, matching HP mode's multiplication order
        firing_global: List[Tuple[int, int]] = []
        for wi in fires2:
            firing_global.append((2, wi))
        for wi in fires1:
            firing_global.append((1, wi))

        new_active_wd: Dict[Tuple[Tuple[int, ...], Tuple[int, ...], int, int], float] = {}
        ev1 = ev2 = evsim = 0.0

        for (hits1, hits2, d1_s, d2_s), prob in active_wd.items():
            # Drop firings whose weapon is already disabled in this state
            firing = [
                (s, wi) for (s, wi) in firing_global
                if (wi < (n1 if s == 1 else n2) - (d1_s if s == 1 else d2_s))
            ]

            if not firing:
                key = (hits1, hits2, d1_s, d2_s)
                new_active_wd[key] = new_active_wd.get(key, 0.0) + prob
                continue

            # Pen rolls use pre-shot armor (consistent with HP mode for simultaneous
            # fire). Disable_chance is a DEFENDER-weapon property: on a penetrating
            # hit, the defender's highest-index live weapon rolls its own
            # disable_chance. Within a tick this cascades — if weapon[n-d-1] gets
            # disabled, the next pen on that defender targets weapon[n-d-2].
            pen_probs: List[float] = []
            for side, wi in firing:
                if side == 1:
                    pen_probs.append(compute_pen_chance(
                        tank2, tank1.weapons[wi], af2(hits2), range_bonus))
                else:
                    pen_probs.append(compute_pen_chance(
                        tank1, tank2.weapons[wi], af1(hits1), range_bonus))

            # Per firing weapon: 0=miss, 1=pen-no-disable, 2=pen-and-disable
            for outcome in product((0, 1, 2), repeat=len(firing)):
                p_branch = prob
                nh1 = list(hits1)
                nh2 = list(hits2)
                nd1 = d1_s
                nd2 = d2_s
                invalid_branch = False
                for (side, wi), pen, o in zip(firing, pen_probs, outcome):
                    # Defender's top-live-weapon disable_chance, scaled by the
                    # attacker weapon's disable_mod and clamped. Recomputed per
                    # shot because the cascade can advance mid-tick.
                    if side == 1:  # firing at tank2
                        dc_def = dc_t2_weapons[n2 - nd2 - 1] if nd2 < n2 else 0.0
                        dc_mod = tank1.weapons[wi].disable_mod
                    else:          # firing at tank1
                        dc_def = dc_t1_weapons[n1 - nd1 - 1] if nd1 < n1 else 0.0
                        dc_mod = tank2.weapons[wi].disable_mod
                    dc_now = max(0.0, min(1.0, dc_def * dc_mod))

                    if o == 0:
                        p_branch *= (1.0 - pen)
                    elif o == 1:
                        p_branch *= pen * (1.0 - dc_now)
                        if side == 1:
                            nh2[wi] += 1
                        else:
                            nh1[wi] += 1
                    else:  # o == 2: pen-and-disable
                        if dc_now <= 0.0:
                            # Disable was impossible (no live weapons left or dc=0);
                            # this outcome branch has prob 0, skip it.
                            invalid_branch = True
                            break
                        p_branch *= pen * dc_now
                        if side == 1:
                            nh2[wi] += 1
                            nd2 = min(n2, nd2 + 1)
                        else:
                            nh1[wi] += 1
                            nd1 = min(n1, nd1 + 1)

                if invalid_branch:
                    continue
                if p_branch < prob_cutoff:
                    continue

                nh1_t = tuple(nh1)
                nh2_t = tuple(nh2)
                hp1_down = dis1(nh1_t)
                hp2_down = dis2(nh2_t)
                d1_full = (nd1 == n1)
                d2_full = (nd2 == n2)
                t1_lost = hp1_down or d1_full
                t2_lost = hp2_down or d2_full

                if t1_lost and t2_lost:
                    evsim += p_branch
                    psim += p_branch
                elif t1_lost:
                    ev2 += p_branch
                    p2w += p_branch
                    if hp1_down:
                        p2w_hp += p_branch
                    else:
                        p2w_dis += p_branch
                elif t2_lost:
                    ev1 += p_branch
                    p1w += p_branch
                    if hp2_down:
                        p1w_hp += p_branch
                    else:
                        p1w_dis += p_branch
                else:
                    key = (nh1_t, nh2_t, nd1, nd2)
                    new_active_wd[key] = new_active_wd.get(key, 0.0) + p_branch

        active_wd = new_active_wd
        events_data_wd.append({
            "event": idx, "time": t,
            "tank1_fired": bool(fires1), "tank2_fired": bool(fires2),
            "pen_on_t1": pen_on_t1,
            "pen_on_t2": pen_on_t2,
            "tank1_wins_this_event": ev1, "tank2_wins_this_event": ev2,
            "simultaneous_this_event": evsim,
            "cumulative_tank1_wins": p1w, "cumulative_tank2_wins": p2w,
            "cumulative_simultaneous": psim,
            "remaining_probability": sum(active_wd.values()),
            "active_states": len(active_wd),
        })

    return {
        "tank1_wins": p1w, "tank2_wins": p2w, "simultaneous": psim,
        "tank1_wins_by_hp": p1w_hp, "tank1_wins_by_disable": p1w_dis,
        "tank2_wins_by_hp": p2w_hp, "tank2_wins_by_disable": p2w_dis,
        "unresolved": sum(active_wd.values()),
        "events": events_data_wd,
        "range_m": range_m,
        "initial_armor_frac1": initial_armor_frac1,
        "initial_armor_frac2": initial_armor_frac2,
        "mode": mode,
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
    if result.get("mode") == "weapon-disable":
        print(f"  -- by trigger --")
        print(f"  {tank1.name:<22} wins by HP      : {result['tank1_wins_by_hp']*100:6.2f} %")
        print(f"  {tank1.name:<22} wins by disable : {result['tank1_wins_by_disable']*100:6.2f} %")
        print(f"  {tank2.name:<22} wins by HP      : {result['tank2_wins_by_hp']*100:6.2f} %")
        print(f"  {tank2.name:<22} wins by disable : {result['tank2_wins_by_disable']*100:6.2f} %")
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
    if "disable_chance" not in w:
        raise KeyError(
            f"weapon '{w.get('name', '<unnamed>')}' is missing required "
            f"field 'disable_chance' (float, 0.0-1.0)"
        )
    return Weapon(
        name=w["name"],
        damage_health=w["damage_health"],
        damage_armor=w["damage_armor"],
        pen_mod=w["pen_mod"],
        reload_time=w["reload_time"],
        fire_cooldown=w["fire_cooldown"],
        disable_chance=w["disable_chance"],
        ammo_capacity=w.get("ammo_capacity", 1),
        disable_mod=w.get("disable_mod", 1.0),
        damage_type=w.get("damage_type", "ap"),
        reload_mode=w.get("reload_mode", "per_shot"),
    )


def _is_tank_entry(entry: dict) -> bool:
    """A tank entry has 'weapon' or 'weapons'. A faction bucket has neither
    at its top level (its values are tank entries)."""
    return isinstance(entry, dict) and ("weapon" in entry or "weapons" in entry)


def _flatten_factions(data: dict) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Return (key -> raw entry, key -> faction). Accepts either:
      - flat:    { tank_key: entry, ... }                (legacy / test file)
      - nested:  { faction: { tank_key: entry, ... } }   (current main library)
    """
    if not data:
        return {}, {}
    sample = next(iter(data.values()))
    if _is_tank_entry(sample):
        return dict(data), {k: "" for k in data}
    flat: Dict[str, dict] = {}
    factions: Dict[str, str] = {}
    for faction, bucket in data.items():
        if not isinstance(bucket, dict):
            raise ValueError(f"faction '{faction}' is not an object")
        for key, entry in bucket.items():
            if key in flat:
                raise ValueError(f"tank key '{key}' appears in multiple factions")
            flat[key] = entry
            factions[key] = faction
    return flat, factions


def load_tanks(path: Path = DEFAULT_TANKS_FILE) -> Dict[str, Tank]:
    """Load tank definitions from a JSON file. Keys are lookup IDs.

    The JSON may be either a flat `{ tank_key: entry, ... }` dict (legacy)
    or grouped by faction: `{ faction: { tank_key: entry, ... } }`. Each
    tank entry must provide either a single "weapon" dict or a "weapons"
    list.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    flat, _factions = _flatten_factions(data)
    tanks: Dict[str, Tank] = {}
    for key, entry in flat.items():
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
            armor_class=entry.get("armor_class", "heavy"),
        )
    return tanks


def load_factions(path: Path = DEFAULT_TANKS_FILE) -> Dict[str, str]:
    """Return `{tank_key: faction_name}`. Empty-string faction for flat-shape
    JSON files (the test library has no factions)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _flat, factions = _flatten_factions(data)
    return factions


# ---------------------------------------------------------------------------
# Sanity-check test suite
# ---------------------------------------------------------------------------

TEST_TANKS_FILE = Path(__file__).with_name("tanks_test.json")

# Each entry: (name, tank1_key, armor1, tank2_key, armor2, range_m, mode,
#              shots_to_show, description). Order matters — it determines
# the output order, which the user diffs against a known-good copy.
_TEST_SCENARIOS: List[Tuple[str, str, float, str, float, float, str, int, str]] = [
    (
        "hp_tiny_one_shot_each",
        "toy_5hp", 1.0, "toy_5hp", 1.0, 30.0, "hp", 8,
        "5 HP, 1-dmg, both fire every 1.0s starting t=0. Symmetric race.",
    ),
    (
        "hp_tiny_speed_mismatch",
        "toy_5hp_fast", 1.0, "toy_5hp", 1.0, 30.0, "hp", 10,
        "Fast (fc=0.5s) vs slow (fc=1.0s). Fast should win clean.",
    ),
    (
        "disable_one_shot_glass",
        "toy_attacker", 1.0, "toy_5hp_glass", 1.0, 30.0, "weapon-disable", 4,
        "Attacker fires; glass target has dc=1.0 and never fires. First pen wins.",
    ),
    (
        "disable_cascade_sequential_2weap",
        "toy_attacker", 1.0, "toy_2weap_cascade", 1.0, 30.0, "weapon-disable", 10,
        "1 attacker shot/tick vs inert 2-weapon target (dc=[0.5, 1.0]). "
        "Tick1: dc=1.0 always disables w1. Tick N: dc=0.5 on w0, geometric.",
    ),
    (
        "disable_cascade_simultaneous_3weap",
        "toy_3weap_attacker", 1.0, "toy_3weap_cascade", 1.0, 30.0, "weapon-disable", 6,
        "3 attacker weapons fire simultaneously at t=0,2,... vs inert 3-weapon "
        "target (dc=[0.0, 0.5, 1.0]). w0 (dc=0.0) never falls -> HP must finish it.",
    ),
]


def run_test_suite() -> int:
    """Run the sanity-check scenarios from tanks_test.json. Output is fully
    deterministic; pipe to a file and diff against a known-good copy to spot
    regressions.
    """
    library = load_tanks(TEST_TANKS_FILE)
    sep = "=" * 70

    for name, t1k, a1, t2k, a2, rng, mode, shots, desc in _TEST_SCENARIOS:
        print(sep)
        print(f"  TEST: {name}")
        print(f"  {desc}")
        print(sep)
        t1 = library[t1k]
        t2 = library[t2k]
        result = simulate_duel(
            tank1=t1, tank2=t2,
            initial_armor_frac1=a1, initial_armor_frac2=a2,
            range_m=rng, mode=mode,
        )
        print_duel_summary(t1, t2, result)
        print_shot_log(t1, t2, result, n=shots)
        total = (result["tank1_wins"] + result["tank2_wins"]
                 + result["simultaneous"] + result["unresolved"])
        print(f"  prob conservation: {total:.10f}")
        print()
    return 0


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
    parser.add_argument("tank1", nargs="?", help="lookup key of tank 1 (see list below)")
    parser.add_argument("armor1", type=float, nargs="?",
                        help="tank 1 initial armor fraction (0.0-1.0)")
    parser.add_argument("tank2", nargs="?", help="lookup key of tank 2")
    parser.add_argument("armor2", type=float, nargs="?",
                        help="tank 2 initial armor fraction (0.0-1.0)")
    parser.add_argument("range_m", type=float, nargs="?",
                        help="engagement range in metres")
    parser.add_argument("--shots", type=int, default=15, help="shot-log length (default 15)")
    parser.add_argument("--tanks-file", type=Path, default=DEFAULT_TANKS_FILE,
                        help=f"path to tanks JSON (default {DEFAULT_TANKS_FILE.name})")
    parser.add_argument("--mode", choices=("hp", "weapon-disable"), default="hp",
                        help="win condition: 'hp' (default) or 'weapon-disable' "
                             "(losing all weapons also disables the tank)")
    parser.add_argument("--tests", action="store_true",
                        help="run the sanity-check suite from tanks_test.json "
                             "instead of a single duel; positional args ignored")
    args = parser.parse_args(argv)

    if args.tests:
        return run_test_suite()

    # Validate positionals (only required when not running --tests)
    missing = [
        name for name, val in [
            ("tank1", args.tank1), ("armor1", args.armor1),
            ("tank2", args.tank2), ("armor2", args.armor2),
            ("range_m", args.range_m),
        ] if val is None
    ]
    if missing:
        parser.error(f"missing required arguments: {', '.join(missing)} "
                     f"(use --tests to run the sanity-check suite instead)")

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
        mode=args.mode,
    )
    print_duel_summary(t1, t2, result)
    print_shot_log(t1, t2, result, n=args.shots)
    return 0


if __name__ == "__main__":
    sys.exit(main())
