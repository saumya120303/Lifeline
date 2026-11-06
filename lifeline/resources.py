"""The resources a hospital can lend out.

Unit is an abstract base class (the promise). ICUBed, OxygenCylinder and
BloodUnit each keep that promise their own way:

* hold_minutes()  - how long a hold lasts for this kind of unit
* matches(...)    - which search criteria apply (ventilator, litres, blood group)
* label()         - how the unit describes itself

The Registry and Hospital call these methods on any Unit without ever
checking which subclass it is (no if/elif on a "kind" field), so adding a
new resource type means adding one subclass and nothing else.

Unit also owns its status. The only way to change it is mark_held(),
mark_confirmed() or mark_available(), and each one refuses a move that is not
in _ALLOWED_MOVES. That makes "held twice" impossible at the lowest level:
an already-held unit cannot be marked held again.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from .exceptions import InvalidStateError, ValidationError


class ResourceType(str, Enum):
    """The three kinds of resource LifeLine tracks."""

    ICU_BED = "icu_bed"
    OXYGEN_CYLINDER = "oxygen_cylinder"
    BLOOD_UNIT = "blood_unit"

    @classmethod
    def parse(cls, value: ResourceType | str) -> ResourceType:
        """Accept a member or its string value; raise ValidationError otherwise."""
        try:
            return cls(value)
        except ValueError:
            allowed = ", ".join(t.value for t in cls)
            raise ValidationError(
                f"unknown resource type {value!r} (expected one of: {allowed})"
            ) from None


class UnitStatus(str, Enum):
    """Where a unit is in its life cycle."""

    AVAILABLE = "available"
    HELD = "held"
    CONFIRMED = "confirmed"


class BloodGroup(str, Enum):
    """ABO group plus RhD sign."""

    O_NEG = "O-"
    O_POS = "O+"
    A_NEG = "A-"
    A_POS = "A+"
    B_NEG = "B-"
    B_POS = "B+"
    AB_NEG = "AB-"
    AB_POS = "AB+"

    @classmethod
    def parse(cls, value: BloodGroup | str) -> BloodGroup:
        try:
            return cls(str(value).strip().upper())
        except ValueError:
            allowed = ", ".join(g.value for g in cls)
            raise ValidationError(
                f"unknown blood group {value!r} (expected one of: {allowed})"
            ) from None

    def can_donate_to(self, patient: BloodGroup) -> bool:
        """Red-cell compatibility: every ABO antigen the donor unit carries
        must also be the patient's, and RhD+ blood only goes to RhD+ patients.
        (O- can go to anyone; AB+ patients can receive any group.)"""
        donor_antigens = set(self.value[:-1].replace("O", ""))
        patient_antigens = set(patient.value[:-1].replace("O", ""))
        abo_ok = donor_antigens <= patient_antigens
        rh_ok = self.value.endswith("-") or patient.value.endswith("+")
        return abo_ok and rh_ok


class Unit(ABC):
    """One physical resource (bed, cylinder or blood unit) at one hospital."""

    # Every status move a unit is allowed to make. Anything else is a bug
    # (or, in M3, a race) and raises InvalidStateError.
    _ALLOWED_MOVES: ClassVar[frozenset[tuple[UnitStatus, UnitStatus]]] = frozenset({
        (UnitStatus.AVAILABLE, UnitStatus.HELD),       # requester places a hold
        (UnitStatus.HELD, UnitStatus.CONFIRMED),       # hospital confirms arrival
        (UnitStatus.HELD, UnitStatus.AVAILABLE),       # release / expiry / cancel hold
        (UnitStatus.CONFIRMED, UnitStatus.AVAILABLE),  # hospital cancels confirmation
    })

    def __init__(
        self,
        unit_id: str,
        hospital_id: str,
        status: UnitStatus | str = UnitStatus.AVAILABLE,
    ) -> None:
        if not unit_id or not hospital_id:
            raise ValidationError("a unit needs both a unit_id and a hospital_id")
        self._unit_id = unit_id
        self._hospital_id = hospital_id
        try:
            self._status = UnitStatus(status)
        except ValueError:
            raise ValidationError(f"unknown unit status {status!r}") from None

    # --- read-only properties (no setters: status changes go through mark_*)
    @property
    def unit_id(self) -> str:
        return self._unit_id

    @property
    def hospital_id(self) -> str:
        return self._hospital_id

    @property
    def status(self) -> UnitStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status is UnitStatus.AVAILABLE

    # --- the only way a unit's status changes (called by the Registry) -------
    def _move_to(self, new_status: UnitStatus) -> None:
        if (self._status, new_status) not in self._ALLOWED_MOVES:
            raise InvalidStateError(
                f"unit {self._unit_id} cannot go from "
                f"{self._status.value} to {new_status.value}"
            )
        self._status = new_status

    def mark_held(self) -> None:
        self._move_to(UnitStatus.HELD)

    def mark_confirmed(self) -> None:
        self._move_to(UnitStatus.CONFIRMED)

    def mark_available(self) -> None:
        self._move_to(UnitStatus.AVAILABLE)

    # --- the promise every subclass must keep ---------------------------------
    @property
    @abstractmethod
    def resource_type(self) -> ResourceType:
        """Which kind of resource this is."""

    @abstractmethod
    def hold_minutes(self) -> int:
        """How long a hold on this kind of unit lasts before it expires."""

    @abstractmethod
    def label(self) -> str:
        """Short human description, e.g. 'O+ blood unit'."""

    @abstractmethod
    def _extra_fields(self) -> dict[str, Any]:
        """The subclass's own fields, for to_dict()."""

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict[str, Any]) -> Unit:
        """Rebuild a unit of this subclass from its to_dict() output."""

    # --- shared behaviour ---------------------------------------------------------
    def matches(self, resource_type: ResourceType, **criteria: Any) -> bool:
        """Does this unit answer a search? Subclasses extend this with their
        own criteria and call super() for the shared type check."""
        return self.resource_type is resource_type

    def to_dict(self) -> dict[str, Any]:
        # "type" is saved so unit_from_dict knows which subclass to rebuild.
        return {
            "type": self.resource_type.value,
            "unit_id": self._unit_id,
            "hospital_id": self._hospital_id,
            "status": self._status.value,
            **self._extra_fields(),
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Unit):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    __hash__ = None  # mutable (status changes), so not hashable

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._unit_id!r}, {self.label()!r}, {self._status.value})"


