#!/usr/bin/env python3
"""Developer CLI for Compa performer pattern generation."""
from __future__ import annotations

import argparse
import os
import sys
import time


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from engine.ai_pattern import (  # noqa: E402
    bank_name,
    bank_to_index,
    chromatic_note_channel,
    device_note_channel,
    export_midi,
    generate_pattern,
    install_clip,
    install_step_grid,
    load_spec_json,
    write_spec_json,
)
from engine.compa_step_persistence import load as load_step_grids  # noqa: E402
from engine.compa_step_persistence import save as save_step_grids  # noqa: E402
from session.defaults import build_default_session  # noqa: E402
from session.persistence import load_session, save_session  # noqa: E402


def _parse_pads(value: str | None) -> list[int] | None:
    if not value:
        return None
    pads = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        pads.append(max(0, int(raw) - 1))
    return pads or None


def _default_step_grid_path() -> str:
    return os.path.join(PROJECT_ROOT, "sessions", "compa_step_grids.json")


def _default_generated_dir() -> str:
    return os.path.join(PROJECT_ROOT, "sessions", "generated")


def cmd_generate(args: argparse.Namespace) -> int:
    prompt = args.prompt or " ".join(args.prompt_words).strip()
    if not prompt:
        prompt = "dusty boom bap"

    spec = generate_pattern(
        prompt,
        device=args.device,
        bank=args.bank,
        bars=args.bars,
        bpm=args.bpm,
        steps_per_bar=args.steps_per_bar,
        available_pads=_parse_pads(args.pads),
        seed=args.seed,
        name=args.name,
    )

    generated_dir = _default_generated_dir()
    os.makedirs(generated_dir, exist_ok=True)
    slug = "".join(c.lower() if c.isalnum() else "-"
                   for c in spec.name).strip("-") or "pattern"

    spec_path = args.spec or os.path.join(generated_dir, f"{slug}.json")
    write_spec_json(spec, spec_path)

    midi_path = args.midi
    if midi_path:
        export_midi(spec, midi_path)

    session_path = ""
    scene = None
    if not args.no_session:
        sess = load_session(args.session) or build_default_session()
        if not os.path.isabs(args.session) and args.session != sess.name:
            sess.name = args.session
        track_idx = max(0, int(args.track) - 1)
        scene_arg = None if args.scene is None else max(0, int(args.scene) - 1)
        scene = install_clip(
            sess, spec, track_idx, scene_arg, overwrite=args.overwrite)
        session_path = save_session(sess, args.session)

    step_path = ""
    pattern_idx = max(0, int(args.pattern) - 1)
    if args.write_step_grid:
        step_path = args.step_grid_path or _default_step_grid_path()
        grids = load_step_grids(step_path)
        install_step_grid(grids, spec, pattern_idx)
        save_step_grids(grids, step_path)

    print(f"generated: {spec.name}")
    print(f"device: {spec.device} bank {bank_name(spec.bank)}")
    print(f"bars: {spec.bars} steps/bar: {spec.steps_per_bar} hits: {len(spec.hits)}")
    print(f"seed: {spec.seed}")
    print(f"spec: {spec_path}")
    if midi_path:
        print(f"midi: {midi_path}")
    if session_path:
        print(f"session: {session_path}")
        if scene is not None:
            print(f"clip slot: track {args.track}, scene {scene + 1}")
    if step_path:
        print(f"step grid: {step_path} pattern {pattern_idx + 1}")
    return 0


def _find_midi_out_port(port_hint: str) -> tuple[object, str]:
    try:
        import rtmidi
    except ImportError as exc:
        raise RuntimeError("python-rtmidi is required for live playback") from exc

    midi_out = rtmidi.MidiOut()
    ports = midi_out.get_ports()
    hint = (port_hint or "").lower()
    if not hint:
        raise RuntimeError("MIDI port hint is required")
    for idx, name in enumerate(ports):
        if hint in name.lower():
            midi_out.open_port(idx)
            return midi_out, name
    available = ", ".join(ports) if ports else "(none)"
    raise RuntimeError(f"MIDI output matching {port_hint!r} not found; available: {available}")


def _play_events(spec, *, bpm: float | None = None,
                 velocity_scale: float = 1.0) -> list[tuple[float, list[int], str]]:
    tempo = float(bpm or spec.bpm)
    step_seconds = (60.0 / max(1.0, tempo)) * 4.0 / float(spec.steps_per_bar)
    events: list[tuple[float, list[int], str]] = []
    for hit in spec.hits:
        note, channel = device_note_channel(spec, hit.pad)
        start = max(0.0, (hit.step + hit.nudge) * step_seconds)
        duration = max(0.035, hit.duration_steps * step_seconds)
        velocity = max(1, min(127, int(round(hit.velocity * velocity_scale))))
        status_on = 0x90 | (channel & 0x0F)
        status_off = 0x80 | (channel & 0x0F)
        label = hit.label or f"pad {hit.pad + 1}"
        events.append((start, [status_on, note & 0x7F, velocity], label))
        events.append((start + duration, [status_off, note & 0x7F, 0], label))
    for hit in spec.chromatic_hits:
        note, channel = chromatic_note_channel(spec, hit.note)
        start = max(0.0, (hit.step + hit.nudge) * step_seconds)
        duration = max(0.035, hit.duration_steps * step_seconds)
        velocity = max(1, min(127, int(round(hit.velocity * velocity_scale))))
        status_on = 0x90 | (channel & 0x0F)
        status_off = 0x80 | (channel & 0x0F)
        label = hit.label or f"chromatic {hit.note}"
        events.append((start, [status_on, note & 0x7F, velocity], label))
        events.append((start + duration, [status_off, note & 0x7F, 0], label))
    events.sort(key=lambda item: (item[0], item[1][0] & 0xF0))
    return events


