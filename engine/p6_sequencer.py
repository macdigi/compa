"""Pi-side step sequencer — multi-device with special rows.

Sends MIDI notes to trigger device pads on beat boundaries.
Counts MIDI clock ticks (24 per beat) to advance steps.

Special row types for SP-404 MK2:
  - PAD rows: trigger sample pads (default, same as before)
  - CHROMATIC row: melodic note on Ch16 (SP-404 chromatic mode)
  - GHOST KICK row: silent trigger for side chain compression source
  - EXT SOURCE row: note 35 gates the SP-404's live audio input
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

# Special row types
ROW_PAD = "pad"            # Normal pad trigger
ROW_CHROMATIC = "chromatic" # Melodic note on Ch16 (SP-404 chromatic mode)
ROW_GHOST = "ghost"         # Silent kick for side chain trigger
ROW_EXT_SRC = "ext_src"    # Note 35 = gate EXT SOURCE input

# SP-404 specific channels/notes
CH_CHROMATIC = 15   # Ch16 — chromatic mode
NOTE_EXT_SOURCE = 35  # B1 — gates the live audio input


@dataclass
class StepData:
    """Data for one cell in the step grid."""
    active: bool = False
    velocity: int = 100
    probability: float = 1.0  # 0.0-1.0, chance of triggering
    note: int = -1  # Override note for chromatic rows (-1 = use default)


@dataclass
class RowConfig:
    """Configuration for a sequencer row."""
    row_type: str = ROW_PAD  # pad, chromatic, ghost, ext_src
    label: str = ""           # Display label
    note: int = -1            # Fixed note (-1 = use pad index default)
    channel: int = -1         # MIDI channel (-1 = use device default)
    color: tuple = (0, 0, 0)  # UI color hint


class PiSequencer:
    """Step sequencer with multi-device support and special row types.

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

        # Row configs — default to PAD rows
        self.row_configs: list[RowConfig] = [
            RowConfig(ROW_PAD, f"PAD {i+1}") for i in range(NUM_PADS)
        ]

        # Playback state
        self.playing = False
        self.current_step = 0
        self._tick_count = 0

        # Step resolution: how many MIDI clock ticks make one step.
        # Default is 24 (= 1 beat per step). Halving this doubles the
        # grid resolution (1/8 → 1/16 → 1/32). The on_tick loop reads
        # this attribute so changes take effect on the next clock
        # tick.
        self.ticks_per_step = 24

        # MIDI output
        self._midi_out = None  # set by the app
        self._active_notes: list[tuple[int, int]] = []  # (note, channel) pairs

        # Callbacks
        self.on_step_change: Optional[Callable[[int], None]] = None

    @property
    def base_note(self) -> int:
        """First pad note (C3 = 48 for P-6, 36 for SP-404 Mode A)."""
        return PAD_NOTE_LO

    def configure_for_device(self, device_short_name: str):
        """Set up row configs for a specific device."""
        if "SP-404" in device_short_name:
            self.num_pads = 8
            self.row_configs = [
                RowConfig(ROW_PAD, "PAD 1"),
                RowConfig(ROW_PAD, "PAD 2"),
                RowConfig(ROW_PAD, "PAD 3"),
                RowConfig(ROW_PAD, "PAD 4"),
                RowConfig(ROW_CHROMATIC, "CHROM", color=(100, 150, 255)),
                RowConfig(ROW_GHOST, "GHOST", color=(80, 80, 80)),
                RowConfig(ROW_EXT_SRC, "EXT IN", note=NOTE_EXT_SOURCE,
                          color=(255, 180, 50)),
                RowConfig(ROW_PAD, "PAD 5"),
            ]
            # Ensure grid has enough rows
            while len(self.grid) < self.num_pads:
                self.grid.append([StepData() for _ in range(MAX_STEPS)])
        else:
            # P-6 or default — 6 pad rows
            self.num_pads = NUM_PADS
            self.row_configs = [
                RowConfig(ROW_PAD, f"PAD {i+1}") for i in range(NUM_PADS)
            ]

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

    # ── Resolution / duplication ────────────────────────────────────

    def zoom_in(self) -> bool:
        """Halve `ticks_per_step` and double `num_steps` so each
        existing step becomes two cells with the same total pattern
        duration. The first cell of each pair holds the original
        step's content; the second is empty (ready for sub-step
        notes — e.g. 1/32-note hi-hat rolls).

        Returns True on success, False if already at the minimum
        resolution (1/32 notes = 3 ticks per step) or expanding would
        exceed MAX_STEPS."""
        if self.ticks_per_step <= 3:
            return False
        if self.num_steps * 2 > MAX_STEPS:
            return False
        new_steps = self.num_steps * 2
        new_tps = self.ticks_per_step // 2
        for pad in range(self.num_pads):
            row = self.grid[pad]
            # Walk from the back so we don't clobber earlier cells.
            for s in range(self.num_steps - 1, -1, -1):
                src = row[s]
                row[s * 2] = StepData(active=src.active,
                                       velocity=src.velocity,
                                       probability=src.probability)
                row[s * 2 + 1] = StepData()
        self.num_steps = new_steps
        self.ticks_per_step = new_tps
        if self.current_step >= new_steps:
            self.current_step = 0
        return True

    def zoom_out(self) -> bool:
        """Double `ticks_per_step` and halve `num_steps`. Each pair of
        cells collapses to one whose state is the OR of the two
        (preserving any active step in either half, dropping precise
        sub-step timing). Returns False if already at the maximum
        coarseness (192 ticks per step = half-bar steps) or if
        num_steps is already at the minimum (1)."""
        if self.num_steps < 2:
            return False
        if self.ticks_per_step >= 192:
            return False
        new_steps = self.num_steps // 2
        new_tps = self.ticks_per_step * 2
        for pad in range(self.num_pads):
            row = self.grid[pad]
            for s in range(new_steps):
                a, b = row[s * 2], row[s * 2 + 1]
                merged_active = a.active or b.active
                # Pick velocity from whichever half had a hit (prefer
                # the on-beat one if both fired).
                vel = a.velocity if a.active else b.velocity
                prob = a.probability if a.active else b.probability
                row[s] = StepData(active=merged_active,
                                   velocity=vel,
                                   probability=prob)
            for s in range(new_steps, MAX_STEPS):
                row[s] = StepData()
        self.num_steps = new_steps
        self.ticks_per_step = new_tps
        if self.current_step >= new_steps:
            self.current_step = 0
        return True

    def duplicate_pattern(self) -> bool:
        """Double the pattern length and copy the existing content into
        the new second half. Pattern timing (ticks_per_step) is
        unchanged — the result plays the same beat twice in a row.
        Returns False if doubling would exceed MAX_STEPS."""
        if self.num_steps * 2 > MAX_STEPS:
            return False
        old_n = self.num_steps
        new_n = old_n * 2
        for pad in range(self.num_pads):
            row = self.grid[pad]
            for s in range(old_n):
                src = row[s]
                row[old_n + s] = StepData(active=src.active,
                                           velocity=src.velocity,
                                           probability=src.probability)
        self.num_steps = new_n
        return True

    def step_note_value(self) -> str:
        """Human-readable note value for one step at the current
        resolution. 24 ticks = 1/4 (quarter), 12 = 1/8, 6 = 1/16,
        3 = 1/32, 48 = 1/2, 96 = 1/1. Returns "?" for unusual values
        (e.g. mid-zoom transient states)."""
        return {
            96: "1/1",  48: "1/2",  24: "1/4",  12: "1/8",
             6: "1/16",  3: "1/32", 192: "2/1",
        }.get(self.ticks_per_step, "?")

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
        if self._tick_count < self.ticks_per_step:
            return

        # Step boundary — advance step
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
            if not cell.active:
                continue
            # Probability check
            if cell.probability < 1.0 and random.random() > cell.probability:
                continue

            cfg = self.row_configs[pad] if pad < len(self.row_configs) else RowConfig()

            if cfg.row_type == ROW_CHROMATIC:
                # Melodic note on Ch16 — use cell.note or default C3
                note = cell.note if cell.note >= 0 else 60  # Middle C
                ch = CH_CHROMATIC
                self._midi_out.send_note_on(note, cell.velocity, ch)
                self._active_notes.append((note, ch))

            elif cfg.row_type == ROW_GHOST:
                # Ghost kick — trigger a pad but at velocity 1 (or muted bus)
                # Useful as side chain source in SP-404
                note = self.base_note  # First pad
                ch = self._midi_out.ch_sampler if hasattr(self._midi_out, "ch_sampler") else CH_SAMPLER
                self._midi_out.send_note_on(note, 1, ch)  # vel=1, barely audible
                self._active_notes.append((note, ch))

            elif cfg.row_type == ROW_EXT_SRC:
                # Gate the EXT SOURCE input (note 35 on pad channel)
                note = NOTE_EXT_SOURCE
                ch = self._midi_out.ch_sampler if hasattr(self._midi_out, "ch_sampler") else CH_SAMPLER
                self._midi_out.send_note_on(note, cell.velocity, ch)
                self._active_notes.append((note, ch))

            else:
                # Normal pad trigger
                note = cfg.note if cfg.note >= 0 else self.base_note + pad
                ch = cfg.channel if cfg.channel >= 0 else (
                    self._midi_out.ch_sampler if hasattr(self._midi_out, "ch_sampler")
                    else CH_SAMPLER)
                self._midi_out.send_note_on(note, cell.velocity, ch)
                self._active_notes.append((note, ch))

    def _all_notes_off(self) -> None:
        """Send note-off for all currently active notes."""
        if not self._midi_out:
            return
        for note, ch in self._active_notes:
            self._midi_out.send_note_off(note, ch)
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
