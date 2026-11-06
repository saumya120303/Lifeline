"""Where LifeLine gets the current time.

Holds expire, so the Registry needs "now". Instead of calling
datetime.now() everywhere, the Registry *has a* Clock (composition) and
depends only on the promise "I can tell you now()" (an ABC):

* SystemClock  - the real time, used in M2/M3 by the server.
* ManualClock  - time you move by hand, so demo.py and the tests can show a
                 hold expiring without sleeping for 30 minutes.

Also holds the two helpers that turn datetimes into JSON strings and back.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from .exceptions import ValidationError


class Clock(ABC):
    """The promise: something that can report the current UTC time."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""


class SystemClock(Clock):
    """The real wall clock."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return "SystemClock()"


class ManualClock(Clock):
    """A clock that only moves when you call advance(); for demos and tests."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValidationError("ManualClock needs a timezone-aware start time")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, *, minutes: float = 0, seconds: float = 0) -> datetime:
        """Move time forward and return the new time."""
        if minutes < 0 or seconds < 0:
            raise ValidationError("time cannot go backwards")
        self._now += timedelta(minutes=minutes, seconds=seconds)
        return self._now

    def __repr__(self) -> str:
        return f"ManualClock({self._now.isoformat()})"


def to_iso(moment: datetime | None) -> str | None:
    """datetime -> ISO-8601 string for JSON (None stays None)."""
    return None if moment is None else moment.isoformat()


def from_iso(text: str | None) -> datetime | None:
    """ISO-8601 string from JSON -> timezone-aware datetime (None stays None)."""
    if text is None:
        return None
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValidationError(f"timestamp {text!r} has no timezone")
    return moment


def hhmm(moment: datetime) -> str:
    """Short display form, e.g. '14:30 UTC'."""
    return moment.astimezone(timezone.utc).strftime("%H:%M UTC")
