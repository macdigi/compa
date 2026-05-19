import unittest

from engine.clip_engine.engine import ClipEngine
from engine.studio_sampler import (
    assign_sample_to_pad,
    clear_sampler_pad,
    load_starter_kit,
    pad_display_name,
    sampler_pad_specs,
    sampler_track_index,
)
from session.defaults import build_default_session


class StudioSamplerTests(unittest.TestCase):
    def test_default_session_exposes_sampler_track(self):
        sess = build_default_session()
        self.assertEqual(sampler_track_index(sess), 0)
        pads = sampler_pad_specs(sess)
        self.assertEqual(len(pads), 16)
        self.assertTrue(pads[0]["use_default"])
        self.assertEqual(pad_display_name(pads[0], 0), "Kick")

    def test_assign_and_clear_pad_are_persisted_on_instrument_params(self):
        sess = build_default_session()
        track_idx = sampler_track_index(sess)
        sample = "/tmp/Kick.wav"
        spec = assign_sample_to_pad(sess, track_idx, 3, sample)
        self.assertEqual(spec["sample_path"], sample)
        self.assertFalse(spec["use_default"])
        self.assertEqual(
            sess.tracks[track_idx].instrument.params["pads"][3]["sample_path"],
            sample,
        )

        cleared = clear_sampler_pad(sess, track_idx, 3)
        self.assertEqual(cleared["sample_path"], "")
        self.assertFalse(cleared["use_default"])
        self.assertEqual(pad_display_name(cleared, 3), "Empty")

    def test_starter_kit_assigns_repo_samples(self):
        sess = build_default_session()
        track_idx = sampler_track_index(sess)
        count = load_starter_kit(sess, track_idx)
        self.assertGreaterEqual(count, 5)
        pads = sampler_pad_specs(sess, track_idx)
        self.assertTrue(pads[0]["sample_path"].endswith("Kick.wav"))
        self.assertTrue(pads[1]["sample_path"].endswith("Snare.wav"))
        engine = ClipEngine(sample_rate=44100)
        engine.set_session(sess)
        rack = engine._instruments[track_idx]
        self.assertIsNotNone(rack.pads[0].sample)
        self.assertIsNotNone(rack.pads[5].sample)

    def test_clip_engine_builds_empty_and_default_sampler_pads(self):
        sess = build_default_session()
        track_idx = sampler_track_index(sess)
        clear_sampler_pad(sess, track_idx, 0)
        engine = ClipEngine(sample_rate=44100)
        engine.set_session(sess)
        rack = engine._instruments[track_idx]
        self.assertIsNone(rack.pads[0].sample)
        self.assertIsNotNone(rack.pads[1].sample)


if __name__ == "__main__":
    unittest.main()
