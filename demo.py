"""Run the LifeLine bike-share story and write ``station.json``."""

from __future__ import annotations

import json
from pathlib import Path

from models import Bike, Station

STATION_FILE = Path(__file__).with_name("station.json")


def run_story(output_path: Path = STATION_FILE) -> Station:
    """Rent and return a bike, then save the station's final state."""
    station = Station(
        name="Central Station",
        capacity=3,
        bikes=[Bike("LIFE-001"), Bike("LIFE-002")],
    )
    bike = station.rent_bike()
    station.add_bike(bike)
    output_path.write_text(
        json.dumps(station.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    return station


def main() -> None:
    """Run the story and report its result."""
    station = run_story()
    print(
        f"{station.name}: {station.available_bikes}/{station.capacity} "
        f"bikes available"
    )


if __name__ == "__main__":
    main()
