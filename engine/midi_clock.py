"""MIDI clock sender with tap tempo.

Sends 24 clock ticks (0xF8) per beat at the configured BPM.
Supports tap tempo, fine BPM adjustment, and start/stop transport.
Uses hybrid sleep/spin-wait for sub-millisecond timing accuracy.
"""

import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

MIN_BPM = 30.0
MAX_BPM = 300.0
TICKS_PER_BEAT = 24


class MidiClockSender:
    """Sends MIDI clock to the P-6 at a configurable BPM.

    The P-6 must be set to SYNC=USB to receive external clock.
    """

    def __init__(self, midi_out):
        self._midi_out = midi_out
        self._bpm = 120.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._tick_count = 0

        # Tap tempo state
        self._tap_times: list[float] = []

        # Beat callback for UI flash
        self.on_beat: Optional[callable] = None

    @property
    def bpm(self) -> float:
        return self._bpm

    @bpm.setter
    def bpm(self, value: float):
        self._bpm = max(MIN_BPM, min(MAX_BPM, round(value, 1)))

    @property
    def running(self) -> bool:
        return self._running

    def start(self):
        """Send MIDI Start and begin clock output."""
        if self._running:
            return
        if self._midi_out:
            self._midi_out.send_message([0xFA])  # MIDI Start
        self._tick_count = 0
        self._running = True
        self._thread = threading.Thread(target=self._clock_loop, daemon=True)
        self._thread.start()
        log.info("Clock started at %.1f BPM", self._bpm)

    def stop(self):
        """Send MIDI Stop and halt clock output."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._midi_out:
            self._midi_out.send_message([0xFC])  # MIDI Stop
        log.info("Clock stopped")

    def tap(self):
        """Record a tap for tap tempo calculation."""
        now = time.perf_counter()

        # Reset if gap > 3 seconds
        if self._tap_times and now - self._tap_times[-1] > 3.0:
            self._tap_times = []

        self._tap_times.append(now)

        # Keep last 8 taps
        if len(self._tap_times) > 8:
            self._tap_times.pop(0)

        # Need at least 2 taps to calculate
        if len(self._tap_times) < 2:
            return

        intervals = [self._tap_times[i + 1] - self._tap_times[i]
                     for i in range(len(self._tap_times) - 1)]

        # Filter outliers (more than 2x median)
        sorted_intervals = sorted(intervals)
        median = sorted_intervals[len(sorted_intervals) // 2]
        filtered = [i for i in intervals if i < median * 2 and i > median * 0.5]

        if filtered:
            avg_interval = sum(filtered) / len(filtered)
            if avg_interval > 0:
                self.bpm = 60.0 / avg_interval

    def nudge(self, delta: float):
        """Adjust BPM by delta (e.g., +1, -0.1)."""
        self.bpm = self._bpm + delta

    def _clock_loop(self):
        """High-priority clock loop sending 24 ticks per beat."""
        try:
            os.nice(-10)
        except Exception:
            pass

        next_tick = time.perf_counter()

        while self._running:
            interval = 60.0 / (self._bpm * TICKS_PER_BEAT)
            next_tick += interval

            # Send clock tick
            if self._midi_out:
                self._midi_out.send_message([0xF8])

            self._tick_count += 1
            if self._tick_count >= TICKS_PER_BEAT:
                self._tick_count = 0
                if self.on_beat:
                    self.on_beat()

            # Hybrid sleep/spin-wait for accuracy
            while True:
                remaining = next_tick - time.perf_counter()
                if remaining <= 0:
                    break
                if remaining > 0.002:
                    time.sleep(remaining * 0.5)
                # Spin-wait for the last ~2ms

    def shutdown(self):
        """Clean shutdown."""
        if self._running:
            self.stop()
