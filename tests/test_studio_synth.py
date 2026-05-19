import unittest

from engine.clip_engine.engine import ClipEngine
from engine.clip_engine.instruments.synth_voice import SynthInstrument
from engine.studio_synth import (
    adjust_synth_param,
    cycle_synth_waveform,
    ensure_synth_track,
    set_synth_preset,
    synth_params,
    synth_track_index,
    synth_track_indices,
    synth_track_role,
)
from session.defaults import build_default_session


class StudioSynthTests(unittest.TestCase):
    def test_default_session_exposes_synth_tracks(self):
        sess = build_default_session()
        self.assertEqual(synth_track_indices(sess), [1, 2, 3])
        self.assertEqual(synth_track_index(sess), 1)
        self.assertEqual(synth_track_role(sess.tracks[1]), "mono")
        self.assertEqual(synth_track_role(sess.tracks[2]), "poly")

    def test_presets_and_macros_persist_on_instrument_params(self):
        sess = build_default_session()
        idx = synth_track_index(sess)
        params = set_synth_preset(sess, idx, "lead")
        self.assertEqual(sess.tracks[idx].instrument.params["preset"], "lead")
        self.assertEqual(sess.tracks[idx].target.key, "internal.poly_synth")
        self.assertEqual(params["waveform"], "square")

        adjusted = adjust_synth_param(sess, idx, "cutoff_hz", 500.0)
        self.assertEqual(
            sess.tracks[idx].instrument.params["cutoff_hz"],
            adjusted["cutoff_hz"],
        )
        self.assertGreater(adjusted["cutoff_hz"], params["cutoff_hz"])

        wave = cycle_synth_waveform(sess, idx)
        self.assertEqual(sess.tracks[idx].instrument.params["waveform"], wave)

    def test_ensure_synth_track_reuses_default_tracks(self):
        sess = build_default_session()
        self.assertEqual(ensure_synth_track(sess), 1)
        self.assertEqual(len(synth_params(sess, 1)), 11)

    def test_clip_engine_builds_synth_instruments(self):
        sess = build_default_session()
        idx = synth_track_index(sess)
        engine = ClipEngine(sample_rate=44100)

        engine.set_session(sess)

        self.assertIsInstance(engine._instruments[idx], SynthInstrument)


if __name__ == "__main__":
    unittest.main()
