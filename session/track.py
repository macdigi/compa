"""Track data model — audio + MIDI tracks with one instrument each.

Audio tracks have no instrument; their clips ARE audio.
MIDI tracks own a single Instrument that consumes MIDI notes.

Instruments are referenced by name + opaque parameters here; the
clip engine resolves names to live Instrument objects at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .clip import Clip, clip_from_dict


class TrackType(Enum):
    AUDIO = "audio"
    MIDI = "midi"


@dataclass
class InstrumentRef:
    """Reference to a clip-engine instrument with persisted params.

    The runtime instantiates the actual instrument from kind + params.
    Kinds shipped in v1: 'drum_rack', 'synth_voice', 'rhythm_generator'.
    """
    kind: str = "synth_voice"
    name: str = ""
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "params": self.params}

    @classmethod
    def from_dict(cls, d: dict) -> "InstrumentRef":
        return cls(
            kind=d.get("kind", "synth_voice"),
            name=d.get("name", ""),
            params=d.get("params", {}),
        )


@dataclass
class Track:
    """A column in the session grid."""
    id: int = 0
    name: str = ""
    color: int = 0  # palette index
    type: TrackType = TrackType.MIDI
    volume: float = 0.85
    pan: float = 0.0   # -1.0 left, +1.0 right
    mute: bool = False
    solo: bool = False
    arm: bool = False
    instrument: Optional[InstrumentRef] = None
    clips: list[Optional[Clip]] = field(default_factory=lambda: [None] * 8)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "type": self.type.value,
            "volume": self.volume,
            "pan": self.pan,
            "mute": self.mute,
            "solo": self.solo,
            "arm": self.arm,
            "instrument": self.instrument.to_dict() if self.instrument else None,
            "clips": [c.to_dict() if c is not None else None for c in self.clips],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        return cls(
            id=int(d.get("id", 0)),
            name=d.get("name", ""),
            color=int(d.get("color", 0)),
            type=TrackType(d.get("type", "midi")),
            volume=float(d.get("volume", 0.85)),
            pan=float(d.get("pan", 0.0)),
            mute=bool(d.get("mute", False)),
            solo=bool(d.get("solo", False)),
            arm=bool(d.get("arm", False)),
            instrument=(InstrumentRef.from_dict(d["instrument"])
                        if d.get("instrument") else None),
            clips=[clip_from_dict(c) for c in d.get("clips", [None] * 8)],
        )
