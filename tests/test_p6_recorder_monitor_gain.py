import json
import queue
import tempfile
import threading
import unittest

import numpy as np

from engine.p6_recorder import (
    MONITOR_GAIN_MAX,
    MONITOR_GAIN_MIN,
    _apply_monitor_gain,
    _clamp_monitor_gain,
    P6_CHANNELS,
    P6Recorder,
    WAVEFORM_POINTS,
)


class _FakeWriter:
    def __init__(self):
        self.blocks = []
        self.closed = False

    def write(self, block):
        self.blocks.append(np.array(block, copy=True))

    def close(self):
        self.closed = True


def _make_recording_recorder(writer, filepath):
    rec = P6Recorder.__new__(P6Recorder)
    rec._lock = threading.Lock()
    rec._recording = True
    rec._writer = writer
    rec._record_queue = queue.Queue()
    rec._record_writer_thread = threading.Thread(
        target=rec._record_writer_loop,
        args=(writer, rec._record_queue, filepath),
        daemon=True,
    )
    rec._record_writer_thread.start()
    rec._current_file = filepath
    rec._sample_rate = 4
    rec._samples_written = 0
    rec._record_metadata = {}
    rec._record_write_error = None
    rec._record_queue_peak_blocks = 0
    rec._input_overruns = 0
    rec._input_underruns = 0
    rec._input_overrun_log_t = 0.0
    rec._input_overrun_console_count = 0
    rec._recall_buf_frames = 16
    rec._recall_buf = np.zeros((rec._recall_buf_frames, P6_CHANNELS), dtype=np.float32)
    rec._recall_write_pos = 0
    rec._recall_total_written = 0
    rec._monitor_out_buf = None
    rec.link_broadcaster = None
    rec._threshold_mode = False
    rec._peak_l = 0.0
    rec._peak_r = 0.0
    rec._waveform = np.zeros(WAVEFORM_POINTS, dtype=np.float32)
    rec._waveform_pos = 0
    rec.on_recording_complete = None
    return rec


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
        rec = P6Recorder.__new__(P6Recorder)
        calls = []

        def recall_buffer(session_name=""):
            calls.append(session_name)
            return "queued"

        rec.recall_buffer = recall_buffer

        self.assertEqual(rec.save_recall("SP-404MKII"), "queued")
        self.assertEqual(calls, ["SP-404MKII"])


class RecordingQueueTests(unittest.TestCase):
    def test_audio_callback_queues_blocks_and_stop_flushes_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            filepath = f"{tmp}/take.wav"
            writer = _FakeWriter()
            rec = _make_recording_recorder(writer, filepath)

            first = np.ones((3, P6_CHANNELS), dtype=np.float32)
            second = np.full((2, P6_CHANNELS), 2.0, dtype=np.float32)

            rec._audio_callback(first, len(first), None, None)
            first[:] = 99.0
            rec._audio_callback(second, len(second), None, None)

            self.assertEqual(rec._stop_recording_impl(), filepath)

            self.assertTrue(writer.closed)
            self.assertFalse(rec._recording)
            self.assertIsNone(rec._record_queue)
            self.assertEqual(len(writer.blocks), 2)
            np.testing.assert_allclose(writer.blocks[0], np.ones((3, P6_CHANNELS)))
            np.testing.assert_allclose(writer.blocks[1], np.full((2, P6_CHANNELS), 2.0))

            with open(filepath + ".meta.json") as handle:
                meta = json.load(handle)
            self.assertEqual(meta["duration"], 1.2)
            self.assertEqual(meta["input_overruns"], 0)
            self.assertGreaterEqual(meta["record_queue_peak_blocks"], 1)

    def test_enqueue_is_noop_when_not_recording(self):
        rec = P6Recorder.__new__(P6Recorder)
        rec._lock = threading.Lock()
        rec._recording = False
        rec._record_queue = queue.Queue()
        rec._samples_written = 0
        rec._record_queue_peak_blocks = 0

        rec._enqueue_recording_block(
            np.ones((4, P6_CHANNELS), dtype=np.float32), 4)

        self.assertEqual(rec._samples_written, 0)
        self.assertTrue(rec._record_queue.empty())


if __name__ == "__main__":
    unittest.main()
