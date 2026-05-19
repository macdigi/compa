import unittest

from engine.ai_pattern import ChromaticHit, PatternHit, PatternSpec
from engine.studio_performer import (
    all_notes_off_messages,
    build_midi_events,
    confirmed_sp404_beat_bass_spec,
    generate_sp404_beat_bass_variation,
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
        loop_seconds = spec.length_beats * 60.0 / spec.bpm
        self.assertLessEqual(max(event.seconds for event in build_midi_events(spec)),
                             loop_seconds)

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

    def test_bpm_changes_retime_events(self):
        spec = confirmed_sp404_beat_bass_spec()
        slow = build_midi_events(spec, bpm=60.0)
        fast = build_midi_events(spec, bpm=120.0)
        slow_time = next(event.seconds for event in slow
                         if event.is_note_on and event.seconds > 0.0)
        fast_time = next(event.seconds for event in fast
                         if event.is_note_on and event.seconds > 0.0)
        self.assertAlmostEqual(
            slow_time,
            fast_time * 2.0,
            places=3,
        )

    def test_generated_variations_are_deterministic(self):
        a = generate_sp404_beat_bass_variation(3)
        b = generate_sp404_beat_bass_variation(3)
        c = generate_sp404_beat_bass_variation(4)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertNotEqual(a.to_dict(), c.to_dict())
        self.assertEqual(a.device, "SP-404MKII")
        self.assertGreater(len(a.hits), 30)
        self.assertEqual(len(a.chromatic_hits), 16)


if __name__ == "__main__":
    unittest.main()
