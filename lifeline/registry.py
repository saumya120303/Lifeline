"""The Registry: the one object that owns all of LifeLine's shared state.

Composition: Registry has Hospitals (which have Units), Accounts and Holds,
plus a Clock and a Notifier. In M2 this object moves onto the server; in M3
one lock wraps each public method below.

Every public method that changes state follows the same four steps
(Lecture 5: "check everything first, raise before you change anything"):

    1. read the clock ONCE                 -> one consistent `now` for the call
    2. expire holds whose time is up       -> expiry is just another writer
    3. look things up and check every rule -> raise a LifeLineError on failure
    4. only then change units/holds/index  -> never half an update

Answers to the TA's M0 feedback
-------------------------------
* Release vs Cancel
    release_hold - REQUESTER only, own ACTIVE hold. "I don't need it."
    cancel       - HOSPITAL STAFF only (of that unit's hospital), ACTIVE or
                   CONFIRMED hold. "Unit no longer usable" / "patient
                   transferred". Cancelling a confirmation is hospital-only.
* What expires a hold
    No background thread. Step 2 above runs at the start of EVERY public
    call (search included), so a hold is expired the first time anyone
    touches the registry after its expires_at.
* Confirm at the moment of expiry
    Both happen inside the same call with the same `now`, so there is no
    race, only a rule: the boundary is inclusive (now >= expires_at means
    expired). Step 2 expires the hold first, then confirm_hold sees EXPIRED
    and raises HoldExpiredError. In M3 the lock keeps these two steps
    together; if we later add a background sweeper, it must take the same lock.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from .accounts import Account, HospitalStaff, Requester, account_from_dict
from .clock import Clock, SystemClock, hhmm
from .events import AuditEvent
from .exceptions import (
    DuplicateError,
    HoldExpiredError,
    HoldLimitError,
    InvalidStateError,
    InvariantViolationError,
    PermissionDeniedError,
    PersistenceError,
    UnitUnavailableError,
    UnknownAccountError,
    UnknownHoldError,
    UnknownHospitalError,
    UnknownUnitError,
    ValidationError,
    AuthenticationError,
)
from .hold import Hold, HoldStatus
from .hospital import Hospital
from .notifier import Notifier, SilentNotifier
from .resources import ResourceType, Unit, UnitStatus

SCHEMA_VERSION = 1
SYSTEM_ACTOR = "system"


class Registry:
    """All hospitals, units, accounts and holds, and the rules that protect them."""

    def __init__(self, clock: Clock | None = None, notifier: Notifier | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._notifier: Notifier = notifier if notifier is not None else SilentNotifier()
        self._hospitals: dict[str, Hospital] = {}
        self._accounts: dict[str, Account] = {}
        self._holds: dict[str, Hold] = {}            # every hold ever made, by id
        # unit_id -> id of its single OPEN hold. A dict key can appear only once,
        # so this index is the M0 invariant written as a data structure.
        self._open_hold_by_unit: dict[str, str] = {}
        self._events: list[AuditEvent] = []
        self._next_hold_number = 1

    # =================================================================== views
    @property
    def clock(self) -> Clock:
        return self._clock

    def hospitals(self) -> list[Hospital]:
        return list(self._hospitals.values())

    def get_hospital(self, hospital_id: str) -> Hospital:
        try:
            return self._hospitals[hospital_id]
        except KeyError:
            raise UnknownHospitalError(f"no hospital with id {hospital_id!r}") from None

    # Views that report hold status expire due holds first, like every other
    # public call, so nobody ever sees a hold that is "active" past its time.
    def get_hold(self, hold_id: str) -> Hold:
        """A copy: changing it does not change the registry."""
        self._expire_due_holds(self._clock.now())
        return replace(self._get_hold(hold_id))

    def holds_for(self, username: str, *, open_only: bool = False) -> list[Hold]:
        self._expire_due_holds(self._clock.now())
        self._get_account(username)
        return [
            replace(hold) for hold in self._holds.values()
            if hold.requester == username and (hold.is_open or not open_only)
        ]

    def active_hold_count(self, username: str) -> int:
        self._expire_due_holds(self._clock.now())
        self._get_account(username)
        return self._active_hold_count(username)

    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def stock_summary(self, city: str | None = None) -> dict[str, dict[str, dict[str, int]]]:
        """Live counts per hospital; the data a map view (stretch goal) would show."""
        self._expire_due_holds(self._clock.now())
        return {
            hospital.name: hospital.stock_summary()
            for hospital in self._hospitals.values()
            if city is None or hospital.location.in_city(city)
        }

    # =================================================================== setup
    def add_hospital(self, hospital: Hospital) -> None:
        if hospital.hospital_id in self._hospitals:
            raise DuplicateError(f"hospital {hospital.hospital_id} already exists")
        for unit in hospital.units():
            if not unit.is_available:
                raise ValidationError(f"new hospital's unit {unit.unit_id} must start available")
            if self._find_unit(unit.unit_id) is not None:
                raise DuplicateError(f"unit id {unit.unit_id} is already used by another hospital")
        self._hospitals[hospital.hospital_id] = hospital

    def register_requester(self, username: str, password: str, *, phone: str = "") -> Requester:
        account = Requester.create(username, password, phone=phone)
        self._add_account(account)
        return account

    def register_staff(self, username: str, password: str, *, hospital_id: str) -> HospitalStaff:
        self.get_hospital(hospital_id)  # must exist
        account = HospitalStaff.create(username, password, hospital_id=hospital_id)
        self._add_account(account)
        return account

    def authenticate(self, username: str, password: str) -> Account:
        """Log in. Same error for unknown user and wrong password, so an
        attacker cannot learn which usernames exist."""
        account = self._accounts.get(username)
        if account is None or not account.verify_password(password):
            raise AuthenticationError("invalid username or password")
        return account

    # ================================================ stock changes (hospital)
    def add_unit(self, staff_username: str, unit: Unit) -> None:
        now = self._clock.now()
        self._expire_due_holds(now)
        staff = self._get_account(staff_username)
        hospital = self.get_hospital(unit.hospital_id)
        self._require_manager(staff, hospital.hospital_id)
        if not unit.is_available:
            raise ValidationError(f"new unit {unit.unit_id} must start available")
        if self._find_unit(unit.unit_id) is not None:
            raise DuplicateError(f"unit id {unit.unit_id} is already in use")
        hospital.add_unit(unit)
        self._record(now, "add_unit", staff_username, unit_id=unit.unit_id, detail=unit.label())

    def remove_unit(self, staff_username: str, unit_id: str) -> Unit:
        now = self._clock.now()
        self._expire_due_holds(now)
        staff = self._get_account(staff_username)
        hospital, unit = self._locate_unit(unit_id)
        self._require_manager(staff, hospital.hospital_id)
        removed = hospital.remove_unit(unit_id)  # refuses held/confirmed units
        self._record(now, "remove_unit", staff_username, unit_id=unit_id, detail=unit.label())
        return removed

    # ========================================================= 1. Search
    def search(self, resource_type: ResourceType | str, city: str | None = None,
               **criteria: Any) -> list[Unit]:
        """Available units of one type, optionally in one city.
        Extra criteria are understood by the matching subclass:
        ventilator=True, min_litres=..., blood_group='O+', for_patient='A-'."""
        self._expire_due_holds(self._clock.now())
        rtype = ResourceType.parse(resource_type)
        results: list[Unit] = []
        for hospital in self._hospitals.values():
            if city is None or hospital.location.in_city(city):
                results.extend(hospital.find_available(rtype, **criteria))
        return sorted(results, key=lambda unit: (unit.hospital_id, unit.unit_id))

    # ========================================================= 2. Hold
    def place_hold(self, username: str, unit_id: str) -> Hold:
        now = self._clock.now()
        self._expire_due_holds(now)

        # --- check everything first ---
        account = self._get_account(username)
        hospital, unit = self._locate_unit(unit_id)
        limit = account.hold_limit()                       # polymorphic: 2 or 0
        if limit == 0:
            raise PermissionDeniedError(f"{username} ({account.role}) cannot place holds")
        active = self._active_hold_count(username)
        if active >= limit:
            raise HoldLimitError(
                f"{username} already has {active} active holds (limit {limit}); "
                "release one first"
            )
        if not unit.is_available or unit_id in self._open_hold_by_unit:
            raise UnitUnavailableError(
                f"{unit_id} ({unit.label()}) at {hospital.name} is already {unit.status.value}"
            )

        # --- then change state ---
        unit.mark_held()
        hold = Hold(
            hold_id=self._new_hold_id(),
            unit_id=unit_id,
            hospital_id=hospital.hospital_id,
            requester=username,
            created_at=now,
            expires_at=now + timedelta(minutes=unit.hold_minutes()),  # polymorphic
        )
        self._holds[hold.hold_id] = hold
        self._open_hold_by_unit[unit_id] = hold.hold_id
        self._record(now, "hold", username, hold.hold_id, unit_id,
                     f"until {hhmm(hold.expires_at)}")
        self._notifier.notify(
            username,
            f"{unit.label()} at {hospital.name} is held for you until "
            f"{hhmm(hold.expires_at)} (hold {hold.hold_id})",
        )
        return replace(hold)

    # ========================================================= 3. Release
    def release_hold(self, username: str, hold_id: str) -> Hold:
        """Requester gives up their own ACTIVE hold."""
        now = self._clock.now()
        self._expire_due_holds(now)

        self._get_account(username)
        hold = self._get_hold(hold_id)
        if hold.requester != username:
            raise PermissionDeniedError(
                f"hold {hold_id} belongs to {hold.requester}; only they can release it"
            )
        if hold.status is HoldStatus.CONFIRMED:
            raise InvalidStateError(
                f"hold {hold_id} is already confirmed; only the hospital can cancel a confirmation"
            )
        self._require_active(hold, "release")

        self._close_hold(hold, HoldStatus.RELEASED, now, by=username, reason="released by requester")
        return replace(hold)

    # ========================================================= 4. Confirm
    def confirm_hold(self, staff_username: str, hold_id: str) -> Hold:
        """Hospital staff confirm the patient arrived; the unit is now taken."""
        now = self._clock.now()
        self._expire_due_holds(now)  # expiry is applied first: at the boundary, expiry wins

        staff = self._get_account(staff_username)
        hold = self._get_hold(hold_id)
        self._require_manager(staff, hold.hospital_id)
        self._require_active(hold, "confirm")
        _, unit = self._locate_unit(hold.unit_id)

        unit.mark_confirmed()
        hold.move_to(HoldStatus.CONFIRMED, now=now, by=staff_username)
        self._record(now, "confirm", staff_username, hold.hold_id, hold.unit_id)
        self._notifier.notify(
            hold.requester, f"{unit.label()} is confirmed for your patient (hold {hold.hold_id})"
        )
        return replace(hold)

    # ========================================================= 5. Cancel
    def cancel(self, staff_username: str, hold_id: str, reason: str = "") -> Hold:
        """Hospital staff cancel an ACTIVE hold or a CONFIRMED unit; it becomes available."""
        now = self._clock.now()
        self._expire_due_holds(now)

        staff = self._get_account(staff_username)
        hold = self._get_hold(hold_id)
        self._require_manager(staff, hold.hospital_id)   # requesters always fail here
        if hold.status is HoldStatus.EXPIRED:
            raise HoldExpiredError(f"hold {hold_id} already expired; nothing to cancel")
        if not hold.is_open:
            raise InvalidStateError(f"hold {hold_id} is already {hold.status.value}")

        was = hold.status.value
        self._close_hold(hold, HoldStatus.CANCELLED, now, by=staff_username,
                         reason=reason or f"{was} hold cancelled by hospital")
        self._notifier.notify(
            hold.requester,
            f"your {was} hold {hold.hold_id} was cancelled by the hospital"
            + (f": {reason}" if reason else ""),
        )
        return replace(hold)

    # ========================================================= expiry
    def expire_due_holds(self) -> list[Hold]:
        """Expire every ACTIVE hold whose time is up. Also runs automatically
        at the start of every other public call."""
        return [replace(hold) for hold in self._expire_due_holds(self._clock.now())]

    def _expire_due_holds(self, now: datetime) -> list[Hold]:
        due = [
            self._holds[hold_id] for hold_id in self._open_hold_by_unit.values()
            if self._holds[hold_id].is_due_to_expire(now)
        ]
        for hold in due:
            self._close_hold(hold, HoldStatus.EXPIRED, now, by=SYSTEM_ACTOR, reason="not confirmed in time")
            self._notifier.notify(
                hold.requester,
                f"hold {hold.hold_id} expired at {hhmm(hold.expires_at)}; the unit was released",
            )
        return due

    # ========================================================= invariant
    def check_invariants(self) -> None:
        """Raise InvariantViolationError if the shared state is inconsistent.

        The M0 invariant: each unit has at most one open (active or confirmed)
        hold. Also checks the things that must agree with it."""
        problems: list[str] = []

        open_holds_by_unit: dict[str, list[Hold]] = defaultdict(list)
        for hold in self._holds.values():
            if hold.is_open:
                open_holds_by_unit[hold.unit_id].append(hold)

        # 1. THE invariant: one unit is never promised to two people.
        for unit_id, holds in open_holds_by_unit.items():
            if len(holds) > 1:
                ids = ", ".join(h.hold_id for h in holds)
                problems.append(f"unit {unit_id} is promised to {len(holds)} holds ({ids})")

        # 2. Unit ids are unique across hospitals.
        id_counts = Counter(unit.unit_id for h in self._hospitals.values() for unit in h.units())
        problems += [f"unit id {uid} appears {n} times" for uid, n in id_counts.items() if n > 1]

        # 3. Each unit's status agrees with its holds, and the index agrees too.
        for hospital in self._hospitals.values():
            for unit in hospital.units():
                holds = open_holds_by_unit.get(unit.unit_id, [])
                if not holds:
                    expected = UnitStatus.AVAILABLE
                elif holds[0].status is HoldStatus.ACTIVE:
                    expected = UnitStatus.HELD
                else:
                    expected = UnitStatus.CONFIRMED
                if unit.status is not expected:
                    problems.append(
                        f"unit {unit.unit_id} is {unit.status.value} but its holds say {expected.value}"
                    )
                indexed = self._open_hold_by_unit.get(unit.unit_id)
                if len(holds) <= 1 and indexed != (holds[0].hold_id if holds else None):
                    problems.append(f"open-hold index for {unit.unit_id} is out of date")

        # 4. Every open hold points at a real unit at the right hospital.
        for unit_id, holds in open_holds_by_unit.items():
            found = self._find_unit(unit_id)
            if found is None:
                problems.append(f"open hold {holds[0].hold_id} points at missing unit {unit_id}")
            elif found[0].hospital_id != holds[0].hospital_id:
                problems.append(f"hold {holds[0].hold_id} names the wrong hospital")

        # 5. Nobody exceeds their hold limit.
        for account in self._accounts.values():
            count = self._active_hold_count(account.username)
            if count > account.hold_limit():
                problems.append(
                    f"{account.username} has {count} active holds (limit {account.hold_limit()})"
                )

        if problems:
            raise InvariantViolationError("; ".join(problems))

    def invariant_holds(self) -> bool:
        try:
            self.check_invariants()
        except InvariantViolationError:
            return False
        return True

    # ========================================================= JSON
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "next_hold_number": self._next_hold_number,
            "hospitals": [hospital.to_dict() for hospital in self._hospitals.values()],
            "accounts": [account.to_dict() for account in self._accounts.values()],
            "holds": [hold.to_dict() for hold in self._holds.values()],
            "events": [event.to_dict() for event in self._events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, clock: Clock | None = None,
                  notifier: Notifier | None = None) -> Registry:
        """Rebuild a registry from to_dict() output. Refuses inconsistent data."""
        if data.get("schema_version") != SCHEMA_VERSION:
            raise PersistenceError(
                f"unsupported schema_version {data.get('schema_version')!r} "
                f"(expected {SCHEMA_VERSION})"
            )
        registry = cls(clock=clock, notifier=notifier)
        try:
            for record in data["hospitals"]:
                hospital = Hospital.from_dict(record)
                if hospital.hospital_id in registry._hospitals:
                    raise PersistenceError(f"hospital {hospital.hospital_id} appears twice")
                registry._hospitals[hospital.hospital_id] = hospital
            for record in data["accounts"]:
                account = account_from_dict(record)
                if account.username in registry._accounts:
                    raise PersistenceError(f"account {account.username} appears twice")
                registry._accounts[account.username] = account
            for record in data["holds"]:
                hold = Hold.from_dict(record)
                registry._holds[hold.hold_id] = hold
                if hold.is_open:
                    if hold.unit_id in registry._open_hold_by_unit:
                        raise InvariantViolationError(
                            f"saved state promises unit {hold.unit_id} to two holds"
                        )
                    registry._open_hold_by_unit[hold.unit_id] = hold.hold_id
            registry._events = [AuditEvent.from_dict(r) for r in data.get("events", [])]
            registry._next_hold_number = int(data["next_hold_number"])
        except (KeyError, TypeError, ValueError, ValidationError, DuplicateError) as exc:
            raise PersistenceError(f"saved state is malformed: {exc}") from exc
        registry.check_invariants()  # never load a file that breaks the rules
        return registry

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Registry):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    __hash__ = None

    def __repr__(self) -> str:
        units = sum(len(h) for h in self._hospitals.values())
        return (
            f"Registry({len(self._hospitals)} hospitals, {units} units, "
            f"{len(self._accounts)} accounts, {len(self._open_hold_by_unit)} open holds)"
        )

    # ========================================================= private helpers
    def _add_account(self, account: Account) -> None:
        if account.username in self._accounts:
            raise DuplicateError(f"username {account.username!r} is taken")
        self._accounts[account.username] = account
        self._record(self._clock.now(), "register", account.username, detail=account.role)

    def _get_account(self, username: str) -> Account:
        try:
            return self._accounts[username]
        except KeyError:
            raise UnknownAccountError(f"no account named {username!r}") from None

    def _get_hold(self, hold_id: str) -> Hold:
        try:
            return self._holds[hold_id]
        except KeyError:
            raise UnknownHoldError(f"no hold with id {hold_id!r}") from None

    def _find_unit(self, unit_id: str) -> tuple[Hospital, Unit] | None:
        for hospital in self._hospitals.values():
            if hospital.has_unit(unit_id):
                return hospital, hospital.get_unit(unit_id)
        return None

    def _locate_unit(self, unit_id: str) -> tuple[Hospital, Unit]:
        found = self._find_unit(unit_id)
        if found is None:
            raise UnknownUnitError(f"no unit with id {unit_id!r}")
        return found

    def _require_manager(self, account: Account, hospital_id: str) -> None:
        if not account.can_manage(hospital_id):              # polymorphic
            raise PermissionDeniedError(
                f"{account.username} ({account.role}) cannot act for hospital {hospital_id}"
            )

    def _require_active(self, hold: Hold, action: str) -> None:
        if hold.status is HoldStatus.EXPIRED:
            raise HoldExpiredError(
                f"cannot {action} hold {hold.hold_id}: it expired at {hhmm(hold.expires_at)} "
                "and the unit went back to the pool"
            )
        if hold.status is not HoldStatus.ACTIVE:
            raise InvalidStateError(f"cannot {action} hold {hold.hold_id}: it is {hold.status.value}")

    def _active_hold_count(self, username: str) -> int:
        return sum(
            1 for hold_id in self._open_hold_by_unit.values()
            if self._holds[hold_id].requester == username and self._holds[hold_id].is_active
        )

    def _close_hold(self, hold: Hold, status: HoldStatus, now: datetime, *, by: str, reason: str) -> None:
        """Release, cancel or expire: the unit goes back to AVAILABLE.
        Validates the hold move before touching anything."""
        if not hold.can_move_to(status):
            raise InvalidStateError(
                f"hold {hold.hold_id} cannot go from {hold.status.value} to {status.value}"
            )
        _, unit = self._locate_unit(hold.unit_id)
        unit.mark_available()
        hold.move_to(status, now=now, by=by, reason=reason)
        del self._open_hold_by_unit[hold.unit_id]
        action = {HoldStatus.RELEASED: "release", HoldStatus.CANCELLED: "cancel",
                  HoldStatus.EXPIRED: "expire"}[status]
        self._record(now, action, by, hold.hold_id, hold.unit_id, reason)

    def _new_hold_id(self) -> str:
        hold_id = f"H{self._next_hold_number:04d}"
        self._next_hold_number += 1
        return hold_id

    def _record(self, now: datetime, action: str, actor: str, hold_id: str | None = None,
                unit_id: str | None = None, detail: str = "") -> None:
        self._events.append(AuditEvent(now, action, actor, hold_id, unit_id, detail))
