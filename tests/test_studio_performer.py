import unittest

from engine.ai_pattern import ChromaticHit, PatternHit, PatternSpec
from engine.studio_performer import (
    MAX_PERFORMER_TAKES,
    PatternPerformer,
    SP404_VARIATION_STYLES,
    all_notes_off_messages,
    build_midi_events,
    confirmed_sp404_beat_bass_spec,
    generate_sp404_beat_bass_variation,
    normalize_sp404_variation_style,
    performer_take_from_spec,
    spec_from_performer_take,
)
from session.session import Session


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
        self.assertGreater(len(a.hits), 20)
        self.assertGreaterEqual(len(a.chromatic_hits), 5)

    def test_generated_variations_cover_distinct_groove_families(self):
        specs = [generate_sp404_beat_bass_variation(i) for i in range(1, 7)]
        styles = {spec.tags[-1] for spec in specs}
        self.assertEqual(len(styles), 6)
        self.assertEqual(styles, set(SP404_VARIATION_STYLES))
        signatures = {
            tuple((hit.pad, hit.step) for hit in spec.hits)
            for spec in specs
        }
        self.assertEqual(len(signatures), 6)
        bass_counts = {len(spec.chromatic_hits) for spec in specs}
        self.assertGreater(len(bass_counts), 2)

    def test_generated_variation_can_force_genre(self):
        electro = generate_sp404_beat_bass_variation(1, style="electro")
        minimal = generate_sp404_beat_bass_variation(1, style="minimal")
        self.assertEqual(electro.tags[-1], "electro")
        self.assertEqual(minimal.tags[-1], "minimal")
        self.assertNotEqual(
            [(hit.pad, hit.step) for hit in electro.hits],
            [(hit.pad, hit.step) for hit in minimal.hits],
        )
        self.assertEqual(normalize_sp404_variation_style("boom bap", 1),
                         "busy_boom_bap")

    def test_performer_take_round_trips_through_session(self):
        spec = generate_sp404_beat_bass_variation(2, style="breakbeat")
        take = performer_take_from_spec(spec, slot=MAX_PERFORMER_TAKES + 2)
        self.assertEqual(take["slot"], MAX_PERFORMER_TAKES - 1)

        sess = Session.empty()
        sess.studio_performer_takes = [take]
        loaded = Session.from_dict(sess.to_dict())
        restored = spec_from_performer_take(loaded.studio_performer_takes[0])
        self.assertIsNotNone(restored)
        self.assertEqual(restored.to_dict(), spec.to_dict())

    def test_performer_can_queue_next_pattern(self):
        first = PatternSpec(
            name="first",
            prompt="first",
            bars=1,
            bpm=300.0,
            hits=[PatternHit(pad=0, step=0, velocity=100)],
        )
        second = PatternSpec(
            name="second",
            prompt="second",
            bars=1,
            bpm=300.0,
            hits=[PatternHit(pad=1, step=0, velocity=100)],
        )
        messages = []
        player = PatternPerformer()
        try:
            player.play(
                first,
                send_message=messages.append,
                target_key="external.sp404.a1_a6_beat_bass",
                loops=1,
                bpm=300.0,
            )
            self.assertTrue(player.queue_spec(second))
            self.assertEqual(player.status()["queued_pattern_name"], "second")
        finally:
            player.stop()

    def test_performer_take_sequence_status(self):
        first = PatternSpec(
            name="first",
            prompt="first",
            hits=[PatternHit(pad=0, step=0, velocity=100)],
        )
        second = PatternSpec(
            name="second",
            prompt="second",
            hits=[PatternHit(pad=1, step=1, velocity=100)],
        )
        player = PatternPerformer()
        self.assertTrue(player.set_sequence([first, second], start_index=1))
        status = player.status()
        self.assertTrue(status["sequence_enabled"])
        self.assertEqual(status["sequence_count"], 2)
        self.assertEqual(status["sequence_position"], 2)
        player.clear_sequence()
        self.assertFalse(player.status()["sequence_enabled"])


if __name__ == "__main__":
    unittest.main()
