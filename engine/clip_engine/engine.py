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

from session.clip import AudioClip, MidiClip, ClipState

from .scheduler import ClipScheduler
from .capture_buffer import CaptureBuffer
from .audio_clip_voice import AudioClipVoice
from .instruments.drum_synth import DrumSynthInstrument
from .instruments.drum_rack import DrumRack, DrumPad
from .instruments.synth_voice import (SynthInstrument, SynthParams,
                                       preset_bass, preset_lead, preset_pad)
from .instruments.synth_kit import default_kit
from .sample_loader import load_sample
from engine.studio_drum_synth import drum_synth_voice_specs
from engine.studio_sampler import normalized_pad_spec, SAMPLER_PAD_COUNT


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

        # Capture-MIDI ring buffer
        self.capture = CaptureBuffer(max_events=4096)

        # AudioClip playback voices: {(track, scene) → AudioClipVoice}
        self._audio_voices: dict[tuple[int, int], AudioClipVoice] = {}

        # Recording state: {(track, scene) → RecordingState}
        # RecordingState dict: armed_beat, start_beat, length_beats,
        #   recorder_start_total, sample_rate, finalized
        self._recordings: dict[tuple[int, int], dict] = {}
        # Reference to compa's existing P6Recorder. The host wires this
        # in after construction.
        self.recorder = None

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
            pad_specs = ref.params.get("pads")
            if isinstance(pad_specs, list):
                for idx in range(SAMPLER_PAD_COUNT):
                    spec = normalized_pad_spec(
                        pad_specs[idx] if idx < len(pad_specs) else None, idx)
                    name = spec["name"]
                    sample = None
                    sample_rate = self.sr
                    if spec["sample_path"]:
                        loaded = load_sample(spec["sample_path"])
                        if loaded is not None:
                            data, sr = loaded
                            sample = self._prepare_drum_sample(data, sr)
                            sample_rate = self.sr
                    elif spec["use_default"] and idx in kit:
                        name, sample = kit[idx]
                    rack.set_pad(idx, DrumPad(
                        name=name,
                        sample=sample,
                        sample_rate=sample_rate,
                        sample_path=spec["sample_path"],
                        gain=spec["gain"],
                        pan=spec["pan"],
                        tune_semitones=spec["tune"],
                        choke_group=spec["choke_group"],
                    ))
            else:
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
        if kind == "drum_synth":
            return DrumSynthInstrument(
                self.sr,
                drum_synth_voice_specs(self.session, self._instrument_ref_track(ref)),
            )
        return None

    def _instrument_ref_track(self, ref: InstrumentRef) -> int | None:
        for idx, track in enumerate(self.session.tracks):
            if track.instrument is ref:
                return idx
        return None

    def _prepare_drum_sample(self, data: np.ndarray, sample_rate: int) -> np.ndarray:
        if data.ndim == 2:
            mono = data.mean(axis=1)
        else:
            mono = data
        mono = mono.astype(np.float32)
        if int(sample_rate) == int(self.sr) or len(mono) < 2:
            return mono
        ratio = float(self.sr) / float(sample_rate)
        new_len = max(1, int(len(mono) * ratio))
        x_old = np.arange(len(mono), dtype=np.float32)
        x_new = np.linspace(0, len(mono) - 1, new_len, dtype=np.float32)
        return np.interp(x_new, x_old, mono).astype(np.float32)

    # ── Public control ─────────────────────────────────────────────
    def launch_clip(self, track: int, scene: int, link_beat: float) -> None:
        # If it's an AudioClip, prep its voice
        clip = self.session.get_clip(track, scene) if self.session else None
        if isinstance(clip, AudioClip):
            voice = self._audio_voices.get((track, scene))
            if voice is None:
                # Lazy-load audio
                if clip.audio is None and clip.audio_path:
                    loaded = load_sample(clip.audio_path)
                    if loaded is not None:
                        data, sr = loaded
                        clip.audio = data
                        clip.sample_rate = sr
                        if clip.end_sample == 0:
                            clip.end_sample = len(data)
                        if clip.loop_end_sample == 0:
                            clip.loop_end_sample = len(data)
                voice = AudioClipVoice(clip, sample_rate=self.sr)
                self._audio_voices[(track, scene)] = voice
            voice.trigger()
            # Stop any other AudioClip on this track (mono-per-track)
            for (t, s), v in list(self._audio_voices.items()):
                if t == track and s != scene:
                    v.stop()
        self.scheduler.launch_clip(track, scene, link_beat)

    def stop_clip(self, track: int, link_beat: float) -> None:
        for (t, s), v in list(self._audio_voices.items()):
            if t == track:
                v.stop()
        self.scheduler.stop_clip(track, link_beat)

    def stop_all(self, link_beat: float) -> None:
        for v in self._audio_voices.values():
            v.stop()
        self.scheduler.stop_all(link_beat)

    def launch_scene(self, scene: int, link_beat: float) -> None:
        self.scheduler.launch_scene(scene, link_beat)

    # ── Recording into audio clip slots ───────────────────────────
    def arm_recording(self, track: int, scene: int,
                      link_beat: float, bpm: float,
                      length_bars: int = 4,
                      time_sig_num: int = 4) -> bool:
        """Arm a (track, scene) for recording from the recorder's input.

        Recording starts on the next bar boundary at link_beat-rounded-up.
        After length_bars complete, finalize() turns the captured audio
        into an AudioClip in the slot.

        Returns True if recording was armed; False if the recorder is
        not available (no input device).
        """
        if self.recorder is None or not getattr(self.recorder, "_monitoring", False):
            return False
        sr = int(self.recorder._sample_rate)
        # Snap start to next bar
        import math
        bar_beats = float(time_sig_num)
        start_beat = math.ceil(link_beat / bar_beats) * bar_beats
        if start_beat <= link_beat:
            start_beat += bar_beats
        length_beats = float(length_bars * time_sig_num)
        # Sample count for this length at session BPM
        seconds = length_beats * 60.0 / bpm
        length_samples = int(seconds * sr)
        # Buffer position at the moment recording will START — we
        # snapshot _recall_total_written + the seconds-until-start delta.
        # We compute the absolute target start in "total frames" units
        # since the recorder's buffer is a ring with monotonic
        # _recall_total_written tracking how many samples have ever
        # been written.
        seconds_until_start = (start_beat - link_beat) * 60.0 / bpm
        recorder_start_total = (self.recorder._recall_total_written
                                 + int(seconds_until_start * sr))
        self._recordings[(track, scene)] = {
            "armed_beat": link_beat,
            "start_beat": start_beat,
            "length_beats": length_beats,
            "length_samples": length_samples,
            "recorder_start_total": recorder_start_total,
            "sample_rate": sr,
            "bpm": bpm,
        }
        return True

    def cancel_recording(self, track: int, scene: int) -> None:
        self._recordings.pop((track, scene), None)

    def is_recording(self, track: int, scene: int) -> bool:
        return (track, scene) in self._recordings

    def _check_finalize_recordings(self, link_beat: float) -> None:
        """Called from render() — finalize any recordings that have
        captured their full length."""
        if not self._recordings or self.recorder is None:
            return
        done = []
        for key, rec in list(self._recordings.items()):
            end_beat = rec["start_beat"] + rec["length_beats"]
            if link_beat < end_beat:
                continue
            # Slice the recorder buffer
            sr = rec["sample_rate"]
            start = rec["recorder_start_total"]
            end = start + rec["length_samples"]
            current_total = self.recorder._recall_total_written
            buf = self.recorder._recall_buf
            buf_len = buf.shape[0]
            if current_total < end:
                continue  # not enough captured yet
            # Map absolute-totals to ring positions
            start_ring = start % buf_len
            end_ring = end % buf_len
            if start_ring < end_ring:
                audio = buf[start_ring:end_ring].copy()
            else:
                audio = np.concatenate(
                    [buf[start_ring:], buf[:end_ring]], axis=0).copy()
            done.append((key, rec, audio))
        for (track, scene), rec, audio in done:
            self._recordings.pop((track, scene), None)
            try:
                self._install_recorded_clip(track, scene, audio,
                                              rec["sample_rate"], rec["bpm"],
                                              rec["length_beats"])
            except Exception as e:
                print(f"finalize recording failed: {e}", flush=True)

    def _install_recorded_clip(self, track: int, scene: int,
                                audio: np.ndarray, sr: int,
                                bpm: float, length_beats: float) -> None:
        from session.clip import AudioClip, LaunchQuantize, WarpMode
        from session.note import Note  # not used; just to be sure imports work
        clip = AudioClip(
            name=f"Rec {track+1}.{scene+1}",
            length_beats=length_beats,
            loop_start_beats=0.0,
            loop_end_beats=length_beats,
            looping=True,
            launch_quantize=LaunchQuantize.GLOBAL,
            sample_rate=sr,
            original_bpm=bpm,
            audio=audio,
            audio_path="",
            warp_mode=WarpMode.BEATS,
            start_sample=0, end_sample=len(audio),
            loop_start_sample=0, loop_end_sample=len(audio),
            tempo_leader=False,
        )
        if self.session is None:
            return
        self.session.set_clip(track, scene, clip)
        # Auto-launch immediately so the user hears it
        self.launch_clip(track, scene, link_beat=0.0)

    def play_note_live(self, track: int, pitch: int, velocity: int,
                       link_beat: float = 0.0) -> None:
        """Play a note immediately on a track's instrument (live keyboard)."""
        inst = self._instruments[track] if track < len(self._instruments) else None
        if inst is None:
            return
        inst.note_on(pitch, velocity)
        # Capture for Capture-MIDI feature
        try:
            self.capture.note_on(link_beat, pitch, velocity, track=track)
        except Exception:
            pass

    def stop_note_live(self, track: int, pitch: int,
                       link_beat: float = 0.0) -> None:
        inst = self._instruments[track] if track < len(self._instruments) else None
        if inst is None:
            return
        inst.note_off(pitch)
        try:
            self.capture.note_off(link_beat, pitch, track=track)
        except Exception:
            pass

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

        # Finalize any recordings that have captured their full length
        self._check_finalize_recordings(link_beat)

        # Schedule note events for this block
        self.scheduler.tick(link_beat, beats_per_sample, frames)

        # Solo logic: if any track is soloed, only soloed tracks render
        any_solo = any(t.solo for t in self.session.tracks)

        # Render each track's instrument + audio clip voice
        for i, track in enumerate(self.session.tracks):
            if track.mute:
                continue
            if any_solo and not track.solo:
                continue
            self._work[:frames, :] = 0.0
            inst = self._instruments[i] if i < len(self._instruments) else None
            if inst is not None:
                inst.render(frames, self._work)
            # Audio voices for this track
            sess_bpm = float(self.session.bpm)
            for (t, s), voice in self._audio_voices.items():
                if t == i and voice.active:
                    try:
                        voice.render(frames, self._work, sess_bpm)
                    except Exception:
                        pass
            # Apply track volume + pan
            vol = float(track.volume)
            pan = max(-1.0, min(1.0, float(track.pan)))
            l_gain = vol * (1.0 if pan <= 0 else 1.0 - pan)
            r_gain = vol * (1.0 if pan >= 0 else 1.0 + pan)
            out[:frames, 0] += self._work[:frames, 0] * l_gain
            out[:frames, 1] += self._work[:frames, 1] * r_gain
