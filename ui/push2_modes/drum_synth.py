"""Push 2 Drum Synth mode for Compa Studio."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from engine.studio_drum_synth import (
    DRUM_SYNTH_KITS,
    DRUM_SYNTH_PAD_COUNT,
    drum_synth_track_index,
    drum_synth_voice_specs,
    voice_display_name,
)
from engine.studio_modules import known_modules
from .base import Mode


@dataclass(frozen=True)
class _NavItem:
    short_label: str
    tab: str


class DrumSynthMode(Mode):
    name = "drum_synth"

    def _screen(self):
        app = getattr(self.control, "host_app", None)
        screens = getattr(app, "screens", {}) if app is not None else {}
        return screens.get("studio") or screens.get("clips")

    def _nav_items(self):
        modules_by_tab = {module.tab: module for module in known_modules()}
        tabs = (
            ("HOME", "overview"),
            ("CLIPS", "clips"),
            ("PERFORM", "performer"),
            ("SAMPLER", "sampler"),
            ("DRUM", "drum_synth"),
            ("SYNTH", "synth"),
            ("MIX", "mixer"),
            ("REC", "recorder"),
        )
        return tuple(
            _NavItem(label, tab)
            for label, tab in tabs
            if tab == "overview" or tab in modules_by_tab
        )

    def _select_tab(self, idx: int) -> bool:
        items = self._nav_items()
        if not (0 <= idx < len(items)):
            return False
        screen = self._screen()
        if screen is None:
            return False
        screen._set_tab(items[idx].tab)
        self.control.request_redraw()
        return True

    def _pad_index(self, col: int, row: int) -> int | None:
        if col >= 4 or row >= 4:
            return None
        return (3 - row) * 4 + col

    def _selected_pad(self) -> int:
        screen = self._screen()
        return int(getattr(screen, "_drum_synth_pad_idx", 0) or 0)

    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        idx = self._pad_index(col, row)
        if idx is None:
            return True
        screen = self._screen()
        if screen is None:
            return False
        if is_press:
            screen._trigger_drum_synth_pad(self.control.session, idx, velocity)
            self.control.request_redraw()
        return True

    def on_button(self, name: str, is_press: bool) -> bool:
        if not is_press:
            return False
        screen = self._screen()
        if name.startswith("upper_display_"):
            try:
                return self._select_tab(int(name.rsplit("_", 1)[1]) - 1)
            except ValueError:
                return False
        if screen is None:
            return False
        sess = self.control.session
        actions = {
            "lower_display_1": screen._stop_drum_synth,
            "lower_display_2": lambda: screen._set_drum_synth_kit(sess, "808"),
            "lower_display_3": lambda: screen._set_drum_synth_kit(sess, "909"),
            "lower_display_4": lambda: screen._adjust_drum_synth_param(
                sess, "tone", -0.05),
            "lower_display_5": lambda: screen._adjust_drum_synth_param(
                sess, "tone", 0.05),
            "lower_display_6": lambda: screen._adjust_drum_synth_param(
                sess, "decay", -0.05),
            "lower_display_7": lambda: screen._adjust_drum_synth_param(
                sess, "decay", 0.05),
            "lower_display_8": lambda: screen._ensure_drum_synth_track(sess),
        }
        action = actions.get(name)
        if action is None:
            return False
        action()
        self.control.request_redraw()
        return True

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        selected = self._selected_pad()
        specs = drum_synth_voice_specs(
            self.control.session, drum_synth_track_index(self.control.session))
        out: dict[tuple[int, int], tuple[int, int]] = {}
        for idx in range(DRUM_SYNTH_PAD_COUNT):
            col = idx % 4
            row = 3 - (idx // 4)
            voice_type = specs[idx].get("voice_type") if idx < len(specs) else ""
            if idx == selected:
                color, anim = C.COLOR_WHITE, C.ANIM_STATIC
            elif voice_type in ("kick", "tom", "conga"):
                color, anim = C.COLOR_GREEN, C.ANIM_STATIC
            elif voice_type in ("snare", "clap", "rim"):
                color, anim = C.COLOR_BLUE, C.ANIM_STATIC
            elif voice_type:
                color, anim = C.COLOR_DARK_GRAY, C.ANIM_STATIC
            else:
                color, anim = C.COLOR_BLACK, C.ANIM_STATIC
            out[(col, row)] = (color, anim)
        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        buttons: dict[int, tuple[int, int]] = {}
        screen = self._screen()
        current = (
            getattr(screen, "_tab", "drum_synth")
            if screen is not None else "drum_synth"
        )
        for idx, item in enumerate(self._nav_items()):
            if idx >= len(C.BTN_UPPER_DISPLAY_CCS):
                break
            buttons[C.BTN_UPPER_DISPLAY_CCS[idx]] = (
                C.COLOR_GREEN if item.tab == current else C.COLOR_DARK_GRAY,
                C.ANIM_STATIC,
            )
        lower_colors = (
            C.COLOR_RED,
            C.COLOR_GREEN,
            C.COLOR_GREEN,
            C.COLOR_DARK_GRAY,
            C.COLOR_DARK_GRAY,
            C.COLOR_DARK_GRAY,
            C.COLOR_DARK_GRAY,
            C.COLOR_BLUE,
        )
        for cc, color in zip(C.BTN_LOWER_DISPLAY_CCS, lower_colors):
            buttons[cc] = (color, C.ANIM_STATIC)
        return buttons

    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        img = Image.new("RGB", (w, h), color=(8, 9, 14))
        d = ImageDraw.Draw(img)
        try:
            f_big = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
            f_med = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            f_sm = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        except Exception:
            f_big = f_med = f_sm = ImageFont.load_default()

        screen = self._screen()
        sess = self.control.session
        track_idx = drum_synth_track_index(sess)
        specs = drum_synth_voice_specs(sess, track_idx)
        selected = self._selected_pad()
        kit = DRUM_SYNTH_KITS[0]
        message = ""
        if screen is not None:
            message = getattr(screen, "_drum_synth_message", "")
        if track_idx is not None and 0 <= track_idx < len(sess.tracks):
            track = sess.tracks[track_idx]
            if track.instrument is not None:
                kit = str(track.instrument.params.get("kit") or kit)

        d.text((10, 5), "DRUM SYNTH", fill=(236, 240, 248), font=f_big)
        selected_name = "-"
        selected_type = "-"
        selected_spec = None
        if 0 <= selected < len(specs):
            selected_spec = specs[selected]
            selected_name = voice_display_name(selected_spec, selected)
            selected_type = str(selected_spec.get("voice_type", "-")).replace(
                "_", " ")
        d.text((160, 9), f"{kit}  Pad {selected + 1}: {selected_name[:26]}",
               fill=(150, 166, 194), font=f_med)
        if message:
            d.text((470, 10), message[:40], fill=(174, 188, 222), font=f_sm)

        pad_w = 88
        pad_h = 24
        start_x = 10
        start_y = 38
        for idx in range(DRUM_SYNTH_PAD_COUNT):
            col = idx % 4
            row = idx // 4
            x = start_x + col * (pad_w + 6)
            y = start_y + row * (pad_h + 5)
            spec = specs[idx] if idx < len(specs) else None
            active = idx == selected
            bg = (44, 72, 62) if active else (22, 26, 38)
            edge = (90, 210, 170) if active else (54, 64, 86)
            d.rounded_rectangle((x, y, x + pad_w, y + pad_h),
                                radius=3, fill=bg, outline=edge, width=1)
            label = voice_display_name(spec, idx)
            d.text((x + 5, y + 5), f"{idx + 1} {label[:10]}",
                   fill=(232, 236, 244), font=f_sm)

        side_x = 390
        d.text((side_x, 42), selected_type.upper()[:18],
               fill=(174, 188, 222), font=f_med)
        if selected_spec:
            d.text((side_x, 64),
                   f"tone {float(selected_spec.get('tone', 0))*100:.0f}  "
                   f"dec {float(selected_spec.get('decay', 0)):.2f}s",
                   fill=(232, 236, 244), font=f_sm)
            d.text((side_x, 82),
                   f"snap {float(selected_spec.get('snap', 0))*100:.0f}",
                   fill=(150, 166, 194), font=f_sm)
        actions = ("STOP", "808", "909", "TONE-", "TONE+",
                   "DEC-", "DEC+", "CREATE")
        for idx, label in enumerate(actions):
            x = side_x + (idx % 4) * 70
            y = 108 + (idx // 4) * 22
            d.text((x, y), label, fill=(150, 166, 194), font=f_sm)
        return img
