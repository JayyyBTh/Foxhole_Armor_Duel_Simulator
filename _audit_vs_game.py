"""
Throwaway audit script: diff tanks.json against the game's
BPVehicleDynamicData.json. Run once, eyeball output, delete.

Mappings (confirmed with user):
  base_health      <- MaxHealth
  base_armor       <- TankArmour
  base_pen_chance  <- TankArmourMinPenetrationChance
  pre_bonus_cap    <- 1 - MinTankArmourPercent
  weapons[i].disable_chance  <- VehicleSubsystemDisableChances[2+i]
"""
import json
from pathlib import Path

OURS = Path(__file__).with_name("tanks.json")
GAME = Path(r"C:\Users\JB\Downloads\BPVehicleDynamicData.json")


def game_rows():
    data = json.loads(GAME.read_text(encoding="utf-8"))
    # File is a list with one DataTable object
    return data[0]["Rows"]


def subsystem(row, i):
    key = "VehicleSubsystemDisableChances" if i == 0 else f"VehicleSubsystemDisableChances[{i}]"
    return row.get(key)


def almost_eq(a, b, tol=1e-9):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= tol


def fmt(v):
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def diff_line(label, ours, theirs):
    if almost_eq(ours, theirs):
        return None
    return f"    {label:<24} ours={fmt(ours):<10} game={fmt(theirs)}"


def main() -> int:
    ours = json.loads(OURS.read_text(encoding="utf-8"))
    rows = game_rows()

    any_diff = False
    total = 0
    missing = []

    for faction, tanks in ours.items():
        for key, entry in tanks.items():
            total += 1
            if key not in rows:
                missing.append(key)
                continue
            row = rows[key]
            our_pre_bonus_cap = entry["pre_bonus_cap"]
            game_pre_bonus_cap = 1.0 - row.get("MinTankArmourPercent", 0.0)

            diffs = []
            for line in (
                diff_line("base_health", entry["base_health"], row.get("MaxHealth")),
                diff_line("base_armor", entry["base_armor"], row.get("TankArmour")),
                diff_line("base_pen_chance",
                          entry["base_pen_chance"],
                          row.get("TankArmourMinPenetrationChance")),
                diff_line("pre_bonus_cap",
                          our_pre_bonus_cap, game_pre_bonus_cap),
            ):
                if line:
                    diffs.append(line)

            # Per-weapon disable_chance
            weapons = entry.get("weapons") or [entry["weapon"]]
            for i, w in enumerate(weapons):
                slot = subsystem(row, 2 + i)
                line = diff_line(f"weapons[{i}].disable_chance ({w['name']})",
                                 w["disable_chance"], slot)
                if line:
                    diffs.append(line)

            # Surface any non-zero subsystem slots we didn't consume
            extra_slots = []
            for i in range(2 + len(weapons), 7):
                slot = subsystem(row, i)
                if slot and slot > 0:
                    extra_slots.append(f"[{i}]={slot}")
            if extra_slots:
                diffs.append(f"    (game has extra non-zero subsystem slots: {', '.join(extra_slots)})")

            if diffs:
                any_diff = True
                print(f"\n[{faction}] {key} ({entry['name']})")
                for d in diffs:
                    print(d)

    print("\n" + "=" * 60)
    if missing:
        print(f"MISSING IN GAME DATA: {', '.join(missing)}")
    if not any_diff and not missing:
        print(f"All {total} tanks match the game data on the audited fields.")
    else:
        print(f"Audited {total} tanks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
