"""Sample format converter — convert between P-6, SP-404 MK2, and Akai MPC formats.

Handles:
- WAV format conversion (sample rate, bit depth, channels)
- SP-404 MK2 SD card structure (IMPORT/EXPORT folders)
- P-6 sample slot structure
- Akai MPC .xpm drum program generation
"""

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:
    import soundfile as sf
except ImportError:
    sf = None

log = logging.getLogger(__name__)


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


def _build_program_pads_json(pad_map: dict[int, dict]) -> str:
    """Build HTML-entity-encoded JSON for the ProgramPads element.

    The MPC stores pad colour info as a JSON object encoded with HTML entities
    inside an XML element.  Each pad index (0-127) maps to a colour integer.
    """
    DEFAULT_PAD_COLOR = 8323072
    pads_dict = {}
    for i in range(128):
        pads_dict[str(i)] = DEFAULT_PAD_COLOR
    raw = json.dumps(pads_dict, separators=(",", ":"))
    # HTML-entity-encode for safe XML embedding
    encoded = (raw
               .replace("&", "&amp;")
               .replace('"', "&quot;")
               .replace("<", "&lt;")
               .replace(">", "&gt;"))
    return encoded


def _xml_layer(number: int, active: bool,
               sample_name: str = "", sample_file: str = "",
               volume: float = 1.0, pan: float = 0.5) -> str:
    """Return XML for one <Layer> element inside an Instrument."""
    active_str = "True" if active else "False"
    return (
        f'          <Layer number="{number}">\n'
        f"            <Active>{active_str}</Active>\n"
        f"            <Volume>{volume:.6f}</Volume>\n"
        f"            <Pan>{pan:.6f}</Pan>\n"
        f"            <Pitch>0.000000</Pitch>\n"
        f"            <SampleName>{sample_name}</SampleName>\n"
        f"            <SampleFile>{sample_file}</SampleFile>\n"
        f"            <SliceStart>0</SliceStart>\n"
        f"            <SliceEnd>0</SliceEnd>\n"
        f"            <SliceLoop>0</SliceLoop>\n"
        f"            <SliceLoopCrossFade>0</SliceLoopCrossFade>\n"
        f"            <SliceTailPosition>0</SliceTailPosition>\n"
        f"            <SliceTailLength>0</SliceTailLength>\n"
        f"            <Direction>Forward</Direction>\n"
        f"            <Offset>0</Offset>\n"
        f"            <SliceIndex>0</SliceIndex>\n"
        f"            <RootNote>60</RootNote>\n"
        f"            <KeyTrack>False</KeyTrack>\n"
        f"            <VelocityToStart>0.000000</VelocityToStart>\n"
        f"            <VelStart>0</VelStart>\n"
        f"            <VelEnd>127</VelEnd>\n"
        f"          </Layer>\n"
    )


def _xml_instrument(number: int,
                    sample_name: str = "", sample_file: str = "",
                    volume: float = 0.707946, pan: float = 0.5) -> str:
    """Return XML for one <Instrument> element (pad slot)."""
    lines = [
        f'      <Instrument number="{number}">',
        "        <AudioRoute>",
        "          <Submix>0</Submix>",
        "          <Output>0</Output>",
        "          <Send1>0.000000</Send1>",
        "          <Send2>0.000000</Send2>",
        "          <Send3>0.000000</Send3>",
        "          <Send4>0.000000</Send4>",
        "        </AudioRoute>",
        f"        <Volume>{volume:.6f}</Volume>",
        f"        <Pan>{pan:.6f}</Pan>",
        "        <Mono>True</Mono>",
        "        <Polyphony>1</Polyphony>",
        "        <VelocitySensitivity>75</VelocitySensitivity>",
        "        <CutoffVelocity>0.000000</CutoffVelocity>",
        "        <FilterType>2</FilterType>",
        "        <FilterCutoff>1.000000</FilterCutoff>",
        "        <FilterResonance>0.000000</FilterResonance>",
        "        <FilterEnvAmount>0.000000</FilterEnvAmount>",
        "        <AttackTime>0.000000</AttackTime>",
        "        <HoldTime>0.000000</HoldTime>",
        "        <DecayTime>0.047244</DecayTime>",
        "        <SustainLevel>1.000000</SustainLevel>",
        "        <ReleaseTime>0.047244</ReleaseTime>",
        "        <OneShot>True</OneShot>",
        "        <LFORate>0.000000</LFORate>",
        "        <LFOAmount>0.000000</LFOAmount>",
        "        <LFOWaveform>Sine</LFOWaveform>",
        "        <LFOTarget>Pitch</LFOTarget>",
        "        <LFOSync>False</LFOSync>",
        "        <LFOReset>False</LFOReset>",
        "        <MuteGroup>Off</MuteGroup>",
        "        <SimultPlay>Off</SimultPlay>",
        "        <Layers>",
    ]

    # Layer 1 — the active sample layer
    has_sample = bool(sample_name and sample_file)
    lines.append(_xml_layer(
        number=1, active=True,
        sample_name=sample_name if has_sample else "",
        sample_file=sample_file if has_sample else "",
        volume=1.0, pan=0.5,
    ))
    # Layers 2-4 — always empty
    for layer_num in range(2, 5):
        lines.append(_xml_layer(
            number=layer_num, active=False,
            sample_name="", sample_file="",
            volume=1.0, pan=0.5,
        ))

    lines.append("        </Layers>")
    lines.append("      </Instrument>")
    return "\n".join(lines) + "\n"


