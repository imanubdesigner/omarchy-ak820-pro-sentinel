"""Configuration loading and validation.

The service reads a small INI file (``/etc/ak820-pro-sentinel.conf`` by
default). Every value can also be overridden on the command line. The module
has no side effects beyond reading the file, so it is safe to use from tests.
"""

from __future__ import annotations

import configparser
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = "/etc/ak820-pro-sentinel.conf"
DEFAULT_STATUS_FILE = "/run/ak820-pro-sentinel/status.json"
DEFAULT_SYS_CLASS = "/sys/class/hidraw"
DEFAULT_DEV_ROOT = "/dev"
DEFAULT_BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(ValueError):
    """Raised when a configuration value cannot be used."""


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration."""

    vendor: str = "0c45"
    product: str = "fdfd"
    # None means every HID interface of the receiver.
    interfaces: tuple[str, ...] | None = None
    rescan_interval: float = 3.0
    settling_interval: float = 0.4
    backoff: tuple[float, ...] = DEFAULT_BACKOFF
    stable_seconds: float = 30.0
    status_file: str = DEFAULT_STATUS_FILE
    status_interval: float = 1.0
    event_history: int = 20
    log_level: str = "INFO"
    sys_class: str = DEFAULT_SYS_CLASS
    dev_root: str = DEFAULT_DEV_ROOT
    usb_devices: str = "/sys/bus/usb/devices"
    stats_file: str = "/var/lib/ak820-pro-sentinel/stats.json"
    config_path: str = DEFAULT_CONFIG_PATH

    def with_overrides(self, **values: Any) -> "Config":
        """Return a copy with the non-None overrides applied."""

        clean = {key: value for key, value in values.items() if value is not None}
        if not clean:
            return self
        return replace(self, **clean)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable view, including list and tuple fields."""

        data = {
            "vendor": self.vendor,
            "product": self.product,
            "interfaces": list(self.interfaces) if self.interfaces else None,
            "rescan_interval": self.rescan_interval,
            "settling_interval": self.settling_interval,
            "backoff": list(self.backoff),
            "stable_seconds": self.stable_seconds,
            "status_file": self.status_file,
            "status_interval": self.status_interval,
            "event_history": self.event_history,
            "log_level": self.log_level,
            "sys_class": self.sys_class,
            "dev_root": self.dev_root,
            "usb_devices": self.usb_devices,
            "stats_file": self.stats_file,
            "config_path": self.config_path,
        }
        return data


def normalize_vendor_id(value: str) -> str:
    """Validate and normalise a four digit hexadecimal USB ID."""

    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text or any(char not in "0123456789abcdef" for char in text):
        raise ConfigError(f"invalid hexadecimal USB ID: {value!r}")
    if len(text) > 4:
        raise ConfigError(f"USB ID is too long: {value!r}")
    return text.zfill(4)


def parse_interfaces(value: str | None) -> tuple[str, ...] | None:
    """Parse an interface list such as ``0,1,2`` or ``all``."""

    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "all", "*"):
        return None
    parts: list[str] = []
    for chunk in text.split(","):
        item = chunk.strip()
        if not item:
            continue
        if not item.isdigit():
            raise ConfigError(f"interface number must be a digit: {item!r}")
        if item not in parts:
            parts.append(item)
    if not parts:
        return None
    return tuple(sorted(parts, key=int))


