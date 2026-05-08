"""Clip data model — MidiClip and AudioClip with launch/loop fields.

A Clip is a cell in the Session grid (one row × one track). Common
fields cover length, loop region, launch behavior. Subclasses hold
either MIDI notes or an audio buffer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

from .note import Note


class LaunchQuantize(Enum):
    NONE = "none"
    GLOBAL = "global"
    EIGHT_BARS = "8bar"
    FOUR_BARS = "4bar"
    TWO_BARS = "2bar"
    ONE_BAR = "1bar"
    HALF = "1/2"
    HALF_TRIPLET = "1/2t"
    QUARTER = "1/4"
    QUARTER_TRIPLET = "1/4t"
    EIGHTH = "1/8"
    EIGHTH_TRIPLET = "1/8t"
    SIXTEENTH = "1/16"
    SIXTEENTH_TRIPLET = "1/16t"
    THIRTYSECOND = "1/32"

    def to_beats(self, time_signature_num: int = 4) -> float:
        """Quantize value in beats. None/global return 0."""
        mapping = {
            LaunchQuantize.NONE: 0.0,
            LaunchQuantize.GLOBAL: 0.0,
            LaunchQuantize.EIGHT_BARS: 8.0 * time_signature_num,
            LaunchQuantize.FOUR_BARS: 4.0 * time_signature_num,
            LaunchQuantize.TWO_BARS: 2.0 * time_signature_num,
            LaunchQuantize.ONE_BAR: 1.0 * time_signature_num,
            LaunchQuantize.HALF: 2.0,
            LaunchQuantize.HALF_TRIPLET: 4.0 / 3.0,
            LaunchQuantize.QUARTER: 1.0,
            LaunchQuantize.QUARTER_TRIPLET: 2.0 / 3.0,
            LaunchQuantize.EIGHTH: 0.5,
            LaunchQuantize.EIGHTH_TRIPLET: 1.0 / 3.0,
            LaunchQuantize.SIXTEENTH: 0.25,
            LaunchQuantize.SIXTEENTH_TRIPLET: 1.0 / 6.0,
            LaunchQuantize.THIRTYSECOND: 0.125,
        }
        return mapping[self]


class LaunchMode(Enum):
    TRIGGER = "trigger"
    GATE = "gate"
    TOGGLE = "toggle"
    REPEAT = "repeat"


class WarpMode(Enum):
    REPITCH = "repitch"
    BEATS = "beats"
    TONES = "tones"
    TEXTURE = "texture"
    COMPLEX = "complex"
    COMPLEX_PRO = "complex_pro"


class ClipState(Enum):
    STOPPED = "stopped"
    QUEUED = "queued"
    PLAYING = "playing"
    RECORDING = "recording"


@dataclass
class Clip:
    """Common fields for any clip type. Subclassed by MidiClip / AudioClip."""
    name: str = ""
    color: int = 0  # palette index; 0 = inherit from track
    length_beats: float = 4.0
    loop_start_beats: float = 0.0
    loop_end_beats: float = 4.0
    looping: bool = True
    launch_quantize: LaunchQuantize = LaunchQuantize.GLOBAL
    launch_mode: LaunchMode = LaunchMode.TRIGGER
    legato: bool = False
    velocity_amount: float = 0.0  # 0 = no effect, 1.0 = full
    ram_mode: bool = False
    hi_quality: bool = True

    # Runtime state — not persisted
    _state: ClipState = field(default=ClipState.STOPPED, init=False, repr=False)
    _queued_at_beat: float = field(default=0.0, init=False, repr=False)
    _start_beat: float = field(default=0.0, init=False, repr=False)

    def to_dict_common(self) -> dict:
        return {
            "name": self.name,
            "color": self.color,
            "length_beats": self.length_beats,
            "loop_start_beats": self.loop_start_beats,
            "loop_end_beats": self.loop_end_beats,
            "looping": self.looping,
            "launch_quantize": self.launch_quantize.value,
            "launch_mode": self.launch_mode.value,
            "legato": self.legato,
            "velocity_amount": self.velocity_amount,
            "ram_mode": self.ram_mode,
            "hi_quality": self.hi_quality,
        }

    @classmethod
    def common_from_dict(cls, d: dict) -> dict:
        return {
            "name": d.get("name", ""),
            "color": int(d.get("color", 0)),
            "length_beats": float(d.get("length_beats", 4.0)),
            "loop_start_beats": float(d.get("loop_start_beats", 0.0)),
            "loop_end_beats": float(d.get("loop_end_beats", 4.0)),
            "looping": bool(d.get("looping", True)),
            "launch_quantize": LaunchQuantize(d.get("launch_quantize", "global")),
            "launch_mode": LaunchMode(d.get("launch_mode", "trigger")),
            "legato": bool(d.get("legato", False)),
            "velocity_amount": float(d.get("velocity_amount", 0.0)),
            "ram_mode": bool(d.get("ram_mode", False)),
            "hi_quality": bool(d.get("hi_quality", True)),
        }


@dataclass
class MidiClip(Clip):
    notes: list[Note] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.to_dict_common()
        d["type"] = "midi"
        d["notes"] = [n.to_dict() for n in self.notes]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MidiClip":
        common = cls.common_from_dict(d)
        notes = [Note.from_dict(n) for n in d.get("notes", [])]
        return cls(notes=notes, **common)


@dataclass
class AudioClip(Clip):
    sample_rate: int = 44100
    original_bpm: float = 120.0
    audio: Optional[np.ndarray] = None  # float32, shape (frames, channels)
    audio_path: str = ""               # source path for persistence
    warp_markers: list[tuple[int, float]] = field(default_factory=list)
    warp_mode: WarpMode = WarpMode.BEATS
    start_sample: int = 0
    end_sample: int = 0
    loop_start_sample: int = 0
    loop_end_sample: int = 0
    gain: float = 1.0
    transpose_semitones: int = 0
    detune_cents: int = 0
    tempo_leader: bool = False

    def to_dict(self) -> dict:
        d = self.to_dict_common()
        d["type"] = "audio"
        d["sample_rate"] = self.sample_rate
        d["original_bpm"] = self.original_bpm
        d["audio_path"] = self.audio_path
        d["warp_markers"] = [list(m) for m in self.warp_markers]
        d["warp_mode"] = self.warp_mode.value
        d["start_sample"] = self.start_sample
        d["end_sample"] = self.end_sample
        d["loop_start_sample"] = self.loop_start_sample
        d["loop_end_sample"] = self.loop_end_sample
        d["gain"] = self.gain
        d["transpose_semitones"] = self.transpose_semitones
        d["detune_cents"] = self.detune_cents
        d["tempo_leader"] = self.tempo_leader
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AudioClip":
        common = cls.common_from_dict(d)
        return cls(
            sample_rate=int(d.get("sample_rate", 44100)),
            original_bpm=float(d.get("original_bpm", 120.0)),
            audio=None,  # loaded on demand
            audio_path=d.get("audio_path", ""),
            warp_markers=[tuple(m) for m in d.get("warp_markers", [])],
            warp_mode=WarpMode(d.get("warp_mode", "beats")),
            start_sample=int(d.get("start_sample", 0)),
            end_sample=int(d.get("end_sample", 0)),
            loop_start_sample=int(d.get("loop_start_sample", 0)),
            loop_end_sample=int(d.get("loop_end_sample", 0)),
            gain=float(d.get("gain", 1.0)),
            transpose_semitones=int(d.get("transpose_semitones", 0)),
            detune_cents=int(d.get("detune_cents", 0)),
            tempo_leader=bool(d.get("tempo_leader", False)),
            **common,
        )


def clip_from_dict(d: dict) -> Optional[Clip]:
    """Polymorphic from_dict — picks MidiClip vs AudioClip from 'type' key."""
    if not d:
        return None
    t = d.get("type", "midi")
    if t == "midi":
        return MidiClip.from_dict(d)
    if t == "audio":
        return AudioClip.from_dict(d)
    return None
