"""Lightweight modes that show 'coming soon' OLED but accept input.

These modes route their primary inputs to the underlying session
mutations (so e.g. Stop button still works) but don't expose all
their parameters yet.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from .base import Mode

if TYPE_CHECKING:
    from ui.push2_control import Push2Control


def _draw_stub_oled(w: int, h: int, title: str, subtitle: str = "") -> Image.Image:
    img = Image.new("RGB", (w, h), color=(0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        f_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        f_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except Exception:
        f_big = ImageFont.load_default()
        f_sm = ImageFont.load_default()
    d.text((20, 40), title, fill=(255, 255, 255), font=f_big)
    if subtitle:
        d.text((20, 80), subtitle, fill=(180, 180, 180), font=f_sm)
    return img


class DeviceMode(Mode):
    name = "device"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        t = self.control.selected_track or 0
        sess = self.control.session
        track_name = sess.tracks[t].name if t < len(sess.tracks) else ""
        return _draw_stub_oled(w, h, "DEVICE",
                                f"Track: {track_name} (chain editing soon)")


class BrowseMode(Mode):
    name = "browse"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        return _draw_stub_oled(w, h, "BROWSE",
                                "Hot Swap = Shift+Browse (coming online)")


class ClipEditorMode(Mode):
    name = "clip_editor"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        t = self.control.selected_track or 0
        s = self.control.selected_scene or 0
        clip = self.control.session.get_clip(t, s)
        if clip is None:
            return _draw_stub_oled(w, h, "CLIP", "no clip selected")
        return _draw_stub_oled(w, h, f"CLIP: {clip.name or '(unnamed)'}",
                                f"length {clip.length_beats:.1f} beats")


class MasterMode(Mode):
    name = "master"
    def on_encoder_turn(self, name: str, delta: int) -> bool:
        if name == "master":
            self.control.session.master_volume = max(
                0.0, min(1.0,
                          self.control.session.master_volume + delta * 0.02))
            self.control.request_redraw()
            return True
        return False
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        vol = self.control.session.master_volume
        return _draw_stub_oled(w, h, "MASTER", f"volume {int(vol*100)}%")


class SetupMode(Mode):
    name = "setup"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        return _draw_stub_oled(w, h, "SETUP", "device settings (more soon)")


class UserMode(Mode):
    name = "user"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        return _draw_stub_oled(w, h, "USER", "raw MIDI passthrough mode")


class OverviewMode(Mode):
    name = "overview"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        return _draw_stub_oled(w, h, "OVERVIEW", "Session map (Layout-held)")
