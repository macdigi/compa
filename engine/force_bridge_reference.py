#!/usr/bin/env python3
"""Quota Bridge: build a standalone Akai Force/MPC Drum Program (.Drum.xpm) from slot WAVs.

This is the "skip the project" workflow:
- Input: slot001.wav..slotNNN.wav (N in {8,16,64,128})
- Output: <outdir>/<outname>/
    - <outname>.Drum.xpm
    - slot001.WAV..slotNNN.WAV (copied; uppercase extension for Force compatibility)

Then you can load the kit in an existing Force/MPC project via the Load browser.

Implementation notes
- `.Drum.xpm` files are XML (MPC-V style) and are used by Force.
- We patch:
  - <ProgramName>
  - ProgramPads JSON (mark pads 1..N active)
  - per-instrument Layer 1 SampleName + SampleFile

Empirically, many Force kits rely on SampleName + sibling WAVs (often .WAV uppercase).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
from pathlib import Path


def sh(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def list_slot_wavs(slot_dir: Path) -> list[Path]:
    wavs = sorted(slot_dir.glob("slot*.wav"))
    out: list[Path] = []
    for p in wavs:
        name = p.name
        if re.fullmatch(r"slot\d{3}\.wav", name, re.IGNORECASE):
            out.append(p)
    return sorted(out, key=lambda p: int(re.search(r"(\d{3})", p.name).group(1)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot-dir", type=Path, required=True)
    ap.add_argument("--template-xpm", type=Path, required=True)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--outname", type=str, required=True)

    ap.add_argument("--force-host", type=str, default=None)
    ap.add_argument("--force-user", type=str, default="root")
    ap.add_argument("--force-key", type=Path, default=Path("~/.ssh/id_ed25519_force"))
    ap.add_argument("--force-dest", type=str, default=None)
    args = ap.parse_args()

    slot_dir = args.slot_dir.expanduser().resolve()
    slot_wavs = list_slot_wavs(slot_dir)
    if not slot_wavs:
        raise SystemExit(f"No slot WAVs found in: {slot_dir}")

    n = len(slot_wavs)
    if n not in (8, 16, 64, 128):
        raise SystemExit(f"Expected 8/16/64/128 slot WAVs, found {n}")

    expected = [f"slot{i:03d}.wav" for i in range(1, n + 1)]
    got = [p.name.lower() for p in slot_wavs]
    if got != expected:
        raise SystemExit(
            "Slot WAVs must be exactly sequential: slot001.wav..slotNNN.wav\n"
            f"Expected: {expected[0]}..{expected[-1]}\nGot: {got[0]}..{got[-1]}"
        )

    tpl = args.template_xpm.expanduser().resolve().read_text("utf-8", errors="ignore")

    # Patch ProgramName
    tpl = re.sub(
        r"<ProgramName>[^<]*</ProgramName>",
        f"<ProgramName>{args.outname}</ProgramName>",
        tpl,
        count=1,
        flags=re.IGNORECASE,
    )

    # Patch ProgramPads JSON: mark pads 1..N active (non-zero) and set a Force-friendly Type.
    mpp = re.search(r"<ProgramPads>([\s\S]*?)</ProgramPads>", tpl, flags=re.IGNORECASE)
    if not mpp:
        raise SystemExit("Template missing <ProgramPads> JSON block")
    pp_raw = html.unescape(mpp.group(1)).strip()
    pp_obj = json.loads(pp_raw)
    pads = pp_obj["ProgramPads"]["pads"]

    # Use the same constant that shows up frequently in Force kits (0x7f0000).
    ACTIVE = 8323072
    for idx in range(128):
        pads[f"value{idx}"] = ACTIVE if idx < n else 0

    # Make sure Type looks like a Force kit (many kits use 5).
    pp_obj["ProgramPads"].setdefault("Universal", {"value0": True})
    pp_obj["ProgramPads"]["Type"] = {"value0": 5}
    pp_obj["ProgramPads"]["universalPad"] = ACTIVE
    pp_obj["ProgramPads"]["UnusedPads"] = {"value0": 1}

    pp_new = json.dumps(pp_obj, separators=(",", ":"))
    # re-escape for XML storage
    pp_new_esc = html.escape(pp_new, quote=True)
    tpl = tpl[: mpp.start(1)] + pp_new_esc + tpl[mpp.end(1) :]

    # Precompute sample lengths (frames) so Force can draw waveforms.
    # If SampleEnd/SliceEnd are 0, Force shows an empty waveform even if it can play audio.
    import wave

    slot_frames: dict[int, int] = {}
    for i in range(1, n + 1):
        src = slot_dir / f"slot{i:03d}.wav"
        with wave.open(str(src), "rb") as w:
            slot_frames[i] = int(w.getnframes())

    # Patch instruments: instrument i uses slot i (1-based).
    # We patch Layer 1: SampleName/SampleFile + SampleEnd/SliceEnd.
    for i in range(1, 129):
        if i <= n:
            base = f"slot{i:03d}"
            wav = f"{base}.WAV"  # uppercase
            end_frames = slot_frames.get(i, 0)
        else:
            base = ""
            wav = ""
            end_frames = 0

        # Narrow to the instrument block first to avoid accidental replacements.
        pat = re.compile(rf"(<Instrument number=\"{i}\">)([\s\S]*?)(</Instrument>)", re.IGNORECASE)
        m = pat.search(tpl)
        if not m:
            raise SystemExit(f"Template missing Instrument {i}")
        inst_block = m.group(0)

        # Within Layer 1, replace SampleName/SampleFile.
        # Some Force kits leave SampleFile empty and resolve by SampleName; we keep SampleFile populated but with .WAV.
        inst_block2 = re.sub(
            r"(<Layer number=\"1\">[\s\S]*?<SampleName>)([^<]*)(</SampleName>)",
            rf"\g<1>{base}\g<3>",
            inst_block,
            count=1,
            flags=re.IGNORECASE,
        )
        inst_block2 = re.sub(
            r"(<Layer number=\"1\">[\s\S]*?<SampleFile>)([^<]*)(</SampleFile>)",
            rf"\g<1>{wav}\g<3>",
            inst_block2,
            count=1,
            flags=re.IGNORECASE,
        )
        # Waveform bounds
        inst_block2 = re.sub(
            r"(<Layer number=\"1\">[\s\S]*?<SampleEnd>)([^<]*)(</SampleEnd>)",
            rf"\g<1>{end_frames}\g<3>",
            inst_block2,
            count=1,
            flags=re.IGNORECASE,
        )
        inst_block2 = re.sub(
            r"(<Layer number=\"1\">[\s\S]*?<SliceEnd>)([^<]*)(</SliceEnd>)",
            rf"\g<1>{end_frames}\g<3>",
            inst_block2,
            count=1,
            flags=re.IGNORECASE,
        )

        tpl = tpl[: m.start()] + inst_block2 + tpl[m.end() :]

    outdir = args.outdir.expanduser().resolve() / args.outname
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    xpm_out = outdir / f"{args.outname}.Drum.xpm"
    xpm_out.write_text(tpl, encoding="utf-8")

    # Copy slot WAVs as uppercase .WAV (Force often expects this exact case)
    for p in slot_wavs:
        m = re.search(r"(\d{3})", p.name)
        if not m:
            continue
        i = int(m.group(1))
        dst = outdir / f"slot{i:03d}.WAV"
        shutil.copy2(p, dst)

    print(str(outdir))

    # Optional upload
    if args.force_host and args.force_dest:
        host = args.force_host
        user = args.force_user
        key = args.force_key.expanduser()
        dest_root = args.force_dest
        dest = f"{dest_root}/{args.outname}"

        # Create dest folder
        sh(["ssh", "-i", str(key), "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"{user}@{host}", f"mkdir -p '{dest}'"]) 

        # Upload xpm + wavs
        sh(["scp", "-i", str(key), "-o", "StrictHostKeyChecking=no", str(xpm_out), f"{user}@{host}:{dest}/"]) 
        wavs = sorted(outdir.glob("slot*.wav")) + sorted(outdir.glob("slot*.WAV"))
        if wavs:
            sh(["scp", "-i", str(key), "-o", "StrictHostKeyChecking=no", *[str(p) for p in wavs], f"{user}@{host}:{dest}/"]) 

        print(f"OK: uploaded -> {dest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
