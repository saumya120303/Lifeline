"""Who can log in: requesters (families) and hospital staff.

Account is an abstract base class. Each kind of account answers two
questions its own way, and the Registry asks them without checking types:

* hold_limit()            - Requester: 2.  HospitalStaff: 0 (staff never hold units).
* can_manage(hospital_id) - Requester: never.  HospitalStaff: only their own hospital.

That is how the TA's question "who may Cancel?" is enforced: Cancel and
Confirm require can_manage(), which only staff of the right hospital pass;
Release requires being the requester who placed the hold.

Passwords are never stored. We store a random salt and a PBKDF2-SHA256 hash,
and compare hashes with hmac.compare_digest.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from .exceptions import ValidationError

PBKDF2_ITERATIONS = 100_000
MIN_PASSWORD_LENGTH = 6
_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,32}")


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Return (salt_hex, hash_hex) for a password; makes a new salt if none given."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


class Account(ABC):
    """A user who can log in."""

    def __init__(self, username: str, salt: str, password_hash: str) -> None:
        if not _USERNAME_PATTERN.fullmatch(username or ""):
            raise ValidationError(
                f"username {username!r} must be 3-32 letters, digits, '_', '.' or '-'"
            )
        self._username = username
        self._salt = salt
        self._password_hash = password_hash

    @property
    def username(self) -> str:
        return self._username

    def verify_password(self, password: str) -> bool:
        try:
            _, candidate = hash_password(password, bytes.fromhex(self._salt))
        except ValidationError:  # too short to be anyone's password
            return False
        return hmac.compare_digest(candidate, self._password_hash)

    # --- the promise each kind of account keeps --------------------------------
    @property
    @abstractmethod
    def role(self) -> str:
        """'requester' or 'hospital_staff' (also the saved JSON tag)."""

    @abstractmethod
    def hold_limit(self) -> int:
        """Maximum number of ACTIVE holds this account may have at once."""

    @abstractmethod
    def can_manage(self, hospital_id: str) -> bool:
        """May this account confirm/cancel holds and change stock at this hospital?"""

    @abstractmethod
    def _extra_fields(self) -> dict[str, Any]:
        """Subclass-specific fields for to_dict()."""

    # --- shared -------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "username": self._username,
            "salt": self._salt,
            "password_hash": self._password_hash,
            **self._extra_fields(),
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Account):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    __hash__ = None

    def __repr__(self) -> str:  # never shows the hash
        return f"{type(self).__name__}({self._username!r})"


class Requester(Account):
    """A patient's family member who searches for and holds units."""

    MAX_ACTIVE_HOLDS: ClassVar[int] = 2

    def __init__(self, username: str, salt: str, password_hash: str, *, phone: str = "") -> None:
        super().__init__(username, salt, password_hash)
        self._phone = phone

    @classmethod
    def create(cls, username: str, password: str, *, phone: str = "") -> Requester:
        salt, password_hash = hash_password(password)
        return cls(username, salt, password_hash, phone=phone)

    @property
    def phone(self) -> str:
        return self._phone

    @property
    def role(self) -> str:
        return "requester"

    def hold_limit(self) -> int:
        return self.MAX_ACTIVE_HOLDS

    def can_manage(self, hospital_id: str) -> bool:
        return False

    def _extra_fields(self) -> dict[str, Any]:
        return {"phone": self._phone}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Requester:
        return cls(data["username"], data["salt"], data["password_hash"], phone=data.get("phone", ""))


class HospitalStaff(Account):
    """A staff member who confirms arrivals and manages stock at one hospital."""

    def __init__(self, username: str, salt: str, password_hash: str, *, hospital_id: str) -> None:
        super().__init__(username, salt, password_hash)
        if not hospital_id:
            raise ValidationError("hospital staff must belong to a hospital")
        self._hospital_id = hospital_id

    @classmethod
    def create(cls, username: str, password: str, *, hospital_id: str) -> HospitalStaff:
        salt, password_hash = hash_password(password)
        return cls(username, salt, password_hash, hospital_id=hospital_id)

    @property
    def hospital_id(self) -> str:
        return self._hospital_id

    @property
    def role(self) -> str:
        return "hospital_staff"

    def hold_limit(self) -> int:
        return 0

    def can_manage(self, hospital_id: str) -> bool:
        return hospital_id == self._hospital_id

    def _extra_fields(self) -> dict[str, Any]:
        return {"hospital_id": self._hospital_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HospitalStaff:
        return cls(data["username"], data["salt"], data["password_hash"], hospital_id=data["hospital_id"])


ACCOUNT_TYPES: dict[str, type[Requester] | type[HospitalStaff]] = {
    "requester": Requester,
    "hospital_staff": HospitalStaff,
}


def account_from_dict(data: dict[str, Any]) -> Account:
    """Rebuild the right Account subclass from a saved record."""
    try:
        account_class = ACCOUNT_TYPES[data["role"]]
    except KeyError:
        raise ValidationError(f"unknown or missing account role in {data!r}") from None
    return account_class.from_dict(data)
