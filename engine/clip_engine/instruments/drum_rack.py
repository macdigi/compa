"""Drum Rack instrument — 16 sample slots, MIDI-note-driven.

Notes 36–51 map to pads 1–16. Each pad holds a sample, gain, pan,
choke group, mute group. Allocation-free render.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class DrumPad:
    name: str = ""
    sample: Optional[np.ndarray] = None  # mono float32, length = N samples
    sample_rate: int = 44100
    sample_path: str = ""
    gain: float = 1.0
    pan: float = 0.0
    tune_semitones: int = 0
    choke_group: int = 0    # 0 = no choke; otherwise notes in same group cut each other
    mute_group: int = 0     # currently same effect as choke


@dataclass
class _Voice:
    pad_idx: int = -1
    pos: int = 0
    velocity: float = 0.0
    active: bool = False


class DrumRack:
    """16 pads + a fixed pool of playback voices.

    Note 36 = pad 0, …, note 51 = pad 15. Notes outside that range are ignored.
    """
    BASE_NOTE = 36
    NUM_PADS = 16
    MAX_VOICES = 16

    def __init__(self, sample_rate: int = 44100) -> None:
        self.sr = sample_rate
        self.pads: list[DrumPad] = [DrumPad() for _ in range(self.NUM_PADS)]
        self._voices: list[_Voice] = [_Voice() for _ in range(self.MAX_VOICES)]

    def set_pad(self, idx: int, pad: DrumPad) -> None:
        if 0 <= idx < self.NUM_PADS:
            self.pads[idx] = pad

    def note_on(self, pitch: int, velocity: int) -> None:
        if not (self.BASE_NOTE <= pitch < self.BASE_NOTE + self.NUM_PADS):
            return
        pad_idx = pitch - self.BASE_NOTE
        pad = self.pads[pad_idx]
        if pad.sample is None or len(pad.sample) == 0:
            return

        # Choke / mute group: stop other active voices in same group.
        if pad.choke_group != 0:
            for v in self._voices:
                if v.active and v.pad_idx >= 0:
                    other = self.pads[v.pad_idx]
                    if other.choke_group == pad.choke_group and v.pad_idx != pad_idx:
                        v.active = False

        # Find idle voice
        for v in self._voices:
            if not v.active:
                v.pad_idx = pad_idx
                v.pos = 0
                v.velocity = max(1, velocity) / 127.0
                v.active = True
                return
        # Steal oldest (index 0 in our naive setup)
        v = self._voices[0]
        v.pad_idx = pad_idx
        v.pos = 0
        v.velocity = max(1, velocity) / 127.0
        v.active = True

    def note_off(self, pitch: int) -> None:
        # Drum samples are one-shots — note_off ignored unless we add
        # release/cut samples later.
        return

    def all_notes_off(self) -> None:
        for v in self._voices:
            v.active = False

    def render(self, frames: int, out: np.ndarray) -> None:
        if frames <= 0:
            return
        for v in self._voices:
            if not v.active:
                continue
            pad = self.pads[v.pad_idx]
            sample = pad.sample
            if sample is None:
                v.active = False
                continue
            avail = len(sample) - v.pos
            n = min(frames, avail)
            if n <= 0:
                v.active = False
                continue
            chunk = sample[v.pos:v.pos + n].astype(np.float32) * (
                v.velocity * pad.gain)
            # Pan: -1 = full L, +1 = full R
            pan = max(-1.0, min(1.0, pad.pan))
            l_gain = 1.0 if pan <= 0 else 1.0 - pan
            r_gain = 1.0 if pan >= 0 else 1.0 + pan
            out[:n, 0] += chunk * l_gain
            out[:n, 1] += chunk * r_gain
            v.pos += n
            if v.pos >= len(sample):
                v.active = False
