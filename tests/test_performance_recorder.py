import os
import tempfile
import unittest

from engine.performance_recorder import PerformanceRecorder, read_events


class PerformanceRecorderTests(unittest.TestCase):
    def test_writes_jsonl_events(self):
        with tempfile.TemporaryDirectory() as td:
            rec = PerformanceRecorder(
                td, clock_fn=lambda: 12.5, bpm_fn=lambda: 98.0)
            path = rec.start("unit")
            rec.record_note(
                source="push2", device="SP-404MKII", note=48,
                velocity=100, channel=0, payload={"pad": 0})
            rec.record_cc(
                source="twister", device="SP-404MKII", cc=16,
                value=90, channel=0)
            rec.stop()

            self.assertTrue(os.path.exists(path))
            events = read_events(path)

        types = [e.event_type for e in events]
        self.assertIn("recorder.start", types)
        self.assertIn("note", types)
        self.assertIn("cc", types)
        self.assertIn("recorder.stop", types)
        note = next(e for e in events if e.event_type == "note")
        self.assertEqual(note.payload["note"], 48)
        self.assertEqual(note.beat, 12.5)
        self.assertEqual(note.bpm, 98.0)

    def test_throttles_redundant_ccs(self):
        with tempfile.TemporaryDirectory() as td:
            rec = PerformanceRecorder(td, cc_min_interval=10.0)
            path = rec.start("cc")
            rec.record_cc(
                source="push2", device="P-6", cc=74, value=64, channel=14)
            rec.record_cc(
                source="push2", device="P-6", cc=74, value=64, channel=14)
            rec.stop()
            events = read_events(path)
        cc_events = [e for e in events if e.event_type == "cc"]
        self.assertEqual(len(cc_events), 1)


if __name__ == "__main__":
    unittest.main()
