"""Charging detection: the keyboard enumerates on its own when the cable is in."""

import tempfile
import unittest
from pathlib import Path

from backend.sentinel.usb_detector import scan_wired


def make_device(root: Path, name: str, vendor: str, product: str, klass: str = "00") -> None:
    path = root / name
    path.mkdir(parents=True)
    (path / "idVendor").write_text(vendor + "\n")
    (path / "idProduct").write_text(product + "\n")
    (path / "bDeviceClass").write_text(klass + "\n")
    (path / "product").write_text("AK820 Pro\n")


class ScanWiredTests(unittest.TestCase):
    def test_receiver_alone_is_not_charging(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_device(Path(tmp), "1-2.2", "0c45", "fdfd")
            self.assertIsNone(scan_wired("0c45", "fdfd", tmp))

    def test_second_device_of_same_vendor_is_the_cable(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_device(Path(tmp), "1-2.2", "0c45", "fdfd")
            make_device(Path(tmp), "1-4", "0c45", "8009")
            found = scan_wired("0c45", "fdfd", tmp)
            self.assertEqual((found.usb_path, found.product), ("1-4", "8009"))

    def test_hubs_and_other_vendors_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_device(Path(tmp), "1-3", "0c45", "1234", klass="09")
            make_device(Path(tmp), "1-5", "1532", "005c")
            self.assertIsNone(scan_wired("0c45", "fdfd", tmp))


if __name__ == "__main__":
    unittest.main()


class PersistenceAndLogTests(unittest.TestCase):
    def test_counters_survive_a_restart(self):
        import logging
        from backend.sentinel.events import EventBus, EventType

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state" / "stats.json")
            bus = EventBus(logging.getLogger("t"), stats_path=path)
            bus.emit(EventType.RECOVERY_SUCCEEDED, "HID interface reopened")
            again = EventBus(logging.getLogger("t"), stats_path=path)
            self.assertEqual(again.session()["recoveries"], 1)
            self.assertIsNotNone(again.session()["last_recovery_at"])

    def test_openings_do_not_push_problems_out_of_the_log(self):
        import logging
        from backend.sentinel.events import EventBus, EventType

        bus = EventBus(logging.getLogger("t"), history=6)
        bus.emit(EventType.HID_INTERFACE_CLOSED, "HID interface disconnected", reason="read_error")
        for number in range(20):
            bus.emit(EventType.HID_INTERFACE_OPENED, "HID interface opened", interface=str(number))
        kinds = [item["type"] for item in bus.notable()]
        self.assertIn("hid_interface_closed", kinds)
