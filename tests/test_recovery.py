"""Tests for the progressive recovery policy."""

from __future__ import annotations

import unittest

import support  # noqa: F401
from sentinel.recovery import RecoveryEngine


class BackoffTests(unittest.TestCase):
    def test_first_attempt_waits_one_second(self) -> None:
        engine = RecoveryEngine((1, 2, 4, 8, 15, 30), 30)
        attempts, wait = engine.on_failure("if0", 100.0, interface="0")
        self.assertEqual(attempts, 1)
        self.assertEqual(wait, 1.0)
        self.assertFalse(engine.ready("if0", 100.5))
        self.assertTrue(engine.ready("if0", 101.0))

    def test_progressive_sequence_and_ceiling(self) -> None:
        engine = RecoveryEngine((1, 2, 4, 8, 15, 30), 30)
        expected = [1, 2, 4, 8, 15, 30]
        now = 0.0
        for index, delay in enumerate(expected, start=1):
            attempts, wait = engine.on_failure("if0", now)
            self.assertEqual(attempts, index)
            self.assertEqual(wait, delay)
            self.assertFalse(engine.ready("if0", now + delay - 0.01))
            self.assertTrue(engine.ready("if0", now + delay))
            now += delay
        # Every attempt after the sixth keeps the maximum delay.
        for index in range(7, 11):
            attempts, wait = engine.on_failure("if0", now)
            self.assertEqual(attempts, index)
            self.assertEqual(wait, 30.0)
            now += wait

    def test_counter_resets_only_after_stability_window(self) -> None:
        engine = RecoveryEngine((1, 2, 4), 30)
        engine.on_failure("if0", 0.0)
        self.assertTrue(engine.on_open("if0", 1.0))
        self.assertEqual(engine.tick(1.0 + 29.9), [])
        self.assertEqual(engine.controller("if0").attempts, 1)
        settled = engine.tick(1.0 + 30.0)
        self.assertEqual([item.key for item in settled], ["if0"])
        self.assertEqual(engine.controller("if0").attempts, 0)
        self.assertTrue(engine.controller("if0").settled)

    def test_early_failure_keeps_previous_attempts(self) -> None:
        engine = RecoveryEngine((1, 2, 4), 30)
        engine.on_failure("if0", 0.0)
        engine.on_open("if0", 1.0)
        attempts, wait = engine.on_failure("if0", 2.0)
        self.assertEqual(attempts, 2)
        self.assertEqual(wait, 2.0)
        # A short lived recovery must not reset the counter.
        self.assertEqual(engine.controller("if0").attempts, 2)

    def test_stable_open_does_not_repeat(self) -> None:
        engine = RecoveryEngine((1,), 30)
        engine.on_failure("if0", 0.0)
        engine.on_open("if0", 1.0)
        self.assertEqual(len(engine.tick(31.0)), 1)
        self.assertEqual(engine.tick(32.0), [])

    def test_interfaces_are_independent(self) -> None:
        engine = RecoveryEngine((1, 2, 4), 30)
        engine.on_failure("if0", 0.0)
        self.assertEqual(engine.controller("if0").attempts, 1)
        self.assertEqual(engine.controller("if1").attempts, 0)
        engine.on_failure("if1", 0.0)
        engine.on_open("if1", 0.5)
        self.assertEqual(engine.controller("if0").attempts, 1)
        self.assertEqual(engine.controller("if1").attempts, 1)

    def test_status_reports_pending_attempts(self) -> None:
        engine = RecoveryEngine((1, 2, 4), 30)
        engine.on_failure("if0", 0.0, interface="0", path="/dev/hidraw0")
        status = engine.status(0.25)
        self.assertTrue(status["active"])
        self.assertEqual(status["pending"][0]["interface"], "0")
        self.assertAlmostEqual(status["pending"][0]["retry_in_seconds"], 0.75)

    def test_invalid_configuration(self) -> None:
        with self.assertRaises(ValueError):
            RecoveryEngine((), 30)
        with self.assertRaises(ValueError):
            RecoveryEngine((1, 0), 30)
        with self.assertRaises(ValueError):
            RecoveryEngine((1,), 0)


if __name__ == "__main__":
    unittest.main()
