"""MIDI LFO automation engine.

Generates low-frequency oscillator waveforms and sends them as CC
messages to any MIDI channel/CC target. Runs in a background thread
at configurable rate. Designed for automating SP-404 MK2 FX parameters
(filter sweeps, delay feedback modulation, etc.) but works with any
device.

Usage::

    lfo = MidiLFO(midi_out)
    lfo.add_target(channel=0, cc=16, shape="sine", rate_hz=0.5,
                   min_val=0, max_val=127)
    lfo.start()
    # ... later ...
    lfo.stop()
"""

import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# Waveform shapes
SHAPE_SINE = "sine"
SHAPE_TRIANGLE = "triangle"
SHAPE_SAW_UP = "saw_up"
SHAPE_SAW_DOWN = "saw_down"
SHAPE_SQUARE = "square"
SHAPE_RANDOM = "random"
SHAPE_SAMPLE_HOLD = "s&h"

ALL_SHAPES = [SHAPE_SINE, SHAPE_TRIANGLE, SHAPE_SAW_UP, SHAPE_SAW_DOWN,
              SHAPE_SQUARE, SHAPE_RANDOM, SHAPE_SAMPLE_HOLD]

# Update rate (how often we send CC messages)
DEFAULT_UPDATE_HZ = 30  # 30 updates per second — smooth but CPU-friendly


@dataclass
class LFOTarget:
    """One CC automation target."""
    channel: int = 0       # MIDI channel (0-indexed)
    cc: int = 16           # CC number
    shape: str = SHAPE_SINE
    rate_hz: float = 0.5   # LFO frequency in Hz (0.01 - 20)
    min_val: int = 0       # CC output range low
    max_val: int = 127     # CC output range high
    phase: float = 0.0     # Phase offset (0.0 - 1.0)
    enabled: bool = True

    # Runtime state (not serialized)
    _last_val: int = -1
    _sh_val: float = 0.0   # Sample & hold cached value


class MidiLFO:
    """Background LFO engine that modulates MIDI CCs.

    Multiple targets can run simultaneously at different rates/shapes,
    all driven by a single thread.
    """

    def __init__(self, midi_out=None):
        self._midi_out = midi_out
        self._targets: list[LFOTarget] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._start_time = 0.0
        self.update_hz = DEFAULT_UPDATE_HZ

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def targets(self) -> list[LFOTarget]:
        return list(self._targets)

    def set_midi_out(self, midi_out):
        self._midi_out = midi_out

    def add_target(self, channel: int, cc: int, shape: str = SHAPE_SINE,
                   rate_hz: float = 0.5, min_val: int = 0, max_val: int = 127,
                   phase: float = 0.0) -> LFOTarget:
        """Add an LFO target. Returns the target for further tweaking."""
        target = LFOTarget(
            channel=channel, cc=cc, shape=shape, rate_hz=rate_hz,
            min_val=min_val, max_val=max_val, phase=phase,
        )
        self._targets.append(target)
        return target

    def remove_target(self, index: int):
        """Remove a target by index."""
        if 0 <= index < len(self._targets):
            self._targets.pop(index)

    def clear_targets(self):
        """Remove all targets."""
        self._targets.clear()

    def start(self):
        """Start the LFO thread."""
        if self._running:
            return
        self._start_time = time.monotonic()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        log.info("LFO engine started (%d targets)", len(self._targets))

    def stop(self):
        """Stop the LFO thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        log.info("LFO engine stopped")

    def _run_loop(self):
        """Background loop — compute waveforms and send CCs."""
        interval = 1.0 / self.update_hz
        while self._running:
            t = time.monotonic() - self._start_time
            self._update_targets(t)
            time.sleep(interval)

    def _update_targets(self, t: float):
        """Compute and send CC values for all targets."""
        if not self._midi_out:
            return

        for target in self._targets:
            if not target.enabled:
                continue

            # Compute normalized waveform (0.0 - 1.0)
            phase = (t * target.rate_hz + target.phase) % 1.0
            raw = self._compute_waveform(target.shape, phase, target)

            # Scale to CC range
            span = target.max_val - target.min_val
            val = int(target.min_val + raw * span)
            val = max(0, min(127, val))

            # Only send if value changed (reduce MIDI traffic)
            if val != target._last_val:
                self._midi_out.send_cc(target.cc, val, channel=target.channel)
                target._last_val = val

    @staticmethod
    def _compute_waveform(shape: str, phase: float, target: LFOTarget) -> float:
        """Compute normalized waveform value (0.0 - 1.0) at given phase."""
        if shape == SHAPE_SINE:
            return (math.sin(phase * 2.0 * math.pi) + 1.0) * 0.5

        elif shape == SHAPE_TRIANGLE:
            if phase < 0.5:
                return phase * 2.0
            else:
                return 2.0 - phase * 2.0

        elif shape == SHAPE_SAW_UP:
            return phase

        elif shape == SHAPE_SAW_DOWN:
            return 1.0 - phase

        elif shape == SHAPE_SQUARE:
            return 1.0 if phase < 0.5 else 0.0

        elif shape == SHAPE_RANDOM:
            return random.random()

        elif shape == SHAPE_SAMPLE_HOLD:
            # Only sample a new value at the start of each cycle
            if phase < (1.0 / max(1, DEFAULT_UPDATE_HZ)):
                target._sh_val = random.random()
            return target._sh_val

        return 0.5  # fallback

    def to_dict(self) -> dict:
        """Serialize for saving."""
        return {
            "update_hz": self.update_hz,
            "targets": [
                {
                    "channel": t.channel, "cc": t.cc, "shape": t.shape,
                    "rate_hz": t.rate_hz, "min_val": t.min_val,
                    "max_val": t.max_val, "phase": t.phase,
                }
                for t in self._targets
            ],
        }

    @classmethod
    def from_dict(cls, data: dict, midi_out=None) -> "MidiLFO":
        """Deserialize from saved data."""
        lfo = cls(midi_out)
        lfo.update_hz = data.get("update_hz", DEFAULT_UPDATE_HZ)
        for t in data.get("targets", []):
            lfo.add_target(
                channel=t.get("channel", 0),
                cc=t.get("cc", 16),
                shape=t.get("shape", SHAPE_SINE),
                rate_hz=t.get("rate_hz", 0.5),
                min_val=t.get("min_val", 0),
                max_val=t.get("max_val", 127),
                phase=t.get("phase", 0.0),
            )
        return lfo
