"""Read only HID monitor with explicit descriptor lifecycle.

The monitor is the part that actually talks to the kernel. It opens every
matching ``/dev/hidraw`` node read only, waits with :func:`select.select` and
reads incoming reports without acting on them.

The important guarantee is that no stale descriptor survives a disconnection:
when a node disappears or is recreated, the old file descriptor is closed and
removed from the connection table before a new one is opened.

No HID output report, firmware command or USB reset is ever sent.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import select
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .events import EventBus, EventType
from .recovery import RecoveryEngine
from .state import StateStore
from .usb_detector import HidInterface, scan_interfaces, scan_wired, usb_device_info

LOG = logging.getLogger("ak820-pro-sentinel.monitor")

# HIDIOCGRAWINFO = _IOR('H', 0x03, struct hidraw_devinfo)
# struct hidraw_devinfo { __u32 bus; __s16 vendor; __s16 product; }
HIDIOCGRAWINFO = (2 << 30) | (8 << 16) | (ord("H") << 8) | 0x03
HIDRAW_DEAD_ERRNOS = {errno.ENODEV, errno.EIO, errno.EBADF, errno.ENXIO}

OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC


class DescriptorGone(OSError):
    """Raised when the ioctl probe proves that a descriptor is no longer valid."""


def probe_devinfo(descriptor: int) -> tuple[str, str] | None:
    """Read vendor and product from the descriptor itself, read only.

    Returns ``None`` when the kernel or the build does not support the ioctl.
    Raises :class:`DescriptorGone` when the descriptor is no longer usable.
    """

    buffer = bytearray(8)
    try:
        fcntl.ioctl(descriptor, HIDIOCGRAWINFO, buffer, True)
    except OSError as exc:
        if exc.errno in HIDRAW_DEAD_ERRNOS:
            raise DescriptorGone(exc.errno, exc.strerror) from exc
        return None
    _bus, vendor, product = struct.unpack("=Ihh", bytes(buffer))
    return f"{vendor & 0xFFFF:04x}", f"{product & 0xFFFF:04x}"


@dataclass
class Connection:
    """An open HID interface."""

    key: str
    interface: str
    path: str
    hid_id: str
    descriptor: int
    inode: int
    rdev: int
    opened_at: float
    reports: int = 0
    last_read: float | None = None


class HidMonitor:
    """Poll the receiver, keep descriptors valid and let recovery reopen them."""

    def __init__(
        self,
        config: Config,
        store: StateStore,
        events: EventBus,
        recovery: RecoveryEngine,
        *,
        scanner: Callable[..., dict[str, HidInterface]] = scan_interfaces,
        clock: Callable[[], float] = time.monotonic,
        opener: Callable[[str, int], int] = os.open,
        closer: Callable[[int], None] = os.close,
        stat: Callable[[str], os.stat_result] = os.stat,
        selector: Callable[..., tuple[list[int], list[int], list[int]]] = select.select,
        reader: Callable[[int, int], bytes] = os.read,
        probe: Callable[[int], tuple[str, str] | None] | None = probe_devinfo,
        usb_info: Callable[[str], Any] = usb_device_info,
        wired_scanner: Callable[..., Any] = scan_wired,
        on_change: Callable[[], None] | None = None,
        on_tick: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.events = events
        self.recovery = recovery
        self.scanner = scanner
        self.clock = clock
        self.opener = opener
        self.closer = closer
        self.stat = stat
        self.selector = selector
        self.reader = reader
        self.probe = probe
        self.usb_info = usb_info
        self.wired_scanner = wired_scanner
        self.on_change = on_change
        self.on_tick = on_tick
        self.connections: dict[str, Connection] = {}
        self.debug_reports = config.log_level == "DEBUG"
        self._next_scan = 0.0
        self._force_scan = True
        self._stopping = threading.Event()

    # ------------------------------------------------------------------ loop

    def request_stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        """Run until :meth:`request_stop` is called."""

        now = self.clock()
        self.rescan(now)
        if self.on_tick:
            self.on_tick(now)
        while not self._stopping.is_set():
            now = self.clock()
            try:
                changed = self.step(now)
            except Exception as exc:  # Keep the watchdog alive on unexpected errors.
                LOG.exception("monitor step failed")
                self.events.emit(EventType.MONITOR_ERROR, "monitor step failed", error=str(exc))
                self._stopping.wait(0.5)
                continue
            if changed and self.on_change:
                self.on_change()
            if self.on_tick:
                self.on_tick(now)
            if not self.connections:
                self._stopping.wait(min(0.2, self.config.settling_interval))
        self._close_all("shutdown", self.clock(), schedule=False)

    def step(self, now: float, timeout: float | None = None) -> bool:
        """One non blocking pass, useful from tests and from ``--once``."""

        changed = False
        if self._force_scan or now >= self._next_scan or self._recovery_due(now):
            changed = self.rescan(now) or changed
        changed = self.check_liveness(now) or changed
        changed = self.apply_stability(now) or changed
        changed = self.poll(now, timeout=timeout) or changed
        return changed

    def _recovery_due(self, now: float) -> bool:
        due = self.recovery.next_due()
        return due is not None and now >= due

    # --------------------------------------------------------------- scanning

    def _scan(self) -> dict[str, HidInterface]:
        return self.scanner(
            vendor=self.config.vendor,
            product=self.config.product,
            interfaces=self.config.interfaces,
            sys_class=self.config.sys_class,
            dev_root=self.config.dev_root,
        )

    def rescan(self, now: float) -> bool:
        """Reconcile open descriptors with what sysfs currently exposes."""

        desired = self._scan()
        changed = self._refresh_receiver(desired, now)
        changed = self._refresh_wired(now) or changed

        for key, connection in list(self.connections.items()):
            info = desired.get(key)
            if info is None:
                changed = self._close(key, "device_removed", now) or changed
                continue
            if (
                info.path != connection.path
                or info.node_inode != connection.inode
                or info.node_rdev != connection.rdev
                or info.hid_id != connection.hid_id
            ):
                changed = self._close(key, "node_replaced", now) or changed

        changed = self._prune_stale_interfaces(desired) or changed

        for key, info in sorted(
            desired.items(), key=lambda item: int(item[1].interface)
        ):
            if self.store.get(key) is None:
                self.store.upsert_interface(info, now)
                self.events.emit(
                    EventType.HID_INTERFACE_FOUND,
                    "HID interface found",
                    interface=info.interface,
                    device=info.path,
                    usb_path=info.usb_interface,
                    hid_id=info.hid_id,
                )
                changed = True
            else:
                self.store.upsert_interface(info, now)
            if key in self.connections:
                continue
            if self.recovery.ready(key, now):
                changed = self._open(info, now) or changed

        self._next_scan = now + self.config.rescan_interval
        self._force_scan = False
        return changed

    def _prune_stale_interfaces(self, desired: dict[str, HidInterface]) -> bool:
        """Drop interface state that can never reappear under the same key.

        An interface of a receiver that is still present is kept while it is
        missing, because it may come back on the same USB path and the
        recovery engine must keep its backoff. When the whole receiver is gone,
        or when it reappears on a different USB path, the old key is useless.
        """

        desired_keys = set(desired)
        desired_paths = {info.usb_path for info in desired.values()}
        removed = False
        for state in self.store.interfaces():
            if state.key in desired_keys:
                continue
            if state.usb_path in desired_paths:
                continue
            self.store.remove_interface(state.key)
            self.recovery.forget(state.key)
            self.events.emit(
                EventType.HID_INTERFACE_STALE,
                "stale interface state removed",
                interface=state.interface,
                device=state.path,
                usb_path=state.usb_path,
            )
            removed = True
        return removed

    def _refresh_wired(self, now: float) -> bool:
        """Track the keyboard attached by cable, which means it is charging."""

        try:
            found = self.wired_scanner(
                self.config.vendor, self.config.product, self.config.usb_devices
            )
        except Exception:  # Never let a sysfs oddity stop the watchdog.
            LOG.debug("wired scan failed", exc_info=True)
            return False
        wired = self.store.wired
        if found is not None and not wired.present:
            wired.present = True
            wired.product = found.product
            wired.name = found.name
            wired.usb_path = found.usb_path
            wired.since = now
            self.events.emit(
                EventType.WIRED_CONNECTED,
                "keyboard cable connected, charging",
                product_id=found.product,
                name=found.name,
                usb_path=found.usb_path,
            )
            return True
        if found is None and wired.present:
            wired.present = False
            wired.since = None
            self.events.emit(
                EventType.WIRED_REMOVED,
                "keyboard cable removed",
                product_id=wired.product,
            )
            return True
        return False

    def _refresh_receiver(self, desired: dict[str, HidInterface], now: float) -> bool:
        first = (
            sorted(desired.values(), key=lambda item: int(item.interface))[0]
            if desired
            else None
        )
        if first is not None and not self.store.receiver.present:
            usb = self.usb_info(first.usb_path)
            self.store.set_receiver(
                True,
                now,
                vendor=first.vendor,
                product=first.product,
                name=(usb.name if usb else first.hid_name),
                usb_path=first.usb_path,
            )
            receiver = self.store.receiver
            self.events.emit(
                EventType.USB_RECEIVER_DETECTED,
                "USB receiver detected",
                vendor_id=receiver.vendor,
                product_id=receiver.product,
                name=receiver.name,
                usb_path=receiver.usb_path,
                interfaces=len(desired),
            )
            return True
        if first is None and self.store.receiver.present:
            self.store.set_receiver(False, now)
            self.events.emit(
                EventType.USB_RECEIVER_REMOVED,
                "USB receiver removed",
                vendor_id=self.store.receiver.vendor,
                product_id=self.store.receiver.product,
            )
            return True
        if (
            first is not None
            and self.store.receiver.present
            and first.usb_path
            and first.usb_path != self.store.receiver.usb_path
        ):
            # The same receiver was moved to another USB port.
            usb = self.usb_info(first.usb_path)
            self.store.set_receiver(
                True,
                now,
                vendor=first.vendor,
                product=first.product,
                name=(usb.name if usb else first.hid_name),
                usb_path=first.usb_path,
            )
            return True
        return False

    # --------------------------------------------------------------- lifecycle

    def _open(self, info: HidInterface, now: float) -> bool:
        self.store.upsert_interface(info, now)
        try:
            descriptor = self.opener(info.path, OPEN_FLAGS)
        except OSError as exc:
            error = f"{exc.strerror or exc.__class__.__name__}"
            attempts, wait = self.recovery.on_failure(
                info.key, now, interface=info.interface, path=info.path
            )
            self.store.mark_closed(info.key, now, error=error, attempts=attempts)
            self.events.emit(
                EventType.HID_OPEN_FAILED,
                "cannot open HID interface",
                interface=info.interface,
                device=info.path,
                error=error,
                errno=exc.errno,
            )
            self.events.emit(
                EventType.RECOVERY_ATTEMPT,
                f"recovery attempt {attempts} scheduled",
                interface=info.interface,
                device=info.path,
                attempt=attempts,
                retry_in_seconds=round(wait, 2),
            )
            return True

        connection = Connection(
            key=info.key,
            interface=info.interface,
            path=info.path,
            hid_id=info.hid_id,
            descriptor=descriptor,
            inode=info.node_inode,
            rdev=info.node_rdev,
            opened_at=now,
        )
        self.connections[info.key] = connection
        self.store.mark_open(info.key, descriptor, now)
        recovered = self.recovery.on_open(
            info.key, now, interface=info.interface, path=info.path
        )
        self.store.set_attempts(info.key, self.recovery.controller(info.key).attempts)
        msg = "Lifeline interface opened" if getattr(info, "is_lifeline", False) else "HID interface opened"
        self.events.emit(
            EventType.HID_INTERFACE_OPENED,
            msg,
            interface=info.interface,
            device=info.path,
            hid_id=info.hid_id,
            is_lifeline=getattr(info, "is_lifeline", False),
            endpoints=getattr(info, "endpoints", 1),
        )
        if recovered:
            attempts = self.recovery.controller(info.key).attempts
            self.events.emit(
                EventType.RECOVERY_SUCCEEDED,
                "HID interface reopened",
                interface=info.interface,
                device=info.path,
                attempts=attempts,
            )
        return True

    def _close(
        self,
        key: str,
        reason: str,
        now: float,
        error: str | None = None,
        schedule: bool = True,
    ) -> bool:
        connection = self.connections.pop(key, None)
        if connection is None:
            return False
        try:
            self.closer(connection.descriptor)
        except OSError:
            pass

        if not schedule:
            self.store.mark_disconnected(key, now, error=error)
            self.events.emit(
                EventType.HID_INTERFACE_CLOSED,
                "HID interface closed",
                interface=connection.interface,
                device=connection.path,
                reason=reason,
            )
            return True

        attempts, wait = self.recovery.on_failure(
            key, now, interface=connection.interface, path=connection.path
        )
        self.store.mark_closed(key, now, error=error or reason, attempts=attempts)
        self.events.emit(
            EventType.HID_INTERFACE_CLOSED,
            "HID interface disconnected",
            interface=connection.interface,
            device=connection.path,
            reason=reason,
            attempt=attempts,
            retry_in_seconds=round(wait, 2),
        )
        if reason in ("node_replaced", "node_missing", "descriptor_gone", "descriptor_mismatch"):
            self.events.emit(
                EventType.HID_INTERFACE_STALE,
                "stale descriptor closed",
                interface=connection.interface,
                device=connection.path,
                reason=reason,
            )
        if attempts == 1:
            self.events.emit(
                EventType.RECOVERY_STARTED,
                "recovery started",
                interface=connection.interface,
                device=connection.path,
            )
        self.events.emit(
            EventType.RECOVERY_ATTEMPT,
            f"recovery attempt {attempts} scheduled",
            interface=connection.interface,
            device=connection.path,
            attempt=attempts,
            retry_in_seconds=round(wait, 2),
        )
        return True

    def _close_all(self, reason: str, now: float, schedule: bool) -> None:
        for key in list(self.connections):
            self._close(key, reason, now, schedule=schedule)

    # --------------------------------------------------------------- liveness

    def check_liveness(self, now: float) -> bool:
        """Close descriptors whose node vanished or now points elsewhere."""

        changed = False
        for key, connection in list(self.connections.items()):
            try:
                node = self.stat(connection.path)
            except OSError as exc:
                changed = self._close(
                    key, "node_missing", now, error=str(exc)
                ) or changed
                continue
            if node.st_ino != connection.inode or node.st_rdev != connection.rdev:
                changed = self._close(key, "node_replaced", now) or changed
                continue
            if self.probe is None:
                continue
            try:
                identity = self.probe(connection.descriptor)
            except DescriptorGone as exc:
                changed = self._close(key, "descriptor_gone", now, error=str(exc)) or changed
                continue
            except OSError as exc:
                LOG.debug("ioctl probe failed on %s: %s", connection.path, exc)
                continue
            if identity is not None and identity != (
                self.config.vendor,
                self.config.product,
            ):
                changed = self._close(
                    key,
                    "descriptor_mismatch",
                    now,
                    error=f"descriptor belongs to {identity[0]}:{identity[1]}",
                ) or changed
        return changed

    # -------------------------------------------------------------- poll/read

    def poll(self, now: float, timeout: float | None = None) -> bool:
        if not self.connections:
            return False
        connections = list(self.connections.values())
        descriptors = [connection.descriptor for connection in connections]
        wait = self.config.settling_interval if timeout is None else max(0.0, timeout)
        try:
            readable, _writable, exceptional = self.selector(
                descriptors, [], descriptors, wait
            )
        except (OSError, ValueError) as exc:
            self.events.emit(
                EventType.HID_SELECT_ERROR,
                "select() failed, rebuilding descriptors",
                error=str(exc),
            )
            self._close_all("select_error", now, schedule=True)
            self._next_scan = 0.0
            return True

        by_descriptor = {
            connection.descriptor: connection for connection in connections
        }
        changed = False
        for descriptor in exceptional:
            connection = by_descriptor.get(descriptor)
            if connection and connection.key in self.connections:
                changed = self._close(
                    connection.key, "select_exception", now
                ) or changed

        for descriptor in readable:
            connection = by_descriptor.get(descriptor)
            if connection is None or connection.key not in self.connections:
                continue
            try:
                data = self.reader(descriptor, 64)
            except BlockingIOError:
                continue
            except OSError as exc:
                changed = self._close(
                    connection.key,
                    "read_error",
                    now,
                    error=f"{exc.strerror or exc}",
                ) or changed
                self.events.emit(
                    EventType.HID_READ_ERROR,
                    "HID read failed",
                    interface=connection.interface,
                    device=connection.path,
                    error=str(exc),
                )
                continue
            if not data:
                changed = self._close(
                    connection.key, "end_of_stream", now
                ) or changed
                continue
            connection.reports += 1
            connection.last_read = now
            self.store.touch(connection.key, now, reports=connection.reports)
            if self.debug_reports:
                LOG.debug(
                    "HID report interface=%s bytes=%s",
                    connection.interface,
                    len(data),
                )
        return changed

    def apply_stability(self, now: float) -> bool:
        changed = False
        for controller in self.recovery.tick(now):
            if controller.key not in self.connections:
                continue
            self.store.mark_stable(controller.key, now)
            self.events.emit(
                EventType.DEVICE_STABLE,
                "connection stable",
                interface=controller.interface,
                device=controller.path,
                stable_seconds=self.recovery.stable_seconds,
            )
            changed = True
        return changed

