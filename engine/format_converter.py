"""Sample format converter — convert between P-6, SP-404 MK2, and Akai MPC formats.

Handles:
- WAV format conversion (sample rate, bit depth, channels)
- SP-404 MK2 SD card structure (IMPORT/EXPORT folders)
- P-6 sample slot structure
- Akai MPC .xpm drum program generation
"""

import html
import json
import logging
import os
import re
import shutil
import wave
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:
    import soundfile as sf
except ImportError:
    sf = None

log = logging.getLogger(__name__)

# Path to the golden Akai Force/MPC drum-program template (26k-line XML).
# Resolved relative to this module: <repo>/docs/akai_drum_template.xpm
TEMPLATE_XPM = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "akai_drum_template.xpm",
)


# ── WAV conversion ───────────────────────────────────────────────────

def convert_wav(src: str, dst: str,
                target_rate: int = 48000,
                target_channels: int = 2,
                target_bits: int = 16,
                normalize: bool = True) -> bool:
    """Convert a WAV file to target format.

    Args:
        src: Source WAV path
        dst: Destination WAV path
        target_rate: Target sample rate (48000 for SP-404, 44100 for P-6)
        target_channels: 1 for mono, 2 for stereo
        target_bits: 16 or 24
        normalize: Normalize to -1dB
    """
    if sf is None:
        return False
    try:
        data, rate = sf.read(src, dtype="float32")
        if data.ndim == 1:
            data = data.reshape(-1, 1)

        # Channel conversion
        if target_channels == 1 and data.shape[1] > 1:
            data = data.mean(axis=1, keepdims=True)
        elif target_channels == 2 and data.shape[1] == 1:
            data = np.column_stack([data[:, 0], data[:, 0]])

        # Resample if needed
        if rate != target_rate:
            ratio = target_rate / rate
            new_len = int(len(data) * ratio)
            indices = np.linspace(0, len(data) - 1, new_len).astype(int)
            data = data[indices]

        # Normalize
        if normalize:
            peak = np.max(np.abs(data))
            if peak > 0:
                data *= 0.9 / peak

        # Write
        subtype = "PCM_16" if target_bits == 16 else "PCM_24"
        sf.write(dst, data, target_rate, subtype=subtype)
        log.info("Converted: %s -> %s (%dHz %dch %dbit)",
                 os.path.basename(src), os.path.basename(dst),
                 target_rate, target_channels, target_bits)
        return True
    except Exception as e:
        log.error("Conversion failed: %s", e)
        return False


# ── SP-404 MK2 format ───────────────────────────────────────────────

def prepare_for_sp404(wav_files: list[str], output_dir: str) -> list[str]:
    """Convert WAV files for SP-404 MK2 import.

    SP-404 MK2 requires: 48kHz, 16-bit, WAV
    Files go in the IMPORT folder on the SD card.

    Returns list of converted file paths.
    """
    import_dir = os.path.join(output_dir, "IMPORT")
    os.makedirs(import_dir, exist_ok=True)

    converted = []
    for src in wav_files:
        name = os.path.basename(src)
        dst = os.path.join(import_dir, name)
        if convert_wav(src, dst, target_rate=48000, target_channels=2, target_bits=16):
            converted.append(dst)

    log.info("Prepared %d files for SP-404 import", len(converted))
    return converted


def prepare_for_p6(wav_files: list[str], output_dir: str) -> list[str]:
    """Convert WAV files for P-6 import.

    P-6 requires: 44.1kHz (or lower), 16-bit, WAV
    Max ~5.9 seconds per sample at 44.1kHz.

    Returns list of converted file paths.
    """
    sample_dir = os.path.join(output_dir, "SAMPLE")
    os.makedirs(sample_dir, exist_ok=True)

    converted = []
    for src in wav_files:
        name = os.path.basename(src)
        dst = os.path.join(sample_dir, name)
        if convert_wav(src, dst, target_rate=44100, target_channels=1, target_bits=16):
            converted.append(dst)

    log.info("Prepared %d files for P-6 import", len(converted))
    return converted


# ── Akai MPC XPM drum program ────────────────────────────────────────

