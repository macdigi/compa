"""Sample-accurate clip + scene scheduler.

Driven by the audio callback. Each block:
  1. The caller passes the Link beat at the start of the block + the
     beats-per-sample for this block.
  2. The scheduler walks queued launches and promotes any whose
     quantize boundary falls within this block's beat range to playing.
  3. For each playing MidiClip, scan the clip's notes whose
     start_beat falls within (block_start_beat, block_end_beat],
     compute the sample offset within the block, and dispatch
     note_on / note_off to the track's instrument.

State is held in `ClipPlayState` per (track, scene) coordinate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from session.clip import (Clip, MidiClip, AudioClip, ClipState,
                          LaunchQuantize, LaunchMode)
from session.session import Session


@dataclass
class _PlayingNote:
    """A note currently sounding — tracked so we can issue note_offs."""
    pitch: int
    end_beat: float           # absolute beat in Link domain
    voice_id: int = -1


@dataclass
class ClipPlayState:
    """Per-cell runtime state."""
    state: ClipState = ClipState.STOPPED
    queued_at_beat: float = 0.0   # when the launch was scheduled
    actual_start_beat: float = 0.0 # absolute Link beat the clip really started
    notes_active: list[_PlayingNote] = field(default_factory=list)
    last_scanned_beat: float = -1.0  # for incremental note iteration


class ClipScheduler:
    """Owns playback state for the whole session.

    Mutations to clip launch/stop happen via the public API:
      launch_clip(track, scene)
      stop_clip(track)
      stop_all()
      launch_scene(scene)

    The audio callback drives advancement via tick(beat_at_block_start,
    beats_per_sample, frames). Returns nothing — emits MIDI events
    through the registered dispatcher.

    Dispatcher is a callback (track_idx, "note_on"|"note_off", pitch,
    velocity, sample_offset_in_block). The clip_engine wires this to
    instrument note_on/off methods.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        # 8x8 of states; resized on session swap
        self._states: dict[tuple[int, int], ClipPlayState] = {}
        self._dispatcher: Optional[Callable[[int, str, int, int, int], None]] = None

    def set_session(self, session: Session) -> None:
        self.session = session
        self._states.clear()

    def set_dispatcher(
        self, fn: Callable[[int, str, int, int, int], None]
    ) -> None:
        self._dispatcher = fn

    def state_for(self, track: int, scene: int) -> ClipPlayState:
        key = (track, scene)
        st = self._states.get(key)
        if st is None:
            st = ClipPlayState()
            self._states[key] = st
        return st

    # ── Launch / stop API ──────────────────────────────────────────
    def launch_clip(self, track: int, scene: int,
                    current_beat: float) -> None:
        clip = self.session.get_clip(track, scene)
        if clip is None:
            return
        # Stop other playing/queued clips on the same track (mono per track)
        for s in range(len(self.session.tracks[track].clips)):
            if s == scene:
                continue
            other = self.state_for(track, s)
            if other.state in (ClipState.PLAYING, ClipState.QUEUED):
                other.state = ClipState.STOPPED
                # Issue note_offs for active notes
                self._flush_active_notes(track, s, current_beat,
                                         block_offset=0)

        st = self.state_for(track, scene)
        st.queued_at_beat = current_beat
        # Resolve quantize → boundary
        q = clip.launch_quantize
        if q == LaunchQuantize.GLOBAL:
            q = self.session.global_quantize
        beats = q.to_beats(self.session.time_signature_num)
        if beats <= 0:
            # Launch immediately
            st.state = ClipState.PLAYING
            st.actual_start_beat = current_beat
        else:
            # Round up to next boundary
            import math
            next_boundary = math.ceil(current_beat / beats) * beats
            if next_boundary <= current_beat:
                next_boundary += beats
            st.state = ClipState.QUEUED
            st.actual_start_beat = next_boundary
        st.last_scanned_beat = -1.0
        st.notes_active.clear()

    def stop_clip(self, track: int, current_beat: float) -> None:
        for s in range(len(self.session.tracks[track].clips)):
            st = self.state_for(track, s)
            if st.state in (ClipState.PLAYING, ClipState.QUEUED):
                self._flush_active_notes(track, s, current_beat,
                                         block_offset=0)
                st.state = ClipState.STOPPED

    def stop_all(self, current_beat: float) -> None:
        for (t, s), st in self._states.items():
            if st.state in (ClipState.PLAYING, ClipState.QUEUED):
                self._flush_active_notes(t, s, current_beat, block_offset=0)
                st.state = ClipState.STOPPED

    def launch_scene(self, scene: int, current_beat: float) -> None:
        for t in range(len(self.session.tracks)):
            clip = self.session.get_clip(t, scene)
            if clip is not None:
                self.launch_clip(t, scene, current_beat)

    # ── Tick (audio callback) ──────────────────────────────────────
    def tick(self, beat_at_block_start: float,
             beats_per_sample: float, frames: int) -> None:
        if self._dispatcher is None:
            return
        block_end_beat = beat_at_block_start + beats_per_sample * frames

        # Promote any queued clips whose start_beat falls within this block
        for (track, scene), st in self._states.items():
            if st.state == ClipState.QUEUED:
                if st.actual_start_beat <= block_end_beat:
                    st.state = ClipState.PLAYING

        # Walk playing clips, scan notes for this block
        for (track, scene), st in list(self._states.items()):
            if st.state != ClipState.PLAYING:
                continue
            clip = self.session.get_clip(track, scene)
            if clip is None or not isinstance(clip, MidiClip):
                continue

            self._scan_midi_clip(track, scene, clip, st,
                                  beat_at_block_start, block_end_beat,
                                  beats_per_sample)

    # ── Helpers ────────────────────────────────────────────────────
    def _scan_midi_clip(self, track: int, scene: int, clip: MidiClip,
                        st: ClipPlayState, block_start: float,
                        block_end: float, bps: float) -> None:
        loop_len = clip.length_beats
        if loop_len <= 0:
            return
        start_beat = st.actual_start_beat

        # Window in clip-local space
        local_start = (block_start - start_beat) % loop_len
        local_end_abs = block_end - start_beat
        # If we cross a loop boundary in this block, do two passes.
        passes: list[tuple[float, float, float]] = []
        # (clip_window_start, clip_window_end, beat_offset_to_block_start_for_clip_t=0)
        if local_end_abs - (block_start - start_beat) <= 0:
            return
        # Compute local_end via modulo so we stay within [0, loop_len]
        # but we also need to handle wrap-around. Simpler: walk in chunks.
        cursor_block_beat = block_start
        cursor_local = local_start
        while cursor_block_beat < block_end:
            remaining_in_loop = loop_len - cursor_local
            chunk_end_block = min(block_end,
                                  cursor_block_beat + remaining_in_loop)
            chunk_local_end = cursor_local + (chunk_end_block - cursor_block_beat)
            passes.append((cursor_local, chunk_local_end,
                           cursor_block_beat - block_start))
            cursor_block_beat = chunk_end_block
            cursor_local = 0.0
            if not clip.looping and cursor_block_beat < block_end:
                # Stop the clip after one play
                self._flush_active_notes(track, scene, block_start, block_offset=0)
                st.state = ClipState.STOPPED
                return

        # Issue note_offs for any active notes whose end_beat is now <= block_end
        for n in list(st.notes_active):
            if n.end_beat <= block_end:
                offset = max(0, int((n.end_beat - block_start) / bps))
                self._dispatcher(track, "note_off", n.pitch, 0, offset)
                st.notes_active.remove(n)

        # For each pass, find notes whose start_beat falls inside (a, b]
        for (a, b, beat_offset) in passes:
            for note in clip.notes:
                if note.muted:
                    continue
                if not (a < note.start_beat <= b):
                    if not (a == 0.0 and note.start_beat == 0.0
                            and st.last_scanned_beat < 0):
                        continue
                # Compute absolute beat at which to fire
                abs_beat = block_start + beat_offset + (note.start_beat - a)
                offset = max(0, int((abs_beat - block_start) / bps))
                # Probability check (simple)
                import random
                if note.chance < 1.0 and random.random() > note.chance:
                    continue
                vel = note.velocity
                if note.velocity_range > 0:
                    delta = random.randint(-note.velocity_range,
                                            note.velocity_range)
                    vel = max(1, min(127, vel + delta))
                self._dispatcher(track, "note_on", note.pitch, vel, offset)
                # Schedule note_off
                end_beat = abs_beat + note.duration_beats
                st.notes_active.append(_PlayingNote(
                    pitch=note.pitch, end_beat=end_beat))

        st.last_scanned_beat = block_end

    def _flush_active_notes(self, track: int, scene: int,
                             current_beat: float, block_offset: int) -> None:
        st = self.state_for(track, scene)
        if self._dispatcher is None:
            return
        for n in st.notes_active:
            self._dispatcher(track, "note_off", n.pitch, 0, block_offset)
        st.notes_active.clear()

    # ── Visual state queries (for Push 2 / touchscreen) ────────────
    def is_playing(self, track: int, scene: int) -> bool:
        return self.state_for(track, scene).state == ClipState.PLAYING

    def is_queued(self, track: int, scene: int) -> bool:
        return self.state_for(track, scene).state == ClipState.QUEUED

    def get_state(self, track: int, scene: int) -> ClipState:
        return self.state_for(track, scene).state
