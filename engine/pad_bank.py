"""Pad and bank data model — 4 banks x 16 pads = 64 slots."""

from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional
import numpy as np


class PlayMode(Enum):
    ONE_SHOT = "one_shot"
    LOOP = "loop"


@dataclass
class Pad:
    """Single pad slot with sample assignment and parameters."""
    sample_path: Optional[str] = None
    volume: float = 0.8
    pan: float = 0.0           # -1.0 (left) to 1.0 (right)
    tune: int = 0              # semitones, -24 to +24
    start: int = 0             # sample frame
    end: int = 0               # sample frame (0 = full length)
    attack: float = 0.0        # ms, 0–500
    decay: float = 0.0         # ms, 0–5000 (0 = full length)
    mode: PlayMode = PlayMode.ONE_SHOT
    choke_group: int = 0       # 0=none, 1–8
    mute_group: int = 0        # 0=none, 1–8

    # Runtime state (not serialized)
    audio_data: Optional[np.ndarray] = field(default=None, repr=False)
    waveform_preview: Optional[np.ndarray] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Serialize pad to dict (excluding runtime data)."""
        return {
            "sample_path": self.sample_path,
            "volume": self.volume,
            "pan": self.pan,
            "tune": self.tune,
            "start": self.start,
            "end": self.end,
            "attack": self.attack,
            "decay": self.decay,
            "mode": self.mode.value,
            "choke_group": self.choke_group,
            "mute_group": self.mute_group,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Pad":
        """Deserialize pad from dict."""
        d = dict(data)
        if "mode" in d:
            d["mode"] = PlayMode(d["mode"])
        # Strip unknown keys
        known = {f.name for f in cls.__dataclass_fields__.values() if f.name not in ("audio_data", "waveform_preview")}
        d = {k: v for k, v in d.items() if k in known}
        return cls(**d)

    @property
    def has_sample(self) -> bool:
        return self.sample_path is not None and self.audio_data is not None


BANK_NAMES = ["A", "B", "C", "D"]
PADS_PER_BANK = 16
TOTAL_PADS = len(BANK_NAMES) * PADS_PER_BANK


class PadBank:
    """Manages 4 banks of 16 pads each (64 total)."""

    def __init__(self):
        self.banks: dict[str, list[Pad]] = {
            name: [Pad() for _ in range(PADS_PER_BANK)]
            for name in BANK_NAMES
        }
        self.current_bank: str = "A"
        self.selected_pad: int = 0  # 0–15 within current bank
        self.kit_name: str = "New Kit"

    @property
    def current_pads(self) -> list[Pad]:
        """Get the 16 pads for the current bank."""
        return self.banks[self.current_bank]

    @property
    def selected(self) -> Pad:
        """Get the currently selected pad."""
        return self.banks[self.current_bank][self.selected_pad]

    def get_pad(self, bank: str, index: int) -> Pad:
        """Get a specific pad by bank letter and index."""
        return self.banks[bank][index]

    def select_bank(self, bank: str):
        if bank in BANK_NAMES:
            self.current_bank = bank

    def select_pad(self, index: int):
        if 0 <= index < PADS_PER_BANK:
            self.selected_pad = index

    def all_pads(self):
        """Iterate over all pads across all banks."""
        for bank_name in BANK_NAMES:
            for i, pad in enumerate(self.banks[bank_name]):
                yield bank_name, i, pad

    def to_dict(self) -> dict:
        """Serialize entire pad bank to dict."""
        return {
            "kit_name": self.kit_name,
            "banks": {
                name: [pad.to_dict() for pad in pads]
                for name, pads in self.banks.items()
            },
        }

    def from_dict(self, data: dict):
        """Load pad bank state from dict."""
        self.kit_name = data.get("kit_name", "New Kit")
        for bank_name in BANK_NAMES:
            if bank_name in data.get("banks", {}):
                bank_data = data["banks"][bank_name]
                for i, pad_data in enumerate(bank_data):
                    if i < PADS_PER_BANK:
                        self.banks[bank_name][i] = Pad.from_dict(pad_data)

    def clear(self):
        """Reset all pads to defaults."""
        for bank_name in BANK_NAMES:
            self.banks[bank_name] = [Pad() for _ in range(PADS_PER_BANK)]
        self.kit_name = "New Kit"
        self.selected_pad = 0
        self.current_bank = "A"

    def memory_usage_bytes(self) -> int:
        """Estimate total memory used by loaded samples."""
        total = 0
        for _, _, pad in self.all_pads():
            if pad.audio_data is not None:
                total += pad.audio_data.nbytes
        return total