@dataclass
class PadAssignment:
    """One pad in an MPC drum program."""
    pad_index: int       # 0-15 (A01-A16)
    sample_path: str     # Relative path to WAV file
    volume: float = 1.0  # 0.0-1.0
    pan: float = 0.5     # 0.0=left, 0.5=center, 1.0=right
    tune: float = 0.0    # Semitones offset


def generate_xpm(name: str, pads: list[PadAssignment],
                 output_dir: str) -> Optional[str]:
    """Generate an Akai Force/MPC .Drum.xpm by patching a golden template.

    Instead of building XML from scratch, we read the known-good 26k-line
    template (``docs/akai_drum_template.xpm``) and surgically patch:

    1. ``<ProgramName>`` -- kit display name
    2. ``<ProgramPads>`` JSON -- mark active/inactive pads with colour codes
    3. Per-instrument Layer 1 ``<SampleName>``, ``<SampleFile>``,
       ``<SampleEnd>``, and ``<SliceEnd>`` -- so Force resolves audio
       and draws waveforms correctly

    WAV files are copied into *output_dir* with an uppercase ``.WAV``
    extension (required by Force).

    Args:
        name: Program name (e.g., "My Kit")
        pads: List of pad assignments (up to 128, typically 16)
        output_dir: Directory to write the .xpm and copy samples

    Returns:
        Path to the generated .xpm file, or None on failure.
    """
    if not os.path.isfile(TEMPLATE_XPM):
        log.error("Template XPM not found: %s", TEMPLATE_XPM)
        return None

    os.makedirs(output_dir, exist_ok=True)

    # ── Build lookup: pad_index -> info (copy WAVs as uppercase .WAV) ──
    ACTIVE_COLOR = 8323072
    pad_map: dict[int, dict] = {}

    for pad in pads:
        if not os.path.exists(pad.sample_path):
            log.warning("Sample not found, skipping pad %d: %s",
                        pad.pad_index, pad.sample_path)
            continue

        src_basename = os.path.basename(pad.sample_path)
        stem = os.path.splitext(src_basename)[0]
        wav_name = f"{stem}.WAV"  # uppercase extension for Force

        dst = os.path.join(output_dir, wav_name)
        if not os.path.exists(dst):
            shutil.copy2(pad.sample_path, dst)

        # Read frame count so Force can draw waveforms
        try:
            with wave.open(pad.sample_path, "rb") as w:
                nframes = int(w.getnframes())
        except Exception:
            nframes = 0

        pad_map[pad.pad_index] = {
            "sample_name": stem,         # filename without extension
            "sample_file": wav_name,     # filename WITH uppercase .WAV
            "nframes": nframes,
        }

    n_active = len(pad_map)

    # ── Read template ────────────────────────────────────────────
    with open(TEMPLATE_XPM, "r", encoding="utf-8", errors="ignore") as f:
        tpl = f.read()

    # ── 1. Patch ProgramName ─────────────────────────────────────
    tpl = re.sub(
        r"<ProgramName>[^<]*</ProgramName>",
        f"<ProgramName>{name}</ProgramName>",
        tpl,
        count=1,
        flags=re.IGNORECASE,
    )

    # ── 2. Patch ProgramPads JSON ────────────────────────────────
    mpp = re.search(
        r"<ProgramPads>([\s\S]*?)</ProgramPads>", tpl, flags=re.IGNORECASE,
    )
    if not mpp:
        log.error("Template missing <ProgramPads> block")
        return None

    pp_raw = html.unescape(mpp.group(1)).strip()
    pp_obj = json.loads(pp_raw)
    pp_pads = pp_obj["ProgramPads"]["pads"]

    for idx in range(128):
        pp_pads[f"value{idx}"] = ACTIVE_COLOR if idx in pad_map else 0

    pp_obj["ProgramPads"].setdefault("Universal", {"value0": True})
    pp_obj["ProgramPads"]["Type"] = {"value0": 5}
    pp_obj["ProgramPads"]["universalPad"] = ACTIVE_COLOR
    pp_obj["ProgramPads"]["UnusedPads"] = {"value0": 1}

    pp_new = json.dumps(pp_obj, separators=(",", ":"))
    pp_new_esc = html.escape(pp_new, quote=True)
    tpl = tpl[: mpp.start(1)] + pp_new_esc + tpl[mpp.end(1) :]

    # ── 3. Patch each instrument (1-128) ─────────────────────────
    for inst_num in range(1, 129):
        pad_idx = inst_num - 1  # instrument 1 = pad index 0
        info = pad_map.get(pad_idx)
        if info:
            sample_name = info["sample_name"]
            sample_file = info["sample_file"]
            end_frames = info["nframes"]
        else:
            sample_name = ""
            sample_file = ""
            end_frames = 0

        # Find the full <Instrument number="N">...</Instrument> block
        pat = re.compile(
            rf'(<Instrument number="{inst_num}">)([\s\S]*?)(</Instrument>)',
            re.IGNORECASE,
        )
        m = pat.search(tpl)
        if not m:
            log.warning("Template missing Instrument %d, skipping", inst_num)
            continue

        block = m.group(0)

        # Within Layer 1, patch SampleName and SampleFile
        block = re.sub(
            r'(<Layer number="1">[\s\S]*?<SampleName>)([^<]*)(</SampleName>)',
            rf"\g<1>{sample_name}\g<3>",
            block,
            count=1,
            flags=re.IGNORECASE,
        )
        block = re.sub(
            r'(<Layer number="1">[\s\S]*?<SampleFile>)([^<]*)(</SampleFile>)',
            rf"\g<1>{sample_file}\g<3>",
            block,
            count=1,
            flags=re.IGNORECASE,
        )

        # Patch SampleEnd and SliceEnd so Force draws waveforms
        block = re.sub(
            r'(<Layer number="1">[\s\S]*?<SampleEnd>)([^<]*)(</SampleEnd>)',
            rf"\g<1>{end_frames}\g<3>",
            block,
            count=1,
            flags=re.IGNORECASE,
        )
        block = re.sub(
            r'(<Layer number="1">[\s\S]*?<SliceEnd>)([^<]*)(</SliceEnd>)',
            rf"\g<1>{end_frames}\g<3>",
            block,
            count=1,
            flags=re.IGNORECASE,
        )

        tpl = tpl[: m.start()] + block + tpl[m.end() :]

    # ── Write patched XPM ────────────────────────────────────────
    xpm_path = os.path.join(output_dir, f"{name}.Drum.xpm")
    try:
        with open(xpm_path, "w", encoding="utf-8") as f:
            f.write(tpl)
        log.info("Generated XPM: %s (%d active pads, template-patched)",
                 xpm_path, n_active)
        return xpm_path
    except Exception as e:
        log.error("Failed to write XPM: %s", e)
        return None


