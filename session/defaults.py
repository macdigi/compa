"""Build the default Compa 2 project — 4 MIDI tracks + 4 audio + starter clips."""
from __future__ import annotations

from .session import Session, NUM_SCENES
from .track import Track, TrackType, InstrumentRef
from .scene import Scene
from .clip import MidiClip, LaunchQuantize
from .note import Note
from engine.push2driver.palette import track_color_index


# ── Default MIDI patterns ──────────────────────────────────────────

def _drum_pattern_four_on_floor() -> list[Note]:
    notes = []
    # Kick on every quarter
    for beat in (0.0, 1.0, 2.0, 3.0):
        notes.append(Note(pitch=36, start_beat=beat, duration_beats=0.25,
                          velocity=110))
    # Snare on 2 + 4
    for beat in (1.0, 3.0):
        notes.append(Note(pitch=37, start_beat=beat, duration_beats=0.25,
                          velocity=100))
    # Closed hat on every 8th
    for i in range(8):
        notes.append(Note(pitch=38, start_beat=i * 0.5,
                          duration_beats=0.2, velocity=80))
    return notes


def _drum_pattern_breakbeat() -> list[Note]:
    notes = []
    notes.append(Note(pitch=36, start_beat=0.0, duration_beats=0.25, velocity=110))
    notes.append(Note(pitch=36, start_beat=2.5, duration_beats=0.25, velocity=100))
    notes.append(Note(pitch=37, start_beat=1.0, duration_beats=0.25, velocity=100))
    notes.append(Note(pitch=37, start_beat=3.0, duration_beats=0.25, velocity=100))
    for off in (0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75):
        notes.append(Note(pitch=38, start_beat=off, duration_beats=0.2, velocity=70))
    return notes


def _drum_pattern_hiphop() -> list[Note]:
    notes = []
    for b in (0.0, 1.5, 2.5):
        notes.append(Note(pitch=36, start_beat=b, duration_beats=0.25, velocity=110))
    notes.append(Note(pitch=37, start_beat=1.0, duration_beats=0.25, velocity=100))
    notes.append(Note(pitch=37, start_beat=3.0, duration_beats=0.25, velocity=100))
    for i in range(8):
        notes.append(Note(pitch=38, start_beat=i * 0.5,
                          duration_beats=0.2, velocity=70))
    return notes


def _drum_pattern_half_time() -> list[Note]:
    notes = []
    notes.append(Note(pitch=36, start_beat=0.0, duration_beats=0.25, velocity=120))
    notes.append(Note(pitch=37, start_beat=2.0, duration_beats=0.25, velocity=110))
    for i in range(4):
        notes.append(Note(pitch=38, start_beat=i,
                          duration_beats=0.2, velocity=70))
    return notes


def _bass_pattern_root_5() -> list[Note]:
    return [
        Note(pitch=36, start_beat=0.0, duration_beats=0.5, velocity=100),
        Note(pitch=43, start_beat=1.0, duration_beats=0.5, velocity=90),
        Note(pitch=36, start_beat=2.0, duration_beats=0.5, velocity=100),
        Note(pitch=41, start_beat=3.0, duration_beats=0.5, velocity=90),
    ]


def _bass_pattern_walking() -> list[Note]:
    pitches = [36, 38, 40, 41, 43, 41, 40, 38]
    return [Note(pitch=p, start_beat=i * 0.5, duration_beats=0.45,
                 velocity=95) for i, p in enumerate(pitches)]


def _lead_pattern_arp() -> list[Note]:
    pitches = [60, 64, 67, 72, 67, 64, 60, 64]
    return [Note(pitch=p, start_beat=i * 0.5, duration_beats=0.45,
                 velocity=90) for i, p in enumerate(pitches)]


def _lead_pattern_riff() -> list[Note]:
    return [
        Note(pitch=60, start_beat=0.0, duration_beats=0.5, velocity=100),
        Note(pitch=63, start_beat=0.5, duration_beats=0.5, velocity=95),
        Note(pitch=67, start_beat=1.0, duration_beats=0.5, velocity=100),
        Note(pitch=70, start_beat=1.5, duration_beats=0.5, velocity=95),
        Note(pitch=72, start_beat=2.0, duration_beats=1.0, velocity=110),
        Note(pitch=70, start_beat=3.0, duration_beats=0.5, velocity=90),
        Note(pitch=67, start_beat=3.5, duration_beats=0.5, velocity=85),
    ]


