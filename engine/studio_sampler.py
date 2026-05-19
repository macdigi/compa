"""Compa Studio sampler state helpers.

The Sampler MVP reuses the existing clip-engine DrumRack.  The persisted
assignment state lives on the drum-rack track's InstrumentRef params, while the
audio engine turns those lightweight pad specs into live DrumPad objects.
"""
from __future__ import annotations

import os

from session.session import Session
from session.track import Track, TrackType


SAMPLER_PAD_COUNT = 16
SUPPORTED_SAMPLE_EXTENSIONS = (".wav", ".aif", ".aiff", ".flac", ".ogg")
DEFAULT_PAD_NAMES = (
    "Kick",
    "Snare",
    "Closed Hat",
    "Open Hat",
    "Clap",
    "Rim",
    "Low Tom",
    "Mid Tom",
    "Hi Tom",
    "Kick 2",
    "Snare 2",
    "Shaker",
    "Open Hat 2",
    "Clap 2",
    "Rim 2",
    "Kick Room",
)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_sample_roots() -> tuple[str, ...]:
    root = _repo_root()
    return (
        os.path.join(root, "samples", "starter", "sp404_mono_1s"),
        os.path.join(root, "samples", "starter", "sr16"),
        os.path.expanduser("~/.compa/samples"),
        "/mnt/samples",
    )


def list_sampler_samples(
    roots: tuple[str, ...] | None = None,
    *,
    limit: int = 256,
) -> list[str]:
    samples: list[str] = []
    seen: set[str] = set()
    for root in roots or default_sample_roots():
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in sorted(filenames):
                if not filename.lower().endswith(SUPPORTED_SAMPLE_EXTENSIONS):
                    continue
                path = os.path.abspath(os.path.join(dirpath, filename))
                if path in seen:
                    continue
                seen.add(path)
                samples.append(path)
                if len(samples) >= limit:
                    return samples
    return samples


def sample_label(path: str) -> str:
    if not path:
        return ""
    return os.path.splitext(os.path.basename(path))[0].replace("_", " ")


def normalized_pad_spec(spec: dict | None, idx: int) -> dict:
    spec = dict(spec or {})
    sample_path = str(spec.get("sample_path") or "")
    name = str(spec.get("name") or sample_label(sample_path)
               or DEFAULT_PAD_NAMES[idx % len(DEFAULT_PAD_NAMES)])
    use_default = bool(spec.get("use_default", not sample_path))
    return {
        "name": name,
        "sample_path": sample_path,
        "use_default": use_default,
        "gain": max(0.0, min(2.0, float(spec.get("gain", 1.0)))),
        "pan": max(-1.0, min(1.0, float(spec.get("pan", 0.0)))),
        "tune": max(-24, min(24, int(spec.get("tune", 0)))),
        "choke_group": max(0, min(8, int(spec.get("choke_group", 0)))),
    }


def sampler_track_index(session: Session) -> int | None:
    for idx, track in enumerate(session.tracks):
        target_key = getattr(getattr(track, "target", None), "key", "")
        if target_key == "internal.sample_drum_rack":
            return idx
    for idx, track in enumerate(session.tracks):
        if (track.type == TrackType.MIDI
                and track.instrument is not None
                and track.instrument.kind == "drum_rack"):
            return idx
    return None


def ensure_sampler_pad_specs(track: Track) -> list[dict]:
    if track.instrument is None:
        raise ValueError("sampler track has no instrument")
    params = track.instrument.params
    raw = params.get("pads")
    if not isinstance(raw, list):
        raw = []
    pads = [
        normalized_pad_spec(raw[idx] if idx < len(raw) else None, idx)
        for idx in range(SAMPLER_PAD_COUNT)
    ]
    params["pads"] = pads
    return pads


def sampler_pad_specs(session: Session, track_idx: int | None = None) -> list[dict]:
    idx = sampler_track_index(session) if track_idx is None else track_idx
    if idx is None or not (0 <= idx < len(session.tracks)):
        return []
    return ensure_sampler_pad_specs(session.tracks[idx])


def pad_display_name(spec: dict | None, idx: int) -> str:
    spec = normalized_pad_spec(spec, idx)
    if spec["sample_path"]:
        return sample_label(spec["sample_path"])
    if spec["use_default"]:
        return spec["name"]
    return "Empty"


def assign_sample_to_pad(
    session: Session,
    track_idx: int,
    pad_idx: int,
    sample_path: str,
) -> dict:
    if not (0 <= track_idx < len(session.tracks)):
        raise IndexError("sampler track out of range")
    if not (0 <= pad_idx < SAMPLER_PAD_COUNT):
        raise IndexError("sampler pad out of range")
    pads = ensure_sampler_pad_specs(session.tracks[track_idx])
    spec = normalized_pad_spec(pads[pad_idx], pad_idx)
    spec.update({
        "name": sample_label(sample_path) or f"Pad {pad_idx + 1}",
        "sample_path": sample_path,
        "use_default": False,
    })
    pads[pad_idx] = spec
    return spec


def clear_sampler_pad(session: Session, track_idx: int, pad_idx: int) -> dict:
    if not (0 <= track_idx < len(session.tracks)):
        raise IndexError("sampler track out of range")
    if not (0 <= pad_idx < SAMPLER_PAD_COUNT):
        raise IndexError("sampler pad out of range")
    pads = ensure_sampler_pad_specs(session.tracks[track_idx])
    spec = normalized_pad_spec(pads[pad_idx], pad_idx)
    spec.update({
        "name": f"Pad {pad_idx + 1}",
        "sample_path": "",
        "use_default": False,
    })
    pads[pad_idx] = spec
    return spec


def load_starter_kit(session: Session, track_idx: int) -> int:
    roots = default_sample_roots()
    starter = roots[0]
    assignments = (
        (0, "Kick.wav"),
        (1, "Snare.wav"),
        (2, "ClosedHat.wav"),
        (3, "OpenHat.wav"),
        (4, "Claps.wav"),
        (5, "SubBass_C2.wav"),
    )
    count = 0
    for pad_idx, filename in assignments:
        path = os.path.join(starter, filename)
        if os.path.exists(path):
            assign_sample_to_pad(session, track_idx, pad_idx, path)
            count += 1
    return count
