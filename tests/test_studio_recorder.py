import unittest

from engine.studio_recorder import (
    active_clip_recordings,
    audio_track_indices,
    format_duration,
    next_empty_scene_index,
    recent_recordings,
    recorder_status,
    selected_audio_track_index,
)
from session.defaults import build_default_session


class _DummyRecorder:
    available = True
    _monitoring = True
    is_recording = True
    duration = 12.4
    device_name = "P-6"
    recall_seconds_available = 17.2
    recall_buffer_seconds = 60
    record_pre_roll_seconds = 4.0
    peak_levels = (0.25, 0.5)
    input_overruns = 2
    input_underruns = 1

    def list_recordings(self):
        return [
            {"filename": "a.wav", "duration": 1.0},
            {"filename": "b.wav", "duration": 2.0},
        ]


class StudioRecorderTests(unittest.TestCase):
    def test_audio_track_selection_uses_default_audio_tracks(self):
        sess = build_default_session()
        self.assertEqual(audio_track_indices(sess), [4, 5, 6, 7])
        self.assertEqual(selected_audio_track_index(sess), 4)
        self.assertEqual(selected_audio_track_index(sess, 6), 6)

    def test_next_empty_scene_finds_first_open_clip_slot(self):
        sess = build_default_session()
        self.assertEqual(next_empty_scene_index(sess, 4), 0)
        sess.tracks[4].clips[0] = sess.tracks[0].clips[0]
        self.assertEqual(next_empty_scene_index(sess, 4), 1)

    def test_recorder_status_and_recent_recordings_are_safe(self):
        status = recorder_status(_DummyRecorder())
        self.assertTrue(status["available"])
        self.assertTrue(status["recording"])
        self.assertEqual(status["device"], "P-6")
        self.assertEqual(status["overruns"], 2)
        self.assertEqual(len(recent_recordings(_DummyRecorder(), 1)), 1)
        self.assertEqual(recorder_status(None)["available"], False)

    def test_active_clip_recordings_reports_engine_slots(self):
        class Engine:
            _recordings = {(4, 2): {"start_beat": 4, "length_beats": 16, "bpm": 98}}

        slots = active_clip_recordings(Engine())
        self.assertEqual(slots[0]["track"], 4)
        self.assertEqual(slots[0]["scene"], 2)
        self.assertEqual(slots[0]["length_beats"], 16)

    def test_format_duration(self):
        self.assertEqual(format_duration(65.2), "01:05.2")


if __name__ == "__main__":
    unittest.main()
