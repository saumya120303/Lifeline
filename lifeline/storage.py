"""Saving the Registry to a JSON file and loading it back.

Converting objects to and from JSON lives in each class's to_dict/from_dict;
this module only does file I/O, in one place.

save() writes to a temporary file first and then swaps it in with
os.replace(), which is atomic: if the program crashes mid-save, the old file
is still intact. (M3 requires this; it costs three lines now.)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .clock import Clock
from .exceptions import PersistenceError
from .notifier import Notifier
from .registry import Registry


class JsonStore:
    """Reads and writes one registry state file."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def save(self, registry: Registry) -> None:
        data = registry.to_dict()
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path)  # atomic swap
        except OSError as exc:
            raise PersistenceError(f"could not save to {self._path}: {exc}") from exc

    def load(self, *, clock: Clock | None = None, notifier: Notifier | None = None) -> Registry:
        try:
            with open(self._path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            raise PersistenceError(f"no saved state at {self._path}") from None
        except json.JSONDecodeError as exc:
            raise PersistenceError(f"{self._path} is not valid JSON ({exc})") from None
        except OSError as exc:
            raise PersistenceError(f"could not read {self._path}: {exc}") from exc
        if not isinstance(data, dict):
            raise PersistenceError(f"{self._path} does not contain a LifeLine state object")
        return Registry.from_dict(data, clock=clock, notifier=notifier)

    def __repr__(self) -> str:
        return f"JsonStore({str(self._path)!r})"
