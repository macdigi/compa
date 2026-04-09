"""
Pattern-based step sequencer for pi-sampler.

MPC/SP-404 style sequencer with 16 patterns, variable step counts,
swing, real-time/step recording, overdub, pattern chaining, and
tap tempo. Designed for real-time use on Raspberry Pi 3B.
"""

import threading
import time
import numpy as np


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class StepEvent:
    """A single event on a step: which pad at what velocity with timing offset."""
    __slots__ = ("pad_index", "velocity", "offset")

    def __init__(self, pad_index, velocity=1.0, offset=0.0):
        self.pad_index = int(pad_index)
        self.velocity = float(velocity)
        self.offset = float(offset)  # fractional step offset (-0.5 .. +0.5)

    def to_dict(self):
        return {
            "pad_index": self.pad_index,
            "velocity": self.velocity,
            "offset": self.offset,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["pad_index"], d.get("velocity", 1.0), d.get("offset", 0.0))


class Pattern:
    """
    A single pattern with up to 64 steps.
    Each step holds a list of StepEvents (multiple pads can trigger on one step).
    """

    MAX_STEPS = 64

    def __init__(self, step_count=16):
        self.step_count = step_count
        # Pre-allocate list of lists for all 64 possible steps
        self.steps = [[] for _ in range(self.MAX_STEPS)]

    def set_step(self, step, pad_index, velocity=1.0, offset=0.0):
        """Add or update an event on a step for a given pad."""
        if step < 0 or step >= self.step_count:
            return
        events = self.steps[step]
        # Replace existing event for this pad, or add new
        for evt in events:
            if evt.pad_index == pad_index:
                evt.velocity = velocity
                evt.offset = offset
                return
        events.append(StepEvent(pad_index, velocity, offset))

    def clear_step(self, step, pad_index=None):
        """Clear a specific pad from a step, or all pads if pad_index is None."""
        if step < 0 or step >= self.step_count:
            return
        if pad_index is None:
            self.steps[step].clear()
        else:
            self.steps[step] = [
                e for e in self.steps[step] if e.pad_index != pad_index
            ]

    def clear(self):
        """Clear all steps."""
        for s in self.steps:
            s.clear()

    def copy_from(self, other):
        """Deep copy another pattern's data into this one."""
        self.step_count = other.step_count
        for i in range(self.MAX_STEPS):
            self.steps[i] = [
                StepEvent(e.pad_index, e.velocity, e.offset)
                for e in other.steps[i]
            ]

    def to_dict(self):
        return {
            "step_count": self.step_count,
            "steps": [
                [e.to_dict() for e in self.steps[i]]
                for i in range(self.step_count)
            ],
        }

    @classmethod
    def from_dict(cls, d):
        p = cls(d.get("step_count", 16))
        for i, step_data in enumerate(d.get("steps", [])):
            if i >= cls.MAX_STEPS:
                break
            p.steps[i] = [StepEvent.from_dict(e) for e in step_data]
        return p


# ---------------------------------------------------------------------------
# Sequencer
# ---------------------------------------------------------------------------

