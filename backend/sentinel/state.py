"""Device and interface state.

The store keeps two different facts apart:

* whether the USB receiver is present at all;
* whether each HID interface is open, recovering or stable.

The presence of a ``/dev/hidraw`` node does not prove that the wireless link
works, so the state model never reports the keyboard as connected on the sole
basis of the receiver being plugged in.

Times are stored on the monotonic clock, which is what the monitor uses for
intervals. The store also holds the wall clock offset captured at start, so
snapshots still expose readable ISO timestamps.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .events import isoformat


class DeviceState(str, Enum):
    UNKNOWN = "unknown"
    DETECTED = "detected"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECOVERING = "recovering"
    STABLE = "stable"
    ERROR = "error"


@dataclass
class InterfaceState:
    """Everything Sentinel knows about one HID interface."""

    key: str
    interface: str
    path: str
    usb_path: str
    hid_id: str
    hid_name: str
    state: DeviceState = DeviceState.DETECTED
    descriptor: int | None = None
    attempts: int = 0
    last_error: str | None = None
    last_event: str | None = None
    first_seen: float | None = None
    connected_at: float | None = None
    stable_at: float | None = None
    last_check: float | None = None
    node_inode: int | None = None
    node_rdev: int | None = None
    endpoints: int = 1
    is_lifeline: bool = False
    reports: int = 0

    def to_dict(self, now: float, wall: Callable[[float | None], str | None]) -> dict[str, Any]:
        open_for = None
        if self.connected_at is not None:
            open_for = max(0.0, now - self.connected_at)
        return {
            "key": self.key,
            "interface": self.interface,
            "device": self.path,
            "usb_path": self.usb_path,
            "hid_id": self.hid_id,
            "state": self.state.value,
            "open": self.descriptor is not None,
            "attempts": self.attempts,
            "endpoints": self.endpoints,
            "is_lifeline": self.is_lifeline,
            "reports": self.reports,
            "last_error": self.last_error,
            "last_event": self.last_event,
            "first_seen": wall(self.first_seen),
            "connected_at": wall(self.connected_at),
            "stable_at": wall(self.stable_at),
            "last_check": wall(self.last_check),
            "open_for_seconds": round(open_for, 2) if open_for is not None else None,
        }


@dataclass
class ReceiverState:
    present: bool = False
    vendor: str = ""
    product: str = ""
    name: str = ""
    usb_path: str = ""
    last_seen: float | None = None

    def to_dict(self, wall: Callable[[float | None], str | None]) -> dict[str, Any]:
        return {
            "state": "detected" if self.present else "disconnected",
            "present": self.present,
            "vendor_id": self.vendor,
            "product_id": self.product,
            "name": self.name,
            "usb_path": self.usb_path,
            "last_seen": wall(self.last_seen),
        }


@dataclass
class WiredState:
    """The keyboard seen on its USB cable, which means it is charging."""

    present: bool = False
    product: str = ""
    name: str = ""
    usb_path: str = ""
    since: float | None = None

    def to_dict(self, wall: Callable[[float | None], str | None]) -> dict[str, Any]:
        return {
            "present": self.present,
            "charging": self.present,
            "product_id": self.product,
            "name": self.name,
            "usb_path": self.usb_path,
            "since": wall(self.since),
        }


@dataclass
class StateStore:
    """Thread safe container for the receiver and its interfaces."""

    vendor: str
    product: str
    started_at: float = field(default_factory=time.monotonic)
    clock_offset: float = field(default_factory=lambda: time.time() - time.monotonic())
    receiver: ReceiverState = field(init=False)
    wired: WiredState = field(default_factory=WiredState)
    _interfaces: dict[str, InterfaceState] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.receiver = ReceiverState(
            present=False, vendor=self.vendor, product=self.product
        )

    def wall(self, timestamp: float | None) -> str | None:
        if timestamp is None:
            return None
        return isoformat(timestamp + self.clock_offset)

    def upsert_interface(self, info: Any, now: float) -> InterfaceState:
        """Create or refresh the state for a discovered interface."""

        with self._lock:
            state = self._interfaces.get(info.key)
            if state is None:
                state = InterfaceState(
                    key=info.key,
                    interface=info.interface,
                    path=info.path,
                    usb_path=info.usb_path,
                    hid_id=info.hid_id,
                    hid_name=info.hid_name,
                    first_seen=now,
                    last_check=now,
                    node_inode=info.node_inode,
                    node_rdev=info.node_rdev,
                    endpoints=getattr(info, "endpoints", 1),
                    is_lifeline=getattr(info, "is_lifeline", False),
                )
                self._interfaces[info.key] = state
            else:
                state.interface = info.interface
                state.path = info.path
                state.usb_path = info.usb_path
                state.hid_id = info.hid_id
                state.hid_name = info.hid_name
                state.last_check = now
                state.node_inode = info.node_inode
                state.node_rdev = info.node_rdev
                state.endpoints = getattr(info, "endpoints", state.endpoints)
                state.is_lifeline = getattr(info, "is_lifeline", state.is_lifeline)
            return state

    def get(self, key: str) -> InterfaceState | None:
        with self._lock:
            return self._interfaces.get(key)

    def interfaces(self) -> list[InterfaceState]:
        with self._lock:
            return list(self._interfaces.values())

    def remove_interface(self, key: str) -> None:
        with self._lock:
            self._interfaces.pop(key, None)

    def mark_open(self, key: str, descriptor: int, now: float) -> None:
        with self._lock:
            state = self._interfaces.get(key)
            if state is None:
                return
            state.state = DeviceState.CONNECTED
            state.descriptor = descriptor
            state.connected_at = now
            state.last_check = now
            state.last_error = None
            state.last_event = "opened"

    def mark_stable(self, key: str, now: float) -> None:
        with self._lock:
            state = self._interfaces.get(key)
            if state is None:
                return
            state.state = DeviceState.STABLE
            state.stable_at = now
            state.attempts = 0
            state.last_check = now
            state.last_event = "stable"

    def mark_closed(
        self,
        key: str,
        now: float,
        error: str | None = None,
        attempts: int | None = None,
    ) -> None:
        with self._lock:
            state = self._interfaces.get(key)
            if state is None:
                return
            state.descriptor = None
            state.connected_at = None
            state.stable_at = None
            state.state = DeviceState.RECOVERING
            state.last_error = error
            state.last_check = now
            state.last_event = "closed"
            if attempts is not None:
                state.attempts = attempts

    def mark_error(self, key: str, now: float, error: str) -> None:
        with self._lock:
            state = self._interfaces.get(key)
            if state is None:
                return
            state.state = DeviceState.ERROR
            state.last_error = error
            state.last_check = now
            state.last_event = "error"

    def mark_disconnected(self, key: str, now: float, error: str | None = None) -> None:
        """Record a clean shutdown without scheduling recovery."""

        with self._lock:
            state = self._interfaces.get(key)
            if state is None:
                return
            state.descriptor = None
            state.connected_at = None
            state.stable_at = None
            state.state = DeviceState.DISCONNECTED
            state.last_error = error
            state.last_check = now
            state.last_event = "disconnected"

    def set_attempts(self, key: str, attempts: int) -> None:
        with self._lock:
            state = self._interfaces.get(key)
            if state is not None:
                state.attempts = attempts

    def touch(self, key: str, now: float, reports: int | None = None) -> None:
        """Record that an open handle just produced data."""

        with self._lock:
            state = self._interfaces.get(key)
            if state is not None:
                state.last_check = now
                state.last_event = "report"
                if reports is not None:
                    state.reports = reports
                else:
                    state.reports += 1

    def set_receiver(
        self,
        present: bool,
        now: float,
        vendor: str | None = None,
        product: str | None = None,
        name: str | None = None,
        usb_path: str | None = None,
    ) -> None:
        with self._lock:
            self.receiver.present = present
            self.receiver.vendor = vendor or self.vendor
            self.receiver.product = product or self.product
            if name:
                self.receiver.name = name
            if usb_path:
                self.receiver.usb_path = usb_path
            if present:
                self.receiver.last_seen = now

    def open_count(self) -> int:
        with self._lock:
            return sum(
                1 for state in self._interfaces.values() if state.descriptor is not None
            )

    def snapshot(
        self,
        now: float,
        recovery: Any = None,
        events: Any = None,
        version: str = "",
        app_name: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the payload written to the status file."""

        with self._lock:
            states = sorted(
                list(self._interfaces.values()),
                key=lambda item: (
                    int(item.interface) if item.interface.isdigit() else 0,
                    item.interface,
                ),
            )
            receiver = self.receiver.to_dict(self.wall)
            open_count = sum(1 for state in states if state.descriptor is not None)
            stable_count = sum(1 for state in states if state.state is DeviceState.STABLE)
            recovering = [
                state for state in states if state.state is DeviceState.RECOVERING
            ]
            error_count = sum(1 for state in states if state.state is DeviceState.ERROR)
            payload: dict[str, Any] = {
                "schema_version": 1,
                "app": app_name,
                "version": version,
                "started_at": self.wall(self.started_at),
                "updated_at": self.wall(now),
                "uptime_seconds": round(max(0.0, now - self.started_at), 2),
                "receiver": receiver,
                "wired": self.wired.to_dict(self.wall),
                "interfaces": [state.to_dict(now, self.wall) for state in states],
                "summary": {
                    "total": len(states),
                    "open": open_count,
                    "stable": stable_count,
                    "recovering": len(recovering),
                    "errors": error_count,
                    "lifeline_active": any(state.descriptor is not None and state.is_lifeline for state in states) or (open_count >= 2),
                    "total_reports": sum(state.reports for state in states),
                },
            }
        if recovery is not None:
            payload["recovery"] = recovery.status(now)
        if events is not None:
            payload["last_event"] = events.last_event.to_dict() if events.last_event else None
            payload["event_log"] = events.notable()
            payload["session"] = events.session()
        if extra:
            payload.update(extra)
        return payload
