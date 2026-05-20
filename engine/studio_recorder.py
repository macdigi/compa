"""Compa Studio recorder helpers."""
from __future__ import annotations

from session.session import Session
from session.track import TrackType


def audio_track_indices(session: Session) -> list[int]:
    return [
        idx for idx, track in enumerate(session.tracks)
        if track.type == TrackType.AUDIO
    ]


def selected_audio_track_index(
    session: Session,
    preferred_idx: int | None = None,
) -> int | None:
    indices = audio_track_indices(session)
    if not indices:
        return None
    if preferred_idx in indices:
        return preferred_idx
    return indices[0]


def next_empty_scene_index(session: Session, track_idx: int) -> int:
    if not (0 <= track_idx < len(session.tracks)):
        return 0
    for idx, clip in enumerate(session.tracks[track_idx].clips):
        if clip is None:
            return idx
    return max(0, len(session.tracks[track_idx].clips) - 1)


def recorder_status(recorder) -> dict:
    if recorder is None:
        return {
            "available": False,
            "monitoring": False,
            "recording": False,
            "duration": 0.0,
            "device": "",
            "recall_seconds": 0.0,
            "recall_capacity": 0,
            "pre_roll": 0.0,
            "peak_l": 0.0,
            "peak_r": 0.0,
            "overruns": 0,
            "underruns": 0,
        }
    peak = getattr(recorder, "peak_levels", (0.0, 0.0))
    return {
        "available": bool(getattr(recorder, "available", False)),
        "monitoring": bool(getattr(recorder, "_monitoring", False)),
        "recording": bool(getattr(recorder, "is_recording", False)),
        "duration": float(getattr(recorder, "duration", 0.0) or 0.0),
        "device": str(getattr(recorder, "device_name", "") or ""),
        "recall_seconds": float(
            getattr(recorder, "recall_seconds_available", 0.0) or 0.0),
        "recall_capacity": int(getattr(recorder, "recall_buffer_seconds", 0) or 0),
        "pre_roll": float(getattr(recorder, "record_pre_roll_seconds", 0.0) or 0.0),
        "peak_l": float(peak[0]) if len(peak) > 0 else 0.0,
        "peak_r": float(peak[1]) if len(peak) > 1 else 0.0,
        "overruns": int(getattr(recorder, "input_overruns", 0) or 0),
        "underruns": int(getattr(recorder, "input_underruns", 0) or 0),
    }


def recent_recordings(recorder, limit: int = 4) -> list[dict]:
    if recorder is None or not hasattr(recorder, "list_recordings"):
        return []
    try:
        return list(recorder.list_recordings())[:max(0, int(limit))]
    except Exception:
        return []


def active_clip_recordings(engine) -> list[dict]:
    raw = getattr(engine, "_recordings", {}) if engine is not None else {}
    out: list[dict] = []
    for (track, scene), rec in list(raw.items()):
        out.append({
            "track": int(track),
            "scene": int(scene),
            "start_beat": float(rec.get("start_beat", 0.0)),
            "length_beats": float(rec.get("length_beats", 0.0)),
            "bpm": float(rec.get("bpm", 0.0)),
        })
    return out


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    mins = int(seconds // 60)
    secs = seconds - mins * 60
    return f"{mins:02d}:{secs:04.1f}"
