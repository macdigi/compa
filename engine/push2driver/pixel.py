"""Push 2 display pixel packing — RGB888 → BGR565 with the XOR pattern.

The Push 2 display protocol is documented in detail in
reference_push2_model.md. Key points:

- Per pixel: 16 bits, bit ordering b4..b0 g5..g0 r4..r0 (MSB to LSB).
  Net effect: BGR565 with red in the low 5 bits.
- Per line: 1920 bytes pixel data + 128 bytes filler = 2048 bytes.
- Each line's pixel bytes are XORed with the repeating pattern
  0xE7 0xF3 0xE7 0xFF before transmission (EMI mitigation). The
  filler bytes are NOT XORed.
- 160 lines per frame.

This module owns the pixel-pack-and-XOR step and nothing else.
"""
from __future__ import annotations

import numpy as np

from . import constants as C


# Pre-built XOR mask for one full line of pixel bytes.
_XOR_LINE = np.frombuffer(
    (C.DISPLAY_XOR_PATTERN * (C.DISPLAY_LINE_PIXEL_BYTES // len(C.DISPLAY_XOR_PATTERN)
                              + 1))[:C.DISPLAY_LINE_PIXEL_BYTES],
    dtype=np.uint8,
)


def rgb_to_bgr565(rgb: np.ndarray) -> np.ndarray:
    """Convert an HxWx3 uint8 RGB array to HxW uint16 BGR565 values.

    Bit layout (high → low): B4 B3 B2 B1 B0 G5 G4 G3 G2 G1 G0 R4 R3 R2 R1 R0.
    """
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError(f"expected HxWx3 uint8, got {rgb.shape} {rgb.dtype}")
    r = rgb[..., 0].astype(np.uint16) >> 3       # 5 bits
    g = rgb[..., 1].astype(np.uint16) >> 2       # 6 bits
    b = rgb[..., 2].astype(np.uint16) >> 3       # 5 bits
    return (b << 11) | (g << 5) | r


def pack_frame(rgb: np.ndarray) -> bytes:
    """Pack a 160×960×3 uint8 RGB array into a Push 2 wire-format frame.

    Returns the bytes payload that follows the 16-byte frame header.
    Length is exactly DISPLAY_FRAME_BYTES (327,680).
    """
    h, w = rgb.shape[:2]
    if (h, w) != (C.DISPLAY_HEIGHT, C.DISPLAY_WIDTH):
        raise ValueError(f"expected {C.DISPLAY_HEIGHT}x{C.DISPLAY_WIDTH}, got {h}x{w}")

    # HxW uint16 BGR565
    pixels = rgb_to_bgr565(rgb)

    # Each line: 1920 bytes of pixels (little-endian) + 128 zero filler.
    # uint16 → 2 uint8 (little endian) per pixel.
    line_pixels = pixels.astype("<u2").view(np.uint8).reshape(h, -1)
    if line_pixels.shape[1] != C.DISPLAY_LINE_PIXEL_BYTES:
        raise RuntimeError(f"pixel line size {line_pixels.shape[1]}")

    # XOR per line
    line_pixels = np.bitwise_xor(line_pixels, _XOR_LINE)

    # Append 128-byte filler to each line
    filler = np.zeros((h, C.DISPLAY_LINE_FILLER_BYTES), dtype=np.uint8)
    full_lines = np.concatenate((line_pixels, filler), axis=1)

    return full_lines.tobytes()


def blank_frame() -> bytes:
    """Black frame, in wire format."""
    rgb = np.zeros((C.DISPLAY_HEIGHT, C.DISPLAY_WIDTH, 3), dtype=np.uint8)
    return pack_frame(rgb)
