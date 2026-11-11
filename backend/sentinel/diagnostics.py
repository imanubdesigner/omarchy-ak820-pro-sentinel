"""Diagnostics and the JSON status snapshot read by the Omarchy panel.

The snapshot is written atomically to a world readable path. It is the only
interface between the backend and the user interface, so the backend never
imports anything graphical and the panel never runs with root privileges.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import APP_NAME, __version__
from .config import Config
from .events import EventBus
from .recovery import RecoveryEngine
from .state import StateStore
from .usb_detector import scan_interfaces

LOG = logging.getLogger("ak820-pro-sentinel.diagnostics")

SERVICE_UNIT = "ak820-pro-sentinel.service"


class StatusPublisher:
    """Build and atomically store the status snapshot."""

    def __init__(
        self,
        config: Config,
        store: StateStore,
        recovery: RecoveryEngine,
        events: EventBus,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.store = store
        self.recovery = recovery
        self.events = events
        self.clock = clock
        self.path = Path(config.status_file)
        self._last_write = 0.0
        self._last_payload: dict[str, Any] | None = None

    def payload(self, now: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self.store.snapshot(
            now,
            recovery=self.recovery,
            events=self.events,
            version=__version__,
            app_name=APP_NAME,
            extra={
                "pid": os.getpid(),
                "running": True,
                "hostname": socket.gethostname(),
                "config_path": self.config.config_path,
            },
        )
        if self._last_payload is not None:
            data["previous_event"] = self._last_payload.get("last_event")
        if extra:
            data.update(extra)
        return data

    def publish(
        self, now: float, force: bool = False, extra: dict[str, Any] | None = None
    ) -> bool:
        if not force and now - self._last_write < self.config.status_interval:
            return False
        payload = self.payload(now, extra=extra)
        try:
            write_json_atomic(self.path, payload)
        except OSError as exc:
            LOG.warning("cannot write %s: %s", self.path, exc)
            return False
        self._last_write = now
        self._last_payload = payload
        return True


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON so a reader never sees a partial document."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o755)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def read_status(path: str) -> dict[str, Any] | None:
    """Read a snapshot written by the service, or return None."""

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def service_state(unit: str = SERVICE_UNIT) -> str:
    """Best effort systemd state, without requiring privileges."""

    if shutil.which("systemctl") is None:
        return "unavailable"
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return completed.stdout.strip() or completed.stderr.strip() or "unknown"


def environment_info(config: Config) -> dict[str, Any]:
    """Static information useful while debugging a misbehaving receiver."""

    try:
        interfaces = scan_interfaces(
            vendor=config.vendor,
            product=config.product,
            interfaces=config.interfaces,
            sys_class=config.sys_class,
            dev_root=config.dev_root,
        )
    except Exception as exc:  # Diagnostics must never crash on a weird sysfs.
        interfaces = {}
        scan_error = str(exc)
    else:
        scan_error = None
    nodes = sorted(
        os.path.join(config.dev_root, name)
        for name in os.listdir(config.dev_root)
        if name.startswith("hidraw")
    ) if os.path.isdir(config.dev_root) else []
    return {
        "app": APP_NAME,
        "version": __version__,
        "service": service_state(),
        "kernel": platform.release(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "uid": os.getuid(),
        "hostname": socket.gethostname(),
        "config_path": config.config_path,
        "config": config.as_dict(),
        "hidraw_nodes": nodes,
        "matching_interfaces": [
            {
                "interface": info.interface,
                "device": info.path,
                "usb_path": info.usb_interface,
                "hid_id": info.hid_id,
                "hid_name": info.hid_name,
            }
            for info in sorted(interfaces.values(), key=lambda item: int(item.interface))
        ],
        "scan_error": scan_error,
    }


def collect(config: Config) -> dict[str, Any]:
    """Full diagnostic document printed by ``--diagnostics``."""

    data = environment_info(config)
    data["status"] = read_status(config.status_file)
    return data


def format_summary(snapshot: dict[str, Any] | None) -> str:
    """Short human readable summary used by ``--status`` and ``--check``."""

    if not snapshot:
        return "Sentinel is not running or has not written a status file yet."
    receiver = snapshot.get("receiver", {})
    summary = snapshot.get("summary", {})
    lines = [
        f"{snapshot.get('app', APP_NAME)} {snapshot.get('version', '')}".strip(),
        f"receiver: {receiver.get('state', 'unknown')} "
        f"({receiver.get('vendor_id', '?')}:{receiver.get('product_id', '?')}"
        f"{', ' + receiver.get('name') if receiver.get('name') else ''})",
        f"interfaces: {summary.get('open', 0)} open, "
        f"{summary.get('stable', 0)} stable, "
        f"{summary.get('recovering', 0)} recovering",
        f"uptime: {int(snapshot.get('uptime_seconds', 0))}s",
    ]
    last = snapshot.get("last_event")
    if last:
        lines.append(f"last event: {last.get('type')} - {last.get('message')}")
    return "\n".join(lines)
