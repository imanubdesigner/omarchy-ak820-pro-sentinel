"""Shared helpers for the test suite.

Running ``python3 -m unittest discover -s tests`` puts the tests directory on
``sys.path``, so every test can simply import this module. It makes the
``backend`` directory importable without setting PYTHONPATH by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1] / "backend"
TESTS = Path(__file__).resolve().parent
for path in (BACKEND, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def make_interface(
    interface: str = "0",
    path: str = "/dev/hidraw0",
    inode: int = 100,
    rdev: int = 1000,
    hid_id: str | None = None,
    usb_path: str = "1-2.2",
    key: str | None = None,
):
    """Build a HidInterface shaped object without touching real sysfs."""

    from sentinel.usb_detector import HidInterface

    usb_interface = f"{usb_path}:1.{interface}"
    return HidInterface(
        key=key or usb_interface,
        interface=interface,
        usb_path=usb_path,
        usb_interface=usb_interface,
        path=path,
        name=Path(path).name,
        hid_id=hid_id or f"0003:0c45:fdfd.000{int(interface) + 1}",
        hid_name="2.4G Dongle",
        vendor="0c45",
        product="fdfd",
        node_inode=inode,
        node_rdev=rdev,
    )


class FakeFileSystem:
    """Tiny in-memory stand in for /dev and the stat calls on it."""

    def __init__(self) -> None:
        self.nodes: dict[str, SimpleNamespace] = {}
        self.opened: list[tuple[str, int]] = []
        self.closed: list[int] = []
        self.next_descriptor = 3
        self.open_error: OSError | None = None

    def add(self, path: str, inode: int = 100, rdev: int = 1000) -> None:
        self.nodes[path] = SimpleNamespace(st_ino=inode, st_rdev=rdev)

    def remove(self, path: str) -> None:
        self.nodes.pop(path, None)

    def stat(self, path: str):
        node = self.nodes.get(path)
        if node is None:
            raise FileNotFoundError(2, "No such file or directory", path)
        return node

    def open(self, path: str, flags: int) -> int:
        if self.open_error is not None:
            raise self.open_error
        self.opened.append((path, flags))
        descriptor = self.next_descriptor
        self.next_descriptor += 1
        return descriptor

    def close(self, descriptor: int) -> None:
        self.closed.append(descriptor)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now
