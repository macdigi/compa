"""Procedural 808/909-style drum synth instrument."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engine.studio_drum_synth import (
    DRUM_SYNTH_PAD_COUNT,
    normalized_voice_spec,
)


@dataclass
class _Voice:
    pad_idx: int = -1
    pos: int = 0
    velocity: float = 0.0
    active: bool = False
    phase: float = 0.0


class DrumSynthInstrument:
    BASE_NOTE = 36
    NUM_PADS = DRUM_SYNTH_PAD_COUNT
    MAX_VOICES = 24

    def __init__(self, sample_rate: int = 44100,
                 specs: list[dict] | None = None) -> None:
        self.sr = sample_rate
        raw = specs if isinstance(specs, list) else []
        self.specs = [
            normalized_voice_spec(raw[idx] if idx < len(raw) else None, idx)
            for idx in range(self.NUM_PADS)
        ]
        self._voices: list[_Voice] = [_Voice() for _ in range(self.MAX_VOICES)]
        self._rng = np.random.default_rng(808)

    def update_specs(self, specs: list[dict]) -> None:
        self.specs = [
            normalized_voice_spec(specs[idx] if idx < len(specs) else None, idx)
            for idx in range(self.NUM_PADS)
        ]

    def note_on(self, pitch: int, velocity: int) -> None:
        if not (self.BASE_NOTE <= pitch < self.BASE_NOTE + self.NUM_PADS):
            return
        pad_idx = pitch - self.BASE_NOTE
        spec = self.specs[pad_idx]
        if spec["choke_group"]:
            for voice in self._voices:
                if voice.active and voice.pad_idx >= 0:
                    other = self.specs[voice.pad_idx]
                    if other["choke_group"] == spec["choke_group"]:
                        voice.active = False
        voice = next((v for v in self._voices if not v.active), self._voices[0])
        voice.pad_idx = pad_idx
        voice.pos = 0
        voice.velocity = max(1, min(127, int(velocity))) / 127.0
        voice.active = True
        voice.phase = 0.0

    def note_off(self, pitch: int) -> None:
        return

    def all_notes_off(self) -> None:
        for voice in self._voices:
            voice.active = False

    def render(self, frames: int, out: np.ndarray) -> None:
        if frames <= 0:
            return
        for voice in self._voices:
            if not voice.active or voice.pad_idx < 0:
                continue
            spec = self.specs[voice.pad_idx]
            total = max(1, int(self._voice_duration(spec) * self.sr))
            remaining = total - voice.pos
            n = min(frames, remaining)
            if n <= 0:
                voice.active = False
                continue
            signal = self._render_voice(voice, spec, n).astype(np.float32)
            signal *= float(spec["gain"]) * voice.velocity
            pan = max(-1.0, min(1.0, float(spec["pan"])))
            l_gain = 1.0 if pan <= 0 else 1.0 - pan
            r_gain = 1.0 if pan >= 0 else 1.0 + pan
            out[:n, 0] += signal * l_gain
            out[:n, 1] += signal * r_gain
            voice.pos += n
            if voice.pos >= total:
                voice.active = False

    def _voice_duration(self, spec: dict) -> float:
        kind = spec["voice_type"]
        decay = float(spec["decay"])
        if kind == "hat_closed":
            return min(0.28, decay * 1.8)
        if kind == "rim" or kind == "clave":
            return min(0.22, decay * 1.5)
        if kind == "kick":
            return max(0.22, decay * 1.25)
        return max(0.08, decay * 1.2)

    def _render_voice(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        kind = spec["voice_type"]
        if kind == "kick":
            return self._kick(voice, spec, frames)
        if kind == "snare":
            return self._snare(voice, spec, frames)
        if kind in ("hat_closed", "hat_open"):
            return self._hat(voice, spec, frames, open_hat=(kind == "hat_open"))
        if kind == "clap":
            return self._clap(voice, spec, frames)
        if kind == "tom":
            return self._tom(voice, spec, frames)
        if kind == "rim":
            return self._rim(voice, spec, frames)
        if kind == "cowbell":
            return self._cowbell(voice, spec, frames)
        if kind == "clave":
            return self._clave(voice, spec, frames)
        if kind == "maraca":
            return self._maraca(voice, spec, frames)
        if kind == "conga":
            return self._conga(voice, spec, frames)
        return self._perc(voice, spec, frames)

    def _time(self, voice: _Voice, frames: int) -> np.ndarray:
        return (np.arange(frames, dtype=np.float32) + voice.pos) / self.sr

    def _decay_env(self, t: np.ndarray, decay: float,
                   curve: float = 1.0) -> np.ndarray:
        env = np.exp(-t / max(0.01, float(decay))).astype(np.float32)
        if curve != 1.0:
            env = env ** curve
        return env

    def _tone_freq(self, base: float, spec: dict, spread: float = 1.0) -> float:
        tone = float(spec["tone"])
        tune = 2.0 ** (float(spec["tune"]) / 12.0)
        return base * (0.65 + tone * spread) * tune

    def _osc(self, voice: _Voice, freq: np.ndarray | float,
             frames: int) -> np.ndarray:
        if np.isscalar(freq):
            phase = (voice.phase
                     + np.arange(frames, dtype=np.float32)
                     * (float(freq) / self.sr))
            voice.phase = float((voice.phase + frames * float(freq) / self.sr) % 1.0)
            return np.sin(2.0 * np.pi * phase).astype(np.float32)
        phase = voice.phase + np.cumsum(np.asarray(freq, dtype=np.float32) / self.sr)
        voice.phase = float(phase[-1] % 1.0)
        return np.sin(2.0 * np.pi * phase).astype(np.float32)

    def _noise(self, frames: int) -> np.ndarray:
        return self._rng.uniform(-1.0, 1.0, frames).astype(np.float32)

    @staticmethod
    def _highpass(noise: np.ndarray, passes: int = 2) -> np.ndarray:
        out = noise.astype(np.float32)
        for _ in range(passes):
            out = np.diff(np.concatenate(([0.0], out))).astype(np.float32)
        peak = max(1e-4, float(np.max(np.abs(out))))
        return out / peak

    def _kick(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        decay = float(spec["decay"])
        tone = float(spec["tone"])
        snap = float(spec["snap"])
        base = self._tone_freq(34.0, spec, spread=1.1)
        start = base * (2.2 + snap * 4.5)
        freq = base + (start - base) * np.exp(-t / (0.018 + 0.055 * (1.0 - tone)))
        body = self._osc(voice, freq, frames) * self._decay_env(t, decay, 1.15)
        click_n = int(0.006 * self.sr)
        click = np.zeros(frames, dtype=np.float32)
        if voice.pos < click_n:
            count = min(frames, click_n - voice.pos)
            click[:count] = self._highpass(self._noise(count), 3)[:count]
            click[:count] *= np.linspace(1.0, 0.0, count) * snap * 0.22
        return (body + click).astype(np.float32)

    def _snare(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        decay = float(spec["decay"])
        snap = float(spec["snap"])
        body_freq = self._tone_freq(145.0, spec, spread=1.15)
        body = self._osc(voice, body_freq, frames) * np.exp(-t / max(0.03, decay * 0.38))
        noise = self._highpass(self._noise(frames), 1)
        noise *= np.exp(-t / max(0.03, decay * (0.55 + snap * 0.5)))
        return (body * (0.35 + 0.25 * (1.0 - snap)) + noise * (0.35 + snap * 0.45))

    def _hat(self, voice: _Voice, spec: dict, frames: int,
             *, open_hat: bool) -> np.ndarray:
        t = self._time(voice, frames)
        decay = float(spec["decay"]) * (1.4 if open_hat else 0.55)
        noise = self._highpass(self._noise(frames), 4)
        tone = float(spec["tone"])
        ring_freq = 5200.0 + tone * 5200.0
        ring = self._osc(voice, ring_freq, frames) * 0.22
        env = np.exp(-t / max(0.012, decay)).astype(np.float32)
        return (noise * (0.65 + tone * 0.35) + ring) * env

    def _clap(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        decay = float(spec["decay"])
        noise = self._highpass(self._noise(frames), 2)
        env = np.exp(-t / max(0.04, decay * 0.55)) * 0.34
        for offset in (0.0, 0.011, 0.023):
            burst = np.exp(-np.maximum(0.0, t - offset) / 0.012)
            burst[t < offset] = 0.0
            env += burst * (0.42 + float(spec["snap"]) * 0.25)
        return noise * env.astype(np.float32)

    def _tom(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        decay = float(spec["decay"])
        base = self._tone_freq(78.0, spec, spread=2.3)
        freq = base * (1.0 + 0.35 * np.exp(-t / 0.035))
        return self._osc(voice, freq, frames) * self._decay_env(t, decay, 1.1)

    def _rim(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        freq = self._tone_freq(1450.0, spec, spread=1.0)
        tone = self._osc(voice, freq, frames)
        click = self._highpass(self._noise(frames), 2) * 0.18
        return (tone + click) * np.exp(-t / max(0.015, float(spec["decay"]) * 0.45))

    def _cowbell(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        base = self._tone_freq(420.0, spec, spread=1.1)
        phase1 = (voice.phase + np.arange(frames, dtype=np.float32) * base / self.sr) % 1.0
        phase2 = (voice.phase + np.arange(frames, dtype=np.float32) * base * 1.48 / self.sr) % 1.0
        voice.phase = float((voice.phase + frames * base / self.sr) % 1.0)
        square = (np.where(phase1 < 0.5, 1.0, -1.0)
                  + np.where(phase2 < 0.5, 1.0, -1.0)) * 0.5
        return square.astype(np.float32) * np.exp(-t / max(0.06, float(spec["decay"])))

    def _clave(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        freq = self._tone_freq(1700.0, spec, spread=0.8)
        return self._osc(voice, freq, frames) * np.exp(-t / max(0.015, float(spec["decay"]) * 0.35))

    def _maraca(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        noise = self._highpass(self._noise(frames), 3)
        return noise * np.exp(-t / max(0.018, float(spec["decay"]) * 0.6))

    def _conga(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        decay = float(spec["decay"])
        freq = self._tone_freq(135.0, spec, spread=1.3)
        body = self._osc(voice, freq, frames)
        slap = self._highpass(self._noise(frames), 1) * np.exp(-t / 0.018) * float(spec["snap"]) * 0.16
        return body * np.exp(-t / max(0.05, decay * 0.75)) + slap

    def _perc(self, voice: _Voice, spec: dict, frames: int) -> np.ndarray:
        t = self._time(voice, frames)
        freq = self._tone_freq(240.0, spec, spread=2.0)
        body = self._osc(voice, freq, frames) * 0.55
        noise = self._highpass(self._noise(frames), 2) * float(spec["snap"]) * 0.25
        return (body + noise) * np.exp(-t / max(0.035, float(spec["decay"]) * 0.55))
