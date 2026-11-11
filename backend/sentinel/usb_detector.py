"""Discover the receiver and its HID interfaces through Linux sysfs.

The detector only reads files. It never writes to the receiver, never sends
HID reports and never resets USB devices.

An interface is identified by its USB path plus its interface number, for
example ``1-2.2:1.3``. That identity survives re-enumeration, while the
``hidrawN`` name and the ``0003:0C45:FDFD.0001`` HID instance do not.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import ConfigError, normalize_vendor_id

LOG = logging.getLogger("ak820-pro-sentinel.detector")

USB_INTERFACE_RE = re.compile(r"^(?P<device>.+):1\.(?P<interface>[0-9]+)$")
HID_DIR_RE = re.compile(
    r"^(?P<bus>[0-9A-Fa-f]{4}):(?P<vendor>[0-9A-Fa-f]{4}):(?P<product>[0-9A-Fa-f]{4})(?:\.(?P<instance>[0-9]+))?$"
)
HID_Uevent_RE = re.compile(r"^([0-9A-Fa-f]{4}):([0-9A-Fa-f]{8}):([0-9A-Fa-f]{8})$")

DEFAULT_USB_DEVICES = "/sys/bus/usb/devices"


def normalize_hex_id(value: str) -> str:
    """Backwards compatible name for USB ID normalisation."""

    try:
        return normalize_vendor_id(value)
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc


@dataclass(frozen=True)
class HidInterface:
    """One discoverable HID interface of the receiver."""

    key: str
    interface: str
    usb_path: str
    usb_interface: str
    path: str
    name: str
    hid_id: str
    hid_name: str
    vendor: str
    product: str
    node_inode: int
    node_rdev: int
    endpoints: int = 1
    is_lifeline: bool = False


@dataclass(frozen=True)
class UsbDeviceInfo:
    """Read-only description of the USB device behind an interface."""

    usb_path: str
    vendor: str
    product: str
    name: str
    manufacturer: str


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _parse_uevent(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _hid_identity(hid_dir: Path, uevent: dict[str, str]) -> tuple[str, str, str, str]:
    """Return (bus, vendor, product, hid_name) for a HID device directory."""

    match = HID_Uevent_RE.match(uevent.get("HID_ID", ""))
    if match:
        bus = match.group(1).lower()
        vendor = f"{int(match.group(2), 16):04x}"
        product = f"{int(match.group(3), 16):04x}"
        return bus, vendor, product, uevent.get("HID_NAME", "")

    dir_match = HID_DIR_RE.match(hid_dir.name)
    if dir_match:
        return (
            dir_match.group("bus").lower(),
            dir_match.group("vendor").lower(),
            dir_match.group("product").lower(),
            uevent.get("HID_NAME", ""),
        )
    return "", "", "", uevent.get("HID_NAME", "")


def _split_usb_interface(hid_dir: Path) -> tuple[str, str, str] | None:
    """Find the USB interface path, interface number and USB device path."""

    candidate = hid_dir.parent
    for _ in range(4):
        match = USB_INTERFACE_RE.match(candidate.name)
        if match:
            return candidate.name, match.group("interface"), match.group("device")
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None


def scan_interfaces(
    vendor: str,
    product: str,
    interfaces: Iterable[str] | None = None,
    sys_class: str = "/sys/class/hidraw",
    dev_root: str = "/dev",
) -> dict[str, HidInterface]:
    """Return every matching interface, keyed by USB interface path.

    A missing or unreadable sysfs tree yields an empty result. That is the
    normal state while the receiver is unplugged, so it is not an error.
    """

    try:
        vendor = normalize_hex_id(vendor)
        product = normalize_hex_id(product)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    wanted = None
    if interfaces is not None:
        wanted = {str(item) for item in interfaces}

    base = Path(sys_class)
    found: dict[str, HidInterface] = {}
    try:
        entries = sorted(base.glob("hidraw*"))
    except OSError as exc:
        LOG.debug("cannot scan %s: %s", sys_class, exc)
        return found

    for entry in entries:
        try:
            device_link = entry / "device"
            if not device_link.exists():
                continue
            hid_dir = Path(os.path.realpath(device_link))
            uevent = _parse_uevent(hid_dir / "uevent")
            _bus, hid_vendor, hid_product, hid_name = _hid_identity(hid_dir, uevent)
            if not hid_vendor or not hid_product:
                LOG.debug("skipping %s: missing HID identity", entry)
                continue
            if hid_vendor != vendor or hid_product != product:
                continue

            split = _split_usb_interface(hid_dir)
            if split is None:
                LOG.debug("skipping %s: cannot locate USB interface", entry)
                continue
            usb_interface, interface_number, usb_path = split
            if wanted is not None and interface_number not in wanted:
                continue

            node_path = os.path.join(dev_root, entry.name)
            try:
                node_stat = os.stat(node_path)
            except OSError:
                LOG.debug("skipping %s: %s does not exist", entry, node_path)
                continue

            hid_id = f"{_bus or '0003'}:{hid_vendor}:{hid_product}"
            instance = HID_DIR_RE.match(hid_dir.name)
            if instance and instance.group("instance"):
                hid_id = f"{hid_id}.{instance.group('instance')}"

            endpoint_count = 1
            try:
                endpoint_count = sum(child.name.startswith("ep_") for child in hid_dir.parent.iterdir())
            except OSError:
                pass
            is_lifeline = endpoint_count >= 2

            found[usb_interface] = HidInterface(
                key=usb_interface,
                interface=interface_number,
                usb_path=usb_path,
                usb_interface=usb_interface,
                path=node_path,
                name=entry.name,
                hid_id=hid_id,
                hid_name=hid_name or "",
                vendor=hid_vendor,
                product=hid_product,
                node_inode=node_stat.st_ino,
                node_rdev=node_stat.st_rdev,
                endpoints=endpoint_count,
                is_lifeline=is_lifeline,
            )
        except OSError as exc:
            LOG.debug("skipping %s: %s", entry, exc)

    return found


def usb_device_info(
    usb_path: str, usb_devices: str = DEFAULT_USB_DEVICES
) -> UsbDeviceInfo | None:
    """Read the USB device description, when sysfs exposes it."""

    base = Path(usb_devices) / usb_path
    if not base.is_dir():
        return None
    return UsbDeviceInfo(
        usb_path=usb_path,
        vendor=_read_text(base / "idVendor").lower(),
        product=_read_text(base / "idProduct").lower(),
        name=_read_text(base / "product"),
        manufacturer=_read_text(base / "manufacturer"),
    )


def scan_wired(
    vendor: str,
    receiver_product: str,
    usb_devices: str = DEFAULT_USB_DEVICES,
) -> UsbDeviceInfo | None:
    """Find the keyboard itself when it is attached with the USB cable.

    The 2.4 GHz receiver is one USB device. Plugging the cable in makes the
    keyboard enumerate as a second device from the same vendor with another
    product ID, which is how the charging state is detected. Hubs and the
    receiver are ignored. Only sysfs is read.
    """

    try:
        vendor = normalize_hex_id(vendor)
        receiver_product = normalize_hex_id(receiver_product)
    except ValueError:
        return None
    base = Path(usb_devices)
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return None
    for entry in entries:
        # USB devices are named like 1-2.2; interfaces contain a colon.
        if ":" in entry.name or entry.name.startswith("usb"):
            continue
        if _read_text(entry / "idVendor").lower() != vendor:
            continue
        product = _read_text(entry / "idProduct").lower()
        if not product or product == receiver_product:
            continue
        if _read_text(entry / "bDeviceClass") == "09":
            continue
        return UsbDeviceInfo(
            usb_path=entry.name,
            vendor=vendor,
            product=product,
            name=_read_text(entry / "product"),
            manufacturer=_read_text(entry / "manufacturer"),
        )
    return None
