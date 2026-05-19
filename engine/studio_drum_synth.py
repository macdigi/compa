"""Studio Drum Synth state and kit helpers."""
from __future__ import annotations

from session.session import Session
from session.track import InstrumentRef, Track, TrackTarget, TrackType
from engine.push2driver.palette import track_color_index


DRUM_SYNTH_PAD_COUNT = 16
DRUM_SYNTH_KITS = ("808", "909")
DRUM_VOICE_TYPES = {
    "kick",
    "snare",
    "hat_closed",
    "hat_open",
    "clap",
    "rim",
    "tom",
    "cowbell",
    "clave",
    "maraca",
    "conga",
    "perc",
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def normalized_voice_spec(spec: dict | None, idx: int) -> dict:
    spec = dict(spec or {})
    voice_type = str(spec.get("voice_type") or "perc")
    if voice_type not in DRUM_VOICE_TYPES:
        voice_type = "perc"
    name = str(spec.get("name") or f"Voice {idx + 1}")
    return {
        "name": name,
        "voice_type": voice_type,
        "decay": _clamp(spec.get("decay", 0.35), 0.03, 1.8),
        "tone": _clamp(spec.get("tone", 0.5), 0.0, 1.0),
        "snap": _clamp(spec.get("snap", 0.5), 0.0, 1.0),
        "gain": _clamp(spec.get("gain", 0.9), 0.0, 2.0),
        "pan": _clamp(spec.get("pan", 0.0), -1.0, 1.0),
        "tune": max(-24, min(24, int(spec.get("tune", 0)))),
        "choke_group": max(0, min(8, int(spec.get("choke_group", 0)))),
    }


def _voice(name: str, voice_type: str, *, decay: float, tone: float,
           snap: float, gain: float = 0.9, pan: float = 0.0,
           tune: int = 0, choke_group: int = 0) -> dict:
    return normalized_voice_spec({
        "name": name,
        "voice_type": voice_type,
        "decay": decay,
        "tone": tone,
        "snap": snap,
        "gain": gain,
        "pan": pan,
        "tune": tune,
        "choke_group": choke_group,
    }, 0)


def kit_voice_specs(kit: str = "808") -> list[dict]:
    kit = kit if kit in DRUM_SYNTH_KITS else "808"
    if kit == "909":
        voices = [
            _voice("909 Kick", "kick", decay=0.42, tone=0.58, snap=0.68, gain=1.0),
            _voice("909 Snare", "snare", decay=0.32, tone=0.64, snap=0.72, gain=0.88),
            _voice("909 CH", "hat_closed", decay=0.08, tone=0.72, snap=0.78, gain=0.58, choke_group=1),
            _voice("909 OH", "hat_open", decay=0.46, tone=0.76, snap=0.7, gain=0.62, choke_group=1),
            _voice("909 Clap", "clap", decay=0.24, tone=0.68, snap=0.82, gain=0.82),
            _voice("Rim", "rim", decay=0.08, tone=0.76, snap=0.55, gain=0.58),
            _voice("Low Tom", "tom", decay=0.36, tone=0.26, snap=0.38, gain=0.72),
            _voice("Mid Tom", "tom", decay=0.28, tone=0.46, snap=0.38, gain=0.68),
            _voice("Hi Tom", "tom", decay=0.22, tone=0.66, snap=0.4, gain=0.62),
            _voice("Ride Bell", "cowbell", decay=0.34, tone=0.82, snap=0.38, gain=0.46),
            _voice("Clave", "clave", decay=0.08, tone=0.72, snap=0.5, gain=0.52),
            _voice("Shaker", "maraca", decay=0.09, tone=0.8, snap=0.64, gain=0.42),
            _voice("Low Conga", "conga", decay=0.34, tone=0.32, snap=0.34, gain=0.64),
            _voice("Mid Conga", "conga", decay=0.28, tone=0.52, snap=0.36, gain=0.58),
            _voice("Zap", "perc", decay=0.16, tone=0.78, snap=0.9, gain=0.5),
            _voice("Long Kick", "kick", decay=0.72, tone=0.42, snap=0.45, gain=0.9),
        ]
    else:
        voices = [
            _voice("808 Kick", "kick", decay=0.72, tone=0.38, snap=0.35, gain=1.0),
            _voice("808 Snare", "snare", decay=0.42, tone=0.46, snap=0.54, gain=0.82),
            _voice("808 CH", "hat_closed", decay=0.07, tone=0.66, snap=0.62, gain=0.5, choke_group=1),
            _voice("808 OH", "hat_open", decay=0.64, tone=0.7, snap=0.48, gain=0.55, choke_group=1),
            _voice("808 Clap", "clap", decay=0.32, tone=0.56, snap=0.65, gain=0.74),
            _voice("Rim", "rim", decay=0.07, tone=0.58, snap=0.42, gain=0.5),
            _voice("Low Tom", "tom", decay=0.44, tone=0.18, snap=0.25, gain=0.68),
            _voice("Mid Tom", "tom", decay=0.36, tone=0.38, snap=0.25, gain=0.62),
            _voice("Hi Tom", "tom", decay=0.28, tone=0.58, snap=0.28, gain=0.56),
            _voice("Cowbell", "cowbell", decay=0.22, tone=0.56, snap=0.38, gain=0.48),
            _voice("Clave", "clave", decay=0.08, tone=0.62, snap=0.42, gain=0.46),
            _voice("Maraca", "maraca", decay=0.12, tone=0.74, snap=0.46, gain=0.38),
            _voice("Low Conga", "conga", decay=0.44, tone=0.24, snap=0.28, gain=0.56),
            _voice("Mid Conga", "conga", decay=0.34, tone=0.48, snap=0.3, gain=0.52),
            _voice("Zap", "perc", decay=0.18, tone=0.74, snap=0.82, gain=0.48),
            _voice("Boom", "kick", decay=1.05, tone=0.28, snap=0.22, gain=0.84),
        ]
    return [
        normalized_voice_spec(voice, idx)
        for idx, voice in enumerate(voices[:DRUM_SYNTH_PAD_COUNT])
    ]


def drum_synth_track_index(session: Session) -> int | None:
    for idx, track in enumerate(session.tracks):
        target_key = getattr(getattr(track, "target", None), "key", "")
        if target_key == "internal.drum_synth":
            return idx
    for idx, track in enumerate(session.tracks):
        if (track.type == TrackType.MIDI
                and track.instrument is not None
                and track.instrument.kind == "drum_synth"):
            return idx
    return None


def ensure_drum_synth_track(session: Session) -> int:
    idx = drum_synth_track_index(session)
    if idx is not None:
        return idx
    idx = len(session.tracks)
    session.tracks.append(Track(
        id=idx,
        name="Drum Synth",
        type=TrackType.MIDI,
        color=track_color_index(idx),
        instrument=InstrumentRef(
            kind="drum_synth",
            name="Drum Synth",
            params={"kit": "808", "voices": kit_voice_specs("808")},
        ),
        target=TrackTarget("internal.drum_synth", "Drum Synth"),
        clips=[None] * len(session.scenes),
    ))
    return idx


def drum_synth_voice_specs(session: Session, track_idx: int | None = None) -> list[dict]:
    idx = drum_synth_track_index(session) if track_idx is None else track_idx
    if idx is None or not (0 <= idx < len(session.tracks)):
        return []
    track = session.tracks[idx]
    if track.instrument is None:
        return []
    raw = track.instrument.params.get("voices")
    if not isinstance(raw, list):
        raw = kit_voice_specs(str(track.instrument.params.get("kit") or "808"))
    voices = [
        normalized_voice_spec(raw[idx] if idx < len(raw) else None, idx)
        for idx in range(DRUM_SYNTH_PAD_COUNT)
    ]
    track.instrument.params["voices"] = voices
    return voices


def set_drum_synth_kit(session: Session, track_idx: int, kit: str) -> list[dict]:
    kit = kit if kit in DRUM_SYNTH_KITS else "808"
    track = session.tracks[track_idx]
    if track.instrument is None:
        raise ValueError("drum synth track has no instrument")
    voices = kit_voice_specs(kit)
    track.instrument.params["kit"] = kit
    track.instrument.params["voices"] = voices
    return voices


def adjust_voice_param(
    session: Session,
    track_idx: int,
    pad_idx: int,
    field: str,
    delta: float,
) -> dict:
    if not (0 <= pad_idx < DRUM_SYNTH_PAD_COUNT):
        raise IndexError("drum synth pad out of range")
    voices = drum_synth_voice_specs(session, track_idx)
    spec = normalized_voice_spec(voices[pad_idx], pad_idx)
    if field == "tone":
        spec["tone"] = _clamp(spec["tone"] + delta, 0.0, 1.0)
    elif field == "decay":
        spec["decay"] = _clamp(spec["decay"] + delta, 0.03, 1.8)
    elif field == "snap":
        spec["snap"] = _clamp(spec["snap"] + delta, 0.0, 1.0)
    elif field == "gain":
        spec["gain"] = _clamp(spec["gain"] + delta, 0.0, 2.0)
    else:
        return spec
    voices[pad_idx] = spec
    session.tracks[track_idx].instrument.params["voices"] = voices
    return spec


def voice_display_name(spec: dict | None, idx: int) -> str:
    return normalized_voice_spec(spec, idx)["name"]