class Sequencer:
    """
    Pattern-based step sequencer.

    Thread-safe: advance() is called from the audio callback thread,
    while UI methods use a lock for state changes.
    """

    NUM_PATTERNS = 16
    VALID_STEP_COUNTS = (4, 8, 16, 32, 64)
    MIN_BPM = 30.0
    MAX_BPM = 300.0

    def __init__(self, bpm=120, steps=16, sample_rate=44100, buffer_size=256):
        self._sample_rate = sample_rate
        self._buffer_size = buffer_size

        # Patterns
        self._patterns = [Pattern(steps) for _ in range(self.NUM_PATTERNS)]
        self._current_pattern = 0

        # Tempo
        self._bpm = float(bpm)
        self._swing = 0.0  # 0-100

        # Transport state
        self._playing = False
        self._recording = False
        self._overdub = False
        self._paused = False

        # Position tracking (in samples)
        self._position_samples = 0  # total samples since play started
        self._current_step = 0
        self._last_triggered_step = -1

        # Tap tempo
        self._tap_times = []
        self._tap_timeout = 2.0  # seconds before tap history resets

        # Pattern chain
        self.pattern_chain = []  # list of pattern indices
        self._chain_position = 0

        # Metronome
        self._metronome_enabled = False
        self._metronome_accent = True  # accent on beat 1
        self._metronome_volume = 0.5

        # Pre-generate metronome click samples
        self._click_normal = self._generate_click(800, 0.02)
        self._click_accent = self._generate_click(1200, 0.02)

        # Real-time recording buffer: events recorded during playback
        # before quantization. Stores (sample_position, pad_index, velocity).
        self._recorded_events = []

        # Erase mode
        self._erase_active = False

        # Lock for state changes (not held during advance)
        self._lock = threading.Lock()

        # Callbacks
        self.on_trigger = None   # (pad_index, velocity)
        self.on_step_change = None  # (step_index)
        self.on_bar = None       # ()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_playing(self):
        return self._playing and not self._paused

    @property
    def is_recording(self):
        return self._recording

    @property
    def is_overdub(self):
        return self._overdub

    @property
    def current_step(self):
        return self._current_step

    @property
    def current_pattern(self):
        return self._current_pattern

    @property
    def bpm(self):
        return self._bpm

    @property
    def swing(self):
        return self._swing

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def play(self):
        """Start playback from current position."""
        with self._lock:
            if self._paused:
                self._paused = False
                return
            self._playing = True
            self._paused = False
            self._position_samples = 0
            self._current_step = 0
            self._last_triggered_step = -1
            self._chain_position = 0

    def stop(self):
        """Stop playback and reset position."""
        with self._lock:
            was_recording = self._recording or self._overdub
            self._playing = False
            self._paused = False
            self._recording = False
            self._overdub = False
            self._position_samples = 0
            self._current_step = 0
            self._last_triggered_step = -1
            self._chain_position = 0

        # Quantize any recorded events after stopping
        if was_recording and self._recorded_events:
            self._commit_recorded_events()

    def pause(self):
        """Pause playback, retaining position."""
        with self._lock:
            if self._playing:
                self._paused = True

    def record(self):
        """Enter real-time record mode (clears current pattern first)."""
        with self._lock:
            self._patterns[self._current_pattern].clear()
            self._recorded_events.clear()
            self._recording = True
            self._overdub = False
            if not self._playing:
                self._playing = True
                self._paused = False
                self._position_samples = 0
                self._current_step = 0
                self._last_triggered_step = -1

    def overdub(self):
        """Enter overdub mode (adds to existing pattern)."""
        with self._lock:
            self._recorded_events.clear()
            self._overdub = True
            self._recording = False
            if not self._playing:
                self._playing = True
                self._paused = False
                self._position_samples = 0
                self._current_step = 0
                self._last_triggered_step = -1

    # ------------------------------------------------------------------
    # Tempo / Swing
    # ------------------------------------------------------------------

    def set_bpm(self, bpm):
        """Set BPM (clamped to 30-300)."""
        self._bpm = max(self.MIN_BPM, min(self.MAX_BPM, float(bpm)))

    def set_swing(self, percent):
        """Set swing amount 0-100%. Delays even-numbered steps."""
        self._swing = max(0.0, min(100.0, float(percent)))

    def tap_tempo(self):
        """
        Call repeatedly to set BPM from tap intervals.
        Resets if more than 2 seconds between taps.
        """
        now = time.monotonic()
        if self._tap_times and (now - self._tap_times[-1]) > self._tap_timeout:
            self._tap_times.clear()

        self._tap_times.append(now)

        # Keep last 8 taps
        if len(self._tap_times) > 8:
            self._tap_times = self._tap_times[-8:]

        if len(self._tap_times) >= 2:
            intervals = [
                self._tap_times[i] - self._tap_times[i - 1]
                for i in range(1, len(self._tap_times))
            ]
            avg_interval = sum(intervals) / len(intervals)
            if avg_interval > 0:
                self.set_bpm(60.0 / avg_interval)

    # ------------------------------------------------------------------
    # Step count / Pattern selection
    # ------------------------------------------------------------------

    def set_step_count(self, count):
        """Set step count for current pattern (4, 8, 16, 32, or 64)."""
        if count in self.VALID_STEP_COUNTS:
            self._patterns[self._current_pattern].step_count = count

    def select_pattern(self, index):
        """Select active pattern (0-15)."""
        if 0 <= index < self.NUM_PATTERNS:
            with self._lock:
                self._current_pattern = int(index)
                if not self._playing:
                    self._current_step = 0
                    self._last_triggered_step = -1

    # ------------------------------------------------------------------
    # Step programming
    # ------------------------------------------------------------------

    def set_step(self, step, pad_index, velocity=1.0):
        """Place a note on the grid (step programming)."""
        self._patterns[self._current_pattern].set_step(
            step, pad_index, velocity
        )

    def clear_step(self, step, pad_index=None):
        """Clear a specific pad or all pads at a step."""
        self._patterns[self._current_pattern].clear_step(step, pad_index)

    def clear_pattern(self):
        """Clear all steps in the current pattern."""
        self._patterns[self._current_pattern].clear()

    def copy_pattern(self, from_idx, to_idx):
        """Copy one pattern to another."""
        if (
            0 <= from_idx < self.NUM_PATTERNS
            and 0 <= to_idx < self.NUM_PATTERNS
        ):
            self._patterns[to_idx].copy_from(self._patterns[from_idx])

    def get_pattern_data(self):
        """Return current pattern step data as a list of lists of dicts."""
        pat = self._patterns[self._current_pattern]
        result = []
        for i in range(pat.step_count):
            result.append([e.to_dict() for e in pat.steps[i]])
        return result

    # ------------------------------------------------------------------
    # Real-time input (pad hits during recording)
    # ------------------------------------------------------------------

    def record_pad_hit(self, pad_index, velocity=1.0):
        """
        Called when a pad is hit during real-time record or overdub.
        Also handles erase mode.
        """
        if self._erase_active and self._playing:
            # Erase this pad from the current step
            self._patterns[self._current_pattern].clear_step(
                self._current_step, pad_index
            )
            return

        if (self._recording or self._overdub) and self._playing:
            self._recorded_events.append(
                (self._position_samples, int(pad_index), float(velocity))
            )

    def set_erase(self, active):
        """Enable/disable erase mode. Hold erase + tap pad to remove."""
        self._erase_active = bool(active)

    # ------------------------------------------------------------------
    # Quantize
    # ------------------------------------------------------------------

    def quantize_recording(self, strength=1.0):
        """
        Quantize recorded events in the current pattern.
        strength: 0.0 = no quantize, 1.0 = full snap to grid.
        """
        strength = max(0.0, min(1.0, float(strength)))
        pat = self._patterns[self._current_pattern]

        for step_idx in range(pat.step_count):
            for evt in pat.steps[step_idx]:
                evt.offset *= (1.0 - strength)

    # ------------------------------------------------------------------
    # Audio callback interface
    # ------------------------------------------------------------------

    def advance(self, num_samples):
        """
        Called from the audio callback to advance the sequencer position.
        Fires on_trigger for any steps that land within this buffer window.

        Args:
            num_samples: number of samples in this buffer period.
        """
        if not self._playing or self._paused:
            return

        pat = self._patterns[self._current_pattern]
        step_count = pat.step_count
        samples_per_beat = (60.0 / self._bpm) * self._sample_rate
        # Each step = 1/4 beat (16th notes when step_count=16 in 4/4)
        # For a 16-step pattern to equal 1 bar: each step = 1 beat / 4
        # More generally: samples_per_step = samples_per_bar / step_count
        # where samples_per_bar = 4 * samples_per_beat (4/4 time)
        samples_per_bar = 4.0 * samples_per_beat
        samples_per_step = samples_per_bar / step_count

        old_pos = self._position_samples
        new_pos = old_pos + num_samples

        # Determine which steps fall in [old_pos, new_pos)
        old_step_float = old_pos / samples_per_step
        new_step_float = new_pos / samples_per_step

        # Walk through each step that starts within this window
        first_step = int(old_step_float)
        last_step = int(new_step_float)

        for abs_step in range(first_step, last_step + 1):
            step_idx = abs_step % step_count

            # Apply swing to even-numbered steps (0-indexed, so step 1, 3, 5...)
            swing_offset = 0.0
            if step_idx % 2 == 1 and self._swing > 0:
                # Swing delays odd steps by up to 50% of a step duration
                swing_offset = (self._swing / 100.0) * 0.5 * samples_per_step

            step_start_sample = abs_step * samples_per_step + swing_offset

            if old_pos <= step_start_sample < new_pos:
                # This step triggers in this buffer
                if step_idx != self._last_triggered_step or abs_step != first_step:
                    self._current_step = step_idx
                    self._last_triggered_step = step_idx

                    # Fire step change callback
                    if self.on_step_change is not None:
                        try:
                            self.on_step_change(step_idx)
                        except Exception:
                            pass

                    # Fire bar callback on step 0
                    if step_idx == 0 and self.on_bar is not None:
                        try:
                            self.on_bar()
                        except Exception:
                            pass

                    # Trigger pad events on this step
                    for evt in pat.steps[step_idx]:
                        if self.on_trigger is not None:
                            try:
                                self.on_trigger(evt.pad_index, evt.velocity)
                            except Exception:
                                pass

        self._position_samples = new_pos

        # Handle pattern wrap / chain advance
        total_pattern_samples = step_count * samples_per_step
        if self._position_samples >= total_pattern_samples:
            self._position_samples -= total_pattern_samples
            self._last_triggered_step = -1
            self._advance_chain()

    def _advance_chain(self):
        """Move to next pattern in chain, if chaining is active."""
        if not self.pattern_chain:
            return
        self._chain_position = (self._chain_position + 1) % len(
            self.pattern_chain
        )
        self._current_pattern = self.pattern_chain[self._chain_position]

    # ------------------------------------------------------------------
    # Metronome
    # ------------------------------------------------------------------

    def set_metronome(self, enabled, accent=True, volume=0.5):
        """Configure the metronome."""
        self._metronome_enabled = bool(enabled)
        self._metronome_accent = bool(accent)
        self._metronome_volume = max(0.0, min(1.0, float(volume)))

    def get_metronome_click(self, step_idx, step_count):
        """
        Return a metronome click sample if this step should have one,
        otherwise None.
        """
        if not self._metronome_enabled:
            return None

        # Click on quarter-note boundaries
        steps_per_beat = step_count / 4.0
        if step_idx % max(1, int(steps_per_beat)) != 0:
            return None

        if step_idx == 0 and self._metronome_accent:
            return self._click_accent * self._metronome_volume
        return self._click_normal * self._metronome_volume

    def _generate_click(self, freq, duration):
        """Generate a short sine click for metronome."""
        n = int(self._sample_rate * duration)
        t = np.linspace(0, duration, n, dtype=np.float32)
        # Sine with fast exponential decay
        envelope = np.exp(-t * 40.0).astype(np.float32)
        click = (np.sin(2.0 * np.pi * freq * t) * envelope).astype(np.float32)
        return click

    # ------------------------------------------------------------------
    # Recording commit
    # ------------------------------------------------------------------

    def _commit_recorded_events(self):
        """
        Quantize and commit recorded pad hits into the current pattern.
        Called when recording stops.
        """
        if not self._recorded_events:
            return

        pat = self._patterns[self._current_pattern]
        step_count = pat.step_count
        samples_per_beat = (60.0 / self._bpm) * self._sample_rate
        samples_per_bar = 4.0 * samples_per_beat
        samples_per_step = samples_per_bar / step_count

        for sample_pos, pad_index, velocity in self._recorded_events:
            # Map sample position to step
            step_float = (sample_pos % samples_per_bar) / samples_per_step
            step_int = int(round(step_float)) % step_count
            offset = step_float - round(step_float)
            # Clamp offset
            offset = max(-0.5, min(0.5, offset))
            pat.set_step(step_int, pad_index, velocity, offset)

        self._recorded_events.clear()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self):
        """Serialize sequencer state for saving."""
        return {
            "bpm": self._bpm,
            "swing": self._swing,
            "current_pattern": self._current_pattern,
            "pattern_chain": list(self.pattern_chain),
            "patterns": [p.to_dict() for p in self._patterns],
        }

    @classmethod
    def from_dict(cls, d, sample_rate=44100, buffer_size=256):
        """Restore sequencer state from a dict."""
        seq = cls(
            bpm=d.get("bpm", 120),
            steps=16,
            sample_rate=sample_rate,
            buffer_size=buffer_size,
        )
        seq._swing = d.get("swing", 0.0)
        seq._current_pattern = d.get("current_pattern", 0)
        seq.pattern_chain = d.get("pattern_chain", [])

        for i, pdata in enumerate(d.get("patterns", [])):
            if i >= cls.NUM_PATTERNS:
                break
            seq._patterns[i] = Pattern.from_dict(pdata)

        return seq
