"""Granular preset save/recall for the P-6.

Saves all 14 granular CC values as named presets.
When recalled, sends all CCs to the P-6 (granular pad must be selected).
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from engine.p6_midi import P6_CC_MAP, CH_GRANULAR, CH_AUTO

log = logging.getLogger(__name__)

# All granular CC numbers
GRANULAR_CCS = [cc for cc, *_ in P6_CC_MAP["granular"]]


@dataclass
class GranularPreset:
    name: str = "Init"
    values: dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "values": {str(k): v for k, v in self.values.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GranularPreset":
        return cls(
            name=data.get("name", "Untitled"),
            values={int(k): v for k, v in data.get("values", {}).items()},
        )


class PresetManager:
    """Manages granular presets for the P-6."""

    def __init__(self, presets_dir: str):
        self._dir = presets_dir
        os.makedirs(presets_dir, exist_ok=True)

    def list_presets(self) -> list[str]:
        """List saved preset names."""
        presets = []
        for f in sorted(os.listdir(self._dir)):
            if f.endswith(".json"):
                presets.append(f[:-5])  # strip .json
        return presets

    def save_preset(self, preset: GranularPreset) -> str:
        """Save a preset to disk. Returns filepath."""
        safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in preset.name)
        safe = safe.strip() or "preset"
        path = os.path.join(self._dir, f"{safe}.json")
        with open(path, "w") as f:
            json.dump(preset.to_dict(), f, indent=2)
        log.info("Preset saved: %s", preset.name)
        return path

    def load_preset(self, name: str) -> Optional[GranularPreset]:
        """Load a preset by name."""
        path = os.path.join(self._dir, f"{name}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                return GranularPreset.from_dict(json.load(f))
        except Exception as e:
            log.error("Failed to load preset %s: %s", name, e)
            return None

    def delete_preset(self, name: str) -> bool:
        path = os.path.join(self._dir, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def capture_from_state(self, name: str, cc_values: dict[int, int]) -> GranularPreset:
        """Create a preset from current P-6 CC state."""
        values = {cc: cc_values.get(cc, 64) for cc in GRANULAR_CCS}
        return GranularPreset(name=name, values=values)

    def apply_preset(self, preset: GranularPreset, p6_midi) -> None:
        """Send all preset CC values to the P-6."""
        for cc, val in preset.values.items():
            p6_midi.send_cc(cc, val, channel=CH_GRANULAR)
            p6_midi.send_cc(cc, val, channel=CH_AUTO)
        log.info("Preset applied: %s (%d CCs)", preset.name, len(preset.values))


# Resample calculator constants
SAMPLE_RATES = {
    44100: 5.9,
    22050: 11.8,
    14700: 17.8,
    11025: 23.7,
}

def resample_calc(bpm: float) -> list[dict]:
    """Calculate bar durations and which sample rates fit.

    Returns list of dicts: [{bars, seconds, fits: {rate: bool}}]
    """
    if bpm <= 0:
        bpm = 120.0
    sec_per_bar = (60.0 / bpm) * 4  # 4 beats per bar

    results = []
    for bars in [1, 2, 4, 8]:
        duration = sec_per_bar * bars
        fits = {}
        for rate, max_sec in SAMPLE_RATES.items():
            fits[rate] = duration <= max_sec
        results.append({
            "bars": bars,
            "seconds": round(duration, 1),
            "fits": fits,
        })
    return results
