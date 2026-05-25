import unittest

import numpy as np

from engine.p6_recorder import (
    MONITOR_GAIN_MAX,
    MONITOR_GAIN_MIN,
    _apply_monitor_gain,
    _clamp_monitor_gain,
)


class MonitorGainTests(unittest.TestCase):
    def test_gain_is_clamped(self):
        self.assertEqual(_clamp_monitor_gain(-1.0), MONITOR_GAIN_MIN)
        self.assertEqual(_clamp_monitor_gain(99.0), MONITOR_GAIN_MAX)
        self.assertEqual(_clamp_monitor_gain(1.5), 1.5)

    def test_monitor_gain_boosts_without_mutating_input(self):
        data = np.array([[0.25, -0.4]], dtype=np.float32)
        boosted = _apply_monitor_gain(data, 2.0)

        np.testing.assert_allclose(boosted, [[0.5, -0.8]])
        np.testing.assert_allclose(data, [[0.25, -0.4]])

    def test_monitor_gain_soft_limits_digital_overs(self):
        data = np.array([[0.75, -0.75]], dtype=np.float32)
        boosted = _apply_monitor_gain(data, 2.0)

        self.assertLessEqual(float(np.max(np.abs(boosted))), 1.0)
        self.assertGreater(float(boosted[0, 0]), 0.95)
        self.assertLess(float(boosted[0, 1]), -0.95)


class RecallCompatibilityTests(unittest.TestCase):
    def test_save_recall_delegates_to_recall_buffer(self):
        from engine.p6_recorder import P6Recorder

        rec = P6Recorder.__new__(P6Recorder)
        calls = []

        def recall_buffer(session_name=""):
            calls.append(session_name)
            return "queued"

        rec.recall_buffer = recall_buffer

        self.assertEqual(rec.save_recall("SP-404MKII"), "queued")
        self.assertEqual(calls, ["SP-404MKII"])


if __name__ == "__main__":
    unittest.main()
