"""Mono subtractive synth voice for melodic MIDI tracks.

One voice = one oscillator + 1-pole LP filter + ADSR amp envelope.
Allocation-free render path: a fixed scratch buffer is provided per
callback, the voice writes into it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def midi_to_hz(pitch: int, detune_cents: int = 0) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0 + detune_cents / 1200.0))


@dataclass
class SynthParams:
    waveform: str = "saw"   # 'saw', 'square', 'sine'
    cutoff_hz: float = 4000.0
    cutoff_env: float = 0.5  # 0..1, multiplier on cutoff via env
    resonance: float = 0.0
    attack: float = 0.005
    decay: float = 0.2
    sustain: float = 0.7
    release: float = 0.3
    glide: float = 0.0       # seconds
    detune_cents: int = 0
    gain: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> "SynthParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class SynthVoice:
    """One monophonic synth voice. Use multiple instances for poly."""

    def __init__(self, sample_rate: int = 44100,
                 params: SynthParams | None = None) -> None:
        self.sr = sample_rate
        self.params = params or SynthParams()

        # State
        self._phase = 0.0
        self._target_freq = 0.0
        self._curr_freq = 0.0
        self._note_pitch = -1
        self._note_velocity = 0
        self._gate = False
        self._env = 0.0
        self._env_stage = "idle"  # 'a','d','s','r','idle'
        # 1-pole LP state
        self._lpf_y = 0.0

    # ── Note events ────────────────────────────────────────────────
    def note_on(self, pitch: int, velocity: int) -> None:
        self._note_pitch = pitch
        self._note_velocity = max(1, min(127, velocity))
        target = midi_to_hz(pitch, self.params.detune_cents)
        if self.params.glide <= 0 or self._curr_freq == 0.0:
            self._curr_freq = target
        self._target_freq = target
        self._gate = True
        # Start ADSR from current env (allows legato)
        self._env_stage = "a"

    def note_off(self) -> None:
        if not self._gate:
            return
        self._gate = False
        self._env_stage = "r"

    def is_active(self) -> bool:
        return self._gate or self._env > 1e-4

    # ── Render ─────────────────────────────────────────────────────
    def render(self, frames: int, out: np.ndarray) -> None:
        """Add the voice's stereo output into `out` (frames, 2)."""
        if not self.is_active() or frames <= 0:
            return

        # Snap glide
        if self.params.glide > 0 and self._curr_freq != self._target_freq:
            step = (self._target_freq - self._curr_freq) / max(
                1, int(self.params.glide * self.sr))
            # Approximate glide across this block
            self._curr_freq += step * frames
        elif self._curr_freq != self._target_freq:
            self._curr_freq = self._target_freq

        # Generate raw waveform
        n = np.arange(frames, dtype=np.float32)
        phase_inc = float(self._curr_freq) / self.sr
        ph = self._phase + n * phase_inc
        ph = ph - np.floor(ph)
        if self.params.waveform == "saw":
            wave = 2.0 * ph - 1.0
        elif self.params.waveform == "square":
            wave = np.where(ph < 0.5, 1.0, -1.0).astype(np.float32)
        else:  # sine
            wave = np.sin(2.0 * np.pi * ph).astype(np.float32)
        self._phase = float((self._phase + frames * phase_inc) % 1.0)

        # ADSR
        env = np.empty(frames, dtype=np.float32)
        e = self._env
        a = max(1e-6, self.params.attack)
        d = max(1e-6, self.params.decay)
        s = float(self.params.sustain)
        r = max(1e-6, self.params.release)
        for i in range(frames):
            stage = self._env_stage
            if stage == "a":
                e += (1.0 / (a * self.sr))
                if e >= 1.0:
                    e = 1.0
                    self._env_stage = "d"
            elif stage == "d":
                e -= ((1.0 - s) / (d * self.sr))
                if e <= s:
                    e = s
                    self._env_stage = "s"
            elif stage == "r":
                e -= (s / (r * self.sr))
                if e <= 0.0:
                    e = 0.0
                    self._env_stage = "idle"
            env[i] = e
        self._env = e

        # 1-pole LP filter sweep with env
        cutoff_min = self.params.cutoff_hz * (1.0 - self.params.cutoff_env)
        cutoff_max = self.params.cutoff_hz
        cutoffs = cutoff_min + (cutoff_max - cutoff_min) * env
        # alpha = 1 - exp(-2*pi*cutoff/sr)
        alphas = 1.0 - np.exp(-2.0 * np.pi * cutoffs / self.sr)
        y = self._lpf_y
        filtered = np.empty(frames, dtype=np.float32)
        for i in range(frames):
            y += alphas[i] * (wave[i] - y)
            filtered[i] = y
        self._lpf_y = float(y)

        # Velocity + gain
        vel = self._note_velocity / 127.0
        gain = self.params.gain * vel
        signal = filtered * env * gain

        # Mix into stereo out (mono → both channels)
        out[:frames, 0] += signal
        out[:frames, 1] += signal


class SynthInstrument:
    """Polyphonic wrapper — N independent SynthVoice instances.

    Manages voice allocation and stealing. v1 caps at 8 voices per track.
    """

    def __init__(self, sample_rate: int = 44100,
                 params: SynthParams | None = None,
                 max_voices: int = 8) -> None:
        self.sr = sample_rate
        self.params = params or SynthParams()
        self.voices: list[SynthVoice] = [
            SynthVoice(sample_rate, self.params) for _ in range(max_voices)
        ]
        self._note_to_voice: dict[int, int] = {}

    def note_on(self, pitch: int, velocity: int) -> None:
        # Reuse if the same pitch is held
        if pitch in self._note_to_voice:
            v = self.voices[self._note_to_voice[pitch]]
            v.note_on(pitch, velocity)
            return
        # Find an idle voice
        for i, v in enumerate(self.voices):
            if not v.is_active():
                v.note_on(pitch, velocity)
                self._note_to_voice[pitch] = i
                return
        # Steal voice 0 (oldest in our naive impl)
        v = self.voices[0]
        # Remove the stolen pitch from map
        for p, idx in list(self._note_to_voice.items()):
            if idx == 0:
                self._note_to_voice.pop(p, None)
                break
        v.note_on(pitch, velocity)
        self._note_to_voice[pitch] = 0

    def note_off(self, pitch: int) -> None:
        idx = self._note_to_voice.pop(pitch, None)
        if idx is not None:
            self.voices[idx].note_off()

    def render(self, frames: int, out: np.ndarray) -> None:
        for v in self.voices:
            v.render(frames, out)

    def update_params(self, params: SynthParams) -> None:
        self.params = params
        for v in self.voices:
            v.params = params

    def all_notes_off(self) -> None:
        for v in self.voices:
            v.note_off()
        self._note_to_voice.clear()


# Preset factory
def preset_bass() -> SynthParams:
    return SynthParams(waveform="saw", cutoff_hz=900.0, cutoff_env=0.6,
                        attack=0.005, decay=0.18, sustain=0.55, release=0.15,
                        gain=0.7)


def preset_lead() -> SynthParams:
    return SynthParams(waveform="square", cutoff_hz=2400.0, cutoff_env=0.5,
                        attack=0.01, decay=0.2, sustain=0.7, release=0.25,
                        gain=0.6)


def preset_pad() -> SynthParams:
    return SynthParams(waveform="saw", cutoff_hz=1800.0, cutoff_env=0.3,
                        attack=0.4, decay=0.5, sustain=0.8, release=0.8,
                        gain=0.5)
