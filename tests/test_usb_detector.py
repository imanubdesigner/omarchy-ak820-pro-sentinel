"""Tests for the sysfs detector, without any physical device."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from sentinel.usb_detector import (
    normalize_hex_id,
    scan_interfaces,
    usb_device_info,
)


class DetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sys_class = self.root / "sys/class/hidraw"
        self.dev_root = self.root / "dev"
        self.usb_devices = self.root / "sys/bus/usb/devices"
        self.sys_class.mkdir(parents=True)
        self.dev_root.mkdir(parents=True)
        self.usb_devices.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_interface(
        self,
        name: str = "hidraw0",
        usb_interface: str = "1-2.2:1.0",
        hid_dirname: str = "0003:0C45:FDFD.0001",
        vendor: str = "0c45",
        product: str = "fdfd",
        node: bool = True,
        uevent: bool = True,
        create_usb_device: bool = False,
    ) -> Path:
        hid_dir = (
            self.root
            / "sys/devices/pci0000:00/0000:00:14.0/usb1/1-2/1-2.2"
            / usb_interface
            / hid_dirname
        )
        hid_dir.mkdir(parents=True)
        if uevent:
            (hid_dir / "uevent").write_text(
                "DRIVER=hid-generic\n"
                f"HID_ID=0003:0000{vendor.upper()}:0000{product.upper()}\n"
                "HID_NAME=2.4G Dongle\n",
                encoding="utf-8",
            )
        entry = self.sys_class / name
        entry.mkdir()
        (entry / "device").symlink_to(hid_dir)
        if node:
            (self.dev_root / name).write_text("", encoding="utf-8")
        if create_usb_device:
            usb_path = usb_interface.rsplit(":", 1)[0]
            device = self.usb_devices / usb_path
            device.mkdir(parents=True, exist_ok=True)
            (device / "idVendor").write_text(vendor + "\n", encoding="utf-8")
            (device / "idProduct").write_text(product + "\n", encoding="utf-8")
            (device / "product").write_text("2.4G Dongle\n", encoding="utf-8")
            (device / "manufacturer").write_text("Microdia\n", encoding="utf-8")
        return hid_dir

    def test_normalize(self) -> None:
        self.assertEqual(normalize_hex_id("0C45"), "0c45")
        self.assertEqual(normalize_hex_id("fdfd"), "fdfd")
        self.assertEqual(normalize_hex_id("0xC45"), "0c45")
        self.assertEqual(normalize_hex_id(" C45 "), "0c45")

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            normalize_hex_id("xyz")
        with self.assertRaises(ValueError):
            normalize_hex_id("0c45f")
        with self.assertRaises(ValueError):
            normalize_hex_id("")

    def test_finds_matching_interface(self) -> None:
        self.add_interface()
        found = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        self.assertEqual(list(found), ["1-2.2:1.0"])
        item = found["1-2.2:1.0"]
        self.assertEqual(item.interface, "0")
        self.assertEqual(item.usb_path, "1-2.2")
        self.assertEqual(item.vendor, "0c45")
        self.assertEqual(item.product, "fdfd")
        self.assertEqual(item.hid_name, "2.4G Dongle")
        self.assertEqual(item.hid_id, "0003:0c45:fdfd.0001")
        self.assertEqual(item.path, str(self.dev_root / "hidraw0"))
        self.assertGreater(item.node_inode, 0)

    def test_multiple_interfaces_are_keyed_by_usb_path(self) -> None:
        for index in range(5):
            self.add_interface(
                name=f"hidraw{index}",
                usb_interface=f"1-2.2:1.{index}",
                hid_dirname=f"0003:0C45:FDFD.000{index + 1}",
            )
        found = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        self.assertEqual(len(found), 5)
        self.assertEqual(
            [found[key].interface for key in sorted(found, key=lambda k: int(found[k].interface))],
            ["0", "1", "2", "3", "4"],
        )

    def test_interface_filter(self) -> None:
        self.add_interface("hidraw0", "1-2.2:1.0", "0003:0C45:FDFD.0001")
        self.add_interface("hidraw3", "1-2.2:1.3", "0003:0C45:FDFD.0004")
        found = scan_interfaces(
            "0c45", "fdfd", ("3",), str(self.sys_class), str(self.dev_root)
        )
        self.assertEqual(list(found), ["1-2.2:1.3"])

    def test_vendor_or_product_mismatch_is_ignored(self) -> None:
        self.add_interface(vendor="1234", product="5678")
        found = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        self.assertEqual(found, {})

    def test_missing_dev_node_is_ignored(self) -> None:
        self.add_interface(node=False)
        found = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        self.assertEqual(found, {})

    def test_missing_sysfs_tree_is_not_an_error(self) -> None:
        found = scan_interfaces(
            "0c45", "fdfd", None, str(self.root / "does-not-exist"), str(self.dev_root)
        )
        self.assertEqual(found, {})

    def test_directory_name_fallback_without_uevent(self) -> None:
        self.add_interface(uevent=False)
        found = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        self.assertEqual(list(found), ["1-2.2:1.0"])
        self.assertEqual(found["1-2.2:1.0"].vendor, "0c45")
        self.assertEqual(found["1-2.2:1.0"].hid_name, "")

    def test_renamed_node_keeps_the_same_key(self) -> None:
        self.add_interface("hidraw0", "1-2.2:1.0", "0003:0C45:FDFD.0001")
        first = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        (self.sys_class / "hidraw0/device").unlink()
        (self.sys_class / "hidraw0").rmdir()
        (self.dev_root / "hidraw0").unlink()
        self.add_interface("hidraw7", "1-2.2:1.0", "0003:0D00:FDFD.0009")
        second = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        self.assertEqual(list(first), list(second))
        self.assertEqual(second["1-2.2:1.0"].path, str(self.dev_root / "hidraw7"))
        self.assertNotEqual(first["1-2.2:1.0"].hid_id, second["1-2.2:1.0"].hid_id)

    def test_incomplete_hid_identity_is_skipped(self) -> None:
        hid_dir = self.root / "sys/devices/usb1/1-2/1-2.2:1.0/strange"
        hid_dir.mkdir(parents=True)
        entry = self.sys_class / "hidraw0"
        entry.mkdir()
        (entry / "device").symlink_to(hid_dir)
        (self.dev_root / "hidraw0").write_text("", encoding="utf-8")
        found = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        self.assertEqual(found, {})

    def test_lifeline_endpoint_detection(self) -> None:
        hid_dir = self.add_interface("hidraw3", "1-2.2:1.3", "0003:0C45:FDFD.0004")
        (hid_dir.parent / "ep_03").mkdir()
        (hid_dir.parent / "ep_83").mkdir()
        found = scan_interfaces(
            "0c45", "fdfd", None, str(self.sys_class), str(self.dev_root)
        )
        self.assertIn("1-2.2:1.3", found)
        self.assertEqual(found["1-2.2:1.3"].endpoints, 2)
        self.assertTrue(found["1-2.2:1.3"].is_lifeline)

    def test_usb_device_info(self) -> None:
        self.add_interface(create_usb_device=True)
        info = usb_device_info("1-2.2", str(self.usb_devices))
        self.assertIsNotNone(info)
        self.assertEqual(info.vendor, "0c45")
        self.assertEqual(info.product, "fdfd")
        self.assertEqual(info.name, "2.4G Dongle")
        self.assertEqual(info.manufacturer, "Microdia")
        self.assertIsNone(usb_device_info("9-9", str(self.usb_devices)))


if __name__ == "__main__":
    unittest.main()
