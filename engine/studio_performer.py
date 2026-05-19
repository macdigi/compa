"""Runtime performer playback for Studio targets.

This is intentionally small: it plays PatternSpec data to an already-open MIDI
sender without blocking the UI thread.  The clip engine can absorb this later,
but today it gives Studio a real performer launch/stop path.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from engine.ai_pattern import (
    ChromaticHit,
    PatternHit,
    PatternSpec,
    SP404,
    chromatic_note_channel,
    device_note_channel,
)


SP404_BEAT_BASS_TARGET = "external.sp404.a1_a6_beat_bass"


@dataclass(frozen=True)
class MidiEvent:
    seconds: float
    message: tuple[int, int, int]
    label: str = ""

    @property
    def is_note_on(self) -> bool:
        return (self.message[0] & 0xF0) == 0x90 and self.message[2] > 0


def build_midi_events(
    spec: PatternSpec,
    *,
    bpm: float | None = None,
    velocity_scale: float = 1.0,
) -> list[MidiEvent]:
    tempo = float(bpm or spec.bpm)
    step_seconds = (60.0 / max(1.0, tempo)) * 4.0 / float(spec.steps_per_bar)
    loop_seconds = spec.length_beats * 60.0 / max(1.0, tempo)
    events: list[MidiEvent] = []

    def add_pair(start_step: float, duration_steps: float, note: int,
                 channel: int, velocity: int, label: str) -> None:
        start = max(0.0, start_step * step_seconds)
        if start >= loop_seconds:
            return
        note_off = min(
            start + max(0.035, duration_steps * step_seconds),
            max(start + 0.01, loop_seconds - 0.002),
        )
        velocity = max(1, min(127, int(round(velocity * velocity_scale))))
        events.append(MidiEvent(
            start, (0x90 | (channel & 0x0F), note & 0x7F, velocity), label))
        events.append(MidiEvent(
            note_off, (0x80 | (channel & 0x0F), note & 0x7F, 0),
            label))

    for hit in spec.hits:
        note, channel = device_note_channel(spec, hit.pad)
        add_pair(
            hit.step + hit.nudge,
            hit.duration_steps,
            note,
            channel,
            hit.velocity,
            hit.label or f"pad {hit.pad + 1}",
        )
    for hit in spec.chromatic_hits:
        note, channel = chromatic_note_channel(spec, hit.note)
        add_pair(
            hit.step + hit.nudge,
            hit.duration_steps,
            note,
            channel,
            hit.velocity,
            hit.label or f"chromatic {hit.note}",
        )
    events.sort(key=lambda event: (
        event.seconds, event.message[0] & 0xF0, event.message[1]))
    return events


def all_notes_off_messages(events: Iterable[MidiEvent]) -> list[tuple[int, int, int]]:
    pairs = {
        (event.message[0] & 0x0F, event.message[1])
        for event in events
        if event.is_note_on
    }
    return [(0x80 | channel, note, 0) for channel, note in sorted(pairs)]


class PatternPerformer:
    """Threaded MIDI PatternSpec player with stop and mute controls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._mute_event = threading.Event()
        self._sender: Callable[[list[int]], None] | None = None
        self._events: list[MidiEvent] = []
        self._target_key = ""
        self._pattern_name = ""
        self._port_label = ""
        self._last_error = ""
        self._running = False
        self._last_bpm = 0.0
        self._loop_count = 0

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "muted": self._mute_event.is_set(),
                "target_key": self._target_key,
                "pattern_name": self._pattern_name,
                "port_label": self._port_label,
                "last_error": self._last_error,
                "last_bpm": self._last_bpm,
                "loop_count": self._loop_count,
            }

    def play(
        self,
        spec: PatternSpec,
        *,
        send_message: Callable[[list[int]], None],
        target_key: str,
        port_label: str = "",
        loops: int = 0,
        bpm: float | None = None,
        bpm_provider: Callable[[], float] | None = None,
        velocity_scale: float = 1.0,
    ) -> None:
        current_bpm = float(bpm_provider() if bpm_provider else (bpm or spec.bpm))
        events = build_midi_events(
            spec, bpm=current_bpm, velocity_scale=velocity_scale)
        if not events:
            raise RuntimeError("pattern has no MIDI events")
        self.stop()
        stop_event = threading.Event()
        mute_event = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(spec, send_message, stop_event, mute_event, loops,
                  current_bpm, bpm_provider, velocity_scale),
            daemon=True,
            name="studio-pattern-performer",
        )
        with self._lock:
            self._thread = thread
            self._stop_event = stop_event
            self._mute_event = mute_event
            self._sender = send_message
            self._events = events
            self._target_key = target_key
            self._pattern_name = spec.name
            self._port_label = port_label
            self._last_error = ""
            self._running = True
            self._last_bpm = current_bpm
            self._loop_count = 0
        thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            sender = self._sender
            events = list(self._events)
        stop_event.set()
        if sender is not None:
            self._send_all_notes_off(sender, events)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.35)
        with self._lock:
            if thread is self._thread:
                self._running = False

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            sender = self._sender
            events = list(self._events)
        if muted:
            self._mute_event.set()
            if sender is not None:
                self._send_all_notes_off(sender, events)
        else:
            self._mute_event.clear()

    def toggle_mute(self) -> bool:
        muted = not self._mute_event.is_set()
        self.set_muted(muted)
        return muted

    def _run(
        self,
        spec: PatternSpec,
        send_message: Callable[[list[int]], None],
        stop_event: threading.Event,
        mute_event: threading.Event,
        loops: int,
        bpm: float,
        bpm_provider: Callable[[], float] | None,
        velocity_scale: float,
    ) -> None:
        loop_idx = 0
        try:
            while not stop_event.is_set() and (loops <= 0 or loop_idx < loops):
                try:
                    current_bpm = float(bpm_provider() if bpm_provider else bpm)
                except Exception:
                    current_bpm = bpm
                current_bpm = max(20.0, min(300.0, current_bpm))
                events = build_midi_events(
                    spec, bpm=current_bpm, velocity_scale=velocity_scale)
                loop_seconds = spec.length_beats * 60.0 / max(1.0, current_bpm)
                with self._lock:
                    self._events = events
                    self._last_bpm = current_bpm
                    self._loop_count = loop_idx + 1
                start_time = time.monotonic()
                for event in events:
                    if stop_event.is_set():
                        break
                    remaining = event.seconds - (time.monotonic() - start_time)
                    if remaining > 0 and stop_event.wait(remaining):
                        break
                    if mute_event.is_set() and event.is_note_on:
                        continue
                    send_message(list(event.message))
                elapsed = time.monotonic() - start_time
                loop_idx += 1
                if stop_event.is_set():
                    break
                if (loops <= 0 or loop_idx < loops) and elapsed < loop_seconds:
                    stop_event.wait(loop_seconds - elapsed)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            self._send_all_notes_off(send_message, events)
            with self._lock:
                self._running = False

    @staticmethod
    def _send_all_notes_off(
        send_message: Callable[[list[int]], None],
        events: Iterable[MidiEvent],
    ) -> None:
        for msg in all_notes_off_messages(events):
            try:
                send_message(list(msg))
            except Exception:
                pass


