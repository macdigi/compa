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

        # View step factor — how many internal cells each visible
        # editable step represents. 1 = view at full grid resolution
        # (every internal cell is one visible cell). 2 = each visible
        # step covers 2 internal cells, 4 = 4 cells, etc. Zoom_out
        # increases this; zoom_in either decreases it (toward 1) or,
        # at 1, subdivides the underlying grid for true resolution
        # increase.
        #
        # Critical: this is a VIEW property, not a grid transform.
        # Zooming out then back in must NEVER lose sub-step data —
        # the cells stay where they were, the visible step count
        # just changes. Playback always uses ticks_per_step, which
        # is the internal step duration regardless of view zoom.
        self._view_step_factor: int = 1

        # Swing — proportion of one step that every odd-indexed step
        # is delayed by. 0 = no swing, 50 = max (odd step lands
        # halfway between two beats, classic shuffle). Applied in
        # on_tick: when about to advance to an odd step, the boundary
        # waits for the extra tick budget. Range clamped 0..50.
        self.swing_amount: int = 0

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
        """Set up row configs for a specific device.

        SP-404 MK2: 16 pad rows, one per SP pad. Notes follow Roland's
        quirky 4×4 layout where pad 1 (top-left) = note 48 and pad 13
        (bottom-left) = note 36. Mapped explicitly so step rows read
        intuitively (row 0 = "PAD 1") regardless of the underlying
        MIDI note. Sent on the active bus channel via current_bank
        in the app dispatcher.

        P-6: 6 pad rows."""
        if "SP-404" in device_short_name:
            self.num_pads = 16
            self.row_configs = []
            for i in range(16):
                sp_row = i // 4         # 0 (top) .. 3 (bottom)
                sp_col = i % 4
                midi_row = 3 - sp_row   # SP MIDI: bottom row = 36-39
                note = 36 + midi_row * 4 + sp_col
                self.row_configs.append(
                    RowConfig(ROW_PAD, f"PAD {i + 1}", note=note)
                )
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
        """Show finer cells. If currently zoomed out (view factor > 1)
        just halve the view factor — the underlying grid was already
        at finer resolution and that data comes back into view. If at
        view factor 1, actually subdivide the internal grid: halve
        ticks_per_step, double num_steps, stretch each cell to two
        cells with the first holding the original content (ready for
        sub-step notes — e.g. 1/32-note hi-hat rolls).

        Returns True on success, False if already at the minimum
        resolution and view factor 1."""
        if self._view_step_factor > 1:
            self._view_step_factor //= 2
            return True
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
        """Show coarser cells. NON-DESTRUCTIVE — just doubles the
        view-step factor so each visible cell now covers two
        internal cells. Sub-step data is preserved; zooming back in
        with Convert restores the detailed view exactly as it was
        before. Returns False if already so far zoomed out that one
        view step would cover the whole pattern."""
        new_factor = self._view_step_factor * 2
        if new_factor > self.num_steps:
            return False
        if new_factor > 64:
            return False
        self._view_step_factor = new_factor
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
        """Human-readable note value for one VISIBLE step at the
        current view zoom. Combines the internal ticks_per_step with
        the view step factor so the user always sees the resolution
        they're currently editing at."""
        tps = self.ticks_per_step * max(1, self._view_step_factor)
        return {
            96: "1/1",  48: "1/2",  24: "1/4",  12: "1/8",
             6: "1/16",  3: "1/32", 192: "2/1",
        }.get(tps, "?")

    # ── View-zoom helpers (cells visible to the user) ───────────────

    @property
    def view_num_steps(self) -> int:
        """Visible step count at the current zoom. = num_steps when
        not zoomed out."""
        return max(1, self.num_steps // max(1, self._view_step_factor))

    def view_step_range(self, view_step: int) -> tuple[int, int]:
        """Internal-cell index range [start, end) covered by the
        given visible step at the current view zoom."""
        f = max(1, self._view_step_factor)
        start = view_step * f
        end = min(self.num_steps, start + f)
        return (start, end)

    def view_step_active(self, pad: int, view_step: int) -> bool:
        """True if any internal cell within the visible step is
        active. Used by the renderer to light visible cells."""
        if not (0 <= pad < self.num_pads):
            return False
        start, end = self.view_step_range(view_step)
        if start >= end:
            return False
        row = self.grid[pad]
        for s in range(start, end):
            if row[s].active:
                return True
        return False

    def view_step_velocity(self, pad: int, view_step: int) -> int:
        """Velocity of the first active internal cell within the
        visible step, or 100 if none are active."""
        if not (0 <= pad < self.num_pads):
            return 100
        start, end = self.view_step_range(view_step)
        row = self.grid[pad]
        for s in range(start, end):
            if row[s].active:
                return row[s].velocity
        return 100

    def toggle_view_step(self, pad: int, view_step: int) -> bool:
        """Toggle the visible step. If any internal cell within the
        view-step range is active, deactivates ALL of them (so a
        single tap clears whatever's there). Otherwise activates the
        first internal cell. Returns the new active state of the
        visible step."""
        if not (0 <= pad < self.num_pads):
            return False
        start, end = self.view_step_range(view_step)
        if start >= end:
            return False
        row = self.grid[pad]
        any_active = any(row[s].active for s in range(start, end))
        if any_active:
            for s in range(start, end):
                row[s].active = False
            return False
        row[start].active = True
        return True

    def view_current_step(self) -> int:
        """Visible step containing the playhead. Used by the
        renderer to highlight the playhead column."""
        return self.current_step // max(1, self._view_step_factor)

    # ── Nudge / rotation ────────────────────────────────────────────

    def nudge(self, delta: int) -> None:
        """Rotate every pad's step grid by `delta` positions. Positive
        delta shifts notes toward later steps (right), negative toward
        earlier (left). Applies to all `num_steps` columns; cells past
        the end wrap around. Used for re-aligning a pattern with a
        downbeat without redrawing it."""
        if self.num_steps <= 0:
            return
        d = delta % self.num_steps
        if d == 0:
            return
        n = self.num_steps
        for pad in range(self.num_pads):
            row = self.grid[pad]
            # Rotate by `d` steps to the right. Build new ordered slice
            # for the active range, leave anything past num_steps alone.
            slice_active = [row[s] for s in range(n)]
            for s in range(n):
                row[(s + d) % n] = slice_active[s]
            # Replace each rotated cell with a fresh StepData copy so
            # references aren't shared across the row (else editing
            # one cell mutates a sibling).
            for s in range(n):
                src = row[s]
                row[s] = StepData(active=src.active,
                                   velocity=src.velocity,
                                   probability=src.probability)

    # ── Randomize ───────────────────────────────────────────────────

    def randomize_density(self, density_pct: int) -> None:
        """Set every step in every pad to a fresh random active state.
        `density_pct` (0-100) controls the probability that each cell
        ends up active. 0 = empty, 100 = every cell on. Velocities
        keep their existing values; only the active flag is rolled."""
        density = max(0, min(100, int(density_pct))) / 100.0
        for pad in range(self.num_pads):
            row = self.grid[pad]
            for s in range(self.num_steps):
                row[s].active = (random.random() < density)

    def randomize_velocities(self, spread_pct: int) -> None:
        """Re-roll the velocity of every active step within a range
        centered on 100. `spread_pct` (0-100) is the half-width as a
        % of full velocity range — 0 leaves velocities untouched,
        100 picks any value 0..127. Doesn't touch the active flags."""
        spread = max(0, min(100, int(spread_pct)))
        if spread == 0:
            return
        half = int(127 * spread / 200)  # half-width in MIDI units
        if half <= 0:
            return
        for pad in range(self.num_pads):
            row = self.grid[pad]
            for s in range(self.num_steps):
                if row[s].active:
                    base = 100
                    lo = max(1, base - half)
                    hi = min(127, base + half)
                    row[s].velocity = random.randint(lo, hi)

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
        """Called on every MIDI clock tick (24 per beat).

        Swing: shifts odd-indexed steps later by a fraction of one
        step's tick budget, and shifts the following even step
        earlier by the same amount, so the total time over an
        even→odd→even cycle is unchanged. The pattern plays at the
        same tempo; only the odd-step placement moves.

        At swing_amount = 50, odd step lands halfway between the two
        even steps (classic triplet shuffle). 0 = straight, 50 = max."""
        if not self.playing:
            return

        self._tick_count += 1

        budget = self.ticks_per_step
        next_step = (self.current_step + 1) % max(1, self.num_steps)
        swing = max(0, min(50, int(self.swing_amount)))
        swing_delta = (self.ticks_per_step * swing) // 100
        if swing_delta > 0:
            if next_step % 2 == 1:
                # Going TO an odd step: hold longer.
                budget += swing_delta
            else:
                # Going TO an even step from an odd step: catch up.
                budget = max(1, budget - swing_delta)
        if self._tick_count < budget:
            return

        # Step boundary — advance step
        self._tick_count = 0
        self._all_notes_off()

        self.current_step = next_step
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
