"""Studio Mixer / Router helpers.

This keeps routing state lightweight: tracks already own volume, pan, mute,
solo, arm, instrument, and TrackTarget.  The router module adds summaries and
safe target choices that the touch and Push 2 surfaces can share.
"""
from __future__ import annotations

from session.session import Session
from session.track import Track, TrackTarget, TrackType
from engine.studio_targets import (
    TargetCapability,
    availability_label,
    capability_for,
    known_targets,
    target_for_track,
)


SP404_BEAT_BASS_TARGET = "external.sp404.a1_a6_beat_bass"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _target(key: str) -> TargetCapability:
    return capability_for(key)


def default_target_params(key: str) -> dict:
    if key == SP404_BEAT_BASS_TARGET:
        return {
            "project": 3,
            "bank": "A",
            "drum_pads": "A1-A5",
            "chromatic_pad": "A6",
        }
    return {}


def target_choices_for_track(track: Track) -> tuple[TargetCapability, ...]:
    current_key = target_for_track(track).key
    keys: list[str] = []

    if track.type == TrackType.AUDIO:
        keys.extend(("internal.audio_track", "network.compa_peer"))
    else:
        kind = track.instrument.kind if track.instrument else ""
        preset = (track.instrument.params or {}).get("preset", "") if track.instrument else ""
        if kind == "drum_rack":
            keys.append("internal.sample_drum_rack")
        elif kind == "drum_synth":
            keys.append("internal.drum_synth")
        elif kind == "synth_voice":
            keys.append("internal.mono_synth" if preset == "bass"
                        else "internal.poly_synth")
        else:
            keys.append("internal.midi")
        keys.extend((
            "internal.midi",
            "external.sp404.a1_a6_beat_bass",
            "external.sp404.pad_bank",
            "external.p6.pads",
            "network.compa_peer",
        ))

    if current_key not in keys:
        keys.insert(0, current_key)

    deduped: list[str] = []
    for key in keys:
        if key not in deduped:
            deduped.append(key)
    return tuple(_target(key) for key in deduped)


def route_track_to_target(session: Session, track_idx: int, key: str) -> TrackTarget:
    if not (0 <= track_idx < len(session.tracks)):
        raise IndexError("track index out of range")
    capability = capability_for(key)
    target = TrackTarget(capability.key, capability.label,
                         default_target_params(capability.key))
    session.tracks[track_idx].target = target
    return target


def adjust_track_mix(session: Session, track_idx: int, field: str,
                     delta: float = 0.0) -> Track:
    if not (0 <= track_idx < len(session.tracks)):
        raise IndexError("track index out of range")
    track = session.tracks[track_idx]
    if field == "volume":
        track.volume = _clamp(track.volume + delta, 0.0, 1.0)
    elif field == "pan":
        track.pan = _clamp(track.pan + delta, -1.0, 1.0)
    elif field == "mute":
        track.mute = not track.mute
    elif field == "solo":
        track.solo = not track.solo
    elif field == "arm":
        track.arm = not track.arm
    return track


def clear_solos(session: Session) -> None:
    for track in session.tracks:
        track.solo = False


def track_route_summary(
    track: Track,
    idx: int,
    *,
    pi_generation: int | None = None,
    studio_audio_enabled: bool = True,
) -> dict:
    target = target_for_track(track)
    capability = capability_for(target)
    instrument = track.instrument
    clip_count = sum(1 for clip in track.clips if clip is not None)
    return {
        "index": idx,
        "name": track.name,
        "type": track.type.value,
        "instrument": instrument.name if instrument else "",
        "instrument_kind": instrument.kind if instrument else "",
        "target_key": target.key,
        "target_label": target.label or capability.label,
        "category": capability.category,
        "available": availability_label(
            capability,
            pi_generation=pi_generation,
            studio_audio_enabled=studio_audio_enabled,
        ),
        "features": capability.feature_labels(),
        "clip_count": clip_count,
        "volume": track.volume,
        "pan": track.pan,
        "mute": track.mute,
        "solo": track.solo,
        "arm": track.arm,
    }


def session_route_summary(
    session: Session,
    *,
    pi_generation: int | None = None,
    studio_audio_enabled: bool = True,
) -> list[dict]:
    return [
        track_route_summary(
            track,
            idx,
            pi_generation=pi_generation,
            studio_audio_enabled=studio_audio_enabled,
        )
        for idx, track in enumerate(session.tracks)
    ]
