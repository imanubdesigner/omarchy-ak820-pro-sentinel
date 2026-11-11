"""Tests for configuration parsing and validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from sentinel.config import (
    ConfigError,
    load_config,
    parse_backoff,
    parse_interfaces,
)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "sentinel.conf"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_defaults_without_a_file(self) -> None:
        config = load_config(str(self.path))
        self.assertEqual(config.vendor, "0c45")
        self.assertEqual(config.product, "fdfd")
        self.assertIsNone(config.interfaces)
        self.assertEqual(config.backoff, (1.0, 2.0, 4.0, 8.0, 15.0, 30.0))
        self.assertEqual(config.stable_seconds, 30.0)
        self.assertEqual(config.log_level, "INFO")

    def test_file_is_read(self) -> None:
        self.path.write_text(
            "[device]\n"
            "vendor = 0C45\n"
            "product = 0xFDFD\n"
            "interfaces = 0, 3\n"
            "\n"
            "[monitor]\n"
            "rescan_interval = 2.5\n"
            "stable_seconds = 12\n"
            "\n"
            "[recovery]\n"
            "backoff = 1,2,4\n"
            "\n"
            "[status]\n"
            "file = /tmp/sentinel-test-status.json\n"
            "event_history = 5\n"
            "\n"
            "[logging]\n"
            "level = debug\n",
            encoding="utf-8",
        )
        config = load_config(str(self.path))
        self.assertEqual(config.vendor, "0c45")
        self.assertEqual(config.product, "fdfd")
        self.assertEqual(config.interfaces, ("0", "3"))
        self.assertEqual(config.rescan_interval, 2.5)
        self.assertEqual(config.stable_seconds, 12.0)
        self.assertEqual(config.backoff, (1.0, 2.0, 4.0))
        self.assertEqual(config.event_history, 5)
        self.assertEqual(config.log_level, "DEBUG")

    def test_command_line_overrides_file(self) -> None:
        self.path.write_text("[monitor]\nrescan_interval = 9\n", encoding="utf-8")
        config = load_config(
            str(self.path), rescan_interval=1.5, interfaces="1,2", log_level="WARNING"
        )
        self.assertEqual(config.rescan_interval, 1.5)
        self.assertEqual(config.interfaces, ("1", "2"))
        self.assertEqual(config.log_level, "WARNING")

    def test_interfaces_can_be_all(self) -> None:
        config = load_config(str(self.path), interfaces="all")
        self.assertIsNone(config.interfaces)
        config = load_config(str(self.path), interfaces="*")
        self.assertIsNone(config.interfaces)

    def test_invalid_values(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(str(self.path), vendor="nope")
        with self.assertRaises(ConfigError):
            load_config(str(self.path), rescan_interval=0)
        with self.assertRaises(ConfigError):
            load_config(str(self.path), settling_interval=10, rescan_interval=5)
        with self.assertRaises(ConfigError):
            load_config(str(self.path), stable_seconds=-1)
        with self.assertRaises(ConfigError):
            load_config(str(self.path), log_level="CHATTY")
        with self.assertRaises(ConfigError):
            load_config(str(self.path), status_file="relative.json")
        with self.assertRaises(ConfigError):
            load_config(str(self.path), event_history=-2)
        with self.assertRaises(ConfigError):
            load_config(str(self.path), backoff="4,2")
        with self.assertRaises(ConfigError):
            load_config(str(self.path), rescan_interval=float("nan"))
        with self.assertRaises(ConfigError):
            load_config(str(self.path), status_interval=float("inf"))

    def test_unreadable_file_is_reported(self) -> None:
        config = load_config(str(self.path), status_file="/tmp/x.json")
        self.assertEqual(config.status_file, "/tmp/x.json")

    def test_parse_interfaces(self) -> None:
        self.assertIsNone(parse_interfaces(None))
        self.assertIsNone(parse_interfaces("all"))
        self.assertEqual(parse_interfaces("3,1,3"), ("1", "3"))
        self.assertEqual(parse_interfaces("0, 1 ,2"), ("0", "1", "2"))
        with self.assertRaises(ConfigError):
            parse_interfaces("0,x")

    def test_parse_backoff(self) -> None:
        self.assertEqual(parse_backoff("1, 2, 4"), (1.0, 2.0, 4.0))
        self.assertEqual(parse_backoff("1.5,2"), (1.5, 2.0))
        with self.assertRaises(ConfigError):
            parse_backoff("")
        with self.assertRaises(ConfigError):
            parse_backoff("1,0")
        with self.assertRaises(ConfigError):
            parse_backoff("8,4")
        with self.assertRaises(ConfigError):
            parse_backoff("1,nan")

    def test_as_dict_is_serialisable(self) -> None:
        config = load_config(str(self.path), interfaces="0,1")
        data = config.as_dict()
        self.assertEqual(data["interfaces"], ["0", "1"])
        self.assertEqual(data["backoff"], [1.0, 2.0, 4.0, 8.0, 15.0, 30.0])


if __name__ == "__main__":
    unittest.main()
