"""Mix mode — encoders = volume, Shift+encoder = pan.

Lower display row = mute toggles per track.
Upper display row = solo toggles per track.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PIL import Image

from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index
from .base import Mode

if TYPE_CHECKING:
    from ui.push2_control import Push2Control


class MixMode(Mode):
    name = "mix"

    def __init__(self, control: "Push2Control") -> None:
        super().__init__(control)
        self.track_offset = 0

    def on_encoder_turn(self, name: str, delta: int) -> bool:
        if not name.startswith("track"):
            return False
        try:
            i = int(name[5:]) - 1
        except ValueError:
            return False
        track_idx = i + self.track_offset
        sess = self.control.session
        if track_idx >= len(sess.tracks):
            return False
        track = sess.tracks[track_idx]
        mods = self.control.modifiers
        step = 0.005 if "shift" in mods else 0.02
        if "shift" in mods:
            track.pan = max(-1.0, min(1.0, track.pan + delta * step))
        else:
            track.volume = max(0.0, min(1.0, track.volume + delta * step))
        self.control.request_redraw()
        return True

    def on_button(self, name: str, is_press: bool) -> bool:
        if not is_press:
            return False
        sess = self.control.session
        if name.startswith("lower_display_"):
            try:
                i = int(name.split("_")[2]) - 1
            except ValueError:
                return False
            t = i + self.track_offset
            if t < len(sess.tracks):
                sess.tracks[t].mute = not sess.tracks[t].mute
                self.control.request_redraw()
            return True
        if name.startswith("upper_display_"):
            try:
                i = int(name.split("_")[2]) - 1
            except ValueError:
                return False
            t = i + self.track_offset
            if t < len(sess.tracks):
                sess.tracks[t].solo = not sess.tracks[t].solo
                self.control.request_redraw()
            return True
        return False

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        # In v1 Mix mode pads = vertical bargraph faders, 8 cols × 8 rows.
        out: dict[tuple[int, int], tuple[int, int]] = {}
        sess = self.control.session
        for c in range(8):
            t = c + self.track_offset
            if t >= len(sess.tracks):
                continue
            track = sess.tracks[t]
            color = track.color or track_color_index(t)
            level = int(track.volume * 8)
            for r in range(8):
                if r < level:
                    out[(c, r)] = (color, C.ANIM_STATIC)
        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        out: dict[int, tuple[int, int]] = {}
        sess = self.control.session
        for i, cc in enumerate(C.BTN_LOWER_DISPLAY_CCS):
            t = i + self.track_offset
            if t >= len(sess.tracks):
                continue
            if sess.tracks[t].mute:
                out[cc] = (C.COLOR_RED, C.ANIM_STATIC)
            else:
                out[cc] = (C.COLOR_DARK_GRAY, C.ANIM_STATIC)
        for i, cc in enumerate(C.BTN_UPPER_DISPLAY_CCS):
            t = i + self.track_offset
            if t >= len(sess.tracks):
                continue
            if sess.tracks[t].solo:
                out[cc] = (126, C.ANIM_STATIC)  # green
            else:
                out[cc] = (C.COLOR_DARK_GRAY, C.ANIM_STATIC)
        return out

    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        from ui.push2_oled.frames.mix_frame import draw_mix_frame
        return draw_mix_frame(w, h, self.control, self)
