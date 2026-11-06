"""LifeLine M1 demo: run with `python demo.py` from this folder.

Tells one story on a single machine (no networking yet): families search,
hold, release; hospitals confirm and cancel; holds expire; invalid
operations are rejected with custom exceptions; state is saved to JSON and
loaded back. A ManualClock lets us jump forward in time instead of sleeping.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lifeline import (
    BloodUnit,
    JsonStore,
    LifeLineError,
    ManualClock,
    PrintNotifier,
    Registry,
    Unit,
)
from lifeline.seed import build_demo_registry

STATE_FILE = Path(__file__).with_name("lifeline_state.json")


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 66 - len(title)))


def attempt(description: str, action: Callable[[], Any]) -> Any:
    """Run one operation; print OK, or the custom exception that rejected it."""
    try:
        result = action()
    except LifeLineError as error:
        print(f"  REJECTED  {description}\n            -> {type(error).__name__}: {error}")
        return None
    print(f"  OK        {description}")
    return result


def show_units(units: list[Unit]) -> None:
    if not units:
        print("            (none available)")
    for unit in units:
        print(f"            {unit.unit_id:<10} {unit.label():<26} at {unit.hospital_id}")


def show_stock(registry: Registry, city: str) -> None:
    for name, summary in registry.stock_summary(city).items():
        parts = []
        for resource, counts in summary.items():
            total = sum(counts.values())
            parts.append(f"{resource} {counts['available']}/{total}")
        print(f"    {name:<24} available: " + ", ".join(parts))


def main() -> None:
    clock = ManualClock(datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc))
    registry = build_demo_registry(clock=clock, notifier=PrintNotifier())
    print(f"LifeLine M1 demo at {clock.now():%Y-%m-%d %H:%M} UTC")
    print(f"  {registry}")

    section("1. Polymorphism: each kind of unit keeps the same promise its own way")
    # One loop over the abstract Unit interface; no checks on which subclass.
    for unit in registry.get_hospital("TRG").units():
        print(f"    {type(unit).__name__:<15} {unit.label():<26} hold lasts {unit.hold_minutes()} min")

    section("2. Log in (passwords are stored only as salted hashes)")
    attempt("asha logs in", lambda: registry.authenticate("asha", "asha-pass"))
    attempt("ben logs in with the wrong password",
            lambda: registry.authenticate("ben", "guess123"))

    section("3. Search")
    print("  ICU beds with a ventilator in Pittsburgh:")
    show_units(registry.search("icu_bed", "Pittsburgh", ventilator=True))
    print("  O+ blood in Pittsburgh:")
    show_units(registry.search("blood_unit", "Pittsburgh", blood_group="O+"))
    print("  Blood that is safe for an A+ patient in Pittsburgh:")
    show_units(registry.search("blood_unit", "pittsburgh", for_patient="A+"))
    attempt("search for an unknown resource type", lambda: registry.search("helicopter"))

    section("4. The M0 conflict: two families want the last O+ unit")
    asha_blood = attempt("asha holds TRG-BLD-1 (last O+)",
                         lambda: registry.place_hold("asha", "TRG-BLD-1"))
    attempt("ben holds TRG-BLD-1 too", lambda: registry.place_hold("ben", "TRG-BLD-1"))
    print("  O+ blood in Pittsburgh now:")
    show_units(registry.search("blood_unit", "Pittsburgh", blood_group="O+"))

    section("5. Max 2 active holds per requester")
    asha_icu = attempt("asha holds TRG-ICU-1", lambda: registry.place_hold("asha", "TRG-ICU-1"))
    attempt("asha holds a third unit OMC-ICU-1",
            lambda: registry.place_hold("asha", "OMC-ICU-1"))
    attempt("hospital staff try to place a hold",
            lambda: registry.place_hold("trg_staff", "TRG-ICU-2"))

    section("6. Release (requester only, own active hold)")
    attempt(f"ben releases asha's hold {asha_icu.hold_id}",
            lambda: registry.release_hold("ben", asha_icu.hold_id))
    attempt(f"asha releases {asha_icu.hold_id}",
            lambda: registry.release_hold("asha", asha_icu.hold_id))
    print(f"  asha now has {registry.active_hold_count('asha')} active hold(s); "
          "TRG-ICU-1 is searchable again:")
    show_units(registry.search("icu_bed", "Pittsburgh", ventilator=True))

    section("7. Confirm (staff of that hospital only)")
    attempt(f"Oakland staff confirm {asha_blood.hold_id} (a Three Rivers unit)",
            lambda: registry.confirm_hold("omc_staff", asha_blood.hold_id))
    attempt(f"Three Rivers staff confirm {asha_blood.hold_id}",
            lambda: registry.confirm_hold("trg_staff", asha_blood.hold_id))
    attempt(f"asha releases the confirmed {asha_blood.hold_id}",
            lambda: registry.release_hold("asha", asha_blood.hold_id))
    attempt(f"asha (a requester) tries to cancel {asha_blood.hold_id}",
            lambda: registry.cancel("asha", asha_blood.hold_id))

    section("8. Hospitals change stock")
    attempt("Three Rivers removes TRG-BLD-1 while it is confirmed",
            lambda: registry.remove_unit("trg_staff", "TRG-BLD-1"))
    attempt("Three Rivers adds a new O+ unit TRG-BLD-4",
            lambda: registry.add_unit("trg_staff", BloodUnit("TRG-BLD-4", "TRG", blood_group="O+")))
    attempt("Oakland staff add a unit to Three Rivers",
            lambda: registry.add_unit("omc_staff", BloodUnit("TRG-BLD-5", "TRG", blood_group="B-")))
    print("  O+ blood in Pittsburgh now:")
    show_units(registry.search("blood_unit", "Pittsburgh", blood_group="O+"))

    section("9. Expiry, and confirm at the exact moment a hold expires")
    ben_oxygen = attempt("ben holds TRG-OX-1 (oxygen: 60 min hold)",
                         lambda: registry.place_hold("ben", "TRG-OX-1"))
    clock.advance(minutes=45)
    print(f"  ...45 minutes later ({clock.now():%H:%M}): "
          f"{registry.get_hold(ben_oxygen.hold_id).minutes_left(clock.now()):.0f} min left")
    clock.advance(minutes=15)
    print(f"  ...exactly at the expiry time ({clock.now():%H:%M}) staff try to confirm:")
    attempt(f"Three Rivers confirms {ben_oxygen.hold_id}",
            lambda: registry.confirm_hold("trg_staff", ben_oxygen.hold_id))
    print(f"  {ben_oxygen.hold_id} is now {registry.get_hold(ben_oxygen.hold_id).status.value}; "
          "oxygen in Pittsburgh with at least 600 L:")
    show_units(registry.search("oxygen_cylinder", "Pittsburgh", min_litres=600))

    section("10. Cancel a confirmation (hospital only)")
    attempt(f"Three Rivers cancels {asha_blood.hold_id}",
            lambda: registry.cancel("trg_staff", asha_blood.hold_id, "patient transferred"))
    attempt(f"Three Rivers cancels {asha_blood.hold_id} again",
            lambda: registry.cancel("trg_staff", asha_blood.hold_id))

    section("11. Invariant check")
    registry.check_invariants()
    print("  OK        every unit has at most one active hold or confirmation")
    print(f"  {registry}")

    section("12. Save to JSON and load it back")
    store = JsonStore(STATE_FILE)
    store.save(registry)
    print(f"  saved     {STATE_FILE.name} ({STATE_FILE.stat().st_size:,} bytes)")
    loaded = store.load(clock=clock)
    print(f"  loaded    {loaded}")
    print(f"  identical to the saved state: {loaded == registry}")
    attempt("loaded copy: chen holds TRG-BLD-4",
            lambda: loaded.place_hold("chen", "TRG-BLD-4"))
    attempt("loaded copy: dana holds TRG-BLD-4", lambda: loaded.place_hold("dana", "TRG-BLD-4"))
    bad_file = STATE_FILE.with_name("broken_state.json")
    bad_file.write_text("{ this is not json")
    attempt("load a corrupted file", lambda: JsonStore(bad_file).load())
    bad_file.unlink()

    section("13. Live stock in Pittsburgh (data for the stretch-goal map)")
    show_stock(registry, "Pittsburgh")

    section("14. Audit log (last 8 events)")
    for event in registry.events()[-8:]:
        print(f"    {event}")


if __name__ == "__main__":
    main()
