"""Compa Studio synth track helpers."""
from __future__ import annotations

from session.session import Session
from session.track import InstrumentRef, Track, TrackTarget, TrackType
from engine.clip_engine.instruments.synth_voice import (
    SynthParams,
    preset_bass,
    preset_lead,
    preset_pad,
)
from engine.push2driver.palette import track_color_index


SYNTH_PRESETS = ("bass", "lead", "pad")
SYNTH_WAVEFORMS = ("saw", "square", "sine")
SYNTH_NOTE_NAMES = (
    "C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"
)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _preset_params(preset: str) -> SynthParams:
    if preset == "bass":
        return preset_bass()
    if preset == "pad":
        return preset_pad()
    return preset_lead()


def synth_track_indices(session: Session) -> list[int]:
    return [
        idx for idx, track in enumerate(session.tracks)
        if (track.type == TrackType.MIDI
            and track.instrument is not None
            and track.instrument.kind == "synth_voice")
    ]


def synth_track_index(session: Session, preferred_idx: int | None = None) -> int | None:
    indices = synth_track_indices(session)
    if not indices:
        return None
    if preferred_idx in indices:
        return preferred_idx
    return indices[0]


def ensure_synth_track(session: Session, preset: str = "bass") -> int:
    idx = synth_track_index(session)
    if idx is not None:
        return idx
    preset = preset if preset in SYNTH_PRESETS else "bass"
    idx = len(session.tracks)
    target = (
        TrackTarget("internal.mono_synth", "Mono Synth")
        if preset == "bass"
        else TrackTarget("internal.poly_synth", "Poly Synth")
    )
    session.tracks.append(Track(
        id=idx,
        name="Mono Synth" if preset == "bass" else "Poly Synth",
        type=TrackType.MIDI,
        color=track_color_index(idx),
        instrument=InstrumentRef(
            kind="synth_voice",
            name=f"{preset.title()} Synth",
            params={"preset": preset, **_preset_params(preset).to_dict()},
        ),
        target=target,
        clips=[None] * len(session.scenes),
    ))
    return idx


def synth_track_role(track: Track) -> str:
    target_key = getattr(getattr(track, "target", None), "key", "")
    preset = ""
    if track.instrument is not None:
        preset = str(track.instrument.params.get("preset") or "")
    if target_key == "internal.mono_synth" or preset == "bass":
        return "mono"
    return "poly"


def synth_params(session: Session, track_idx: int | None = None) -> dict:
    idx = synth_track_index(session, track_idx)
    if idx is None:
        return _preset_params("bass").to_dict()
    track = session.tracks[idx]
    if track.instrument is None:
        return _preset_params("bass").to_dict()
    preset = str(track.instrument.params.get("preset") or "lead")
    base = _preset_params(preset).to_dict()
    for key, value in list(track.instrument.params.items()):
        if key in SynthParams.__dataclass_fields__:
            base[key] = value
    normalized = normalize_synth_params(base)
    track.instrument.params.update(normalized)
    track.instrument.params["preset"] = preset if preset in SYNTH_PRESETS else "lead"
    return normalized


def normalize_synth_params(params: dict) -> dict:
    waveform = str(params.get("waveform") or "saw")
    if waveform not in SYNTH_WAVEFORMS:
        waveform = "saw"
    return {
        "waveform": waveform,
        "cutoff_hz": _clamp(params.get("cutoff_hz", 2400.0), 80.0, 12000.0),
        "cutoff_env": _clamp(params.get("cutoff_env", 0.5), 0.0, 1.0),
        "resonance": _clamp(params.get("resonance", 0.0), 0.0, 1.0),
        "attack": _clamp(params.get("attack", 0.01), 0.001, 1.2),
        "decay": _clamp(params.get("decay", 0.2), 0.01, 2.0),
        "sustain": _clamp(params.get("sustain", 0.7), 0.0, 1.0),
        "release": _clamp(params.get("release", 0.25), 0.01, 2.5),
        "glide": _clamp(params.get("glide", 0.0), 0.0, 1.0),
        "detune_cents": max(-1200, min(1200, int(params.get("detune_cents", 0)))),
        "gain": _clamp(params.get("gain", 0.6), 0.0, 1.5),
    }


def set_synth_preset(session: Session, track_idx: int, preset: str) -> dict:
    preset = preset if preset in SYNTH_PRESETS else "lead"
    track = session.tracks[track_idx]
    if track.instrument is None:
        raise ValueError("synth track has no instrument")
    params = _preset_params(preset).to_dict()
    track.instrument.params.clear()
    track.instrument.params.update({"preset": preset, **params})
    track.instrument.name = f"{preset.title()} Synth"
    if preset == "bass":
        track.target = TrackTarget("internal.mono_synth", "Mono Synth")
    elif getattr(track.target, "key", "") == "internal.mono_synth":
        track.target = TrackTarget("internal.poly_synth", "Poly Synth")
    return params


def cycle_synth_waveform(session: Session, track_idx: int) -> str:
    params = synth_params(session, track_idx)
    waveform = params["waveform"]
    next_wave = SYNTH_WAVEFORMS[
        (SYNTH_WAVEFORMS.index(waveform) + 1) % len(SYNTH_WAVEFORMS)
    ]
    track = session.tracks[track_idx]
    track.instrument.params["waveform"] = next_wave
    return next_wave


def adjust_synth_param(
    session: Session,
    track_idx: int,
    field: str,
    delta: float,
) -> dict:
    params = synth_params(session, track_idx)
    if field == "cutoff_hz":
        params[field] = _clamp(params[field] + delta, 80.0, 12000.0)
    elif field in ("cutoff_env", "sustain", "gain"):
        params[field] = _clamp(params[field] + delta, 0.0, 1.5 if field == "gain" else 1.0)
    elif field in ("attack", "decay", "release", "glide"):
        hi = 2.5 if field == "release" else (2.0 if field == "decay" else 1.2)
        params[field] = _clamp(params[field] + delta, 0.001, hi)
    else:
        return params
    session.tracks[track_idx].instrument.params.update(params)
    return params


def note_name(pitch: int) -> str:
    octave = (int(pitch) // 12) - 1
    return f"{SYNTH_NOTE_NAMES[int(pitch) % 12]}{octave}"
