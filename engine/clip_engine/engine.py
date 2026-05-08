"""Top-level clip engine — owns instruments, scheduler, mix bus.

Owned by P6App as `app.clip_engine`. The audio thread calls
`render(out, frames, link_beat, bps)` to produce mixed output for
this block. The control thread calls launch/stop methods.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from session.session import Session
from session.track import Track, TrackType, InstrumentRef
from session.clip import MidiClip, AudioClip

from .scheduler import ClipScheduler
from .instruments.drum_rack import DrumRack, DrumPad
from .instruments.synth_voice import (SynthInstrument, SynthParams,
                                       preset_bass, preset_lead, preset_pad)
from .instruments.synth_kit import default_kit


class ClipEngine:
    """Mixes clip output into a stereo bus the host engine sums."""

    def __init__(self, sample_rate: int = 44100, max_frames: int = 2048) -> None:
        self.sr = sample_rate
        self.session: Session = Session.empty()
        self.scheduler = ClipScheduler(self.session)
        self.scheduler.set_dispatcher(self._dispatch_event)

        # Per-track instrument; index = track_idx
        self._instruments: list[Optional[object]] = [None] * 8

        # Pre-allocated work buffer for instrument render
        self._work = np.zeros((max_frames, 2), dtype=np.float32)

        # Active flag — when False, render() returns silence
        self.active = False

    # ── Session swap ───────────────────────────────────────────────
    def set_session(self, session: Session) -> None:
        self.session = session
        self.scheduler.set_session(session)
        self._instantiate_instruments()

    def _instantiate_instruments(self) -> None:
        self._instruments = [None] * len(self.session.tracks)
        for i, track in enumerate(self.session.tracks):
            if track.type != TrackType.MIDI:
                continue
            ref = track.instrument
            if ref is None:
                continue
            inst = self._build_instrument(ref)
            self._instruments[i] = inst

    def _build_instrument(self, ref: InstrumentRef):
        kind = ref.kind
        if kind == "drum_rack":
            rack = DrumRack(self.sr)
            kit = default_kit()
            for idx, (name, sample) in kit.items():
                rack.set_pad(idx, DrumPad(
                    name=name, sample=sample, sample_rate=self.sr,
                    gain=1.0, pan=0.0, choke_group=0,
                ))
            return rack
        if kind == "synth_voice":
            preset = ref.params.get("preset", "lead")
            if preset == "bass":
                params = preset_bass()
            elif preset == "pad":
                params = preset_pad()
            else:
                params = preset_lead()
            for k, v in ref.params.items():
                if hasattr(params, k):
                    setattr(params, k, v)
            return SynthInstrument(self.sr, params, max_voices=8)
        return None

    # ── Public control ─────────────────────────────────────────────
    def launch_clip(self, track: int, scene: int, link_beat: float) -> None:
        self.scheduler.launch_clip(track, scene, link_beat)

    def stop_clip(self, track: int, link_beat: float) -> None:
        self.scheduler.stop_clip(track, link_beat)

    def stop_all(self, link_beat: float) -> None:
        self.scheduler.stop_all(link_beat)

    def launch_scene(self, scene: int, link_beat: float) -> None:
        self.scheduler.launch_scene(scene, link_beat)

    def play_note_live(self, track: int, pitch: int, velocity: int) -> None:
        """Play a note immediately on a track's instrument (live keyboard)."""
        inst = self._instruments[track] if track < len(self._instruments) else None
        if inst is None:
            return
        inst.note_on(pitch, velocity)

    def stop_note_live(self, track: int, pitch: int) -> None:
        inst = self._instruments[track] if track < len(self._instruments) else None
        if inst is None:
            return
        inst.note_off(pitch)

    def all_notes_off(self) -> None:
        for inst in self._instruments:
            if inst is not None and hasattr(inst, "all_notes_off"):
                inst.all_notes_off()

    # ── Audio render ───────────────────────────────────────────────
    def _dispatch_event(self, track: int, kind: str,
                        pitch: int, velocity: int, sample_offset: int) -> None:
        """Called by the scheduler during tick. We dispatch to the track's
        instrument. The sample_offset within the block isn't observed
        by our instruments yet (they're block-aligned), so this is
        approximate to the block boundary — accurate to ~6 ms which
        is fine for clip launching. Per-step quantization is the
        scheduler's job."""
        inst = self._instruments[track] if track < len(self._instruments) else None
        if inst is None:
            return
        if kind == "note_on":
            inst.note_on(pitch, velocity)
        elif kind == "note_off":
            inst.note_off(pitch)

    def render(self, out: np.ndarray, frames: int,
               link_beat: float, beats_per_sample: float) -> None:
        """Mix clip-engine output into `out` (frames, 2)."""
        if not self.active:
            return

        # Schedule note events for this block
        self.scheduler.tick(link_beat, beats_per_sample, frames)

        # Render each track's instrument
        for i, track in enumerate(self.session.tracks):
            if track.mute:
                continue
            inst = self._instruments[i]
            if inst is None:
                continue
            # Reset work buffer
            self._work[:frames, :] = 0.0
            inst.render(frames, self._work)
            # Apply track volume + pan
            vol = float(track.volume)
            pan = max(-1.0, min(1.0, float(track.pan)))
            l_gain = vol * (1.0 if pan <= 0 else 1.0 - pan)
            r_gain = vol * (1.0 if pan >= 0 else 1.0 + pan)
            out[:frames, 0] += self._work[:frames, 0] * l_gain
            out[:frames, 1] += self._work[:frames, 1] * r_gain
