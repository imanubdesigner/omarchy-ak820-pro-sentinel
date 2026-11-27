"""The opt-in report capture: distinct payloads, private file, bounded size."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (puts backend on sys.path)
from sentinel.capture import ReportCapture
from sentinel.config import Config, ConfigError, load_config, validate_config


class ReportCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "state" / "reports.log")

    def lines(self) -> list[str]:
        return Path(self.path).read_text().splitlines()

    def test_file_is_private_and_starts_with_a_note(self) -> None:
        capture = ReportCapture(self.path)
        capture.close()
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertIn("# capture started", self.lines()[0])

    def test_existing_file_is_tightened_to_private(self) -> None:
        Path(self.path).parent.mkdir(parents=True)
        Path(self.path).write_text("old\n")
        os.chmod(self.path, 0o644)
        ReportCapture(self.path).close()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_only_distinct_payloads_are_written(self) -> None:
        capture = ReportCapture(self.path)
        self.assertTrue(capture.record("3", b"\x01\x50\x00"))
        self.assertFalse(capture.record("3", b"\x01\x50\x00"))
        self.assertTrue(capture.record("3", b"\x01\x4f\x00"))
        # The same bytes on another interface are a different report.
        self.assertTrue(capture.record("4", b"\x01\x50\x00"))
        capture.close()
        reports = [line for line in self.lines() if " IF" in line]
        self.assertEqual(len(reports), 3)
        self.assertTrue(reports[0].endswith("IF3 len=3 01 50 00"))
        self.assertTrue(reports[1].endswith("IF3 len=3 01 4f 00"))

    def test_marks_are_written_as_comments(self) -> None:
        capture = ReportCapture(self.path)
        capture.mark("cable connected")
        capture.close()
        self.assertTrue(self.lines()[-1].endswith("# cable connected"))

    def test_the_file_cannot_grow_without_bound(self) -> None:
        capture = ReportCapture(self.path, max_unique=3)
        written = [capture.record("3", bytes([value])) for value in range(10)]
        capture.close()
        self.assertEqual(written.count(True), 3)
        self.assertIn("limit of 3 distinct reports", self.lines()[-1])
        self.assertEqual(len(self.lines()), 1 + 3 + 1)

    def test_unwritable_path_disables_capture_without_raising(self) -> None:
        capture = ReportCapture("/proc/ak820-sentinel/cannot/exist.log")
        self.assertFalse(capture.active)
        self.assertFalse(capture.record("3", b"\x01"))
        capture.mark("ignored")


class CaptureConfigTests(unittest.TestCase):
    def test_off_by_default(self) -> None:
        self.assertEqual(validate_config(Config()).capture_file, "")

    def test_must_be_absolute(self) -> None:
        with self.assertRaises(ConfigError):
            validate_config(Config(capture_file="reports.log"))

    def test_read_from_the_config_file_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "sentinel.conf"
            conf.write_text("[capture]\nfile = /var/lib/x/reports.log\n")
            self.assertEqual(load_config(str(conf)).capture_file, "/var/lib/x/reports.log")
            override = load_config(str(conf), capture_file="/tmp/other.log")
            self.assertEqual(override.capture_file, "/tmp/other.log")


if __name__ == "__main__":
    unittest.main()
