import unittest

from engine.studio_targets import (
    availability_label,
    capability_for,
    is_available,
)
from session.defaults import build_default_session
from session.session import Session


class StudioTargetTests(unittest.TestCase):
    def test_default_session_tracks_have_explicit_targets(self):
        sess = build_default_session()
        keys = [track.target.key for track in sess.tracks]
        self.assertEqual(keys[:4], [
            "internal.sample_drum_rack",
            "internal.mono_synth",
            "internal.poly_synth",
            "internal.poly_synth",
        ])
        self.assertEqual(keys[4:], ["internal.audio_track"] * 4)

        loaded = Session.from_dict(sess.to_dict())
        self.assertEqual([track.target.key for track in loaded.tracks], keys)

    def test_legacy_tracks_without_targets_are_inferred(self):
        data = build_default_session().to_dict()
        for track in data["tracks"]:
            track.pop("target", None)

        loaded = Session.from_dict(data)
        self.assertEqual(loaded.tracks[0].target.key,
                         "internal.sample_drum_rack")
        self.assertEqual(loaded.tracks[1].target.key, "internal.mono_synth")
        self.assertEqual(loaded.tracks[2].target.key, "internal.poly_synth")
        self.assertEqual(loaded.tracks[4].target.key, "internal.audio_track")

    def test_external_sp_performer_target_is_pi3_safe(self):
        capability = capability_for("external.sp404.a1_a6_beat_bass")
        self.assertEqual(capability.device, "SP-404MKII")
        self.assertEqual(capability.pads, 6)
        self.assertTrue(capability.chromatic)
        self.assertTrue(capability.fx_cc)
        self.assertTrue(is_available(
            capability, pi_generation=3, studio_audio_enabled=False))

    def test_internal_audio_targets_are_gated_on_pi3(self):
        capability = capability_for("internal.drum_synth")
        self.assertFalse(is_available(
            capability, pi_generation=3, studio_audio_enabled=True))
        self.assertEqual(
            availability_label(
                capability, pi_generation=3, studio_audio_enabled=True),
            "Pi 4+",
        )
        self.assertFalse(is_available(
            capability, pi_generation=4, studio_audio_enabled=False))
        self.assertTrue(is_available(
            capability, pi_generation=4, studio_audio_enabled=True))


if __name__ == "__main__":
    unittest.main()
