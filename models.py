"""Domain models for the LifeLine bike-share story."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


class BikeShareError(Exception):
    """Base exception for invalid bike-share operations."""


class BikeUnavailableError(BikeShareError):
    """Raised when a user tries to rent a bike that is not available."""


class StationFullError(BikeShareError):
    """Raised when a bike cannot be returned to a full station."""


@dataclass
class Bike:
    """A bike that can be rented from a station."""

    bike_id: str
    available: bool = True


@dataclass
class Station:
    """A bike-share station with a fixed capacity."""

    name: str
    capacity: int
    bikes: list[Bike] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("Station capacity must be positive")
        if len(self.bikes) > self.capacity:
            raise ValueError("Station cannot start above its capacity")

    def add_bike(self, bike: Bike) -> None:
        """Return a bike to this station."""
        if len(self.bikes) >= self.capacity:
            raise StationFullError(f"{self.name} is full")
        bike.available = True
        self.bikes.append(bike)

    def rent_bike(self) -> Bike:
        """Rent the first available bike from this station."""
        for bike in self.bikes:
            if bike.available:
                bike.available = False
                self.bikes.remove(bike)
                return bike
        raise BikeUnavailableError(f"No bikes are available at {self.name}")

    @property
    def available_bikes(self) -> int:
        """Return the number of bikes currently at the station."""
        return sum(bike.available for bike in self.bikes)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable station representation."""
        return asdict(self)
