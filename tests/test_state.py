"""Tests for the state store and the status snapshot."""

from __future__ import annotations

import unittest

import support
from sentinel.state import DeviceState, StateStore


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = StateStore("0c45", "fdfd")
        self.interface = support.make_interface()

    def test_new_interface_starts_detected(self) -> None:
        state = self.store.upsert_interface(self.interface, 10.0)
        self.assertEqual(state.state, DeviceState.DETECTED)
        self.assertIsNone(state.descriptor)
        self.assertEqual(state.first_seen, 10.0)
        self.assertEqual(state.last_check, 10.0)

    def test_full_transition(self) -> None:
        self.store.upsert_interface(self.interface, 1.0)
        self.store.mark_open(self.interface.key, 42, 2.0)
        state = self.store.get(self.interface.key)
        self.assertEqual(state.state, DeviceState.CONNECTED)
        self.assertEqual(state.descriptor, 42)
        self.assertEqual(self.store.open_count(), 1)

        self.store.mark_stable(self.interface.key, 32.0)
        state = self.store.get(self.interface.key)
        self.assertEqual(state.state, DeviceState.STABLE)
        self.assertEqual(state.attempts, 0)
        self.assertEqual(state.stable_at, 32.0)

        self.store.mark_closed(self.interface.key, 40.0, error="device removed", attempts=2)
        state = self.store.get(self.interface.key)
        self.assertEqual(state.state, DeviceState.RECOVERING)
        self.assertIsNone(state.descriptor)
        self.assertIsNone(state.connected_at)
        self.assertEqual(state.attempts, 2)
        self.assertEqual(state.last_error, "device removed")
        self.assertEqual(self.store.open_count(), 0)

    def test_attempts_are_tracked(self) -> None:
        self.store.upsert_interface(self.interface, 1.0)
        self.store.set_attempts(self.interface.key, 3)
        self.assertEqual(self.store.get(self.interface.key).attempts, 3)

    def test_touch_updates_last_check(self) -> None:
        self.store.upsert_interface(self.interface, 1.0)
        self.store.touch(self.interface.key, 9.0)
        self.assertEqual(self.store.get(self.interface.key).last_check, 9.0)
        self.assertEqual(self.store.get(self.interface.key).last_event, "report")

    def test_interface_removal(self) -> None:
        self.store.upsert_interface(self.interface, 1.0)
        self.store.remove_interface(self.interface.key)
        self.assertIsNone(self.store.get(self.interface.key))
        self.assertEqual(self.store.interfaces(), [])

    def test_receiver_and_interface_are_separate(self) -> None:
        self.store.set_receiver(True, 5.0, name="2.4G Dongle", usb_path="1-2.2")
        self.assertTrue(self.store.receiver.present)
        self.assertEqual(self.store.receiver.name, "2.4G Dongle")
        # Receiver present, no interface open: never reported as connected.
        snapshot = self.store.snapshot(6.0)
        self.assertEqual(snapshot["receiver"]["state"], "detected")
        self.assertEqual(snapshot["summary"]["open"], 0)
        self.assertEqual(snapshot["interfaces"], [])

    def test_snapshot_summary(self) -> None:
        first = support.make_interface(interface="0", path="/dev/hidraw0")
        second = support.make_interface(interface="1", path="/dev/hidraw1")
        self.store.set_receiver(True, 1.0, name="2.4G Dongle", usb_path="1-2.2")
        self.store.upsert_interface(first, 1.0)
        self.store.upsert_interface(second, 1.0)
        self.store.mark_open(first.key, 10, 2.0)
        self.store.mark_stable(first.key, 32.0)
        self.store.mark_open(second.key, 11, 3.0)

        snapshot = self.store.snapshot(5.0)
        self.assertEqual(snapshot["summary"], {
            "total": 2,
            "open": 2,
            "stable": 1,
            "recovering": 0,
            "errors": 0,
            "lifeline_active": True,
            "total_reports": 0,
        })
        self.assertEqual(snapshot["interfaces"][0]["interface"], "0")
        self.assertTrue(snapshot["interfaces"][0]["open"])
        self.assertEqual(snapshot["receiver"]["state"], "detected")
        self.assertIsNotNone(snapshot["updated_at"])

    def test_snapshot_marks_recovering_interfaces(self) -> None:
        self.store.upsert_interface(self.interface, 1.0)
        self.store.mark_closed(self.interface.key, 2.0, error="gone", attempts=1)
        snapshot = self.store.snapshot(3.0)
        self.assertEqual(snapshot["summary"]["recovering"], 1)
        self.assertEqual(snapshot["interfaces"][0]["state"], "recovering")
        self.assertFalse(snapshot["interfaces"][0]["open"])

    def test_wall_clock_is_readable(self) -> None:
        self.store = StateStore("0c45", "fdfd", started_at=0.0, clock_offset=1_700_000_000.0)
        self.store.upsert_interface(self.interface, 1.0)
        snapshot = self.store.snapshot(2.0)
        self.assertTrue(snapshot["started_at"].startswith("2023-"))
        self.assertTrue(snapshot["interfaces"][0]["first_seen"].startswith("2023-"))


if __name__ == "__main__":
    unittest.main()