def confirmed_sp404_beat_bass_spec() -> PatternSpec:
    """Return the Project 3 A1-A6 pattern Jordan confirmed live."""

    hits: list[PatternHit] = []

    def h(pad: int, step: int, velocity: int, duration: float,
          label: str, nudge: float = 0.0, probability: float = 1.0) -> None:
        hits.append(PatternHit(
            pad=pad,
            step=step,
            velocity=velocity,
            probability=probability,
            nudge=nudge,
            duration_steps=duration,
            label=label,
        ))

    for bar, kick_steps in enumerate((
            (0, 6, 10), (0, 6, 9, 14), (0, 6, 10, 13), (0, 6, 10, 15))):
        base = bar * 16
        for step in kick_steps:
            h(0, base + step, 116 if step == 0 else 98, 2.1, "kick")
        for step in (4, 12):
            h(1, base + step, 108, 1.45, "snare", 0.01)
        for step in (3, 11):
            h(1, base + step, 48, 1.45, "ghost snare", 0.1, 0.85)
        for step in range(0, 16, 2):
            h(2, base + step, 86 if step % 8 == 0 else 68, 0.7,
              "closed hat", 0.02 if step % 4 == 0 else 0.11)
        for step in (7, 15):
            h(2, base + step, 56, 0.7, "hat pickup", 0.14)
        h(3, base + 15, 78 + bar * 2, 1.8, "open hat", 0.04)
        if bar in (1, 2, 3):
            h(4, base + 12, 72, 1.45, "clap layer")
    h(1, 57, 72, 1.45, "fill snare", 0.03)
    h(4, 58, 88, 1.45, "fill clap")
    h(1, 60, 118, 1.45, "fill snare")
    h(4, 61, 96, 1.45, "fill clap", 0.03)
    h(1, 62, 94, 1.45, "fill snare")
    h(4, 63, 110, 1.45, "fill clap", 0.03)

    chromatic = [
        (60, 0, 108, 5.6, "bass C"),
        (55, 6, 96, 3.6, "bass G"),
        (58, 10, 102, 4.8, "bass Bb"),
        (55, 15, 88, 1.0, "bass G pickup"),
        (60, 16, 110, 5.6, "bass C"),
        (63, 22, 94, 2.8, "bass Eb"),
        (58, 26, 100, 3.8, "bass Bb"),
        (48, 31, 104, 1.0, "bass low C pickup"),
        (60, 32, 112, 5.2, "bass C"),
        (55, 38, 96, 3.4, "bass G"),
        (58, 42, 104, 4.0, "bass Bb"),
        (63, 46, 92, 1.4, "bass Eb pickup"),
        (60, 48, 112, 4.8, "bass C"),
        (58, 54, 102, 2.8, "bass Bb"),
        (55, 58, 98, 1.8, "bass G"),
        (48, 61, 114, 2.6, "bass low C resolve"),
    ]
    return PatternSpec(
        name="project3-a1-a6-beat-bass-v3",
        prompt="Project 3 A1-A6 confirmed beat plus chromatic bass lane",
        device=SP404,
        bank=0,
        bars=4,
        steps_per_bar=16,
        bpm=94.0,
        swing=56.0,
        seed=4040305,
        tags=["boom_bap", "swing", "bass", "chromatic"],
        hits=sorted(hits, key=lambda hit: (hit.step + hit.nudge, hit.pad)),
        chromatic_hits=[
            ChromaticHit(note=note, step=step, velocity=velocity,
                         duration_steps=duration, label=label)
            for note, step, velocity, duration, label in chromatic
        ],
    )


