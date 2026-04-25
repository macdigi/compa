"""Compa master clock — tempo source for the Push 2 step sequencer.

Runs a daemon thread firing tick callbacks at 24 PPQN (MIDI-clock
standard) at the configured BPM. Drives PiSequencer.on_tick directly,
bypassing the focused device's MIDI clock so the sequencer tempo
stays stable independent of the device side.

Usage:

    clock = MasterClock(bpm=120.0)
    clock.add_listener(seq.on_tick)
    clock.start()
    ...
    clock.set_bpm(128)         # safe to call any time, takes effect next tick
    ...
    clock.remove_listener(seq.on_tick)
    clock.stop()
"""

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

PPQN = 24                  # MIDI-clock pulses per quarter note
BPM_MIN = 20.0
BPM_MAX = 300.0


class MasterClock:
    def __init__(self, bpm: float = 120.0) -> None:
        self._bpm = max(BPM_MIN, min(BPM_MAX, float(bpm)))
        self._listeners: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

    # ── BPM ─────────────────────────────────────────────────────────

    def get_bpm(self) -> float:
        return self._bpm

    def set_bpm(self, bpm: float) -> None:
        with self._lock:
            self._bpm = max(BPM_MIN, min(BPM_MAX, float(bpm)))

    def nudge_bpm(self, delta: float) -> None:
        self.set_bpm(self.get_bpm() + delta)

    # ── Listeners ───────────────────────────────────────────────────

    def add_listener(self, cb: Callable[[], None]) -> None:
        with self._lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

    # ── Lifecycle ───────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            return
        self._stop_evt.clear()
        t = threading.Thread(target=self._loop, daemon=True,
                             name="CompaMasterClock")
        self._thread = t
        t.start()
        log.info("MasterClock started @ %.1f BPM", self._bpm)

    def stop(self) -> None:
        self._stop_evt.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=0.5)
        self._thread = None
        log.info("MasterClock stopped")

    # ── Tick loop ───────────────────────────────────────────────────

    def _loop(self) -> None:
        next_tick = time.monotonic()
        while not self._stop_evt.is_set():
            interval = 60.0 / (self._bpm * PPQN)
            next_tick += interval
            sleep = next_tick - time.monotonic()
            if sleep > 0:
                # Use Event.wait so stop() returns promptly.
                if self._stop_evt.wait(timeout=sleep):
                    break
            elif sleep < -0.25:
                # Got way behind (system stall) — resync rather than burst.
                next_tick = time.monotonic()
            with self._lock:
                listeners = list(self._listeners)
            for cb in listeners:
                try:
                    cb()
                except Exception as e:
                    log.debug("MasterClock listener failed: %s", e)
