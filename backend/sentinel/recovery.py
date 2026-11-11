"""Recovery policy: progressive backoff with a stability window.

The engine is deliberately hardware free. It only decides *when* an interface
may be reopened; opening and closing descriptors belongs to the HID monitor.
This keeps the policy easy to test with a simulated clock.

Rules implemented here:

* attempts wait 1, 2, 4, 8, 15 and then 30 seconds;
* the wait repeats at the maximum from the sixth attempt onwards;
* the attempt counter is reset only after the interface has stayed up for the
  configured stability window (30 seconds by default);
* every interface has its own counter, so one bad interface cannot delay the
  others.

No USB reset is performed. Recovery is limited to reopening HID descriptors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RecoveryController:
    """Backoff state for one interface."""

    key: str
    backoff: tuple[float, ...]
    stable_seconds: float
    interface: str = ""
    path: str = ""
    attempts: int = 0
    next_due: float | None = None
    opened_at: float | None = None
    settled: bool = False

    def delay(self) -> float:
        """Delay for the current attempt number, capped at the last value."""

        index = min(max(self.attempts, 1) - 1, len(self.backoff) - 1)
        return self.backoff[index]

    def note_failure(self, now: float) -> tuple[int, float]:
        """Register a failure and return the attempt number and its delay."""

        self.attempts += 1
        self.opened_at = None
        self.settled = False
        wait = self.delay()
        self.next_due = now + wait
        return self.attempts, wait

    def note_open(self, now: float) -> bool:
        """Register a successful open. Returns True when this was a recovery."""

        recovered = self.attempts > 0
        self.opened_at = now
        self.settled = False
        self.next_due = None
        return recovered

    def ready(self, now: float) -> bool:
        return self.next_due is None or now >= self.next_due

    def settle(self, now: float) -> bool:
        """Reset the counter once the interface has been up long enough."""

        if self.settled or self.opened_at is None:
            return False
        if now - self.opened_at < self.stable_seconds:
            return False
        self.settled = True
        self.attempts = 0
        self.next_due = None
        return True

    def is_connected(self, now: float) -> bool:
        return self.opened_at is not None

    def status(self, now: float) -> dict[str, Any]:
        retry_in = None
        if self.next_due is not None:
            retry_in = max(0.0, self.next_due - now)
        return {
            "key": self.key,
            "interface": self.interface,
            "device": self.path,
            "attempts": self.attempts,
            "retry_in_seconds": round(retry_in, 2) if retry_in is not None else None,
            "connected": self.opened_at is not None,
            "settled": self.settled,
        }


class RecoveryEngine:
    """A collection of per-interface controllers plus aggregate reporting."""

    def __init__(
        self,
        backoff: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0),
        stable_seconds: float = 30.0,
    ) -> None:
        if not backoff:
            raise ValueError("backoff cannot be empty")
        if any(value <= 0 for value in backoff):
            raise ValueError("backoff values must be greater than zero")
        if stable_seconds <= 0:
            raise ValueError("stable_seconds must be greater than zero")
        self.backoff = tuple(float(value) for value in backoff)
        self.stable_seconds = float(stable_seconds)
        self._controllers: dict[str, RecoveryController] = {}

    def controller(self, key: str) -> RecoveryController:
        controller = self._controllers.get(key)
        if controller is None:
            controller = RecoveryController(
                key=key,
                backoff=self.backoff,
                stable_seconds=self.stable_seconds,
            )
            self._controllers[key] = controller
        return controller

    def forget(self, key: str) -> None:
        self._controllers.pop(key, None)

    def controller_count(self) -> int:
        return len(self._controllers)

    def on_failure(
        self,
        key: str,
        now: float,
        interface: str | None = None,
        path: str | None = None,
    ) -> tuple[int, float]:
        controller = self.controller(key)
        if interface is not None:
            controller.interface = interface
        if path is not None:
            controller.path = path
        return controller.note_failure(now)

    def on_open(
        self,
        key: str,
        now: float,
        interface: str | None = None,
        path: str | None = None,
    ) -> bool:
        controller = self.controller(key)
        if interface is not None:
            controller.interface = interface
        if path is not None:
            controller.path = path
        return controller.note_open(now)

    def ready(self, key: str, now: float) -> bool:
        controller = self._controllers.get(key)
        if controller is None:
            return True
        return controller.ready(now)

    def tick(self, now: float) -> list[RecoveryController]:
        """Return controllers that just reached the stability window."""

        settled: list[RecoveryController] = []
        for controller in self._controllers.values():
            if controller.settle(now):
                settled.append(controller)
        return settled

    def next_due(self) -> float | None:
        """Earliest moment at which a new attempt may run."""

        due = [
            controller.next_due
            for controller in self._controllers.values()
            if controller.next_due is not None
        ]
        return min(due) if due else None

    def status(self, now: float) -> dict[str, Any]:
        pending = [
            controller
            for controller in self._controllers.values()
            if controller.next_due is not None and not controller.is_connected(now)
        ]
        pending.sort(key=lambda item: item.next_due or 0.0)
        return {
            "active": any(
                controller.attempts > 0 and not controller.settled
                for controller in self._controllers.values()
            ),
            "stable_seconds": self.stable_seconds,
            "backoff": list(self.backoff),
            "pending": [controller.status(now) for controller in pending],
            "interfaces": [
                controller.status(now)
                for controller in self._controllers.values()
                if controller.attempts > 0 or controller.opened_at is not None
            ],
        }
