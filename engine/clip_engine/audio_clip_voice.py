"""AudioClip playback voice.

Two warp paths:
- repitch / beats → linear-interpolation resample (fast, pitch-tied-to-tempo)
- tones / texture / complex / complex_pro → Rubber Band Library
  real-time stretcher (LGPL, dynamic-linked)

When the clip's audio + transpose changes, time_ratio + pitch_scale on
the stretcher get updated to keep the clip locked to session BPM.
"""
from __future__ import annotations

import numpy as np

from .warp.repitch import resample_block
from .warp.rubberband import RubberBandStretcher, HAVE_RUBBERBAND


_RB_MODES = {"beats", "tones", "texture", "complex", "complex_pro"}


class AudioClipVoice:
    def __init__(self, clip, sample_rate: int = 44100) -> None:
        self.clip = clip
        self.sr = sample_rate
        self.position = 0.0       # source sample position (re-pitch path)
        self.active = False
        self._stretcher: RubberBandStretcher | None = None
        self._rb_input_pos = 0
        self._init_stretcher()

    def _init_stretcher(self) -> None:
        mode = self.clip.warp_mode.value if self.clip.warp_mode else "repitch"
        if mode in _RB_MODES and HAVE_RUBBERBAND:
            ratio, pitch = self._compute_rb_params()
            self._stretcher = RubberBandStretcher(
                sample_rate=self.sr, mode=mode,
                time_ratio=ratio, pitch_scale=pitch,
            )
        else:
            self._stretcher = None

    def _compute_rb_params(self) -> tuple[float, float]:
        """Return (time_ratio, pitch_scale) for current clip + session BPM.

        time_ratio = source_duration / output_duration. So if session BPM
        is double the clip's original BPM, time_ratio = 0.5 (compress).
        Pitch is independent.
        """
        # Session BPM is set at trigger time via render(); fall back to
        # clip.original_bpm here. The render path updates each block.
        original = max(20.0, float(self.clip.original_bpm or 120.0))
        # Default: assume same BPM at first; render() updates each block
        time_ratio = 1.0
        # Pitch scale for transpose + detune
        semitones = (self.clip.transpose_semitones
                     + self.clip.detune_cents / 100.0)
        pitch_scale = 2.0 ** (semitones / 12.0)
        return time_ratio, pitch_scale

    def trigger(self) -> None:
        self.position = float(self.clip.start_sample or 0)
        self._rb_input_pos = self.position
        if self._stretcher is not None:
            self._stretcher.reset()
        self.active = True

    def stop(self) -> None:
        self.active = False

    def render(self, frames: int, out: np.ndarray, session_bpm: float) -> None:
        if not self.active or self.clip.audio is None:
            return
        if self._stretcher is not None and self._stretcher.available:
            self._render_rubberband(frames, out, session_bpm)
        else:
            self._render_repitch(frames, out, session_bpm)

    # ── Re-pitch (linear interp) ───────────────────────────────────
    def _render_repitch(self, frames: int, out: np.ndarray,
                         session_bpm: float) -> None:
        buf = self.clip.audio
        original_bpm = max(20.0, float(self.clip.original_bpm or session_bpm))
        # Pitch + tempo linked
        ratio = (session_bpm / original_bpm) * (self.clip.sample_rate / self.sr)
        # Apply transpose / detune as additional pitch multiplier
        semitones = (self.clip.transpose_semitones
                     + self.clip.detune_cents / 100.0)
        ratio *= 2.0 ** (semitones / 12.0)

        result, self.position = resample_block(buf, self.position, frames, ratio)
        gain = float(self.clip.gain) if self.clip.gain else 1.0
        if result.shape[1] == 1:
            out[:frames, 0] += result[:, 0] * gain
            out[:frames, 1] += result[:, 0] * gain
        else:
            out[:frames, 0] += result[:, 0] * gain
            out[:frames, 1] += result[:, 1] * gain

        loop_end = self.clip.loop_end_sample or len(buf)
        loop_start = self.clip.loop_start_sample or 0
        if self.position >= loop_end:
            if self.clip.looping:
                self.position = float(loop_start)
            else:
                self.active = False

    # ── Rubber Band (real-time stretch + independent pitch) ────────
    def _render_rubberband(self, frames: int, out: np.ndarray,
                            session_bpm: float) -> None:
        buf = self.clip.audio
        original_bpm = max(20.0, float(self.clip.original_bpm or session_bpm))
        # time_ratio: how much we stretch source. session 90, original
        # 120 → ratio 120/90 = 1.33 (output longer than input). RB
        # convention: ratio > 1 = stretch out. We want session length
        # / original length, so:
        time_ratio = original_bpm / max(1e-3, session_bpm)
        # Pitch independent: transpose + detune in semitones
        semitones = (self.clip.transpose_semitones
                     + self.clip.detune_cents / 100.0)
        pitch_scale = 2.0 ** (semitones / 12.0)
        self._stretcher.set_time_ratio(time_ratio)
        self._stretcher.set_pitch_scale(pitch_scale)

        # Feed RB enough input until it can produce `frames` output.
        # We pull as much as is available; if not enough, push more.
        produced: list[np.ndarray] = []
        produced_total = 0
        gain = float(self.clip.gain) if self.clip.gain else 1.0

        loop_end = self.clip.loop_end_sample or len(buf)
        loop_start = self.clip.loop_start_sample or 0

        # Make stereo input if mono
        if buf.ndim == 1:
            stereo_buf = np.column_stack([buf, buf])
        elif buf.shape[1] == 1:
            stereo_buf = np.column_stack([buf[:, 0], buf[:, 0]])
        else:
            stereo_buf = buf

        max_iter = 8  # safety
        iters = 0
        while produced_total < frames and iters < max_iter:
            iters += 1
            need_in = self._stretcher.samples_required()
            if need_in > 0:
                end_pos = int(self._rb_input_pos) + need_in
                if end_pos > loop_end:
                    # Wrap around loop
                    chunk = stereo_buf[int(self._rb_input_pos):loop_end]
                    if self.clip.looping:
                        self._rb_input_pos = float(loop_start)
                        remainder = need_in - chunk.shape[0]
                        if remainder > 0 and loop_end - loop_start > 0:
                            wrap_chunk = stereo_buf[
                                loop_start:loop_start + remainder]
                            chunk = np.concatenate([chunk, wrap_chunk], axis=0)
                            self._rb_input_pos = float(loop_start
                                                        + wrap_chunk.shape[0])
                    else:
                        # Final chunk, signal end
                        if chunk.shape[0] > 0:
                            self._stretcher.process(chunk.astype(np.float32),
                                                     final=True)
                        self.active = False
                        break
                else:
                    chunk = stereo_buf[int(self._rb_input_pos):end_pos]
                    self._rb_input_pos = float(end_pos)
                self._stretcher.process(chunk.astype(np.float32))

            avail = self._stretcher.available_output()
            if avail > 0:
                want = min(avail, frames - produced_total)
                got = self._stretcher.retrieve(want)
                if got.shape[0] > 0:
                    produced.append(got)
                    produced_total += got.shape[0]
            elif need_in == 0:
                # RB has nothing pending and doesn't want input — give up
                break

        if produced_total > 0:
            block = np.concatenate(produced, axis=0)[:frames]
            out[:block.shape[0], 0] += block[:, 0] * gain
            out[:block.shape[0], 1] += block[:, 1] * gain
