"""SP-404 MK2 local storage reader.

Reads the Roland Librarian app's local cache to show what's loaded
on the SP-404 MK2. The cache lives at:
  ~/SP404 User/ROLAND/SP-404MKII_LOCAL/PROJECT_XX/SMPL/

File formats:
  BANKN-PP.SMP — sample file (RFWV header + audio data)
  PADCONF.BIN  — pad configuration (RFPD header + pad params)

SMP (RFWV) format:
  0x00: "RFWV" magic (4 bytes)
  0x04: data_size (uint32 BE)
  0x08: sample_rate (uint32 BE) — typically 48000
  0x0C: channels (uint32 BE) — 1=mono, 2=stereo
  0x10: bit_depth (uint32 BE) — 16
"""

import logging
import os
import struct
from typing import Optional

log = logging.getLogger(__name__)

# Default locations
SP404_LOCAL_PATHS = [
    os.path.expanduser("~/SP404 User/ROLAND/SP-404MKII_LOCAL"),
    os.path.expanduser("~/Documents/Roland/SP-404MKII"),
]


def find_sp404_cache() -> str:
    """Find the SP-404 Librarian local cache directory."""
    for path in SP404_LOCAL_PATHS:
        if os.path.isdir(path):
            return path
    return ""


def list_projects(cache_dir: str = "") -> list[dict]:
    """List all projects in the SP-404 cache."""
    if not cache_dir:
        cache_dir = find_sp404_cache()
    if not cache_dir:
        return []

    projects = []
    try:
        for name in sorted(os.listdir(cache_dir)):
            proj_dir = os.path.join(cache_dir, name)
            if os.path.isdir(proj_dir) and name.startswith("PROJECT_"):
                smpl_dir = os.path.join(proj_dir, "SMPL")
                num_samples = 0
                if os.path.isdir(smpl_dir):
                    num_samples = sum(1 for f in os.listdir(smpl_dir)
                                     if f.endswith(".SMP"))
                projects.append({
                    "name": name,
                    "path": proj_dir,
                    "num_samples": num_samples,
                })
    except Exception as e:
        log.error("Error listing projects: %s", e)

    return projects


def read_project_pads(project_dir: str) -> list[Optional[dict]]:
    """Read all pad data from a project.

    Returns a list of 160 entries (10 banks x 16 pads).
    Each entry is None (empty) or a dict with sample info.
    """
    pads: list[Optional[dict]] = [None] * 160
    smpl_dir = os.path.join(project_dir, "SMPL")

    if not os.path.isdir(smpl_dir):
        return pads

    for filename in os.listdir(smpl_dir):
        if not filename.endswith(".SMP"):
            continue

        # Parse BANKN-PP.SMP filename
        try:
            stem = filename[:-4]  # Remove .SMP
            parts = stem.split("-")
            bank_str = parts[0].replace("BANK", "")
            pad_str = parts[1]
            bank_num = int(bank_str) - 1  # 0-indexed
            pad_num = int(pad_str) - 1     # 0-indexed
            pad_idx = bank_num * 16 + pad_num
        except (ValueError, IndexError):
            continue

        if pad_idx < 0 or pad_idx >= 160:
            continue

        filepath = os.path.join(smpl_dir, filename)
        info = read_smp_header(filepath)
        if info:
            info["filename"] = filename
            info["path"] = filepath
            bank_letter = chr(65 + bank_num)
            info["pad_id"] = f"{bank_letter}{pad_num + 1:02d}"
            pads[pad_idx] = info

    return pads


def read_smp_header(filepath: str) -> Optional[dict]:
    """Read the RFWV header from an .SMP file."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(32)

        if len(header) < 20:
            return None

        magic = header[:4]
        if magic != b"RFWV":
            return None

        data_size = struct.unpack_from(">I", header, 4)[0]
        sample_rate = struct.unpack_from(">I", header, 8)[0]
        channels = struct.unpack_from(">I", header, 12)[0]
        bit_depth = struct.unpack_from(">I", header, 16)[0]

        # Calculate duration
        if sample_rate > 0 and channels > 0 and bit_depth > 0:
            bytes_per_sample = bit_depth // 8
            num_frames = data_size // (channels * bytes_per_sample)
            duration = num_frames / sample_rate
        else:
            duration = 0.0

        file_size = os.path.getsize(filepath)

        return {
            "data_size": data_size,
            "sample_rate": sample_rate,
            "channels": channels,
            "bit_depth": bit_depth,
            "duration": round(duration, 2),
            "file_size": file_size,
        }

    except Exception as e:
        log.error("Error reading SMP %s: %s", filepath, e)
        return None