def generate_xpm(name: str, pads: list[PadAssignment],
                 output_dir: str) -> Optional[str]:
    """Generate an Akai MPC .xpm drum program file.

    Produces the real MPCVObject XML format that Akai MPC and Force
    hardware/software can load.  The .xpm references WAV files by
    filename only -- they must live in the same directory.

    Args:
        name: Program name (e.g., "My Kit")
        pads: List of pad assignments (up to 128, typically 16)
        output_dir: Directory to write the .xpm and copy samples

    Returns:
        Path to the generated .xpm file, or None on failure.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Copy/convert samples into output dir and build lookup by pad index
    pad_map: dict[int, dict] = {}
    for pad in pads:
        if not os.path.exists(pad.sample_path):
            log.warning("Sample not found, skipping pad %d: %s",
                        pad.pad_index, pad.sample_path)
            continue

        sample_file = os.path.basename(pad.sample_path)
        dst = os.path.join(output_dir, sample_file)
        if not os.path.exists(dst):
            convert_wav(pad.sample_path, dst,
                        target_rate=44100, target_channels=2, target_bits=16)

        # Display name is filename without extension
        sample_display = os.path.splitext(sample_file)[0]

        pad_map[pad.pad_index] = {
            "sample_name": sample_display,
            "sample_file": sample_file,
            "volume": pad.volume,
            "pan": pad.pan,
        }

    # ── Build XML ────────────────────────────────────────────────
    pads_json = _build_program_pads_json(pad_map)

    parts: list[str] = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append("<MPCVObject>")

    # Version block
    parts.append("  <Version>")
    parts.append("    <File_Version>2.1</File_Version>")
    parts.append("    <Application>MPC-V</Application>")
    parts.append("    <Application_Version>2.6.0.17</Application_Version>")
    parts.append("    <Platform>Windows</Platform>")
    parts.append("  </Version>")

    # Program block
    parts.append('  <Program type="Drum">')
    parts.append(f"    <ProgramName>{name}</ProgramName>")
    parts.append(f"    <ProgramPads>{pads_json}</ProgramPads>")

    # Global audio route
    parts.append("    <AudioRoute>")
    parts.append("      <Submix>0</Submix>")
    parts.append("      <Output>0</Output>")
    parts.append("      <Send1>0.000000</Send1>")
    parts.append("      <Send2>0.000000</Send2>")
    parts.append("      <Send3>0.000000</Send3>")
    parts.append("      <Send4>0.000000</Send4>")
    parts.append("    </AudioRoute>")

    # Global program settings
    parts.append("    <Volume>0.707946</Volume>")
    parts.append("    <Pan>0.500000</Pan>")

    # Instruments — always 128, regardless of how many have samples
    parts.append("    <Instruments>")
    for inst_num in range(1, 129):
        pad_idx = inst_num - 1  # instrument 1 = pad index 0
        info = pad_map.get(pad_idx)
        if info:
            parts.append(_xml_instrument(
                number=inst_num,
                sample_name=info["sample_name"],
                sample_file=info["sample_file"],
                volume=0.707946,
                pan=info["pan"],
            ))
        else:
            parts.append(_xml_instrument(
                number=inst_num,
                sample_name="",
                sample_file="",
            ))
    parts.append("    </Instruments>")
    parts.append("  </Program>")
    parts.append("</MPCVObject>")

    xpm_content = "\n".join(parts) + "\n"

    # ── Write file ───────────────────────────────────────────────
    xpm_path = os.path.join(output_dir, f"{name}.xpm")
    try:
        with open(xpm_path, "w", encoding="utf-8") as f:
            f.write(xpm_content)
        log.info("Generated XPM: %s (%d pads with samples, 128 instruments)",
                 xpm_path, len(pad_map))
        return xpm_path
    except Exception as e:
        log.error("Failed to generate XPM: %s", e)
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
