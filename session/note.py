"""MIDI note dataclass for clips."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Note:
    """One note within a MIDI clip.

    Times are in beats relative to the clip's start.
    """
    pitch: int                    # 0–127
    start_beat: float             # beats from clip start
    duration_beats: float         # beats
    velocity: int = 100           # 1–127
    release_velocity: int = 64    # 1–127
    chance: float = 1.0           # 0.0–1.0, probability of playing this firing
    velocity_range: int = 0       # ± random velocity offset (0–127)
    muted: bool = False
    color: int = 0                # palette index, 0 = use clip color

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Note":
        return cls(
            pitch=int(d.get("pitch", 60)),
            start_beat=float(d.get("start_beat", 0.0)),
            duration_beats=float(d.get("duration_beats", 0.25)),
            velocity=int(d.get("velocity", 100)),
            release_velocity=int(d.get("release_velocity", 64)),
            chance=float(d.get("chance", 1.0)),
            velocity_range=int(d.get("velocity_range", 0)),
            muted=bool(d.get("muted", False)),
            color=int(d.get("color", 0)),
        )
