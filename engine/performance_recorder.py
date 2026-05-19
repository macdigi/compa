"""Lightweight performance event recorder.

The recorder writes local JSONL takes for later style analysis. It logs
musical gestures, not audio: notes, CC moves, transport changes, generated
clips, and UI actions with timestamps plus optional beat/BPM context.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional


@dataclass
class PerformanceEvent:
    """One timestamped performance event."""

    timestamp: float
    event_type: str
    source: str = ""
    device: str = ""
    beat: Optional[float] = None
    bpm: Optional[float] = None
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


class PerformanceRecorder:
    """Append-only local recorder for performance gestures.

    JSONL keeps writes cheap and recoverable: if power dies mid-line, every
    earlier line is still valid. CC events are lightly throttled per target
    so high-rate knobs do not create huge logs.
    """

    def __init__(
        self,
        directory: str,
        *,
        clock_fn: Optional[Callable[[], float]] = None,
        bpm_fn: Optional[Callable[[], float]] = None,
        cc_min_interval: float = 0.04,
    ) -> None:
        self.directory = directory
        self.clock_fn = clock_fn
        self.bpm_fn = bpm_fn
        self.cc_min_interval = max(0.0, float(cc_min_interval))
        self.path: str = ""
        self._fh = None
        self._enabled = False
        self._last_cc: dict[tuple, tuple[float, int]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled and self._fh is not None

    def start(self, label: str = "take") -> str:
        os.makedirs(self.directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        safe = "".join(c for c in label if c.isalnum() or c in "._-").strip()
        if not safe:
            safe = "take"
        self.path = os.path.join(self.directory, f"{stamp}_{safe}.jsonl")
        self._fh = open(self.path, "a", encoding="utf-8", buffering=1)
        self._enabled = True
        self.record("recorder.start", source="compa", payload={"path": self.path})
        return self.path

    def stop(self) -> None:
        if self._fh is None:
            self._enabled = False
            return
        try:
            self.record("recorder.stop", source="compa", payload={"path": self.path})
        finally:
            with self._lock:
                try:
                    self._fh.close()
                finally:
                    self._fh = None
                    self._enabled = False

    def record(
        self,
        event_type: str,
        *,
        source: str = "",
        device: str = "",
        payload: Optional[dict] = None,
        beat: Optional[float] = None,
        bpm: Optional[float] = None,
    ) -> None:
        if not self.enabled:
            return
        payload = dict(payload or {})
        if beat is None and self.clock_fn is not None:
            try:
                beat = float(self.clock_fn())
            except Exception:
                beat = None
        if bpm is None and self.bpm_fn is not None:
            try:
                bpm = float(self.bpm_fn())
            except Exception:
                bpm = None
        evt = PerformanceEvent(
            timestamp=time.time(),
            event_type=str(event_type),
            source=str(source),
            device=str(device),
            beat=beat,
            bpm=bpm,
            payload=payload,
        )
        self._write(evt)

    def record_note(
        self,
        *,
        source: str,
        device: str,
        note: int,
        velocity: int,
        channel: int = 0,
        payload: Optional[dict] = None,
    ) -> None:
        data = dict(payload or {})
        data.update({
            "note": int(note),
            "velocity": int(velocity),
            "channel": int(channel),
        })
        self.record("note", source=source, device=device, payload=data)

    def record_cc(
        self,
        *,
        source: str,
        device: str,
        cc: int,
        value: int,
        channel: int = 0,
        payload: Optional[dict] = None,
    ) -> None:
        now = time.monotonic()
        key = (source, device, int(channel), int(cc))
        last = self._last_cc.get(key)
        if last is not None:
            last_ts, last_value = last
            if int(value) == last_value:
                return
            if now - last_ts < self.cc_min_interval:
                return
        self._last_cc[key] = (now, int(value))
        data = dict(payload or {})
        data.update({"cc": int(cc), "value": int(value), "channel": int(channel)})
        self.record("cc", source=source, device=device, payload=data)

    def _write(self, event: PerformanceEvent) -> None:
        with self._lock:
            if self._fh is None:
                return
            try:
                self._fh.write(json.dumps(
                    event.to_dict(), separators=(",", ":")))
                self._fh.write("\n")
            except Exception:
                # Recorder failures must never interrupt live performance.
                pass


def read_events(path: str) -> list[PerformanceEvent]:
    """Read a JSONL take, skipping malformed partial lines."""

    events: list[PerformanceEvent] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(PerformanceEvent(
                    timestamp=float(data.get("timestamp", 0.0)),
                    event_type=str(data.get("event_type", "")),
                    source=str(data.get("source", "")),
                    device=str(data.get("device", "")),
                    beat=(float(data["beat"]) if "beat" in data else None),
                    bpm=(float(data["bpm"]) if "bpm" in data else None),
                    payload=dict(data.get("payload", {})),
                ))
            except Exception:
                continue
    return events
