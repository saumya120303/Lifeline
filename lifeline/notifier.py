"""How LifeLine tells a requester what happened to their hold.

Same pattern as Lecture 5's Notifier: the Registry *has a* Notifier and only
relies on the promise notify(recipient, message). Which notifier it gets
decides the behaviour:

* PrintNotifier     - prints, for demo.py.
* RecordingNotifier - remembers messages, for tests (no real messages sent).
* SilentNotifier    - does nothing; the default.

Later (M2/M3) a notifier could push the message to the client over the
network without the Registry changing at all.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    """The promise: 'I can deliver a message to a user'."""

    @abstractmethod
    def notify(self, recipient: str, message: str) -> None:
        """Deliver `message` to the user named `recipient`."""


class PrintNotifier(Notifier):
    """Prints each message; used by demo.py."""

    def __init__(self, prefix: str = "    [message]") -> None:
        self._prefix = prefix

    def notify(self, recipient: str, message: str) -> None:
        print(f"{self._prefix} to {recipient}: {message}")


class RecordingNotifier(Notifier):
    """Keeps every message in a list so tests can check what was sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def notify(self, recipient: str, message: str) -> None:
        self.sent.append((recipient, message))

    def messages_for(self, recipient: str) -> list[str]:
        return [message for who, message in self.sent if who == recipient]


class SilentNotifier(Notifier):
    """Drops every message."""

    def notify(self, recipient: str, message: str) -> None:
        return None