def generate_sp404_beat_bass_variation(seed: int) -> PatternSpec:
    """Generate a small SP A1-A6 beat+bass variation for live auditioning."""

    rng = random.Random(int(seed))
    hits: list[PatternHit] = []

    def add(pad: int, step: int, velocity: int, duration: float,
            label: str, nudge: float = 0.0, probability: float = 1.0) -> None:
        if not 0 <= step < 64:
            return
        hits.append(PatternHit(
            pad=pad,
            step=step,
            velocity=max(1, min(127, velocity + rng.randint(-8, 8))),
            probability=probability,
            nudge=nudge,
            duration_steps=duration,
            label=label,
        ))

    kick_templates = [
        (0, 6, 10),
        (0, 5, 10, 14),
        (0, 7, 11),
        (0, 3, 10, 13),
    ]
    for bar in range(4):
        base = bar * 16
        kicks = list(rng.choice(kick_templates))
        if bar == 3 and rng.random() < 0.7:
            kicks.append(rng.choice((12, 15)))
        for step in sorted(set(kicks)):
            add(0, base + step, 116 if step == 0 else 100, 2.0, "kick")
        for step in (4, 12):
            add(1, base + step, 106, 1.35, "snare", 0.01)
        for step in (3, 11):
            if rng.random() < 0.75:
                add(1, base + step, 48, 1.2, "ghost snare", 0.1, 0.8)
        hat_steps = (range(0, 16, 2) if rng.random() < 0.65
                     else (0, 2, 4, 7, 8, 10, 12, 15))
        for step in hat_steps:
            add(2, base + step, 84 if step % 8 == 0 else 66, 0.65,
                "closed hat", 0.02 if step % 4 == 0 else 0.11)
        if rng.random() < 0.8:
            add(3, base + 15, 78, 1.6, "open hat", 0.04)
        if bar and rng.random() < 0.7:
            add(4, base + 12, 72, 1.35, "clap layer")

    if rng.random() < 0.8:
        add(1, 57, 72, 1.2, "fill snare", 0.03)
        add(4, 58, 88, 1.2, "fill clap")
        add(1, 60, 116, 1.2, "fill snare")
        add(4, 63, 108, 1.2, "fill clap", 0.03)

    bass_shapes = [
        [60, 55, 58, 55, 60, 63, 58, 48],
        [48, 55, 60, 58, 55, 58, 63, 60],
        [60, 60, 58, 55, 48, 55, 58, 60],
    ]
    bass_notes = rng.choice(bass_shapes)
    bass_steps = [0, 6, 10, 15, 16, 22, 26, 31,
                  32, 38, 42, 46, 48, 54, 58, 61]
    chromatic: list[ChromaticHit] = []
    for idx, step in enumerate(bass_steps):
        note = bass_notes[idx % len(bass_notes)]
        dur = rng.choice((1.0, 1.8, 2.8, 3.6, 4.8))
        if step % 16 == 0:
            dur = rng.choice((4.8, 5.2, 5.6))
        chromatic.append(ChromaticHit(
            note=note,
            step=step,
            velocity=max(84, min(116, 104 + rng.randint(-10, 10))),
            duration_steps=dur,
            label=f"bass {note}",
        ))

    return PatternSpec(
        name=f"sp404-beat-bass-gen-{int(seed)}",
        prompt="Generated SP-404 A1-A6 beat plus chromatic bass variation",
        device=SP404,
        bank=0,
        bars=4,
        steps_per_bar=16,
        bpm=94.0,
        swing=56.0,
        seed=int(seed),
        tags=["boom_bap", "swing", "bass", "chromatic", "generated"],
        hits=sorted(hits, key=lambda hit: (hit.step + hit.nudge, hit.pad)),
        chromatic_hits=chromatic,
    )
