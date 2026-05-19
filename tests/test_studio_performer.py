import unittest

from engine.ai_pattern import ChromaticHit, PatternHit, PatternSpec
from engine.studio_performer import (
    all_notes_off_messages,
    build_midi_events,
    confirmed_sp404_beat_bass_spec,
)


class StudioPerformerTests(unittest.TestCase):
    def test_confirmed_sp404_spec_has_pad_and_chromatic_lanes(self):
        spec = confirmed_sp404_beat_bass_spec()
        self.assertEqual(spec.device, "SP-404MKII")
        self.assertEqual(spec.bank, 0)
        self.assertGreater(len(spec.hits), 40)
        self.assertEqual(len(spec.chromatic_hits), 16)

        note_ons = [
            event.message for event in build_midi_events(spec)
            if event.is_note_on
        ]
        channels = {msg[0] & 0x0F for msg in note_ons}
        self.assertIn(0, channels)
        self.assertIn(15, channels)
        self.assertIn((0x90, 48, 116), note_ons)
        self.assertTrue(any(msg[0] == 0x9F and msg[1] == 60
                            for msg in note_ons))

    def test_build_events_and_all_notes_off(self):
        spec = PatternSpec(
            name="tiny",
            prompt="tiny",
            hits=[PatternHit(pad=0, step=0, velocity=100)],
            chromatic_hits=[ChromaticHit(note=60, step=1, velocity=90)],
        )
        events = build_midi_events(spec, bpm=120.0)
        note_ons = [event for event in events if event.is_note_on]
        self.assertEqual(len(note_ons), 2)
        self.assertLess(note_ons[0].seconds, note_ons[1].seconds)
        offs = all_notes_off_messages(events)
        self.assertIn((0x80, 48, 0), offs)
        self.assertIn((0x8F, 60, 0), offs)


if __name__ == "__main__":
    unittest.main()
