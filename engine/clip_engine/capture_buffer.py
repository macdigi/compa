"""MIDI input capture buffer for Live-style 'Capture MIDI'.

Every MIDI note event the user plays gets timestamped and parked here.
On Record + New (Capture MIDI), we walk back to the last 'musical
reset' (silence gap > 1.5s OR transport start) and convert events
since that point into a MidiClip.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional


class CaptureBuffer:
    """Bounded ring of (link_beat, kind, pitch, velocity)."""

    SILENCE_GAP_BEATS = 2.0  # gap that signals a phrase boundary

    def __init__(self, max_events: int = 4096) -> None:
        self._events: deque = deque(maxlen=max_events)
        self._lock = __import__("threading").Lock()
        self._last_event_beat: float = -1.0

    def note_on(self, beat: float, pitch: int, velocity: int,
                track: int = -1) -> None:
        with self._lock:
            self._events.append((beat, "on", pitch, velocity, track))
            self._last_event_beat = beat

    def note_off(self, beat: float, pitch: int, track: int = -1) -> None:
        with self._lock:
            self._events.append((beat, "off", pitch, 0, track))
            self._last_event_beat = beat

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_event_beat = -1.0

    def capture_clip(self, ending_beat: float,
                     bpm: float = 120.0,
                     track_filter: Optional[int] = None,
                     time_sig_num: int = 4):
        """Build a MidiClip from the most recent phrase up to ending_beat.

        Returns a MidiClip or None if nothing's there.
        """
        from session.clip import MidiClip, LaunchQuantize
        from session.note import Note

        with self._lock:
            evs = [e for e in self._events
                   if (track_filter is None or e[4] == track_filter)
                   and e[0] <= ending_beat]
        if not evs:
            return None

        # Walk backwards to find a phrase boundary: silence gap.
        evs.sort(key=lambda e: e[0])
        gap = self.SILENCE_GAP_BEATS
        start_idx = 0
        for i in range(len(evs) - 1, 0, -1):
            if evs[i][0] - evs[i - 1][0] > gap:
                start_idx = i
                break
        phrase = evs[start_idx:]
        if not phrase:
            return None

        first_beat = phrase[0][0]
        # Build notes — match note-on with note-off
        active: dict[int, tuple[float, int]] = {}
        notes: list[Note] = []
        for (beat, kind, pitch, vel, _track) in phrase:
            local_beat = beat - first_beat
            if kind == "on" and vel > 0:
                active[pitch] = (local_beat, vel)
            else:  # off (or note-on velocity 0)
                if pitch in active:
                    start, on_vel = active.pop(pitch)
                    dur = max(0.05, local_beat - start)
                    notes.append(Note(pitch=pitch, start_beat=start,
                                       duration_beats=dur,
                                       velocity=on_vel))
        # Close any still-held notes
        for pitch, (start, on_vel) in active.items():
            local_beat = phrase[-1][0] - first_beat
            dur = max(0.05, local_beat - start + 0.1)
            notes.append(Note(pitch=pitch, start_beat=start,
                               duration_beats=dur, velocity=on_vel))

        if not notes:
            return None

        # Round phrase length to nearest power of 2 bars
        max_end = max(n.start_beat + n.duration_beats for n in notes)
        bars = max(1, int(max_end / time_sig_num) + (1 if max_end % time_sig_num else 0))
        # Snap up to next power of 2
        snap = 1
        while snap < bars:
            snap *= 2
        length_beats = float(snap * time_sig_num)
        return MidiClip(
            name=f"Captured {int(time.time()) % 1000}",
            length_beats=length_beats,
            loop_start_beats=0.0,
            loop_end_beats=length_beats,
            looping=True,
            launch_quantize=LaunchQuantize.GLOBAL,
            notes=notes,
        )
