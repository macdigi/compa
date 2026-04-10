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


def generate_xpm(name: str, pads: list[PadAssignment],
                 output_dir: str) -> Optional[str]:
    """Generate an Akai MPC .xpm drum program file.

    The .xpm file is a text/XML-based format that references WAV samples.
    The WAV files must be in the same directory as the .xpm file.

    Args:
        name: Program name (e.g., "My Kit")
        pads: List of pad assignments (up to 128, typically 16)
        output_dir: Directory to write the .xpm and copy samples

    Returns:
        Path to the generated .xpm file, or None on failure.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Copy samples to output directory and build pad list
    pad_entries = []
    for pad in pads:
        if not os.path.exists(pad.sample_path):
            continue

        # Copy WAV to output dir (convert to 44.1kHz 16-bit if needed)
        sample_name = os.path.basename(pad.sample_path)
        dst = os.path.join(output_dir, sample_name)
        if not os.path.exists(dst):
            convert_wav(pad.sample_path, dst,
                       target_rate=44100, target_channels=2, target_bits=16)

        # MPC pad numbering: A01-A16 = 0-15, B01-B16 = 16-31, etc.
        bank = chr(65 + pad.pad_index // 16)  # A, B, C, D...
        pad_num = (pad.pad_index % 16) + 1
        pad_name = f"{bank}{pad_num:02d}"

        pad_entries.append({
            "pad": pad_name,
            "pad_index": pad.pad_index,
            "sample": sample_name,
            "volume": pad.volume,
            "pan": pad.pan,
            "tune": pad.tune,
        })

    # Generate XPM content
    # Modern MPC XPM is XML-based
    xpm_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<MPCVObject type="DrumProgram" version="2.8">
  <ProgramName>{name}</ProgramName>
  <ProgramType>0</ProgramType>
  <PadPlayMode>0</PadPlayMode>
  <SimultaneousPlayMode>0</SimultaneousPlayMode>
  <MonoPoly>1</MonoPoly>
  <MuteTarget1>0</MuteTarget1>
  <MuteTarget2>0</MuteTarget2>
  <Pads>
"""
    for entry in pad_entries:
        vol_db = 20 * np.log10(max(0.001, entry["volume"]))
        pan_val = int((entry["pan"] - 0.5) * 200)  # -100 to +100
        tune_cents = int(entry["tune"] * 100)

        xpm_content += f"""    <Pad number="{entry['pad_index']}">
      <PadPlayMode>0</PadPlayMode>
      <SliderParameter>0</SliderParameter>
      <Layers>
        <Layer number="0">
          <SampleName>{entry['sample']}</SampleName>
          <SliceIndex>0</SliceIndex>
          <Volume>{vol_db:.1f}</Volume>
          <Pan>{pan_val}</Pan>
          <Tune>{tune_cents}</Tune>
          <RootNote>60</RootNote>
          <KeyRangeLow>0</KeyRangeLow>
          <KeyRangeHigh>127</KeyRangeHigh>
          <VelocityRangeLow>0</VelocityRangeLow>
          <VelocityRangeHigh>127</VelocityRangeHigh>
          <LoopStart>0</LoopStart>
          <LoopEnd>0</LoopEnd>
          <LoopCrossFade>0</LoopCrossFade>
          <LoopTune>0</LoopTune>
          <AttackTime>0</AttackTime>
          <HoldTime>0</HoldTime>
          <DecayTime>0</DecayTime>
          <SustainLevel>100</SustainLevel>
          <ReleaseTime>10</ReleaseTime>
          <FilterType>0</FilterType>
          <FilterCutoff>100</FilterCutoff>
          <FilterResonance>0</FilterResonance>
          <FilterEnvAmount>0</FilterEnvAmount>
        </Layer>
      </Layers>
      <MuteGroup>0</MuteGroup>
    </Pad>
"""

    xpm_content += """  </Pads>
</MPCVObject>
"""

    # Write XPM file
    xpm_path = os.path.join(output_dir, f"{name}.xpm")
    try:
        with open(xpm_path, "w") as f:
            f.write(xpm_content)
        log.info("Generated XPM: %s (%d pads)", xpm_path, len(pad_entries))
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
