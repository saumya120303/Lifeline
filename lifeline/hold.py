"""A Hold: one requester's short reservation of one unit.

Hold is a @dataclass because it is mostly facts (who, which unit, when it
expires). It still guards its own life cycle: move_to() only allows the
transitions in _ALLOWED_MOVES, so a released hold can never be confirmed and
a confirmation can only be cancelled.

    ACTIVE ──confirm──> CONFIRMED ──cancel (hospital)──> CANCELLED
      │
      ├──release (requester)──> RELEASED
      ├──cancel (hospital)────> CANCELLED
      └──time runs out────────> EXPIRED

ACTIVE and CONFIRMED are "open": the unit is promised to this requester.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .clock import from_iso, to_iso
from .exceptions import InvalidStateError, ValidationError


class HoldStatus(str, Enum):
    ACTIVE = "active"
    CONFIRMED = "confirmed"
    RELEASED = "released"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


OPEN_STATUSES: frozenset[HoldStatus] = frozenset({HoldStatus.ACTIVE, HoldStatus.CONFIRMED})

_ALLOWED_MOVES: dict[HoldStatus, frozenset[HoldStatus]] = {
    HoldStatus.ACTIVE: frozenset({
        HoldStatus.CONFIRMED, HoldStatus.RELEASED, HoldStatus.CANCELLED, HoldStatus.EXPIRED,
    }),
    HoldStatus.CONFIRMED: frozenset({HoldStatus.CANCELLED}),
    # RELEASED, CANCELLED, EXPIRED are final: no moves out.
}


@dataclass
class Hold:
    hold_id: str
    unit_id: str
    hospital_id: str
    requester: str
    created_at: datetime
    expires_at: datetime
    status: HoldStatus = HoldStatus.ACTIVE
    confirmed_at: datetime | None = None
    closed_at: datetime | None = None
    closed_by: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        self.status = HoldStatus(self.status)
        if self.expires_at <= self.created_at:
            raise ValidationError(f"hold {self.hold_id} must expire after it is created")

    @property
    def is_open(self) -> bool:
        """True while the unit is promised to this requester."""
        return self.status in OPEN_STATUSES

    @property
    def is_active(self) -> bool:
        """True while the hold is waiting for the hospital to confirm."""
        return self.status is HoldStatus.ACTIVE

    def is_due_to_expire(self, now: datetime) -> bool:
        """Expiry boundary is inclusive: at exactly expires_at, the hold is over."""
        return self.status is HoldStatus.ACTIVE and now >= self.expires_at

    def minutes_left(self, now: datetime) -> float:
        if not self.is_active:
            return 0.0
        return max(0.0, (self.expires_at - now).total_seconds() / 60)

    def can_move_to(self, new_status: HoldStatus) -> bool:
        return new_status in _ALLOWED_MOVES.get(self.status, frozenset())

    def move_to(self, new_status: HoldStatus, *, now: datetime, by: str, reason: str = "") -> None:
        """Change status, refusing any transition not in _ALLOWED_MOVES."""
        if not self.can_move_to(new_status):
            raise InvalidStateError(
                f"hold {self.hold_id} cannot go from {self.status.value} to {new_status.value}"
            )
        self.status = new_status
        if new_status is HoldStatus.CONFIRMED:
            self.confirmed_at = now
        else:
            self.closed_at = now
            self.closed_by = by
            self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "hold_id": self.hold_id,
            "unit_id": self.unit_id,
            "hospital_id": self.hospital_id,
            "requester": self.requester,
            "created_at": to_iso(self.created_at),
            "expires_at": to_iso(self.expires_at),
            "status": self.status.value,
            "confirmed_at": to_iso(self.confirmed_at),
            "closed_at": to_iso(self.closed_at),
            "closed_by": self.closed_by,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hold:
        return cls(
            hold_id=data["hold_id"],
            unit_id=data["unit_id"],
            hospital_id=data["hospital_id"],
            requester=data["requester"],
            created_at=from_iso(data["created_at"]),
            expires_at=from_iso(data["expires_at"]),
            status=HoldStatus(data["status"]),
            confirmed_at=from_iso(data.get("confirmed_at")),
            closed_at=from_iso(data.get("closed_at")),
            closed_by=data.get("closed_by"),
            reason=data.get("reason", ""),
        )
