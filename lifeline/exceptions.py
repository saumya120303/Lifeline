"""The LifeLine exception family.

Every invalid operation raises a subclass of LifeLineError instead of
returning False or printing, so a caller can catch one specific problem
(e.g. HoldLimitError) or every LifeLine problem at once (LifeLineError).

    LifeLineError
    ├── NotFoundError
    │   ├── UnknownHospitalError
    │   ├── UnknownUnitError
    │   ├── UnknownHoldError
    │   └── UnknownAccountError
    ├── ValidationError
    ├── DuplicateError
    ├── UnitUnavailableError      <- the M0 conflict: unit already promised
    ├── HoldLimitError            <- requester already has 2 active holds
    ├── HoldExpiredError          <- hold timed out before confirm/release
    ├── InvalidStateError         <- e.g. requester releasing a confirmed unit
    ├── PermissionDeniedError     <- wrong requester / wrong hospital / wrong role
    ├── AuthenticationError
    ├── PersistenceError
    └── InvariantViolationError   <- state is inconsistent (should never happen)
"""
from __future__ import annotations


class LifeLineError(Exception):
    """Base class: catch this to handle any LifeLine rule violation."""


# --- lookups ------------------------------------------------------------------
class NotFoundError(LifeLineError):
    """An id or username that does not exist."""


class UnknownHospitalError(NotFoundError):
    """No hospital has this id."""


class UnknownUnitError(NotFoundError):
    """No hospital owns a unit with this id."""


class UnknownHoldError(NotFoundError):
    """No hold has this id."""


class UnknownAccountError(NotFoundError):
    """No account has this username."""


# --- bad input ----------------------------------------------------------------
class ValidationError(LifeLineError):
    """Input is malformed: unknown blood group, empty city, short password..."""


class DuplicateError(LifeLineError):
    """An id or username that must be unique is already taken."""


# --- rule violations ----------------------------------------------------------
class UnitUnavailableError(LifeLineError):
    """The unit is already held or confirmed for someone else."""


class HoldLimitError(LifeLineError):
    """The requester already has the maximum number of active holds."""


class HoldExpiredError(LifeLineError):
    """The hold ran out of time; its unit has gone back to the pool."""


class InvalidStateError(LifeLineError):
    """The operation is not allowed from the object's current status."""


class PermissionDeniedError(LifeLineError):
    """The caller is not allowed to do this (wrong owner, hospital or role)."""


class AuthenticationError(LifeLineError):
    """Username or password is wrong (deliberately does not say which)."""


# --- system -------------------------------------------------------------------
class PersistenceError(LifeLineError):
    """Saving or loading the JSON state file failed."""


class InvariantViolationError(LifeLineError):
    """The shared state broke a rule that must always hold."""