class ICUBed(Unit):
    """An intensive-care bed, optionally with a ventilator."""

    HOLD_MINUTES: ClassVar[int] = 30  # time to move a critical patient

    def __init__(
        self,
        unit_id: str,
        hospital_id: str,
        *,
        has_ventilator: bool = False,
        status: UnitStatus | str = UnitStatus.AVAILABLE,
    ) -> None:
        super().__init__(unit_id, hospital_id, status)
        self._has_ventilator = bool(has_ventilator)

    @property
    def resource_type(self) -> ResourceType:
        return ResourceType.ICU_BED

    @property
    def has_ventilator(self) -> bool:
        return self._has_ventilator

    def hold_minutes(self) -> int:
        return self.HOLD_MINUTES

    def label(self) -> str:
        return "ICU bed with ventilator" if self._has_ventilator else "ICU bed"

    def matches(
        self, resource_type: ResourceType, *, ventilator: bool | None = None, **criteria: Any
    ) -> bool:
        if not super().matches(resource_type, **criteria):
            return False
        # ventilator=True means "must have one"; None/False means "any bed".
        return not ventilator or self._has_ventilator

    def _extra_fields(self) -> dict[str, Any]:
        return {"has_ventilator": self._has_ventilator}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ICUBed:
        return cls(
            data["unit_id"],
            data["hospital_id"],
            has_ventilator=bool(data.get("has_ventilator", False)),
            status=data["status"],
        )


