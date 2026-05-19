"""Pattern generation helpers for Compa performer workflows.

This module keeps the first "AI performer" slice intentionally local and
deterministic: a text prompt becomes a PatternSpec, and that spec can be
installed as a native Compa MidiClip, written into the Push 2 overlay
step-grid persistence format, or exported as a standard MIDI file.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from session.clip import LaunchQuantize, MidiClip
from session.note import Note


SP404 = "SP-404MKII"
P6 = "P-6"


@dataclass
class PatternHit:
    """One drum/pad hit in a generated pattern.

    pad is zero-indexed within the target device's current bank.
    step is zero-indexed within the pattern grid.
    nudge is measured in fractions of one step. Positive values are late.
    """

    pad: int
    step: int
    velocity: int = 100
    probability: float = 1.0
    nudge: float = 0.0
    duration_steps: float = 0.75
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PatternHit":
        return cls(
            pad=int(data.get("pad", 0)),
            step=int(data.get("step", 0)),
            velocity=int(data.get("velocity", 100)),
            probability=float(data.get("probability", 1.0)),
            nudge=float(data.get("nudge", 0.0)),
            duration_steps=float(data.get("duration_steps", 0.75)),
            label=str(data.get("label", "")),
        )


@dataclass
class PatternSpec:
    """Portable pattern representation used by Compa performer tools."""

    name: str
    prompt: str
    device: str = SP404
    bank: int = 0
    bars: int = 4
    steps_per_bar: int = 16
    bpm: float = 98.0
    swing: float = 0.0
    seed: int = 0
    tags: list[str] = field(default_factory=list)
    hits: list[PatternHit] = field(default_factory=list)

    @property
    def total_steps(self) -> int:
        return self.bars * self.steps_per_bar

    @property
    def step_beats(self) -> float:
        return 4.0 / float(self.steps_per_bar)

    @property
    def length_beats(self) -> float:
        return self.bars * 4.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["hits"] = [h.to_dict() for h in self.hits]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PatternSpec":
        hits = [PatternHit.from_dict(h) for h in data.get("hits", [])]
        return cls(
            name=str(data.get("name", "Generated")),
            prompt=str(data.get("prompt", "")),
            device=normalize_device(str(data.get("device", SP404))),
            bank=int(data.get("bank", 0)),
            bars=int(data.get("bars", 4)),
            steps_per_bar=int(data.get("steps_per_bar", 16)),
            bpm=float(data.get("bpm", 98.0)),
            swing=float(data.get("swing", 0.0)),
            seed=int(data.get("seed", 0)),
            tags=[str(t) for t in data.get("tags", [])],
            hits=hits,
        )


def normalize_device(device: str) -> str:
    d = (device or "").strip().lower().replace("_", "-")
    if "sp" in d or "404" in d:
        return SP404
    if "p6" in d or "p-6" in d:
        return P6
    return device or SP404


def bank_to_index(bank: str | int) -> int:
    if isinstance(bank, int):
        return max(0, bank)
    text = str(bank).strip()
    if not text:
        return 0
    if text.isdigit():
        return max(0, int(text) - 1)
    ch = text.upper()[0]
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A")
    return 0


def bank_name(index: int) -> str:
    if index < 0:
        index = 0
    return chr(ord("A") + index)


def device_pad_count(device: str) -> int:
    return 16 if normalize_device(device) == SP404 else 6


def _stable_seed(*parts: object) -> int:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return int.from_bytes(h.digest()[:8], "big")


def _prompt_tags(prompt: str) -> list[str]:
    text = prompt.lower()
    tags = []
    checks = {
        "boom_bap": ("boom bap", "boombap", "dusty", "hip hop", "hip-hop"),
        "house": ("house", "four on the floor", "4 on the floor"),
        "techno": ("techno", "electro"),
        "breakbeat": ("break", "breakbeat", "jungle", "amen"),
        "half_time": ("half time", "halftime", "trap"),
        "busy": ("busy", "dense", "many", "fast"),
        "sparse": ("sparse", "minimal", "simple", "few"),
        "fill": ("fill", "turnaround", "transition"),
        "swing": ("swing", "shuffle", "dilla", "late"),
        "weird": ("weird", "broken", "glitch", "off-kilter"),
    }
    for tag, needles in checks.items():
        if any(n in text for n in needles):
            tags.append(tag)
    if not tags:
        tags.append("boom_bap")
    return tags


def _choose_roles(device: str, available_pads: Optional[Iterable[int]]) -> dict[str, int]:
    count = device_pad_count(device)
    if available_pads is None:
        pads = list(range(count))
    else:
        pads = sorted({p for p in available_pads if 0 <= int(p) < count})
        if not pads:
            pads = list(range(count))
    while len(pads) < 6:
        pads.append(pads[-1] if pads else 0)
    return {
        "kick": pads[0],
        "snare": pads[1],
        "hat": pads[2],
        "open_hat": pads[3],
        "perc": pads[4],
        "extra": pads[5],
    }


def _density(tags: list[str]) -> float:
    density = 0.55
    if "busy" in tags:
        density += 0.22
    if "sparse" in tags:
        density -= 0.25
    if "weird" in tags:
        density += 0.08
    return max(0.15, min(0.95, density))


def _vel(rng: random.Random, base: int, spread: int = 10) -> int:
    return max(1, min(127, base + rng.randint(-spread, spread)))


def _late_nudge(step: int, tags: list[str], rng: random.Random) -> float:
    if "swing" not in tags and "boom_bap" not in tags:
        return 0.0
    if step % 2 == 0:
        return rng.uniform(-0.015, 0.015)
    return rng.uniform(0.04, 0.16)


def _add_hit(hits: list[PatternHit], hit: PatternHit, total_steps: int) -> None:
    if not (0 <= hit.step < total_steps):
        return
    hit.velocity = max(1, min(127, int(hit.velocity)))
    hit.probability = max(0.0, min(1.0, float(hit.probability)))
    hits.append(hit)


def generate_pattern(
    prompt: str,
    *,
    device: str = SP404,
    bank: str | int = 0,
    bars: int = 4,
    bpm: float = 98.0,
    steps_per_bar: int = 16,
    available_pads: Optional[Iterable[int]] = None,
    seed: Optional[int] = None,
    name: Optional[str] = None,
) -> PatternSpec:
    """Generate a deterministic PatternSpec from a short musical prompt."""

    device = normalize_device(device)
    bars = max(1, min(16, int(bars)))
    steps_per_bar = max(4, min(64, int(steps_per_bar)))
    bank_idx = bank_to_index(bank)
    tags = _prompt_tags(prompt)
    seed_val = int(seed if seed is not None else _stable_seed(
        prompt, device, bank_idx, bars, bpm, steps_per_bar))
    rng = random.Random(seed_val)
    roles = _choose_roles(device, available_pads)
    dens = _density(tags)
    total_steps = bars * steps_per_bar
    hits: list[PatternHit] = []

    if "house" in tags or "techno" in tags:
        kick_template = [0, 4, 8, 12]
        snare_template = [4, 12] if "house" in tags else [4, 12, 14]
        hat_template = [2, 6, 10, 14]
    elif "breakbeat" in tags:
        kick_template = [0, 3, 7, 10]
        snare_template = [4, 9, 12]
        hat_template = list(range(0, 16, 2))
    elif "half_time" in tags:
        kick_template = [0, 6, 11]
        snare_template = [8]
        hat_template = list(range(0, 16, 2))
    else:
        kick_template = [0, rng.choice([5, 6, 7]), rng.choice([10, 11])]
        if dens > 0.65:
            kick_template.append(rng.choice([13, 14]))
        snare_template = [4, 12]
        hat_template = list(range(0, 16, 2))

    if dens > 0.72:
        hat_template = list(range(16))
    if dens < 0.35:
        hat_template = [2, 6, 10, 14]

    for bar in range(bars):
        bar_off = bar * steps_per_bar
        is_last = bar == bars - 1
        for st in kick_template:
            if rng.random() > (0.92 if st == 0 else 0.76 + dens * 0.2):
                continue
            _add_hit(hits, PatternHit(
                roles["kick"], bar_off + st,
                _vel(rng, 112 if st == 0 else 104, 9),
                1.0, _late_nudge(st, tags, rng), label="kick",
            ), total_steps)
        for st in snare_template:
            _add_hit(hits, PatternHit(
                roles["snare"], bar_off + st, _vel(rng, 104, 8),
                1.0, _late_nudge(st, tags, rng), label="snare",
            ), total_steps)
        for st in hat_template:
            if rng.random() > dens:
                continue
            accent = 88 if st % 4 == 0 else 70
            _add_hit(hits, PatternHit(
                roles["hat"], bar_off + st, _vel(rng, accent, 14),
                0.96, _late_nudge(st, tags, rng), 0.45, "hat",
            ), total_steps)

        if dens > 0.45:
            for st in (3, 11):
                if rng.random() < 0.45:
                    _add_hit(hits, PatternHit(
                        roles["snare"], bar_off + st, _vel(rng, 45, 10),
                        0.65, _late_nudge(st, tags, rng), 0.35,
                        "ghost snare",
                    ), total_steps)
        if rng.random() < (0.25 + dens * 0.35):
            st = rng.choice([6, 7, 10, 13, 15])
            _add_hit(hits, PatternHit(
                roles["perc"], bar_off + st, _vel(rng, 72, 20),
                0.75, _late_nudge(st, tags, rng), 0.45, "perc",
            ), total_steps)
        if is_last and ("fill" in tags or dens > 0.65):
            fill_steps = [12, 13, 14, 15]
            for i, st in enumerate(fill_steps):
                pad = roles["snare"] if i % 2 == 0 else roles["perc"]
                _add_hit(hits, PatternHit(
                    pad, bar_off + st, _vel(rng, 78 + i * 8, 18),
                    0.9, _late_nudge(st, tags, rng), 0.35, "fill",
                ), total_steps)
        if "weird" in tags and rng.random() < 0.55:
            st = rng.randrange(1, steps_per_bar)
            _add_hit(hits, PatternHit(
                roles["extra"], bar_off + st, _vel(rng, 68, 24),
                0.55, rng.uniform(-0.18, 0.20), 0.3, "weird",
            ), total_steps)

    hits.sort(key=lambda h: (h.step + h.nudge, h.pad, -h.velocity))
    title = name or _name_from_prompt(prompt, tags)
    swing = 58.0 if "swing" in tags else (44.0 if "boom_bap" in tags else 0.0)
    return PatternSpec(
        name=title,
        prompt=prompt,
        device=device,
        bank=bank_idx,
        bars=bars,
        steps_per_bar=steps_per_bar,
        bpm=float(bpm),
        swing=swing,
        seed=seed_val,
        tags=tags,
        hits=hits,
    )


def _name_from_prompt(prompt: str, tags: list[str]) -> str:
    words = [w.strip(" ,.;:!?()[]{}").title()
             for w in prompt.split() if w.strip(" ,.;:!?()[]{}")]
    cleaned = " ".join(words[:4]).strip()
    if cleaned:
        return cleaned
    return tags[0].replace("_", " ").title()


def to_midi_clip(spec: PatternSpec, *, color: int = 0,
                 clip_pitch_base: int = 36) -> MidiClip:
    """Convert a generated pattern into a native Compa MidiClip."""

    notes: list[Note] = []
    step_beats = spec.step_beats
    for hit in spec.hits:
        start = max(0.0, (hit.step + hit.nudge) * step_beats)
        notes.append(Note(
            pitch=clip_pitch_base + int(hit.pad),
            start_beat=start,
            duration_beats=max(0.03, hit.duration_steps * step_beats),
            velocity=hit.velocity,
            chance=hit.probability,
            velocity_range=6 if hit.label in ("hat", "ghost snare") else 3,
        ))
    return MidiClip(
        name=spec.name,
        color=color,
        length_beats=spec.length_beats,
        loop_start_beats=0.0,
        loop_end_beats=spec.length_beats,
        looping=True,
        launch_quantize=LaunchQuantize.GLOBAL,
        notes=notes,
    )


def install_clip(session, spec: PatternSpec, track: int,
                 scene: Optional[int], *, overwrite: bool = False) -> int:
    """Install spec as a MidiClip in session. Returns the scene index."""

    if not (0 <= track < len(session.tracks)):
        raise IndexError(f"track {track} out of range")
    if scene is None:
        scene = _first_empty_scene(session, track)
    if scene is None:
        raise RuntimeError("no empty clip slot on target track")
    if session.get_clip(track, scene) is not None and not overwrite:
        raise RuntimeError(
            f"clip slot track {track + 1}, scene {scene + 1} is occupied")
    color = getattr(session.tracks[track], "color", 0)
    session.set_clip(track, scene, to_midi_clip(spec, color=color))
    return scene


def _first_empty_scene(session, track: int) -> Optional[int]:
    for scene, clip in enumerate(session.tracks[track].clips):
        if clip is None:
            return scene
    return None


def to_step_grid(spec: PatternSpec) -> list[list[tuple[int, int]]]:
    """Convert a spec to the persisted Push 2 overlay step-grid shape."""

    rows = device_pad_count(spec.device)
    grid = [
        [(0, 100) for _ in range(max(64, spec.total_steps))]
        for _ in range(rows)
    ]
    for hit in spec.hits:
        if 0 <= hit.pad < rows and 0 <= hit.step < len(grid[hit.pad]):
            current = grid[hit.pad][hit.step]
            if not current[0] or hit.velocity >= current[1]:
                grid[hit.pad][hit.step] = (1, hit.velocity)
    return grid


def install_step_grid(grids: dict, spec: PatternSpec, pattern_idx: int) -> None:
    """Install spec into an in-memory compa_step_persistence grid dict."""

    grids[(spec.device, int(pattern_idx))] = to_step_grid(spec)


def device_note_channel(spec: PatternSpec, pad: int) -> tuple[int, int]:
    """Return (note, zero-indexed MIDI channel) for a device pad hit."""

    device = normalize_device(spec.device)
    if device == SP404:
        pad = max(0, min(15, int(pad)))
        sp_row = pad // 4
        col = pad % 4
        midi_row = 3 - sp_row
        note = 36 + midi_row * 4 + col
        return note, max(0, min(9, int(spec.bank)))
    pad = max(0, min(5, int(pad)))
    bank = max(0, min(7, int(spec.bank)))
    return 48 + bank * 6 + pad, 10


def write_spec_json(spec: PatternSpec, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(spec.to_dict(), f, indent=2)
    os.replace(tmp, path)
    return path


def load_spec_json(path: str) -> PatternSpec:
    with open(path, encoding="utf-8") as f:
        return PatternSpec.from_dict(json.load(f))


def export_midi(spec: PatternSpec, path: str, *,
                ticks_per_beat: int = 480) -> str:
    """Write a small format-0 Standard MIDI File for the pattern."""

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    step_ticks = ticks_per_beat * 4.0 / spec.steps_per_bar
    events: list[tuple[int, int, bytes]] = []
    for hit in spec.hits:
        note, channel = device_note_channel(spec, hit.pad)
        start = max(0, int(round((hit.step + hit.nudge) * step_ticks)))
        dur = max(1, int(round(hit.duration_steps * step_ticks)))
        on = bytes([0x90 | (channel & 0x0F), note & 0x7F, hit.velocity & 0x7F])
        off = bytes([0x80 | (channel & 0x0F), note & 0x7F, 0])
        events.append((start, 1, on))
        events.append((start + dur, 0, off))
    events.sort(key=lambda e: (e[0], e[1]))

    tempo_us = int(round(60_000_000 / max(1.0, spec.bpm)))
    track = bytearray()
    track.extend(_vlq(0))
    track.extend(b"\xff\x03")
    name = spec.name.encode("utf-8")[:127]
    track.extend(_vlq(len(name)))
    track.extend(name)
    track.extend(_vlq(0))
    track.extend(b"\xff\x51\x03")
    track.extend(tempo_us.to_bytes(3, "big"))
    track.extend(_vlq(0))
    track.extend(b"\xff\x58\x04\x04\x02\x18\x08")

    last_tick = 0
    for tick, _, payload in events:
        track.extend(_vlq(max(0, tick - last_tick)))
        track.extend(payload)
        last_tick = tick
    end_tick = int(round(spec.length_beats * ticks_per_beat))
    track.extend(_vlq(max(0, end_tick - last_tick)))
    track.extend(b"\xff\x2f\x00")

    header = bytearray()
    header.extend(b"MThd")
    header.extend((6).to_bytes(4, "big"))
    header.extend((0).to_bytes(2, "big"))  # format 0
    header.extend((1).to_bytes(2, "big"))  # one track
    header.extend(int(ticks_per_beat).to_bytes(2, "big"))
    header.extend(b"MTrk")
    header.extend(len(track).to_bytes(4, "big"))
    header.extend(track)
    with open(path, "wb") as f:
        f.write(header)
    return path


def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    buf = [value & 0x7F]
    value >>= 7
    while value:
        buf.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(buf)
