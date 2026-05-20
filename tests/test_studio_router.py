import unittest

from engine.studio_router import (
    adjust_track_mix,
    clear_solos,
    route_track_to_target,
    session_route_summary,
    target_choices_for_track,
)
from session.defaults import build_default_session


class StudioRouterTests(unittest.TestCase):
    def test_target_choices_match_track_kind(self):
        sess = build_default_session()
        drum_choices = [choice.key for choice in target_choices_for_track(sess.tracks[0])]
        audio_choices = [choice.key for choice in target_choices_for_track(sess.tracks[4])]

        self.assertIn("internal.sample_drum_rack", drum_choices)
        self.assertIn("external.sp404.pad_bank", drum_choices)
        self.assertIn("internal.audio_track", audio_choices)
        self.assertIn("network.compa_peer", audio_choices)
        self.assertNotIn("external.sp404.pad_bank", audio_choices)

    def test_route_track_to_external_target_persists_defaults(self):
        sess = build_default_session()
        target = route_track_to_target(
            sess, 1, "external.sp404.a1_a6_beat_bass")

        self.assertEqual(target.key, "external.sp404.a1_a6_beat_bass")
        self.assertEqual(sess.tracks[1].target.key, target.key)
        self.assertEqual(target.params["project"], 3)
        self.assertEqual(target.params["chromatic_pad"], "A6")

    def test_mix_controls_update_track_state(self):
        sess = build_default_session()
        track = adjust_track_mix(sess, 0, "volume", -0.2)
        self.assertLess(track.volume, 0.85)

        track = adjust_track_mix(sess, 0, "pan", 0.5)
        self.assertEqual(track.pan, 0.5)

        adjust_track_mix(sess, 0, "mute")
        adjust_track_mix(sess, 0, "solo")
        adjust_track_mix(sess, 0, "arm")
        self.assertTrue(sess.tracks[0].mute)
        self.assertTrue(sess.tracks[0].solo)
        self.assertTrue(sess.tracks[0].arm)

        clear_solos(sess)
        self.assertFalse(any(track.solo for track in sess.tracks))

    def test_route_summary_includes_status_and_features(self):
        sess = build_default_session()
        summary = session_route_summary(
            sess, pi_generation=4, studio_audio_enabled=True)

        self.assertEqual(summary[0]["target_key"], "internal.sample_drum_rack")
        self.assertEqual(summary[0]["available"], "ready")
        self.assertIn("16 pads", summary[0]["features"])
        self.assertGreaterEqual(summary[0]["clip_count"], 1)


if __name__ == "__main__":
    unittest.main()
