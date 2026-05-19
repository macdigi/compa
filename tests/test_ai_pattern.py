import os
import tempfile
import unittest

from engine.ai_pattern import (
    SP404,
    bank_to_index,
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


if __name__ == "__main__":
    unittest.main()