def _pad_pattern_chord() -> list[Note]:
    # Sustained C minor chord with a change halfway through
    return [
        Note(pitch=60, start_beat=0.0, duration_beats=2.0, velocity=80),
        Note(pitch=63, start_beat=0.0, duration_beats=2.0, velocity=80),
        Note(pitch=67, start_beat=0.0, duration_beats=2.0, velocity=80),
        Note(pitch=58, start_beat=2.0, duration_beats=2.0, velocity=75),
        Note(pitch=63, start_beat=2.0, duration_beats=2.0, velocity=75),
        Note(pitch=65, start_beat=2.0, duration_beats=2.0, velocity=75),
    ]


def _make_clip(name: str, notes: list[Note], color: int) -> MidiClip:
    return MidiClip(
        name=name, color=color, length_beats=4.0,
        loop_start_beats=0.0, loop_end_beats=4.0, looping=True,
        launch_quantize=LaunchQuantize.GLOBAL,
        notes=notes,
    )


def build_default_session() -> Session:
    sess = Session(name="default", bpm=98.0,
                    global_quantize=LaunchQuantize.ONE_BAR)
    sess.scenes = [Scene(name=f"Scene {i+1}") for i in range(NUM_SCENES)]

    # Track 1 — Drums
    drum_color = track_color_index(0)
    drums = Track(
        id=0, name="Drums", type=TrackType.MIDI, color=drum_color,
        instrument=InstrumentRef(kind="drum_rack", name="Default Kit"),
        clips=[
            _make_clip("Four", _drum_pattern_four_on_floor(), drum_color),
            _make_clip("Hip-Hop", _drum_pattern_hiphop(), drum_color),
            _make_clip("Break", _drum_pattern_breakbeat(), drum_color),
            _make_clip("Half-Time", _drum_pattern_half_time(), drum_color),
            None, None, None, None,
        ],
    )

    # Track 2 — Bass
    bass_color = track_color_index(1)
    bass = Track(
        id=1, name="Bass", type=TrackType.MIDI, color=bass_color,
        instrument=InstrumentRef(kind="synth_voice", name="Bass Synth",
                                  params={"preset": "bass"}),
        clips=[
            _make_clip("Root-5", _bass_pattern_root_5(), bass_color),
            _make_clip("Walk", _bass_pattern_walking(), bass_color),
            _make_clip("Root-5", _bass_pattern_root_5(), bass_color),
            _make_clip("Walk", _bass_pattern_walking(), bass_color),
            None, None, None, None,
        ],
    )

    # Track 3 — Lead
    lead_color = track_color_index(2)
    lead = Track(
        id=2, name="Lead", type=TrackType.MIDI, color=lead_color,
        instrument=InstrumentRef(kind="synth_voice", name="Lead Synth",
                                  params={"preset": "lead"}),
        clips=[
            _make_clip("Arp", _lead_pattern_arp(), lead_color),
            _make_clip("Riff", _lead_pattern_riff(), lead_color),
            None, None,
            _make_clip("Arp", _lead_pattern_arp(), lead_color),
            None, None, None,
        ],
    )

    # Track 4 — Pad
    pad_color = track_color_index(3)
    pad = Track(
        id=3, name="Pad", type=TrackType.MIDI, color=pad_color,
        instrument=InstrumentRef(kind="synth_voice", name="Pad Synth",
                                  params={"preset": "pad"}),
        clips=[
            _make_clip("Cm chord", _pad_pattern_chord(), pad_color),
            _make_clip("Cm chord", _pad_pattern_chord(), pad_color),
            None, None, None, None, None, None,
        ],
    )

    # Tracks 5–8 — Audio (empty)
    audio_tracks = []
    for i in range(4):
        idx = 4 + i
        audio_tracks.append(Track(
            id=idx,
            name=f"Audio {i+1}",
            type=TrackType.AUDIO,
            color=track_color_index(idx),
            clips=[None] * NUM_SCENES,
        ))

    sess.tracks = [drums, bass, lead, pad, *audio_tracks]
    return sess
