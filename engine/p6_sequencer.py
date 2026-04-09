"""Pi-side step sequencer for the P-6.

Sends MIDI notes to trigger P-6 sample pads on beat boundaries.
Counts MIDI clock ticks (24 per beat) to advance steps.
Works alongside or instead of the P-6's internal sequencer.
"""

import logging
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

from engine.p6_midi import CH_SAMPLER, PAD_NOTE_LO

log = logging.getLogger(__name__)

# Default 6 pad rows matching P-6's 6 pads per bank
NUM_PADS = 6
DEFAULT_STEPS = 16
MAX_STEPS = 64


@dataclass
class StepData:
    """Data for one cell in the step grid."""
    active: bool = False
    velocity: int = 100
    probability: float = 1.0  # 0.0-1.0, chance of triggering


class PiSequencer:
    """Step sequencer that sends notes to the P-6 via MIDI.

    Wire on_tick() to MIDI clock. Each tick = 1/24th of a beat.
    Steps advance every beat (24 ticks).
    """

    def __init__(self, num_steps: int = DEFAULT_STEPS):
        self.num_steps = num_steps
        self.num_pads = NUM_PADS

        # Grid: [pad_index][step_index] = StepData
        self.grid: list[list[StepData]] = [
            [StepData() for _ in range(MAX_STEPS)]
            for _ in range(NUM_PADS)
        ]

        # Playback state
        self.playing = False
        self.current_step = 0
        self._tick_count = 0

        # MIDI output
        self._midi_out = None  # set by the app
        self._active_notes: list[int] = []  # currently sounding notes

        # Callbacks
        self.on_step_change: Optional[Callable[[int], None]] = None

    @property
    def base_note(self) -> int:
        """First pad note (C3 = 48)."""
        return PAD_NOTE_LO

    def set_midi_out(self, p6_midi) -> None:
        """Wire up the P-6 MIDI output for note sending."""
        self._midi_out = p6_midi

    def toggle_step(self, pad: int, step: int) -> bool:
        """Toggle a step on/off. Returns new state."""
        if 0 <= pad < self.num_pads and 0 <= step < self.num_steps:
            cell = self.grid[pad][step]
            cell.active = not cell.active
            return cell.active
        return False

    def set_step(self, pad: int, step: int, active: bool,
                 velocity: int = 100, probability: float = 1.0) -> None:
        """Set step data directly."""
        if 0 <= pad < self.num_pads and 0 <= step < self.num_steps:
            cell = self.grid[pad][step]
            cell.active = active
            cell.velocity = velocity
            cell.probability = probability

    def clear_all(self) -> None:
        """Clear all steps."""
        for pad in range(self.num_pads):
            for step in range(MAX_STEPS):
                self.grid[pad][step].active = False

    def clear_pad(self, pad: int) -> None:
        """Clear all steps for one pad."""
        if 0 <= pad < self.num_pads:
            for step in range(MAX_STEPS):
                self.grid[pad][step].active = False

    def start(self) -> None:
        """Start playback from step 0."""
        self.current_step = 0
        self._tick_count = 0
        self.playing = True
        self._trigger_current_step()

    def stop(self) -> None:
        """Stop playback and silence all notes."""
        self.playing = False
        self._all_notes_off()
        self.current_step = 0
        self._tick_count = 0

    def on_tick(self) -> None:
        """Called on every MIDI clock tick (24 per beat)."""
        if not self.playing:
            return

        self._tick_count += 1
        if self._tick_count < 24:
            return

        # Beat boundary — advance step
        self._tick_count = 0
        self._all_notes_off()

        self.current_step = (self.current_step + 1) % self.num_steps
        self._trigger_current_step()

        if self.on_step_change:
            self.on_step_change(self.current_step)

    def _trigger_current_step(self) -> None:
        """Send note-on for all active pads at the current step."""
        if not self._midi_out:
            return

        step = self.current_step
        for pad in range(self.num_pads):
            cell = self.grid[pad][step]
            if cell.active:
                # Probability check
                if cell.probability < 1.0 and random.random() > cell.probability:
                    continue

                note = self.base_note + pad
                self._midi_out.send_note_on(note, cell.velocity, CH_SAMPLER)
                self._active_notes.append(note)

    def _all_notes_off(self) -> None:
        """Send note-off for all currently active notes."""
        if not self._midi_out:
            return
        for note in self._active_notes:
            self._midi_out.send_note_off(note, CH_SAMPLER)
        self._active_notes = []

    def get_step_count(self, pad: int) -> int:
        """Count active steps for a pad."""
        return sum(1 for s in range(self.num_steps) if self.grid[pad][s].active)

    def to_dict(self) -> dict:
        """Serialize for saving."""
        data = {"num_steps": self.num_steps, "pads": []}
        for pad in range(self.num_pads):
            steps = []
            for s in range(self.num_steps):
                cell = self.grid[pad][s]
                if cell.active:
                    steps.append({
                        "step": s,
                        "velocity": cell.velocity,
                        "probability": cell.probability,
                    })
            data["pads"].append(steps)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PiSequencer":
        """Deserialize from saved data."""
        seq = cls(num_steps=data.get("num_steps", DEFAULT_STEPS))
        for pad_idx, steps in enumerate(data.get("pads", [])):
            if pad_idx >= NUM_PADS:
                break
            for s in steps:
                seq.set_step(
                    pad_idx, s["step"],
                    active=True,
                    velocity=s.get("velocity", 100),
                    probability=s.get("probability", 1.0),
                )
        return seq
