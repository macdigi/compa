"""Drum type detection from folder and file names.

Classifies audio files as kick, snare, hat, clap, etc. by matching
folder names and filenames against common naming conventions used in
sample libraries (Splice, Cymatics, KSHMR, Goldbaby, etc.).

Usage::

    # Single file
    drum_type = detect_type("/samples/Kicks/808_kick_hard.wav")
    # Returns "kick"

    # Whole library
    classified = scan_library("/samples/My Kit")
    # Returns {"kick": ["/samples/My Kit/Kicks/808.wav", ...],
    #          "snare": [...], "hat_closed": [...], ...}
"""

import logging
import os
import re
from typing import Optional

log = logging.getLogger(__name__)

# ── Drum type definitions ────────────────────────────────────────────

DRUM_TYPES = [
    "kick", "snare", "hat_closed", "hat_open", "clap", "rim",
    "tom", "crash", "ride", "perc", "shaker", "cymbal",
    "snap", "cowbell", "tambourine", "fx",
]

# Patterns matched against LOWERCASED folder or file names.
# Checked in order — first match wins. More specific patterns first.
_PATTERNS: dict[str, list[str]] = {
    "hat_open": [
        "open hat", "open hi", "openhat", "openhh",
        "op hat", "op_hat", "op-hat", "ohh", "o hat", "o_hat",
        "open_hh", "open-hat", "openhat",
        "hat_open", "hat open", "hat-open",
    ],
    "hat_closed": [
        "closed hat", "closed hi", "closedhat", "closedhh",
        "cl hat", "cl_hat", "cl-hat", "chh", "c hat", "c_hat",
        "closed_hh", "closed-hat", "closehat",
        "hat_closed", "hat closed", "hat-closed",
        "hihat", "hi hat", "hi-hat", "hi_hat", "hats",
        "hat",  # Generic "hat" folder = default to closed
    ],
    "kick": [
        "kick", "kicks", "bass drum", "bassdrum", "bass_drum",
        "bd", "kck", "kik", "808 kick", "kick808", "909kick",
        "boom", "thump",
    ],
    "snare": [
        "snare", "snares", "sn ", "sd ", "snr",
        "snare_", "snare-", "rimsnare",
    ],
    "clap": [
        "clap", "claps", "cp", "handclap", "hand_clap",
    ],
    "snap": [
        "snap", "snaps", "finger snap", "fingersnap",
    ],
    "rim": [
        "rimshot", "rim shot", "rim_shot", "rim-shot",
        "rim", "rs",
    ],
    "tom": [
        "tom", "toms", "tom_", "tom-", "floor tom", "rack tom",
        "hi tom", "lo tom", "mid tom", "tm",
    ],
    "crash": [
        "crash", "crashes", "cr_", "cr-",
    ],
    "ride": [
        "ride", "rides", "rd_", "rd-",
    ],
    "cymbal": [
        "cymbal", "cymbals", "cym_", "cym-",
    ],
    "shaker": [
        "shaker", "shakers", "shk", "maracas",
    ],
    "tambourine": [
        "tambourine", "tamb", "tambo",
    ],
    "cowbell": [
        "cowbell", "cow bell", "cow_bell", "cb_",
    ],
    "perc": [
        "perc", "percussion", "misc", "other", "extra",
        "conga", "bongo", "djembe", "tabla", "woodblock", "block",
        "triangle", "agogo", "guiro", "cabasa", "timbale",
    ],
    "fx": [
        "fx", "sfx", "effect", "effects", "riser", "sweep",
        "noise", "texture", "atmos", "ambient", "foley",
        "vinyl", "tape", "glitch",
    ],
}

# Audio file extensions we care about
AUDIO_EXTS = {".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg"}


def detect_type(filepath: str) -> Optional[str]:
    """Detect drum type from a file's path.

    Checks parent folder name first (stronger signal), then filename.
    Returns a type string from DRUM_TYPES or None if undetectable.
    """
    # Normalize path components to lowercase
    parent_dir = os.path.basename(os.path.dirname(filepath)).lower()
    filename_stem = os.path.splitext(os.path.basename(filepath))[0].lower()

    # Strategy 1: Match filename FIRST (more specific — "hat_open_01" beats "HiHats" folder)
    file_match = _match_patterns(filename_stem)
    if file_match:
        return file_match

    # Strategy 2: Match parent folder name
    folder_match = _match_patterns(parent_dir)
    if folder_match:
        return folder_match

    # Strategy 3: Check grandparent folder (e.g. Drums/Kicks/Processed/)
    grandparent = os.path.basename(
        os.path.dirname(os.path.dirname(filepath))).lower()
    if grandparent:
        gp_match = _match_patterns(grandparent)
        if gp_match:
            return gp_match

    return None


def _match_patterns(text: str) -> Optional[str]:
    """Match text against all pattern lists. Returns first matching type."""
    if not text:
        return None
    for drum_type, patterns in _PATTERNS.items():
        for pattern in patterns:
            # Word boundary-ish match: pattern appears as substring
            # with non-alpha boundaries (or at start/end of string)
            if pattern in text:
                return drum_type
            # Also try with underscores/hyphens replaced
            normalized = text.replace("-", " ").replace("_", " ")
            if pattern in normalized:
                return drum_type
    return None


def scan_library(root_dir: str, max_depth: int = 4) -> dict[str, list[str]]:
    """Scan a sample library directory and classify all audio files.

    Returns a dict mapping drum type → list of file paths.
    Unclassified files go under "unknown".
    """
    classified: dict[str, list[str]] = {t: [] for t in DRUM_TYPES}
    classified["unknown"] = []

    if not os.path.isdir(root_dir):
        log.warning("scan_library: not a directory: %s", root_dir)
        return classified

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Limit depth
        depth = dirpath.replace(root_dir, "").count(os.sep)
        if depth > max_depth:
            dirnames.clear()
            continue

        # Skip hidden directories
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in AUDIO_EXTS:
                continue

            filepath = os.path.join(dirpath, fname)
            dtype = detect_type(filepath)
            if dtype:
                classified[dtype].append(filepath)
            else:
                classified["unknown"].append(filepath)

    # Log summary
    total = sum(len(v) for v in classified.values())
    found = {k: len(v) for k, v in classified.items() if v}
    log.info("Scanned %s: %d files — %s", root_dir, total, found)

    return classified


def scan_summary(classified: dict[str, list[str]]) -> str:
    """Human-readable summary of scan results."""
    parts = []
    for dtype in DRUM_TYPES:
        count = len(classified.get(dtype, []))
        if count:
            label = dtype.replace("_", " ").title()
            parts.append(f"{count} {label}")
    unknown = len(classified.get("unknown", []))
    if unknown:
        parts.append(f"{unknown} other")
    return ", ".join(parts) if parts else "No samples found"
