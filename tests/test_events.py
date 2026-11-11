"""Tests for structured events and the history kept for the panel."""

from __future__ import annotations

import logging
import unittest

import support  # noqa: F401
from sentinel.events import EventBus, EventType


class EventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("sentinel.test.events")
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False

    def test_emit_keeps_history_and_last_event(self) -> None:
        bus = EventBus(self.logger, history=3, clock=lambda: 1000.0)
        bus.emit(EventType.SERVICE_STARTED, "started", pid=1)
        bus.emit(EventType.USB_RECEIVER_DETECTED, "found", vendor_id="0c45")
        self.assertEqual(bus.history_size, 2)
        self.assertEqual(bus.last_event.kind, EventType.USB_RECEIVER_DETECTED)
        recent = bus.recent()
        self.assertEqual(recent[0]["type"], "service_started")
        self.assertEqual(recent[1]["details"], {"vendor_id": "0c45"})
        self.assertTrue(recent[0]["timestamp"].startswith("1970-01-01T00:16:40"))

    def test_history_is_bounded(self) -> None:
        bus = EventBus(self.logger, history=2, clock=lambda: 0.0)
        for _ in range(5):
            bus.emit(EventType.MONITOR_ERROR, "error")
        self.assertEqual(bus.history_size, 2)
        self.assertEqual(len(bus.recent()), 2)

    def test_subscribers_receive_events(self) -> None:
        received = []
        bus = EventBus(self.logger, history=5, clock=lambda: 1.0)
        bus.subscribe(received.append)
        bus.emit(EventType.HID_INTERFACE_OPENED, "opened", interface="0")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].interface, "0")
        bus.unsubscribe(received.append)
        bus.emit(EventType.HID_INTERFACE_CLOSED, "closed", interface="0")
        self.assertEqual(len(received), 1)

    def test_broken_subscriber_does_not_stop_the_bus(self) -> None:
        def broken(_event):
            raise RuntimeError("boom")

        bus = EventBus(self.logger, history=5, clock=lambda: 1.0)
        bus.subscribe(broken)
        bus.emit(EventType.SERVICE_STARTED, "started")
        self.assertEqual(bus.history_size, 1)

    def test_event_line_contains_kind_and_fields(self) -> None:
        bus = EventBus(self.logger, history=5, clock=lambda: 1.0)
        event = bus.emit(
            EventType.RECOVERY_ATTEMPT, "retry", interface="2", attempt=3
        )
        line = event.to_line()
        self.assertIn("[2]", line)
        self.assertIn("event=recovery_attempt", line)
        self.assertIn("attempt=3", line)
        self.assertIn("type", event.to_dict())
        self.assertIn("recovery_attempt", event.to_json())

    def test_notable_keeps_only_significant_events(self) -> None:
        bus = EventBus(self.logger, history=10, clock=lambda: 1.0)
        bus.emit(EventType.CONFIG_LOADED, "configuration loaded")
        bus.emit(EventType.HID_INTERFACE_FOUND, "HID interface found", interface="0")
        bus.emit(EventType.DEVICE_STABLE, "connection stable", interface="0")
        bus.emit(EventType.USB_RECEIVER_REMOVED, "USB receiver removed")
        bus.emit(EventType.RECOVERY_ATTEMPT, "recovery attempt 1 scheduled", attempt=1)
        bus.emit(EventType.RECOVERY_SUCCEEDED, "HID interface reopened", attempts=2)
        self.assertEqual(bus.history_size, 6)
        self.assertEqual(bus.notable_size, 2)
        self.assertEqual(
            [event["type"] for event in bus.notable()],
            ["usb_receiver_removed", "recovery_succeeded"],
        )

    def test_interface_opened_is_a_log_event(self) -> None:
        bus = EventBus(self.logger, history=10, clock=lambda: 1.0)
        bus.emit(EventType.HID_INTERFACE_OPENED, "HID interface opened", interface="0")
        self.assertEqual(bus.notable_size, 1)
        self.assertEqual(bus.notable()[0]["type"], "hid_interface_opened")

    def test_clean_shutdown_closures_stay_out_of_the_log(self) -> None:
        bus = EventBus(self.logger, history=10, clock=lambda: 1.0)
        bus.emit(
            EventType.HID_INTERFACE_CLOSED,
            "HID interface closed",
            interface="0",
            reason="shutdown",
        )
        bus.emit(
            EventType.HID_INTERFACE_CLOSED,
            "HID interface disconnected",
            interface="1",
            reason="node_missing",
        )
        self.assertEqual(bus.history_size, 2)
        self.assertEqual(bus.notable_size, 1)
        self.assertEqual(bus.notable()[0]["details"]["reason"], "node_missing")

    def test_notable_is_bounded(self) -> None:
        bus = EventBus(self.logger, history=3, clock=lambda: 1.0)
        for _ in range(5):
            bus.emit(EventType.MONITOR_ERROR, "monitor step failed")
        self.assertEqual(bus.notable_size, 3)
        self.assertEqual(len(bus.notable()), 3)

    def test_session_counts_one_episode_per_second(self) -> None:
        now = [100.0]
        bus = EventBus(self.logger, history=20, clock=lambda: now[0])
        for _ in range(5):
            bus.emit(EventType.RECOVERY_SUCCEEDED, "HID interface reopened", attempts=1)
        bus.emit(EventType.USB_RECEIVER_REMOVED, "USB receiver removed")
        bus.emit(EventType.USB_RECEIVER_REMOVED, "USB receiver removed")
        session = bus.session()
        self.assertEqual(session["recoveries"], 1)
        self.assertEqual(session["disconnects"], 1)
        self.assertIsNotNone(session["last_recovery_at"])

        now[0] += 1
        bus.emit(EventType.RECOVERY_SUCCEEDED, "HID interface reopened", attempts=2)
        self.assertEqual(bus.session()["recoveries"], 2)

    def test_session_starts_at_zero(self) -> None:
        bus = EventBus(self.logger, history=5, clock=lambda: 1.0)
        self.assertEqual(
            bus.session(),
            {"recoveries": 0, "disconnects": 0, "last_recovery_at": None},
        )

    def test_snapshot_exposes_session_counters(self) -> None:
        from sentinel.state import StateStore

        bus = EventBus(self.logger, history=10, clock=lambda: 1.0)
        bus.emit(EventType.RECOVERY_SUCCEEDED, "HID interface reopened", attempts=1)
        snapshot = StateStore("0c45", "fdfd").snapshot(2.0, events=bus)
        self.assertEqual(snapshot["session"]["recoveries"], 1)
        self.assertEqual(snapshot["session"]["disconnects"], 0)
        self.assertIn("last_recovery_at", snapshot["session"])

    def test_snapshot_exposes_only_the_notable_log(self) -> None:
        from sentinel.state import StateStore

        bus = EventBus(self.logger, history=10, clock=lambda: 1.0)
        bus.emit(EventType.DEVICE_STABLE, "connection stable", interface="0")
        bus.emit(EventType.USB_RECEIVER_REMOVED, "USB receiver removed")
        snapshot = StateStore("0c45", "fdfd").snapshot(2.0, events=bus)
        self.assertIn("event_log", snapshot)
        self.assertNotIn("events", snapshot)
        self.assertEqual(
            [event["type"] for event in snapshot["event_log"]],
            ["usb_receiver_removed"],
        )


if __name__ == "__main__":
    unittest.main()
