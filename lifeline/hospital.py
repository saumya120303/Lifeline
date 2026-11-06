"""A Hospital and where it is.

Hospital *has* Units (composition): it owns a private dict of them and is the
only place units are added or removed. It refuses to remove a unit that is
held or confirmed, because that would break a promise made to a family.

Location is a frozen @dataclass: plain facts (city, coordinates) that never
change. The coordinates are there for the stretch goal (a map view).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from .exceptions import DuplicateError, UnitUnavailableError, UnknownUnitError, ValidationError
from .resources import ResourceType, Unit, UnitStatus, unit_from_dict


@dataclass(frozen=True)
class Location:
    city: str
    latitude: float | None = None
    longitude: float | None = None

    def __post_init__(self) -> None:
        if not self.city or not self.city.strip():
            raise ValidationError("a location needs a city")
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            raise ValidationError(f"latitude {self.latitude} is out of range")
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            raise ValidationError(f"longitude {self.longitude} is out of range")

    def in_city(self, city: str) -> bool:
        """Case- and space-insensitive city match ('pittsburgh ' == 'Pittsburgh')."""
        return self.city.strip().casefold() == city.strip().casefold()

    def to_dict(self) -> dict[str, Any]:
        return {"city": self.city, "latitude": self.latitude, "longitude": self.longitude}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Location:
        return cls(data["city"], data.get("latitude"), data.get("longitude"))


class Hospital:
    """A hospital with a name, a location and the units it owns."""

    def __init__(self, hospital_id: str, name: str, location: Location) -> None:
        if not hospital_id or not name:
            raise ValidationError("a hospital needs an id and a name")
        self._hospital_id = hospital_id
        self._name = name
        self._location = location
        self._units: dict[str, Unit] = {}  # unit_id -> Unit (owned, private)

    # --- read-only properties -------------------------------------------------
    @property
    def hospital_id(self) -> str:
        return self._hospital_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def location(self) -> Location:
        return self._location

    @property
    def city(self) -> str:
        return self._location.city

    # --- managing the owned units ---------------------------------------------
    def add_unit(self, unit: Unit) -> None:
        if unit.hospital_id != self._hospital_id:
            raise ValidationError(
                f"unit {unit.unit_id} belongs to {unit.hospital_id}, not {self._hospital_id}"
            )
        if unit.unit_id in self._units:
            raise DuplicateError(f"{self._name} already has a unit {unit.unit_id}")
        self._units[unit.unit_id] = unit

    def remove_unit(self, unit_id: str) -> Unit:
        """Take a unit out of stock. Only allowed while nobody holds it."""
        unit = self.get_unit(unit_id)
        if not unit.is_available:
            raise UnitUnavailableError(
                f"cannot remove {unit_id}: it is {unit.status.value}; "
                "only available units can be taken out of stock"
            )
        del self._units[unit_id]
        return unit

    def get_unit(self, unit_id: str) -> Unit:
        try:
            return self._units[unit_id]
        except KeyError:
            raise UnknownUnitError(f"{self._name} has no unit {unit_id!r}") from None

    def has_unit(self, unit_id: str) -> bool:
        return unit_id in self._units

    def units(self) -> list[Unit]:
        """A copy of the list, so callers cannot add or remove units behind our back."""
        return list(self._units.values())

    def find_available(self, resource_type: ResourceType, **criteria: Any) -> list[Unit]:
        """Available units that match. Each unit decides for itself whether it
        matches (polymorphism): no checks on which subclass it is."""
        return [
            unit for unit in self._units.values()
            if unit.is_available and unit.matches(resource_type, **criteria)
        ]

    def stock_summary(self) -> dict[str, dict[str, int]]:
        """{'icu_bed': {'available': 1, 'held': 1, 'confirmed': 0}, ...}"""
        summary = {rt.value: {st.value: 0 for st in UnitStatus} for rt in ResourceType}
        for unit in self._units.values():
            summary[unit.resource_type.value][unit.status.value] += 1
        return summary

    # --- dunder methods -------------------------------------------------------
    def __len__(self) -> int:
        return len(self._units)

    def __contains__(self, unit_id: object) -> bool:
        return unit_id in self._units

    def __iter__(self) -> Iterator[Unit]:
        return iter(list(self._units.values()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Hospital):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    __hash__ = None

    def __repr__(self) -> str:
        return f"Hospital({self._hospital_id!r}, {self._name!r}, {self.city}, {len(self)} units)"

    # --- JSON -----------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "hospital_id": self._hospital_id,
            "name": self._name,
            "location": self._location.to_dict(),
            "units": [unit.to_dict() for unit in self._units.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hospital:
        hospital = cls(data["hospital_id"], data["name"], Location.from_dict(data["location"]))
        for record in data["units"]:
            hospital.add_unit(unit_from_dict(record))
        return hospital
