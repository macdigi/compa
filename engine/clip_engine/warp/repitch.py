"""Re-Pitch warp — straight resample. Pitch follows tempo. Cheapest mode."""
from __future__ import annotations

import numpy as np


def resample_block(buf: np.ndarray, src_pos: float,
                   frames: int, ratio: float) -> tuple[np.ndarray, float]:
    """Read `frames` samples from `buf` starting at fractional position
    `src_pos`, advancing at speed `ratio` samples-per-output-sample.

    Returns (output, new_src_pos). Linear interpolation, no anti-alias.
    For drum/loop-style clips this is fine; for melodic re-pitch the
    user can switch to Beats / Complex later.
    """
    if buf.ndim == 1:
        n_ch = 1
    else:
        n_ch = buf.shape[1]
    out = np.zeros((frames, max(1, n_ch)), dtype=np.float32)
    pos = src_pos
    L = len(buf)
    for i in range(frames):
        ip = int(pos)
        if ip + 1 >= L:
            # Loop back to start
            pos = pos % L
            ip = int(pos)
        frac = pos - ip
        if buf.ndim == 1:
            out[i, 0] = (buf[ip] * (1 - frac) + buf[ip + 1] * frac)
        else:
            out[i] = buf[ip] * (1 - frac) + buf[ip + 1] * frac
        pos += ratio
    return out, pos
