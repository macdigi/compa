"""Persistence for the per-(device, pattern) Compa step grids.

The Push 2 pattern mode snapshots PiSequencer.grid into
app._compa_step_grids each time the user launches a different
pattern. This module handles JSON save/load so those snapshots
survive a service restart instead of starting empty every boot.

File format (one JSON file, default at sessions/compa_step_grids.json):

    {
      "P-6:0":   [ [[active, velocity], ...] per step, ... per pad ],
      "P-6:1":   [...],
      "SP-404MKII:5": [...]
    }

Keys are "<device_short_name>:<pattern_idx>". Values are the same
nested list shape produced by app._save_step_grid (per-pad rows of
(active, velocity) tuples).
"""

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def save(grids: dict, path: str) -> bool:
    """Serialize `grids` to `path` atomically. Returns True on success."""
    if not grids:
        # Don't bother creating an empty file.
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception as e:
        log.warning("step-grids: makedirs %s failed: %s", path, e)
        return False
    out: dict[str, Any] = {}
    for key, grid in grids.items():
        try:
            dev, pat = key
            out[f"{dev}:{int(pat)}"] = [
                [[int(active), int(vel)] for (active, vel) in row]
                for row in grid
            ]
        except Exception:
            continue
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.warning("step-grids: write %s failed: %s", path, e)
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        return False


def load(path: str) -> dict:
    """Read step-grid JSON. Returns empty dict on missing or malformed."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning("step-grids: load %s failed: %s", path, e)
        return {}
    out: dict = {}
    if not isinstance(data, dict):
        return {}
    for key, grid in data.items():
        if not isinstance(key, str) or ":" not in key:
            continue
        dev, _, pat_str = key.rpartition(":")
        try:
            pat = int(pat_str)
        except ValueError:
            continue
        try:
            out[(dev, pat)] = [
                [(int(active), int(vel)) for (active, vel) in row]
                for row in grid
            ]
        except Exception:
            continue
    return out
