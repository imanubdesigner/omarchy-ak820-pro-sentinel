"""Tests for descriptor lifecycle, stale handle cleanup and recovery wiring."""

from __future__ import annotations

import errno
import logging
import os
import unittest

import support
from sentinel.config import Config, validate_config
from sentinel.events import EventBus
from sentinel.hid_monitor import HidMonitor
from sentinel.recovery import RecoveryEngine
from sentinel.state import DeviceState, StateStore


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("sentinel.test.monitor")
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.fs = support.FakeFileSystem()
        self.clock = support.FakeClock(0.0)
        self.config = validate_config(
            Config(
                vendor="0c45",
                product="fdfd",
                rescan_interval=3.0,
                settling_interval=0.4,
                status_file="/tmp/sentinel-test-status.json",
            )
        )
        self.store = StateStore("0c45", "fdfd")
        self.events = EventBus(self.logger, history=100, clock=self.clock)
        self.recovery = RecoveryEngine(self.config.backoff, self.config.stable_seconds)
        self.desired: dict[str, object] = {}
        self.readable: list[int] = []
        self.exceptional: list[int] = []
        self.select_error: OSError | None = None
        self.reader_result: bytes = b""
        self.reader_error: OSError | None = None
        self.scan_calls = 0

        def scanner(**_kwargs):
            self.scan_calls += 1
            return dict(self.desired)

        def selector(_read, _write, _exceptional, _timeout):
            if self.select_error is not None:
                raise self.select_error
            return (list(self.readable), [], list(self.exceptional))

        def reader(_descriptor, _size):
            if self.reader_error is not None:
                raise self.reader_error
            return self.reader_result

        self.monitor = HidMonitor(
            self.config,
            self.store,
            self.events,
            self.recovery,
            scanner=scanner,
            clock=self.clock,
            opener=self.fs.open,
            closer=self.fs.close,
            stat=self.fs.stat,
            selector=selector,
            reader=reader,
            probe=None,
            usb_info=lambda _path: None,
        )

    def discover(
        self,
        interface: str = "0",
        inode: int = 100,
        path: str | None = None,
        usb_path: str = "1-2.2",
    ):
        key = f"{usb_path}:1.{interface}"
        item = support.make_interface(
            interface=interface,
            path=path or f"/dev/hidraw{interface}",
            inode=inode,
            key=key,
            usb_path=usb_path,
        )
        self.fs.add(item.path, inode=inode, rdev=item.node_rdev)
        self.desired[key] = item
        return item

    def test_rescan_opens_interfaces_read_only(self) -> None:
        item = self.discover()
        changed = self.monitor.rescan(0.0)
        self.assertTrue(changed)
        self.assertIn(item.key, self.monitor.connections)
        self.assertEqual(len(self.fs.opened), 1)
        _path, flags = self.fs.opened[0]
        self.assertTrue((flags & os.O_ACCMODE) == os.O_RDONLY)
        self.assertTrue(flags & os.O_NONBLOCK)
        state = self.store.get(item.key)
        self.assertEqual(state.state, DeviceState.CONNECTED)
        self.assertEqual(state.descriptor, 3)
        self.assertEqual(self.store.receiver.present, True)
        self.assertIn("hid_interface_opened", [event["type"] for event in self.events.recent()])

    def test_rescan_interval_is_respected(self) -> None:
        self.discover()
        self.monitor.step(0.0)
        self.assertEqual(self.scan_calls, 1)
        self.monitor.step(1.0)
        self.assertEqual(self.scan_calls, 1)
        self.monitor.step(3.01)
        self.assertEqual(self.scan_calls, 2)

    def test_receiver_detection_events(self) -> None:
        self.discover()
        self.monitor.rescan(0.0)
        types = [event["type"] for event in self.events.recent()]
        self.assertIn("usb_receiver_detected", types)
        self.desired.clear()
        self.monitor.rescan(1.0)
        types = [event["type"] for event in self.events.recent()]
        self.assertIn("usb_receiver_removed", types)
        self.assertFalse(self.store.receiver.present)

    def test_removed_interface_closes_descriptor_and_schedules_recovery(self) -> None:
        item = self.discover()
        self.discover("1")
        self.monitor.rescan(0.0)
        del self.desired[item.key]
        self.monitor.rescan(1.0)
        self.assertNotIn(item.key, self.monitor.connections)
        self.assertEqual(self.fs.closed, [3])
        state = self.store.get(item.key)
        self.assertEqual(state.state, DeviceState.RECOVERING)
        self.assertIsNone(state.descriptor)
        self.assertEqual(state.attempts, 1)
        self.assertAlmostEqual(self.recovery.next_due(), 2.0)
        reasons = [
            event["details"].get("reason")
            for event in self.events.recent()
            if event["type"] == "hid_interface_closed"
        ]
        self.assertIn("device_removed", reasons)

    def test_recreated_node_reopens_only_after_backoff(self) -> None:
        item = self.discover(inode=100)
        self.monitor.rescan(0.0)
        # The node is recreated with the same name but a different inode.
        self.desired.clear()
        self.discover(inode=300, path="/dev/hidraw0")
        self.clock.advance(1.0)
        self.monitor.rescan(1.0)
        self.assertEqual(self.monitor.connections, {})
        self.assertEqual(self.fs.closed, [3])
        self.assertEqual(self.store.get(item.key).attempts, 1)
        # Not ready before the one second backoff has elapsed.
        self.clock.advance(0.5)
        self.monitor.rescan(1.5)
        self.assertEqual(self.monitor.connections, {})
        # Ready exactly at the deadline: a new descriptor is opened.
        self.clock.advance(0.5)
        self.monitor.rescan(2.0)
        self.assertEqual(len(self.monitor.connections), 1)
        self.assertEqual(len(self.fs.opened), 2)
        descriptors = [connection.descriptor for connection in self.monitor.connections.values()]
        self.assertEqual(descriptors, [4])
        self.assertEqual(self.fs.closed, [3])

    def test_stale_descriptor_event_is_emitted(self) -> None:
        self.discover(inode=100)
        self.monitor.rescan(0.0)
        self.desired.clear()
        self.discover(inode=200)
        self.monitor.rescan(1.0)
        types = [event["type"] for event in self.events.recent()]
        self.assertIn("hid_interface_stale", types)

    def test_liveness_detects_missing_node(self) -> None:
        item = self.discover()
        self.monitor.rescan(0.0)
        self.fs.remove(item.path)
        changed = self.monitor.check_liveness(1.0)
        self.assertTrue(changed)
        self.assertEqual(self.monitor.connections, {})
        self.assertEqual(self.fs.closed, [3])
        self.assertEqual(self.store.get(item.key).attempts, 1)

    def test_liveness_detects_mismatched_descriptor(self) -> None:
        item = self.discover()
        self.monitor.rescan(0.0)
        self.monitor.probe = lambda _descriptor: ("1234", "5678")
        changed = self.monitor.check_liveness(1.0)
        self.assertTrue(changed)
        self.assertEqual(self.monitor.connections, {})
        self.assertIn("1234:5678", self.store.get(item.key).last_error)

    def test_select_error_rebuilds_every_descriptor(self) -> None:
        self.discover("0")
        self.discover("1")
        self.monitor.rescan(0.0)
        self.assertEqual(len(self.monitor.connections), 2)
        self.select_error = OSError(errno.EBADF, "bad descriptor")
        changed = self.monitor.poll(1.0)
        self.assertTrue(changed)
        self.assertEqual(self.monitor.connections, {})
        self.assertEqual(self.fs.closed, [3, 4])
        types = [event["type"] for event in self.events.recent()]
        self.assertIn("hid_select_error", types)
        for state in self.store.interfaces():
            self.assertEqual(state.attempts, 1)

    def test_read_error_closes_the_interface(self) -> None:
        item = self.discover()
        self.monitor.rescan(0.0)
        self.readable = [3]
        self.reader_error = OSError(errno.EIO, "input/output error")
        changed = self.monitor.poll(1.0)
        self.assertTrue(changed)
        self.assertEqual(self.monitor.connections, {})
        self.assertEqual(self.fs.closed, [3])
        self.assertEqual(self.store.get(item.key).attempts, 1)

    def test_empty_read_closes_the_interface(self) -> None:
        item = self.discover()
        self.monitor.rescan(0.0)
        self.readable = [3]
        self.reader_result = b""
        changed = self.monitor.poll(1.0)
        self.assertTrue(changed)
        self.assertEqual(self.monitor.connections, {})
        self.assertEqual(self.fs.closed, [3])
        self.assertEqual(self.store.get(item.key).state, DeviceState.RECOVERING)

    def test_reports_are_counted_and_ignored(self) -> None:
        self.discover()
        self.monitor.rescan(0.0)
        self.readable = [3]
        self.reader_result = b"\x01\x02"
        changed = self.monitor.poll(1.0)
        # Incoming data is not a state change and must not close the handle.
        self.assertFalse(changed)
        connection = self.monitor.connections["1-2.2:1.0"]
        self.assertEqual(connection.reports, 1)
        self.assertEqual(connection.last_read, 1.0)
        self.assertEqual(len(self.fs.closed), 0)

    def test_stability_resets_attempts(self) -> None:
        item = self.discover()
        self.monitor.rescan(0.0)
        self.assertFalse(self.monitor.apply_stability(29.9))
        self.assertTrue(self.monitor.apply_stability(30.0))
        state = self.store.get(item.key)
        self.assertEqual(state.state, DeviceState.STABLE)
        self.assertEqual(state.attempts, 0)
        self.assertTrue(self.recovery.controller(item.key).settled)
        # The next tick must not repeat the event.
        self.assertFalse(self.monitor.apply_stability(31.0))

    def test_no_orphan_descriptors(self) -> None:
        self.discover("0")
        self.discover("1")
        self.monitor.rescan(0.0)
        self.desired.clear()
        self.monitor.rescan(1.0)
        self.assertEqual(self.fs.closed, [3, 4])
        self.assertEqual(len(self.fs.opened), 2)
        self.assertEqual(len(set(self.fs.closed)), 2)

    def test_shutdown_closes_every_descriptor(self) -> None:
        self.discover("0")
        self.discover("1")
        self.monitor.rescan(0.0)
        self.monitor._close_all("shutdown", 1.0, schedule=False)
        self.assertEqual(self.fs.closed, [3, 4])
        self.assertEqual(self.monitor.connections, {})
        # Shutdown must not schedule recovery attempts.
        self.assertIsNone(self.recovery.next_due())
        for state in self.store.interfaces():
            self.assertEqual(state.state, DeviceState.DISCONNECTED)

    def test_probe_is_used_for_mismatch_detection(self) -> None:
        self.discover()
        self.monitor.rescan(0.0)
        self.monitor.probe = lambda _descriptor: ("0c45", "fdfd")
        self.assertFalse(self.monitor.check_liveness(1.0))
        self.assertEqual(len(self.monitor.connections), 1)

    def test_receiver_removal_prunes_interface_state(self) -> None:
        self.discover("0")
        self.discover("1")
        self.monitor.rescan(0.0)
        self.desired.clear()
        self.monitor.rescan(1.0)
        self.assertEqual(self.store.interfaces(), [])
        self.assertEqual(self.recovery.controller_count(), 0)
        snapshot = self.store.snapshot(2.0, recovery=self.recovery)
        self.assertEqual(snapshot["receiver"]["state"], "disconnected")
        self.assertEqual(snapshot["summary"]["total"], 0)
        self.assertEqual(snapshot["interfaces"], [])

    def test_missing_interface_is_kept_while_receiver_is_present(self) -> None:
        self.discover("0")
        missing = self.discover("1")
        self.monitor.rescan(0.0)
        del self.desired[missing.key]
        self.monitor.rescan(1.0)
        state = self.store.get(missing.key)
        self.assertIsNotNone(state)
        self.assertEqual(state.state, DeviceState.RECOVERING)
        snapshot = self.store.snapshot(2.0, recovery=self.recovery)
        self.assertEqual(snapshot["receiver"]["state"], "detected")
        self.assertEqual(snapshot["summary"]["total"], 2)
        self.assertEqual(snapshot["summary"]["recovering"], 1)

    def test_receiver_on_another_port_replaces_stale_state(self) -> None:
        old = self.discover("0")
        self.monitor.rescan(0.0)
        self.desired.clear()
        new = self.discover("0", inode=700, path="/dev/hidraw7", usb_path="1-3")
        self.monitor.rescan(1.0)
        self.assertIsNone(self.store.get(old.key))
        self.assertIsNotNone(self.store.get(new.key))
        self.assertEqual(self.recovery.controller_count(), 1)
        self.assertEqual(len(self.monitor.connections), 1)
        self.assertEqual(self.fs.closed, [3])


if __name__ == "__main__":
    unittest.main()
