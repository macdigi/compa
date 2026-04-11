"""Auto-map classified drum samples to MPC-style pad layout.

Takes the output of drum_detector.scan_library() and maps samples
to pad positions following the standard MPC drum kit convention.
Overflow samples go to Bank B, C, etc.

Usage::

    from engine.drum_detector import scan_library
    from engine.drum_mapper import auto_map

    classified = scan_library("/path/to/sample/library")
    pads = auto_map(classified)
    # pads[0] = {"path": ".../kick_01.wav", "filename": "kick_01.wav", ...}
    # pads[2] = {"path": ".../snare_01.wav", ...}
"""

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# How many pads total (8 banks x 16 pads)
TOTAL_PADS = 128

# ── Standard MPC drum pad layout (Bank A, pads 0-15) ────────────────
#
# Physical pad layout (MPC/Force):
#   [13] [14] [15] [16]     ← top row
#   [ 9] [10] [11] [12]
#   [ 5] [ 6] [ 7] [ 8]
#   [ 1] [ 2] [ 3] [ 4]     ← bottom row (index 0-3)
#
# Standard mapping (GM-ish, common in MPC factory kits):
#   Pad  1 (idx  0): Kick 1
#   Pad  2 (idx  1): Kick 2
#   Pad  3 (idx  2): Snare 1
#   Pad  4 (idx  3): Snare 2
#   Pad  5 (idx  4): Closed Hat
#   Pad  6 (idx  5): Open Hat
#   Pad  7 (idx  6): Clap
#   Pad  8 (idx  7): Rim / Snap
#   Pad  9 (idx  8): Tom 1 (Low)
#   Pad 10 (idx  9): Tom 2 (Mid)
#   Pad 11 (idx 10): Tom 3 (High)
#   Pad 12 (idx 11): Shaker / Tambourine
#   Pad 13 (idx 12): Crash
#   Pad 14 (idx 13): Ride
#   Pad 15 (idx 14): Perc 1
#   Pad 16 (idx 15): Perc 2 / FX

# Map drum type → list of pad indices (in Bank A) for that type.
# First sample of each type fills the first slot, second fills second, etc.
DEFAULT_PAD_MAP: dict[str, list[int]] = {
    "kick":       [0, 1],
    "snare":      [2, 3],
    "hat_closed": [4],
    "hat_open":   [5],
    "clap":       [6],
    "rim":        [7],
    "snap":       [7],       # Shares pad 8 with rim (snap is alternate)
    "tom":        [8, 9, 10],
    "shaker":     [11],
    "tambourine": [11],      # Shares with shaker
    "crash":      [12],
    "ride":       [13],
    "cymbal":     [12, 13],  # Shares with crash/ride
    "cowbell":    [14],
    "perc":       [14, 15],
    "fx":         [15],
}


def _wav_duration(path: str) -> float:
    """Get WAV duration in seconds (quick header read)."""
    try:
        import soundfile as sf
        info = sf.info(path)
        return info.duration
    except Exception:
        return 0.0


def auto_map(classified: dict[str, list[str]],
             pad_map: Optional[dict[str, list[int]]] = None) -> list[Optional[dict]]:
    """Map classified drum samples to 128 pad slots.

    Args:
        classified: Output from drum_detector.scan_library().
                    {type_name: [file_paths]}
        pad_map: Optional custom type→pad mapping. Uses DEFAULT_PAD_MAP if None.

    Returns:
        List of 128 pad dicts (same format as Kit Builder _pads).
        Each is None (empty) or {"path", "filename", "duration", "drum_type"}.
    """
    if pad_map is None:
        pad_map = DEFAULT_PAD_MAP

    pads: list[Optional[dict]] = [None] * TOTAL_PADS
    used_pads: set[int] = set()

    # Phase 1: Fill primary slots from the pad map
    for drum_type, primary_slots in pad_map.items():
        samples = classified.get(drum_type, [])
        if not samples:
            continue

        for i, sample_path in enumerate(samples):
            if i < len(primary_slots):
                # Use the designated slot
                pad_idx = primary_slots[i]
            else:
                # Overflow: find next available slot in higher banks
                pad_idx = _find_overflow_slot(pad_idx=primary_slots[0],
                                              used=used_pads)
            if pad_idx is None:
                continue
            if pad_idx in used_pads:
                pad_idx = _find_overflow_slot(pad_idx, used_pads)
                if pad_idx is None:
                    continue

            pads[pad_idx] = {
                "path": sample_path,
                "filename": os.path.basename(sample_path),
                "duration": _wav_duration(sample_path),
                "drum_type": drum_type,
            }
            used_pads.add(pad_idx)

    # Phase 2: Place "unknown" samples in remaining empty slots
    unknowns = classified.get("unknown", [])
    if unknowns:
        for sample_path in unknowns:
            # Find first empty pad (starting from pad 0)
            empty_idx = None
            for idx in range(TOTAL_PADS):
                if idx not in used_pads:
                    empty_idx = idx
                    break
            if empty_idx is None:
                break  # All pads full
            pads[empty_idx] = {
                "path": sample_path,
                "filename": os.path.basename(sample_path),
                "duration": _wav_duration(sample_path),
                "drum_type": "unknown",
            }
            used_pads.add(empty_idx)

    assigned = sum(1 for p in pads if p is not None)
    log.info("Auto-mapped %d samples to %d pads", assigned, TOTAL_PADS)
    return pads


def _find_overflow_slot(pad_idx: int, used: set[int]) -> Optional[int]:
    """Find the next available pad slot for overflow samples.

    Tries the same position in the next bank (e.g., A01 overflow → B01),
    then searches linearly from the end of the current bank.
    """
    base = pad_idx % 16  # Position within bank

    # Try same position in each subsequent bank
    for bank in range(8):
        candidate = bank * 16 + base
        if candidate not in used:
            return candidate

    # Try any empty slot from the beginning
    for idx in range(TOTAL_PADS):
        if idx not in used:
            return idx

    return None  # All 128 pads full