def parse_backoff(value: str | tuple | list) -> tuple[float, ...]:
    """Parse the progressive backoff list and keep it ascending."""

    if isinstance(value, str):
        raw = [chunk.strip() for chunk in value.split(",") if chunk.strip()]
    else:
        raw = list(value)
    if not raw:
        raise ConfigError("recovery backoff cannot be empty")
    try:
        numbers = tuple(float(item) for item in raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError("recovery backoff must be a list of seconds") from exc
    if any(not math.isfinite(number) or number <= 0 for number in numbers):
        raise ConfigError("recovery backoff values must be finite and greater than zero")
    for previous, current in zip(numbers, numbers[1:]):
        if current < previous:
            raise ConfigError("recovery backoff values must not decrease")
    return numbers


def validate_config(config: Config) -> Config:
    """Normalise and validate the fields that have constraints."""

    vendor = normalize_vendor_id(config.vendor)
    product = normalize_hex_like(config.product)
    interfaces = config.interfaces
    if isinstance(interfaces, str):
        interfaces = parse_interfaces(interfaces)
    elif interfaces is not None:
        interfaces = parse_interfaces(",".join(str(item) for item in interfaces))
    backoff = parse_backoff(config.backoff)
    level = str(config.log_level).strip().upper()
    if level not in LOG_LEVELS:
        raise ConfigError(f"unknown log level: {config.log_level!r}")
    if not math.isfinite(config.rescan_interval) or config.rescan_interval <= 0:
        raise ConfigError("rescan_interval must be greater than zero")
    if not math.isfinite(config.settling_interval) or config.settling_interval <= 0:
        raise ConfigError("settling_interval must be greater than zero")
    if config.settling_interval > config.rescan_interval:
        raise ConfigError("settling_interval must not exceed rescan_interval")
    if not math.isfinite(config.stable_seconds) or config.stable_seconds <= 0:
        raise ConfigError("stable_seconds must be greater than zero")
    if not math.isfinite(config.status_interval) or config.status_interval <= 0:
        raise ConfigError("status_interval must be greater than zero")
    if config.event_history < 0:
        raise ConfigError("event_history cannot be negative")
    if not config.status_file:
        raise ConfigError("status_file cannot be empty")
    if not Path(config.status_file).is_absolute():
        raise ConfigError("status_file must be an absolute path")
    if not config.sys_class or not config.dev_root:
        raise ConfigError("sys_class and dev_root cannot be empty")
    return replace(
        config,
        vendor=vendor,
        product=product,
        interfaces=interfaces,
        backoff=backoff,
        log_level=level,
    )


def normalize_hex_like(value: str) -> str:
    """Normalise the product ID the same way as the vendor ID."""

    return normalize_vendor_id(value)


def load_config(path: str | None = None, **overrides: Any) -> Config:
    """Load the configuration file, then apply command line overrides.

    A missing file is not an error: the built in defaults are used. Invalid
    values raise :class:`ConfigError`.
    """

    config_path = path or DEFAULT_CONFIG_PATH
    values: dict[str, Any] = {"config_path": config_path}
    file_path = Path(config_path)
    if file_path.is_file():
        parser = configparser.ConfigParser()
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                parser.read_file(handle)
        except (OSError, configparser.Error) as exc:
            raise ConfigError(f"cannot read {config_path}: {exc}") from exc

        def read(section: str, key: str) -> str | None:
            if parser.has_option(section, key):
                return parser.get(section, key)
            return None

        mappings = {
            "vendor": read("device", "vendor"),
            "product": read("device", "product"),
            "interfaces": read("device", "interfaces"),
            "rescan_interval": read("monitor", "rescan_interval"),
            "settling_interval": read("monitor", "settling_interval"),
            "stable_seconds": read("monitor", "stable_seconds"),
            "backoff": read("recovery", "backoff"),
            "status_file": read("status", "file"),
            "status_interval": read("status", "interval"),
            "event_history": read("status", "event_history"),
            "log_level": read("logging", "level"),
            "sys_class": read("paths", "sys_class"),
            "dev_root": read("paths", "dev_root"),
        }
        for key, raw in mappings.items():
            if raw is None:
                continue
            if key == "interfaces":
                values[key] = parse_interfaces(raw)
            elif key == "backoff":
                values[key] = parse_backoff(raw)
            elif key in ("rescan_interval", "settling_interval", "stable_seconds", "status_interval"):
                values[key] = _parse_float(key, raw)
            elif key == "event_history":
                values[key] = _parse_int(key, raw)
            else:
                values[key] = raw.strip()

    config = Config(**values)
    config = config.with_overrides(**overrides)
    return validate_config(config)


def _parse_float(key: str, raw: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be a number: {raw!r}") from exc


def _parse_int(key: str, raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be an integer: {raw!r}") from exc


EXAMPLE_CONFIG = """\
# AK820 Pro Sentinel configuration.
# Edit this file and restart the service:
#   sudo systemctl restart ak820-pro-sentinel.service

[device]
# USB IDs of the receiver. These are the defaults for the AK820 Pro.
vendor = 0c45
product = fdfd
# "all" monitors every HID interface of the receiver. A list such as
# "0,1,2,3,4" restricts monitoring to specific interface numbers.
interfaces = all

[monitor]
# How often the receiver is rescanned, in seconds.
rescan_interval = 3.0
# Upper bound for a single poll wait, in seconds.
settling_interval = 0.4
# A reopened interface must stay up this long before the retry counter resets.
stable_seconds = 30.0

[recovery]
# Wait before each recovery attempt, in seconds. The last value is reused.
backoff = 1,2,4,8,15,30

[status]
# JSON snapshot consumed by the Omarchy panel. World readable by design.
file = /run/ak820-pro-sentinel/status.json
# How often the snapshot is refreshed while nothing changes, in seconds.
interval = 1.0
# Number of significant events kept inside the snapshot log.
event_history = 20

[logging]
# DEBUG, INFO, WARNING, ERROR or CRITICAL.
level = INFO

[paths]
sys_class = /sys/class/hidraw
dev_root = /dev
"""
