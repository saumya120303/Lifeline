import json

import pytest

from demo import run_story
from models import Bike, BikeUnavailableError, Station, StationFullError


def test_station_can_rent_and_return_a_bike():
    station = Station("Central", 2, [Bike("LIFE-001")])

    bike = station.rent_bike()
    assert bike.bike_id == "LIFE-001"
    assert station.available_bikes == 0

    station.add_bike(bike)
    assert station.available_bikes == 1


def test_renting_from_an_empty_station_raises():
    station = Station("Central", 1)

    with pytest.raises(BikeUnavailableError):
        station.rent_bike()


def test_returning_to_a_full_station_raises():
    station = Station("Central", 1, [Bike("LIFE-001")])

    with pytest.raises(StationFullError):
        station.add_bike(Bike("LIFE-002"))


def test_demo_writes_station_snapshot(tmp_path):
    output_path = tmp_path / "station.json"

    station = run_story(output_path)

    assert station.available_bikes == 2
    assert json.loads(output_path.read_text(encoding="utf-8"))["name"] == (
        "Central Station"
    )
