"""An append-only audit log entry.

Every successful state change records one AuditEvent. In M3 the load script
can compare "how many hold events succeeded" against the final state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .clock import from_iso, hhmm, to_iso


@dataclass(frozen=True)
class AuditEvent:
    at: datetime
    action: str          # register, add_unit, remove_unit, hold, release, confirm, cancel, expire
    actor: str           # username, or "system" for expiry
    hold_id: str | None = None
    unit_id: str | None = None
    detail: str = ""

    def __str__(self) -> str:
        target = " ".join(part for part in (self.hold_id, self.unit_id) if part)
        return f"{hhmm(self.at)}  {self.action:<11} {self.actor:<10} {target:<16} {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": to_iso(self.at),
            "action": self.action,
            "actor": self.actor,
            "hold_id": self.hold_id,
            "unit_id": self.unit_id,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvent:
        return cls(
            at=from_iso(data["at"]),
            action=data["action"],
            actor=data["actor"],
            hold_id=data.get("hold_id"),
            unit_id=data.get("unit_id"),
            detail=data.get("detail", ""),
        )
