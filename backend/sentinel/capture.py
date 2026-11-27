"""Opt-in capture of vendor HID reports, to look for battery data.

The keyboard knows its charge level (it draws it on its own screen), but it is
not known whether the receiver ever sends it to the host. This module answers
that question without writing a single byte to the device: it records the
distinct reports that Sentinel already reads and then discards.

Privacy rules, all enforced here and in the monitor:

* only the vendor "lifeline" interfaces are recorded. The standard keyboard
  interfaces carry typed keys and are never passed to this module;
* the file is created with mode 0600, so only root can read it;
* the capture is off unless ``capture_file`` is configured.

Each distinct payload is written once, with the time it was first seen, so a
byte that changes together with the battery icon shows up as a new line. The
number of lines is capped to keep a counter byte from filling the disk.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Callable

LOG = logging.getLogger("ak820-pro-sentinel.capture")

DEFAULT_MAX_UNIQUE = 2000


class ReportCapture:
    """Append distinct vendor reports to a root only text file."""

    def __init__(
        self,
        path: str,
        *,
        wall: Callable[[], float] = time.time,
        max_unique: int = DEFAULT_MAX_UNIQUE,
    ) -> None:
        self.path = path
        self.wall = wall
        self.max_unique = max_unique
        self._seen: set[tuple[str, bytes]] = set()
        self._descriptor: int | None = None
        self._full = False
        self._open()
        self.mark("capture started: vendor interfaces only, read only")

    # ----------------------------------------------------------------- file

    def _open(self) -> None:
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, mode=0o755, exist_ok=True)
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            os.fchmod(descriptor, 0o600)
            self._descriptor = descriptor
        except OSError as exc:
            LOG.warning("report capture disabled, cannot open %s: %s", self.path, exc)
            self._descriptor = None

    def _write(self, text: str) -> None:
        if self._descriptor is None:
            return
        try:
            os.write(self._descriptor, text.encode("utf-8"))
        except OSError as exc:
            LOG.warning("report capture disabled, cannot write %s: %s", self.path, exc)
            self.close()

    def _stamp(self) -> str:
        return (
            datetime.fromtimestamp(self.wall())
            .astimezone()
            .isoformat(timespec="milliseconds")
        )

    def close(self) -> None:
        if self._descriptor is not None:
            try:
                os.close(self._descriptor)
            except OSError:
                pass
            self._descriptor = None

    # ------------------------------------------------------------------ api

    @property
    def active(self) -> bool:
        return self._descriptor is not None

    def record(self, interface: str, data: bytes) -> bool:
        """Write the report if this exact payload was not seen on the interface."""

        if self._descriptor is None or self._full:
            return False
        key = (str(interface), bytes(data))
        if key in self._seen:
            return False
        if len(self._seen) >= self.max_unique:
            self._full = True
            self._write(f"{self._stamp()} # limit of {self.max_unique} distinct reports reached\n")
            return False
        self._seen.add(key)
        self._write(
            f"{self._stamp()} IF{interface} len={len(data)} {bytes(data).hex(' ')}\n"
        )
        return True

    def mark(self, text: str) -> None:
        """Write a note, for example when the cable is plugged in or removed."""

        self._write(f"{self._stamp()} # {text}\n")