# ── Batch converter ──────────────────────────────────────────────────

def convert_recordings_to_kit(recordings: list[str], kit_name: str,
                              output_dir: str, target: str = "mpc") -> Optional[str]:
    """Convert a list of recordings into a kit for the target device.

    Args:
        recordings: List of WAV file paths
        kit_name: Name for the kit
        output_dir: Where to write the output
        target: "mpc" for Akai XPM, "sp404" for SP-404 IMPORT folder,
                "p6" for P-6 SAMPLE folder

    Returns:
        Path to the output directory/file, or None on failure.
    """
    if target == "mpc":
        pads = []
        for i, rec in enumerate(recordings[:128]):
            pads.append(PadAssignment(
                pad_index=i,
                sample_path=rec,
                volume=1.0,
                pan=0.5,
            ))
        return generate_xpm(kit_name, pads, output_dir)

    elif target == "sp404":
        converted = prepare_for_sp404(recordings, output_dir)
        return output_dir if converted else None

    elif target == "p6":
        converted = prepare_for_p6(recordings, output_dir)
        return output_dir if converted else None

    else:
        log.error("Unknown target: %s", target)
        return None


def list_supported_formats() -> list[dict]:
    """List all supported conversion targets."""
    return [
        {
            "id": "sp404",
            "name": "Roland SP-404 MK2",
            "desc": "48kHz 16-bit stereo WAV in IMPORT folder",
            "ext": "wav",
        },
        {
            "id": "p6",
            "name": "Roland P-6",
            "desc": "44.1kHz 16-bit mono WAV (max 5.9s)",
            "ext": "wav",
        },
        {
            "id": "mpc",
            "name": "Akai MPC / Force",
            "desc": "44.1kHz 16-bit stereo WAV + .xpm drum program",
            "ext": "xpm",
        },
    ]