class OxygenCylinder(Unit):
    """A filled oxygen cylinder of a given capacity."""

    HOLD_MINUTES: ClassVar[int] = 60  # families often collect cylinders themselves

    def __init__(
        self,
        unit_id: str,
        hospital_id: str,
        *,
        capacity_litres: int,
        status: UnitStatus | str = UnitStatus.AVAILABLE,
    ) -> None:
        super().__init__(unit_id, hospital_id, status)
        if int(capacity_litres) <= 0:
            raise ValidationError("an oxygen cylinder needs a positive capacity")
        self._capacity_litres = int(capacity_litres)

    @property
    def resource_type(self) -> ResourceType:
        return ResourceType.OXYGEN_CYLINDER

    @property
    def capacity_litres(self) -> int:
        return self._capacity_litres

    def hold_minutes(self) -> int:
        return self.HOLD_MINUTES

    def label(self) -> str:
        return f"{self._capacity_litres} L oxygen cylinder"

    def matches(
        self, resource_type: ResourceType, *, min_litres: int | None = None, **criteria: Any
    ) -> bool:
        if not super().matches(resource_type, **criteria):
            return False
        return min_litres is None or self._capacity_litres >= min_litres

    def _extra_fields(self) -> dict[str, Any]:
        return {"capacity_litres": self._capacity_litres}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OxygenCylinder:
        return cls(
            data["unit_id"],
            data["hospital_id"],
            capacity_litres=data["capacity_litres"],
            status=data["status"],
        )


class BloodUnit(Unit):
    """One unit of red cells of a specific blood group."""

    HOLD_MINUTES: ClassVar[int] = 20  # scarcest resource per group: short holds stop hoarding

    def __init__(
        self,
        unit_id: str,
        hospital_id: str,
        *,
        blood_group: BloodGroup | str,
        status: UnitStatus | str = UnitStatus.AVAILABLE,
    ) -> None:
        super().__init__(unit_id, hospital_id, status)
        self._blood_group = BloodGroup.parse(blood_group)

    @property
    def resource_type(self) -> ResourceType:
        return ResourceType.BLOOD_UNIT

    @property
    def blood_group(self) -> BloodGroup:
        return self._blood_group

    def hold_minutes(self) -> int:
        return self.HOLD_MINUTES

    def label(self) -> str:
        return f"{self._blood_group.value} blood unit"

    def matches(
        self,
        resource_type: ResourceType,
        *,
        blood_group: BloodGroup | str | None = None,
        for_patient: BloodGroup | str | None = None,
        **criteria: Any,
    ) -> bool:
        """blood_group='O+' -> exactly O+.
        for_patient='A+'   -> any group that can safely be given to an A+ patient."""
        if not super().matches(resource_type, **criteria):
            return False
        if blood_group is not None and self._blood_group is not BloodGroup.parse(blood_group):
            return False
        if for_patient is not None and not self._blood_group.can_donate_to(BloodGroup.parse(for_patient)):
            return False
        return True

    def _extra_fields(self) -> dict[str, Any]:
        return {"blood_group": self._blood_group.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BloodUnit:
        return cls(
            data["unit_id"],
            data["hospital_id"],
            blood_group=data["blood_group"],
            status=data["status"],
        )


# Which subclass to rebuild for each saved "type". This lookup is used only
# when loading JSON; everywhere else units are used through the Unit interface.
UNIT_TYPES: dict[ResourceType, type[Unit]] = {
    ResourceType.ICU_BED: ICUBed,
    ResourceType.OXYGEN_CYLINDER: OxygenCylinder,
    ResourceType.BLOOD_UNIT: BloodUnit,
}


def unit_from_dict(data: dict[str, Any]) -> Unit:
    """Rebuild the right Unit subclass from a saved record."""
    try:
        unit_class = UNIT_TYPES[ResourceType.parse(data["type"])]
        return unit_class.from_dict(data)
    except KeyError as missing:
        raise ValidationError(f"unit record is missing field {missing}") from None
