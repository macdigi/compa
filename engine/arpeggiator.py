"""Chord arpeggiator for keys-mode pad presses.

Takes a chord (the list of MIDI notes a chord-mode pad would play as a
block) and instead plays its notes in sequence, synced to the global
tempo. Supports the standard arpeggiator vocabulary: pattern, rate,
octaves, swing, gate (stab), density (chord extensions), inversion,
humanize (random vel variation), and accent (every-Nth-note emphasis).

Architecture:

  ArpParams     — current arp config (one instance owned by the app,
                  mutated by encoder turns + top-button presses)
  ArpInstance   — runs one arp; one per held pad. Owns sequence
                  state, currently-sounding note, scheduling clocks
                  for next-tick + note-off
  ArpScheduler  — single daemon thread polling all active instances
                  every ~5 ms. Cheap; one thread regardless of how
                  many pads are held

Tempo: pulled live each tick from the app's BPM source (P-6 state
when present, fallback to 120). Tempo changes propagate immediately
to all running arps.

Hold: when ArpParams.hold is True, an active arp keeps running after
the pad releases. Pressing the same pad again toggles it off; pressing
a different chord-pad swaps the arp's chord without stopping.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


# ── Constants ──────────────────────────────────────────────────────

# Pattern names (canonical strings — used in user-facing labels and
# stable for future config persistence).
PATTERNS: tuple[str, ...] = (
    "off", "up", "down", "up_down", "down_up", "random",
)

# Rate string → fraction of a beat. 1/4 = 1.0, 1/8 = 0.5, etc.
# Triplets are shown with T, dotted notes with `.`.
RATES: dict[str, float] = {
    "1/4":   1.0,
    "1/4T":  2.0 / 3.0,
    "1/8":   0.5,
    "1/8T":  1.0 / 3.0,
    "1/8.":  0.75,
    "1/16":  0.25,
    "1/16T": 1.0 / 6.0,
    "1/16.": 0.375,
    "1/32":  0.125,
}
RATE_ORDER: tuple[str, ...] = (
    "1/4", "1/4T", "1/8.", "1/8", "1/8T",
    "1/16.", "1/16", "1/16T", "1/32",
)

# Stab presets (note hold-time as fraction of slot).
STAB_FACTORS: dict[str, float] = {
    "staccato":  0.20,
    "normal":    0.55,
    "sustained": 0.85,
    "legato":    0.99,   # hold almost the whole slot, slight gap
}
STAB_ORDER: tuple[str, ...] = (
    "staccato", "normal", "sustained", "legato",
)

# Density steps (number of notes in the chord — triad / 7th / 9th / 11th).
DENSITY_VALUES: tuple[int, ...] = (3, 4, 5, 6)

# Inversion steps (root pos / 1st / 2nd).
INVERSION_VALUES: tuple[int, ...] = (0, 1, 2)

# Accent options: 0 = off, otherwise every-Nth-note loud.
ACCENT_VALUES: tuple[int, ...] = (0, 2, 3, 4)


# ── Config ─────────────────────────────────────────────────────────

@dataclass
class ArpParams:
    """Live arp configuration. Mutated by the encoder dispatcher and
    the top-row pattern buttons."""
    pattern: str = "off"
    rate: str = "1/16"
    octaves: int = 1           # 1..4
    stab: str = "normal"
    swing: int = 50            # 50..75 (% of slot devoted to first note)
    density: int = 3           # 3..6
    inversion: int = 0         # 0..2
    humanize: int = 0          # 0..100 (vel +/- range)
    accent: int = 0            # 0 (off), 2, 3, 4 (every Nth note loud)
    hold: bool = False         # arp continues after pad release

    def cycle_pattern(self, delta: int = 1) -> None:
        i = (PATTERNS.index(self.pattern) + delta) % len(PATTERNS)
        self.pattern = PATTERNS[i]

    def cycle_rate(self, delta: int = 1) -> None:
        cur = RATE_ORDER.index(self.rate) if self.rate in RATE_ORDER else 6
        self.rate = RATE_ORDER[(cur + delta) % len(RATE_ORDER)]

    def cycle_stab(self, delta: int = 1) -> None:
        cur = STAB_ORDER.index(self.stab) if self.stab in STAB_ORDER else 1
        self.stab = STAB_ORDER[(cur + delta) % len(STAB_ORDER)]

    def cycle_density(self, delta: int = 1) -> None:
        cur = (DENSITY_VALUES.index(self.density)
               if self.density in DENSITY_VALUES else 0)
        self.density = DENSITY_VALUES[(cur + delta) % len(DENSITY_VALUES)]

    def cycle_inversion(self, delta: int = 1) -> None:
        cur = (INVERSION_VALUES.index(self.inversion)
               if self.inversion in INVERSION_VALUES else 0)
        self.inversion = INVERSION_VALUES[
            (cur + delta) % len(INVERSION_VALUES)]

    def cycle_accent(self, delta: int = 1) -> None:
        cur = (ACCENT_VALUES.index(self.accent)
               if self.accent in ACCENT_VALUES else 0)
        self.accent = ACCENT_VALUES[(cur + delta) % len(ACCENT_VALUES)]

    def adjust_octaves(self, delta: int) -> None:
        self.octaves = max(1, min(4, self.octaves + delta))

    def adjust_swing(self, delta: int) -> None:
        self.swing = max(50, min(75, self.swing + delta))

    def adjust_humanize(self, delta: int) -> None:
        self.humanize = max(0, min(100, self.humanize + delta))


# ── Single arp instance ────────────────────────────────────────────

class ArpInstance:
    """One running arp for a single chord. Driven by ArpScheduler ticks."""

    def __init__(
        self,
        chord_notes: list[int],
        params: ArpParams,
        send_note_on: Callable[[int, int], None],
        send_note_off: Callable[[int], None],
        get_bpm: Callable[[], float],
    ) -> None:
        self.params = params
        self.send_note_on = send_note_on
        self.send_note_off = send_note_off
        self.get_bpm = get_bpm

        self.chord_notes = sorted(set(chord_notes))
        self._sequence: list[int] = []
        self._step: int = 0
        self._held_note: int = -1
        self._next_tick_at: float = 0.0
        self._note_off_at: float = 0.0

        self._rebuild_sequence_for(self.params.pattern)

    # ── Sequence construction ─────────────────────────────────────

    def set_chord(self, chord_notes: list[int]) -> None:
        """Swap to a new chord without restarting the step counter
        (smooth chord change while the arp keeps running)."""
        new_chord = sorted(set(chord_notes))
        if new_chord != self.chord_notes:
            self.chord_notes = new_chord
            self._rebuild_sequence_for(self.params.pattern)

    def _rebuild_sequence_for(self, pattern: str) -> None:
        if not self.chord_notes:
            self._sequence = []
            return
        full = list(self.chord_notes)
        for o in range(1, max(1, self.params.octaves)):
            full.extend(n + o * 12 for n in self.chord_notes)
        full = sorted(set(full))
        if pattern == "down":
            self._sequence = list(reversed(full))
        elif pattern == "up_down":
            self._sequence = (full + list(reversed(full[1:-1]))
                              if len(full) > 1 else list(full))
        elif pattern == "down_up":
            self._sequence = (list(reversed(full)) + full[1:-1]
                              if len(full) > 1 else list(full))
        else:
            # "up", "random", and "off" all use ascending — random
            # picks freely from chord_notes inside tick(); off
            # shouldn't be running an instance at all.
            self._sequence = list(full)

    # ── Tick (called by scheduler) ────────────────────────────────

    def tick(self, now: float) -> None:
        # Release any expired held note first.
        if self._held_note >= 0 and now >= self._note_off_at:
            try:
                self.send_note_off(self._held_note)
            except Exception:
                pass
            self._held_note = -1

        # Sequence might be stale if pattern/octaves changed.
        # Cheap to rebuild; do it lazily once per tick when needed.
        if (
            self._sequence
            and self.params.pattern != "random"
            and not self._sequence_matches_pattern()
        ):
            self._rebuild_sequence_for(self.params.pattern)

        if now < self._next_tick_at:
            return

        # Pick next note.
        if not self.chord_notes:
            return
        if self.params.pattern == "random":
            note = random.choice(self.chord_notes)
            if self.params.octaves > 1:
                note += random.randint(0, self.params.octaves - 1) * 12
        else:
            if not self._sequence:
                self._rebuild_sequence_for(self.params.pattern)
            if not self._sequence:
                return
            note = self._sequence[self._step % len(self._sequence)]

        # Velocity: base 100 with optional accent + humanize.
        velocity = 100
        if self.params.accent > 0 and (
            self._step % self.params.accent == 0
        ):
            velocity = 127
        if self.params.humanize > 0:
            vary = random.randint(
                -self.params.humanize, self.params.humanize)
            velocity = max(1, min(127, velocity + vary))

        # Fire — release any still-held note first so the new note
        # has its own envelope (no stuck overlaps under "legato").
        if self._held_note >= 0:
            try:
                self.send_note_off(self._held_note)
            except Exception:
                pass
            self._held_note = -1
        try:
            self.send_note_on(note, velocity)
        except Exception:
            return
        self._held_note = note

        # Schedule note-off + next tick.
        bpm = max(30.0, min(300.0, self.get_bpm()))
        beat_seconds = 60.0 / bpm
        rate_factor = RATES.get(self.params.rate, 0.25)
        slot = beat_seconds * rate_factor
        # Swing on odd steps — first slot lengthens, second shortens.
        if self._step % 2 == 1:
            swing_pct = (self.params.swing - 50) / 50.0  # 0..0.5
            slot *= max(0.5, 1.0 - swing_pct * 0.5)
        elif self._step % 2 == 0 and self.params.swing > 50:
            swing_pct = (self.params.swing - 50) / 50.0
            slot *= 1.0 + swing_pct * 0.5

        stab_factor = STAB_FACTORS.get(self.params.stab, 0.55)
        self._note_off_at = now + slot * stab_factor
        self._next_tick_at = now + slot
        self._step += 1

    def _sequence_matches_pattern(self) -> bool:
        """Cheap heuristic to detect if pattern changed since last
        rebuild — compares the first two notes against expectation."""
        if len(self._sequence) < 2 or not self.chord_notes:
            return False
        first, second = self._sequence[0], self._sequence[1]
        chord = sorted(self.chord_notes)
        if self.params.pattern == "down":
            return first >= second
        return first <= second

    def stop(self) -> None:
        if self._held_note >= 0:
            try:
                self.send_note_off(self._held_note)
            except Exception:
                pass
            self._held_note = -1


# ── Scheduler ──────────────────────────────────────────────────────

class ArpScheduler:
    """Single tick thread driving any number of ArpInstances. Cheap —
    sleeps 5ms between sweeps and only does work for active arps."""

    TICK_INTERVAL = 0.005   # 5 ms wake-up

    def __init__(self, get_bpm: Optional[Callable[[], float]] = None):
        self._instances: dict[int, ArpInstance] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._get_bpm = get_bpm or (lambda: 120.0)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ArpScheduler", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            for inst in self._instances.values():
                inst.stop()
            self._instances.clear()

    def add(
        self,
        pad_idx: int,
        chord_notes: list[int],
        params: ArpParams,
        send_note_on: Callable[[int, int], None],
        send_note_off: Callable[[int], None],
    ) -> None:
        """Start (or replace) the arp instance for `pad_idx`."""
        with self._lock:
            old = self._instances.get(pad_idx)
            if old is not None:
                old.stop()
            inst = ArpInstance(
                chord_notes, params,
                send_note_on, send_note_off,
                self._get_bpm,
            )
            inst._next_tick_at = time.monotonic()  # fire on first tick
            self._instances[pad_idx] = inst

    def remove(self, pad_idx: int) -> None:
        with self._lock:
            inst = self._instances.pop(pad_idx, None)
        if inst is not None:
            inst.stop()

    def has(self, pad_idx: int) -> bool:
        with self._lock:
            return pad_idx in self._instances

    def active_pads(self) -> list[int]:
        with self._lock:
            return list(self._instances.keys())

    def all_active_notes(self) -> set[int]:
        """Union of every currently-sounding note across all running
        arps. Used by the screen renderers to show held notes."""
        notes: set[int] = set()
        with self._lock:
            for inst in self._instances.values():
                if inst._held_note >= 0:
                    notes.add(inst._held_note)
        return notes

    def shutdown_all(self) -> None:
        """Release every held note; used by PANIC."""
        with self._lock:
            for inst in self._instances.values():
                inst.stop()
            self._instances.clear()

    # ── Tick loop ─────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                instances = list(self._instances.values())
            for inst in instances:
                try:
                    inst.tick(now)
                except Exception:
                    # Don't let one bad arp kill the scheduler.
                    pass
            self._stop.wait(self.TICK_INTERVAL)
