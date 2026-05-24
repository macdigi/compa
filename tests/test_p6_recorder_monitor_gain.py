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


if __name__ == "__main__":
    unittest.main()
