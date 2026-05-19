import unittest

import numpy as np

from engine.clip_engine.engine import ClipEngine
from engine.clip_engine.instruments.drum_synth import DrumSynthInstrument
from engine.studio_drum_synth import (
    adjust_voice_param,
    drum_synth_track_index,
    drum_synth_voice_specs,
    ensure_drum_synth_track,
    kit_voice_specs,
    set_drum_synth_kit,
)
from session.defaults import build_default_session


class StudioDrumSynthTests(unittest.TestCase):
    def test_ensure_drum_synth_track_appends_internal_target(self):
        sess = build_default_session()
        self.assertIsNone(drum_synth_track_index(sess))

        idx = ensure_drum_synth_track(sess)

        self.assertEqual(idx, len(sess.tracks) - 1)
        self.assertEqual(sess.tracks[idx].target.key, "internal.drum_synth")
        self.assertEqual(sess.tracks[idx].instrument.kind, "drum_synth")
        self.assertEqual(len(drum_synth_voice_specs(sess, idx)), 16)

    def test_kit_switch_and_voice_macros_persist_on_track(self):
        sess = build_default_session()
        idx = ensure_drum_synth_track(sess)

        set_drum_synth_kit(sess, idx, "909")
        specs = drum_synth_voice_specs(sess, idx)
        self.assertEqual(sess.tracks[idx].instrument.params["kit"], "909")
        self.assertEqual(specs[0]["name"], "909 Kick")

        before = specs[0]["tone"]
        adjusted = adjust_voice_param(sess, idx, 0, "tone", 0.2)
        self.assertGreater(adjusted["tone"], before)
        self.assertEqual(
            sess.tracks[idx].instrument.params["voices"][0]["tone"],
            adjusted["tone"],
        )

    def test_drum_synth_instrument_renders_audio(self):
        instrument = DrumSynthInstrument(44100, kit_voice_specs("808"))
        out = np.zeros((1024, 2), dtype=np.float32)

        instrument.note_on(36, 110)
        instrument.render(1024, out)

        self.assertGreater(float(np.max(np.abs(out))), 0.0)

    def test_clip_engine_builds_drum_synth_instrument(self):
        sess = build_default_session()
        idx = ensure_drum_synth_track(sess)
        engine = ClipEngine(sample_rate=44100)

        engine.set_session(sess)

        self.assertIsInstance(engine._instruments[idx], DrumSynthInstrument)


if __name__ == "__main__":
    unittest.main()
