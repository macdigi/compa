import os
import tempfile
import unittest

from engine.ai_pattern import (
    ChromaticHit,
    PatternSpec,
    SP404,
    bank_to_index,
    chromatic_note_channel,
    device_note_channel,
    export_midi,
    generate_pattern,
    install_clip,
    install_step_grid,
    to_midi_clip,
)
from session.defaults import build_default_session


class AIPatternTests(unittest.TestCase):
    def test_generates_deterministic_pattern(self):
        a = generate_pattern("dusty boom bap fill", seed=1234)
        b = generate_pattern("dusty boom bap fill", seed=1234)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(a.device, SP404)
        self.assertGreater(len(a.hits), 0)
        self.assertTrue(all(0 <= h.step < a.total_steps for h in a.hits))

    def test_converts_to_native_midi_clip(self):
        spec = generate_pattern("sparse half time", device="P-6", seed=55)
        clip = to_midi_clip(spec)
        self.assertEqual(clip.length_beats, spec.length_beats)
        self.assertEqual(len(clip.notes), len(spec.hits))
        self.assertTrue(all(36 <= n.pitch <= 41 for n in clip.notes))

    def test_installs_clip_and_step_grid(self):
        sess = build_default_session()
        spec = generate_pattern("house", seed=7)
        scene = install_clip(sess, spec, 0, None)
        self.assertIsNotNone(sess.get_clip(0, scene))

        grids = {}
        install_step_grid(grids, spec, 0)
        grid = grids[(spec.device, 0)]
        self.assertEqual(len(grid), 16)
        active = sum(1 for row in grid for on, _ in row if on)
        self.assertGreater(active, 0)

    def test_device_note_channels_and_midi_export(self):
        spec = generate_pattern("boom bap", device="SP-404", bank="B", seed=1)
        note, channel = device_note_channel(spec, 0)
        self.assertEqual(bank_to_index("B"), 1)
        self.assertEqual(channel, 1)
        self.assertEqual(note, 48)

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "pattern.mid")
            export_midi(spec, path)
            with open(path, "rb") as f:
                data = f.read()
        self.assertTrue(data.startswith(b"MThd"))
        self.assertIn(b"MTrk", data)

    def test_chromatic_hits_round_trip_and_export(self):
        spec = PatternSpec(
            name="bass test",
            prompt="sub bass",
            device="SP-404MKII",
            bank=0,
            chromatic_hits=[
                ChromaticHit(note=60, step=0, velocity=100,
                             duration_steps=4.0, label="root"),
                ChromaticHit(note=55, step=8, velocity=90,
                             duration_steps=2.0, label="fifth"),
            ],
        )
        self.assertEqual(spec.to_dict()["chromatic_hits"][0]["note"], 60)
        loaded = PatternSpec.from_dict(spec.to_dict())
        self.assertEqual(len(loaded.chromatic_hits), 2)
        self.assertEqual(chromatic_note_channel(loaded, 60), (60, 15))

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bass.mid")
            export_midi(loaded, path)
            with open(path, "rb") as f:
                data = f.read()
        self.assertIn(bytes([0x9F, 60, 100]), data)


if __name__ == "__main__":
    unittest.main()
