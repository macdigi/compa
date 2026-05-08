"""Push 2 LED palette — 128 indexed colors with curated track colors.

Push 2 stores a per-device 128-entry palette. Sending a Note On to a
pad with `velocity = idx` lights it with palette[idx]. We populate
the palette explicitly at boot rather than relying on factory defaults.

The palette layout we use:
- 0      black (off)
- 1–119  curated colors covering hue/saturation/brightness space, used
         for clip + track + state colors.
- 120    queued (used for clip launch queue blink — pale blue-white)
- 121    recording red
- 122    white
- 123    light gray
- 124    dark gray
- 125    pure blue
- 126    pure green
- 127    pure red
"""
from __future__ import annotations

import colorsys

from . import constants as C
from . import sysex


def _hsv_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def build_palette() -> list[tuple[int, int, int, int]]:
    """Return the full 128-entry palette as (r, g, b, w) tuples."""
    palette: list[tuple[int, int, int, int]] = [(0, 0, 0, 0)] * 128

    # Slot 0 = off.
    palette[0] = (0, 0, 0, 0)

    # Slots 1–119: curated rainbow with brightness + saturation variations.
    # 12 hue positions × 10 brightness/saturation combos = 120; we use 119.
    idx = 1
    hues = 12
    levels = [
        (0.6, 0.4),  # dim, slightly desaturated
        (0.8, 0.6),
        (1.0, 0.7),
        (1.0, 0.85),
        (1.0, 1.0),  # full bright
        (0.7, 0.5),
        (0.9, 0.7),
        (0.5, 0.9),  # bright pastel
        (1.0, 0.55),
        (0.85, 0.45),  # mid
    ]
    for s, v in levels:
        for h_step in range(hues):
            if idx >= 120:
                break
            h = h_step / hues
            r, g, b = _hsv_rgb(h, s, v)
            palette[idx] = (r, g, b, 0)
            idx += 1
        if idx >= 120:
            break

    # Named slots
    palette[120] = (180, 200, 255, 0)  # queued — pale blue-white
    palette[121] = (255, 40, 40, 0)    # recording red
    palette[122] = (255, 255, 255, 255)  # white (with white channel for warmth)
    palette[123] = (160, 160, 160, 80)
    palette[124] = (60, 60, 60, 0)
    palette[125] = (0, 0, 255, 0)
    palette[126] = (0, 255, 0, 0)
    palette[127] = (255, 0, 0, 0)

    return palette


def upload_palette_messages(palette: list[tuple[int, int, int, int]]) -> list[bytes]:
    """Return a list of sysex messages to upload + reapply the palette.

    Caller sends each one in order through the Live MIDI port.
    """
    msgs: list[bytes] = []
    for idx, (r, g, b, w) in enumerate(palette):
        msgs.append(sysex.set_palette_entry(idx, r, g, b, w))
    msgs.append(sysex.reapply_palette())
    return msgs


# ── Convenience helpers for converting "I want this color" → palette idx ──

def closest_palette_index(palette: list[tuple[int, int, int, int]],
                          r: int, g: int, b: int,
                          search_range: tuple[int, int] = (1, 119)) -> int:
    """Return the palette index whose RGB is closest to (r, g, b).

    Restricts the search to the curated range by default so we don't
    accidentally pick a special slot (white/red/etc.).
    """
    best_idx = search_range[0]
    best_dist = float("inf")
    for i in range(search_range[0], search_range[1] + 1):
        pr, pg, pb, _ = palette[i]
        d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


def track_color_index(track_idx: int) -> int:
    """Default per-track color for tracks 0–7."""
    return C.TRACK_COLOR_INDICES[track_idx % len(C.TRACK_COLOR_INDICES)]
