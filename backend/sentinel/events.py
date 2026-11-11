"""Structured events shared by logging, diagnostics and the user interface."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    """Stable event names. They are part of the status file contract."""

    SERVICE_STARTED = "service_started"
    SERVICE_STOPPING = "service_stopping"
    SERVICE_STOPPED = "service_stopped"
    CONFIG_LOADED = "config_loaded"
    USB_RECEIVER_DETECTED = "usb_receiver_detected"
    USB_RECEIVER_REMOVED = "usb_receiver_removed"
    HID_INTERFACE_FOUND = "hid_interface_found"
    HID_INTERFACE_OPENED = "hid_interface_opened"
    HID_INTERFACE_CLOSED = "hid_interface_closed"
    HID_INTERFACE_STALE = "hid_interface_stale"
    HID_OPEN_FAILED = "hid_open_failed"
    HID_READ_ERROR = "hid_read_error"
    HID_SELECT_ERROR = "hid_select_error"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_ATTEMPT = "recovery_attempt"
    RECOVERY_SUCCEEDED = "recovery_succeeded"
    DEVICE_STABLE = "device_stable"
    MONITOR_ERROR = "monitor_error"
    WIRED_CONNECTED = "wired_connected"
    WIRED_REMOVED = "wired_removed"


# Severity used for the journal. Events default to INFO unless listed here.
_WARNING_EVENTS = {
    EventType.USB_RECEIVER_REMOVED,
    EventType.HID_INTERFACE_CLOSED,
    EventType.HID_INTERFACE_STALE,
    EventType.HID_OPEN_FAILED,
    EventType.HID_READ_ERROR,
    EventType.HID_SELECT_ERROR,
    EventType.RECOVERY_STARTED,
    EventType.RECOVERY_ATTEMPT,
    EventType.MONITOR_ERROR,
}

# Events shown in the panel log. Individual recovery attempts and stale
# descriptors stay out: the log tells the story of a problem and its
# resolution, not every step of the monitor loop. Interface openings are
# kept so the log also shows the service holding the receivers open.
_LOG_EVENTS = {
    EventType.SERVICE_STARTED,
    EventType.SERVICE_STOPPED,
    EventType.USB_RECEIVER_DETECTED,
    EventType.USB_RECEIVER_REMOVED,
    EventType.HID_INTERFACE_OPENED,
    EventType.HID_INTERFACE_CLOSED,
    EventType.HID_OPEN_FAILED,
    EventType.HID_READ_ERROR,
    EventType.HID_SELECT_ERROR,
    EventType.RECOVERY_STARTED,
    EventType.RECOVERY_SUCCEEDED,
    EventType.MONITOR_ERROR,
    EventType.WIRED_CONNECTED,
    EventType.WIRED_REMOVED,
}


def _is_log_event(event: "Event") -> bool:
    """Return whether an event belongs in the short panel log."""

    if event.kind not in _LOG_EVENTS or event.kind is EventType.HID_INTERFACE_OPENED:
        return False
    # A clean shutdown closes every descriptor. That is not a problem, and the
    # service_stopped row already marks the end of the session.
    if (
        event.kind is EventType.HID_INTERFACE_CLOSED
        and event.details.get("reason") == "shutdown"
    ):
        return False
    return True


def isoformat(timestamp: float | None) -> str | None:
    """Convert a wall clock timestamp to a stable ISO 8601 string."""

    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Event:
    """A single observation produced by the backend."""

    kind: EventType
    message: str
    timestamp: float = field(default_factory=time.time)
    interface: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind.value,
            "timestamp": isoformat(self.timestamp),
            "interface": self.interface,
            "message": self.message,
            "details": self.details,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def to_line(self) -> str:
        fields = " ".join(f"{key}={value!r}" for key, value in sorted(self.details.items()))
        prefix = f"[{self.interface}] " if self.interface else ""
        suffix = f" {fields}" if fields else ""
        return f"{prefix}{self.message} (event={self.kind.value}){suffix}"


@dataclass
class SessionStats:
    """Counters for the current service session.

    They live in memory only and disappear when the service restarts. Events
    of the same kind inside the same second collapse into one episode, so a
    single receiver unplug that closes five interfaces is one disconnect.
    """

    recoveries: int = 0
    disconnects: int = 0
    last_recovery_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recoveries": self.recoveries,
            "disconnects": self.disconnects,
            "last_recovery_at": isoformat(self.last_recovery_at),
        }


class EventBus:
    """Fan out events to the journal, an in-memory history and subscribers.

    Subscribers receive the :class:`Event` object. The status publisher uses
    a subscriber to refresh the JSON snapshot as soon as something happens.
    The bus keeps two bounded lists: every event for diagnostics and a shorter
    list of significant events for the panel log.
    """

    def __init__(
        self,
        logger: logging.Logger,
        history: int = 20,
        clock: Callable[[], float] = time.time,
        stats_path: str | None = None,
    ) -> None:
        self.logger = logger
        self.clock = clock
        self._history: deque[Event] = deque(maxlen=max(0, history))
        self._log: deque[Event] = deque(maxlen=max(0, history))
        # Interface openings are routine. They get their own short list so a
        # fresh session cannot push a real problem out of the panel log.
        self._opened: deque[Event] = deque(maxlen=min(max(0, history), 5))
        self.stats_path = Path(stats_path) if stats_path else None
        self._subscribers: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()
        self.last_event: Event | None = None
        self._session = SessionStats()
        self._last_recovery_second: int | None = None
        self._last_disconnect_second: int | None = None
        self._load_stats()

    def _load_stats(self) -> None:
        """Restore the counters saved by a previous run, if any."""

        if self.stats_path is None:
            return
        try:
            data = json.loads(self.stats_path.read_text(encoding="utf-8"))
            self._session.recoveries = max(0, int(data.get("recoveries", 0)))
            self._session.disconnects = max(0, int(data.get("disconnects", 0)))
            last = data.get("last_recovery_at")
            self._session.last_recovery_at = float(last) if last is not None else None
        except (OSError, ValueError, TypeError, AttributeError):
            return

    def _save_stats(self) -> None:
        """Write the counters atomically. A failure never stops monitoring."""

        if self.stats_path is None:
            return
        data = {
            "recoveries": self._session.recoveries,
            "disconnects": self._session.disconnects,
            "last_recovery_at": self._session.last_recovery_at,
        }
        try:
            self.stats_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.stats_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.stats_path)
        except OSError:
            self.logger.debug("cannot save stats", exc_info=True)

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Event], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def emit(
        self,
        kind: EventType,
        message: str,
        interface: str | None = None,
        **details: Any,
    ) -> Event:
        event = Event(
            kind=kind,
            message=message,
            timestamp=self.clock(),
            interface=interface,
            details=details,
        )
        level = logging.WARNING if kind in _WARNING_EVENTS else logging.INFO
        self.logger.log(level, "%s", event.to_line())
        with self._lock:
            self._history.append(event)
            if _is_log_event(event):
                self._log.append(event)
            elif event.kind is EventType.HID_INTERFACE_OPENED:
                self._opened.append(event)
            self._track_session(event)
            self.last_event = event
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:  # A broken subscriber must not stop monitoring.
                self.logger.exception("event subscriber failed")
        return event

    def _track_session(self, event: Event) -> None:
        """Update the session counters. Called with the lock held."""

        second = int(event.timestamp)
        if event.kind is EventType.RECOVERY_SUCCEEDED:
            if self._last_recovery_second != second:
                self._last_recovery_second = second
                self._session.recoveries += 1
                self._session.last_recovery_at = event.timestamp
                self._save_stats()
        elif event.kind is EventType.USB_RECEIVER_REMOVED:
            if self._last_disconnect_second != second:
                self._last_disconnect_second = second
                self._session.disconnects += 1
                self._save_stats()

    def session(self) -> dict[str, Any]:
        """Counters for the current session, safe to publish."""

        with self._lock:
            return self._session.to_dict()

    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._history)
        if limit is not None:
            events = events[-limit:] if limit > 0 else []
        return [event.to_dict() for event in events]

    def notable(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Significant events for the panel log, oldest first."""

        with self._lock:
            events = sorted(
                list(self._log) + list(self._opened), key=lambda item: item.timestamp
            )
        if limit is not None:
            events = events[-limit:] if limit > 0 else []
        return [event.to_dict() for event in events]

    @property
    def history_size(self) -> int:
        with self._lock:
            return len(self._history)

    @property
    def notable_size(self) -> int:
        with self._lock:
            return len(self._log) + len(self._opened)
