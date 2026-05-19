#!/usr/bin/env python3
"""Developer CLI for Compa performer pattern generation."""
from __future__ import annotations

import argparse
import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from engine.ai_pattern import (  # noqa: E402
    bank_name,
    bank_to_index,
    export_midi,
    generate_pattern,
    install_clip,
    install_step_grid,
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
        if args.session != sess.name:
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
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
