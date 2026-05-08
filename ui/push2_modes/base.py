"""Base Mode interface for Push 2."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from PIL import Image
    from ui.push2_control import Push2Control


class Mode:
    """All Push 2 modes implement this interface.

    Methods are called by Push2Control on the control thread (event
    handlers) and the render thread (draw_pads, draw_oled). Modes do
    NOT touch the audio thread directly — they go through the
    ClipEngine API which handles thread safety.
    """

    name: str = "base"

    def __init__(self, control: "Push2Control") -> None:
        self.control = control

    # ── Lifecycle ─────────────────────────────────────────────────
    def enter(self) -> None: ...
    def exit(self) -> None: ...

    # ── Input handlers ─────────────────────────────────────────────
    # All methods take (event, modifiers); return True if handled.
    def on_pad(self, col: int, row: int, velocity: int,
               is_press: bool) -> bool:
        return False

    def on_button(self, name: str, is_press: bool) -> bool:
        return False

    def on_encoder_turn(self, name: str, delta: int) -> bool:
        return False

    def on_encoder_touch(self, note: int, is_touched: bool) -> bool:
        return False

    def on_touch_strip(self, value: int, is_touch: bool) -> bool:
        return False

    # ── Render ─────────────────────────────────────────────────────
    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        """Return {(col, row): (palette_idx, anim_channel)} for every lit pad.

        Pads not in the dict are turned off. Animation channel:
        0 = static, 6–10 = pulse, 11–15 = blink. See constants.
        """
        return {}

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        """Return {cc: (palette_idx, anim_channel)} for buttons we own."""
        return {}

    def draw_oled(self, w: int, h: int) -> Optional["Image.Image"]:
        """Return a PIL Image of size (w, h). None = blank."""
        return None
