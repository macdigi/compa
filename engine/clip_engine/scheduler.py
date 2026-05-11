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

import random

from session.clip import (Clip, MidiClip, AudioClip, ClipState,
                          LaunchQuantize, LaunchMode,
                          FollowAction, FollowActionType)
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
        """Fire every clip in the row. Tracks with an empty cell in
        this scene get stopped so the row is what you hear (not
        leftovers from the previous scene)."""
        for t in range(len(self.session.tracks)):
            clip = self.session.get_clip(t, scene)
            if clip is not None:
                self.launch_clip(t, scene, current_beat)
            else:
                self.stop_clip(t, current_beat)

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
            if clip is None:
                continue
            if isinstance(clip, MidiClip):
                self._scan_midi_clip(track, scene, clip, st,
                                      beat_at_block_start, block_end_beat,
                                      beats_per_sample)

            # Follow Action — fires when clip has played for after_bars
            self._check_follow_action(track, scene, clip, st,
                                       beat_at_block_start, block_end_beat)

    # ── Helpers ────────────────────────────────────────────────────
    def _scan_midi_clip(self, track: int, scene: int, clip: MidiClip,
                        st: ClipPlayState, block_start: float,
                        block_end: float, bps: float) -> None:
        """Walk this block in clip-local time and fire any notes whose
        start_beat falls inside the block. Half-open intervals
        [a, b) so notes at the loop boundary (beat 0) fire exactly
        once on each wrap.

        Wrapped state is tracked via st.last_scanned_beat which holds
        the absolute Link beat we've already scanned through.
        """
        import random
        loop_len = clip.length_beats
        if loop_len <= 0:
            return
        start_beat = st.actual_start_beat
        if block_end <= start_beat:
            return  # Clip not playing yet in this block

        # Issue note_offs that fall in this block
        for n in list(st.notes_active):
            if n.end_beat <= block_end:
                offset = max(0, min(int((n.end_beat - block_start) / bps),
                                     int((block_end - block_start) / bps) - 1))
                self._dispatcher(track, "note_off", n.pitch, 0, offset)
                st.notes_active.remove(n)

        # Track per-state: how far we've already scanned (absolute beat).
        # Initialize on first pass to start_beat so we include note at 0.
        if st.last_scanned_beat < start_beat:
            scan_from = start_beat
        else:
            scan_from = st.last_scanned_beat

        # Walk the block in loop-aware chunks: [scan_from, block_end)
        cursor = scan_from
        while cursor < block_end:
            local = (cursor - start_beat) % loop_len
            remaining_in_loop = loop_len - local
            chunk_end = min(block_end, cursor + remaining_in_loop)
            # Half-open interval [local, local_end) within the clip
            local_end = local + (chunk_end - cursor)
            # Fire notes in this clip-local window
            for note in clip.notes:
                if note.muted:
                    continue
                if not (local <= note.start_beat < local_end):
                    continue
                if note.chance < 1.0 and random.random() > note.chance:
                    continue
                # Absolute Link beat for this note
                abs_beat = cursor + (note.start_beat - local)
                offset = max(0, min(int((abs_beat - block_start) / bps),
                                     int((block_end - block_start) / bps) - 1))
                vel = note.velocity
                if note.velocity_range > 0:
                    delta = random.randint(-note.velocity_range,
                                            note.velocity_range)
                    vel = max(1, min(127, vel + delta))
                self._dispatcher(track, "note_on", note.pitch, vel, offset)
                end_beat = abs_beat + note.duration_beats
                st.notes_active.append(_PlayingNote(
                    pitch=note.pitch, end_beat=end_beat))
            cursor = chunk_end
            if not clip.looping and cursor >= start_beat + loop_len:
                self._flush_active_notes(track, scene, block_start,
                                          block_offset=0)
                st.state = ClipState.STOPPED
                break

        st.last_scanned_beat = block_end

    # ── Follow Action ──────────────────────────────────────────────
    def _check_follow_action(self, track: int, scene: int, clip: Clip,
                              st: ClipPlayState, block_start: float,
                              block_end: float) -> None:
        fa = getattr(clip, "follow_action", None)
        if fa is None or fa.type == FollowActionType.NONE:
            return
        if fa.after_bars <= 0:
            return
        ts_num = self.session.time_signature_num
        period_beats = fa.after_bars * ts_num
        # Determine if a multiple of period_beats falls inside this block.
        elapsed_at_block_end = block_end - st.actual_start_beat
        elapsed_at_block_start = block_start - st.actual_start_beat
        if elapsed_at_block_end <= 0:
            return
        # Last and current period count
        prev_count = max(0, int(elapsed_at_block_start // period_beats))
        curr_count = int(elapsed_at_block_end // period_beats)
        if curr_count <= prev_count:
            return
        # Roll chance
        if fa.chance < 1.0 and random.random() > fa.chance:
            return
        target = self._resolve_follow_action(track, scene, fa)
        if target is None:
            return
        target_scene = target
        # Stop current then launch target
        self.stop_clip(track, block_start)
        if target_scene >= 0:
            self.launch_clip(track, target_scene, block_start)

    def _resolve_follow_action(self, track: int, scene: int,
                                 fa: FollowAction) -> int | None:
        clips = self.session.tracks[track].clips
        n = len(clips)
        nonempty = [s for s in range(n) if clips[s] is not None]
        if not nonempty:
            return None
        if fa.type == FollowActionType.STOP:
            return -1   # signal "stop without launching"
        if fa.type == FollowActionType.NEXT:
            for s in nonempty:
                if s > scene:
                    return s
            return nonempty[0]   # wrap
        if fa.type == FollowActionType.PREVIOUS:
            for s in reversed(nonempty):
                if s < scene:
                    return s
            return nonempty[-1]
        if fa.type == FollowActionType.FIRST:
            return nonempty[0]
        if fa.type == FollowActionType.LAST:
            return nonempty[-1]
        if fa.type == FollowActionType.ANY:
            return random.choice(nonempty)
        if fa.type == FollowActionType.OTHER:
            others = [s for s in nonempty if s != scene]
            return random.choice(others) if others else None
        if fa.type == FollowActionType.JUMP:
            if 0 <= fa.target_scene < n and clips[fa.target_scene] is not None:
                return fa.target_scene
            return None
        return None

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