def _send_all_notes_off(midi_out, events: list[tuple[float, list[int], str]]) -> None:
    pairs = {
        (msg[0] & 0x0F, msg[1])
        for _time_sec, msg, _label in events
        if (msg[0] & 0xF0) == 0x90 and len(msg) >= 3
    }
    for channel, note in sorted(pairs):
        midi_out.send_message([0x80 | channel, note, 0])


def cmd_play(args: argparse.Namespace) -> int:
    spec = load_spec_json(args.spec)
    port_hint = args.port or spec.device
    events = _play_events(
        spec,
        bpm=args.bpm if args.bpm > 0 else None,
        velocity_scale=args.velocity_scale,
    )
    loops = max(1, int(args.loops))
    tempo = float(args.bpm if args.bpm > 0 else spec.bpm)
    loop_seconds = spec.length_beats * 60.0 / max(1.0, tempo)

    print(f"pattern: {spec.name}")
    print(f"device: {spec.device} bank {bank_name(spec.bank)}")
    total_hits = len(spec.hits) + len(spec.chromatic_hits)
    print(
        f"bpm: {tempo:g} loops: {loops}"
        f" hits: {total_hits}"
        f" pad_hits: {len(spec.hits)}"
        f" chromatic_hits: {len(spec.chromatic_hits)}"
    )
    if args.dry_run:
        for t, msg, label in events:
            if (msg[0] & 0xF0) == 0x90:
                print(
                    f"{t:7.3f}s ch={(msg[0] & 0x0F) + 1}"
                    f" note={msg[1]} vel={msg[2]} {label}"
                )
        return 0

    try:
        midi_out, port_name = _find_midi_out_port(port_hint)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"port: {port_name}")
    try:
        for loop_idx in range(loops):
            start_time = time.monotonic()
            print(f"loop: {loop_idx + 1}/{loops}")
            for event_time, msg, label in events:
                while True:
                    remaining = event_time - (time.monotonic() - start_time)
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 0.005))
                midi_out.send_message(msg)
                if args.verbose and (msg[0] & 0xF0) == 0x90:
                    print(
                        f"{time.monotonic() - start_time:7.3f}s"
                        f" ch={(msg[0] & 0x0F) + 1}"
                        f" note={msg[1]} vel={msg[2]} {label}"
                    )
            elapsed = time.monotonic() - start_time
            if loop_idx + 1 < loops and elapsed < loop_seconds:
                time.sleep(loop_seconds - elapsed)
    finally:
        _send_all_notes_off(midi_out, events)
        try:
            midi_out.close_port()
        except Exception:
            pass
    print("done")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate Compa performer patterns.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate a prompt-based pattern")
    g.add_argument("prompt_words", nargs="*", help="prompt text")
    g.add_argument("--prompt", help="prompt text")
    g.add_argument("--name", help="clip/spec name")
    g.add_argument("--device", default="SP-404MKII",
                   help="target device: SP-404MKII or P-6")
    g.add_argument("--bank", default="A",
                   help="target bank, e.g. A or 1")
    g.add_argument("--bars", type=int, default=4)
    g.add_argument("--bpm", type=float, default=98.0)
    g.add_argument("--steps-per-bar", type=int, default=16)
    g.add_argument("--pads",
                   help="comma-separated available pad numbers, 1-based")
    g.add_argument("--seed", type=int)
    g.add_argument("--session", default="default")
    g.add_argument("--track", type=int, default=1,
                   help="1-based session track")
    g.add_argument("--scene", type=int,
                   help="1-based scene. Defaults to first empty slot.")
    g.add_argument("--overwrite", action="store_true",
                   help="allow replacing an occupied clip slot")
    g.add_argument("--no-session", action="store_true",
                   help="only write spec/MIDI, do not update a Compa session")
    g.add_argument("--write-step-grid", action="store_true",
                   help="also write Push 2 overlay step-grid persistence")
    g.add_argument("--pattern", type=int, default=1,
                   help="1-based device pattern slot for --write-step-grid")
    g.add_argument("--step-grid-path", help="override step-grid JSON path")
    g.add_argument("--midi", help="optional .mid export path")
    g.add_argument("--spec", help="optional generated spec JSON path")
    g.set_defaults(func=cmd_generate)

    play = sub.add_parser("play", help="play a saved performer spec over MIDI")
    play.add_argument("spec", help="PatternSpec JSON to play")
    play.add_argument("--port", default="",
                      help="MIDI output name hint. Defaults to the spec device.")
    play.add_argument("--loops", type=int, default=1)
    play.add_argument("--bpm", type=float, default=0.0,
                      help="override spec BPM for playback")
    play.add_argument("--velocity-scale", type=float, default=1.0)
    play.add_argument("--dry-run", action="store_true",
                      help="print scheduled note-ons without sending MIDI")
    play.add_argument("--verbose", action="store_true")
    play.set_defaults(func=cmd_play)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
