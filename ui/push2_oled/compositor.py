"""OLED compositor — PIL Image → Push 2 wire-format frame.

Sits between mode `draw_oled()` (which returns a PIL Image) and
the Push 2 USB display sender (which expects bytes from
engine/push2/pixel.pack_frame).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from engine.push2driver import constants as C
from engine.push2driver.pixel import pack_frame, blank_frame


def image_to_frame(img: Image.Image) -> bytes:
    """Convert a PIL RGB image to a Push 2 wire-format frame."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.size != (C.DISPLAY_WIDTH, C.DISPLAY_HEIGHT):
        img = img.resize((C.DISPLAY_WIDTH, C.DISPLAY_HEIGHT))
    arr = np.asarray(img, dtype=np.uint8)
    return pack_frame(arr)


def blank() -> bytes:
    return blank_frame()
