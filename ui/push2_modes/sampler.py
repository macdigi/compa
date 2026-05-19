"""Push 2 Sampler mode for Compa Studio."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from engine.studio_modules import known_modules
from engine.studio_sampler import (
    SAMPLER_PAD_COUNT,
    pad_display_name,
    sampler_pad_specs,
    sampler_track_index,
    sample_label,
)
from .base import Mode


@dataclass(frozen=True)
class _NavItem:
    short_label: str
    tab: str


class SamplerMode(Mode):
    name = "sampler"

    def _screen(self):
        app = getattr(self.control, "host_app", None)
        screens = getattr(app, "screens", {}) if app is not None else {}
        screen = screens.get("studio") or screens.get("clips")
        if screen is not None and getattr(screen, "_tab", "") == "sampler":
            return screen
        return screen

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

    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        idx = self._pad_index(col, row)
        if idx is None:
            return True
        screen = self._screen()
        if screen is None:
            return False
        if is_press:
            screen._trigger_sampler_pad(self.control.session, idx, velocity)
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
        actions = {
            "lower_display_1": screen._stop_sampler,
            "lower_display_2": lambda: screen._cycle_sampler_sample(-1),
            "lower_display_3": lambda: screen._cycle_sampler_sample(1),
            "lower_display_4": lambda: screen._assign_sampler_sample(
                self.control.session),
            "lower_display_5": lambda: screen._clear_sampler_pad(
                self.control.session),
            "lower_display_6": lambda: screen._load_sampler_starter(
                self.control.session),
            "lower_display_7": lambda: self._select_tab(0),
            "lower_display_8": lambda: self._select_tab(1),
        }
        action = actions.get(name)
        if action is None:
            return False
        action()
        self.control.request_redraw()
        return True

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        screen = self._screen()
        sess = self.control.session
        track_idx = sampler_track_index(sess)
        pads = sampler_pad_specs(sess, track_idx)
        selected = getattr(screen, "_sampler_pad_idx", 0) if screen is not None else 0
        out: dict[tuple[int, int], tuple[int, int]] = {}
        for idx in range(SAMPLER_PAD_COUNT):
            col = idx % 4
            row = 3 - (idx // 4)
            spec = pads[idx] if idx < len(pads) else None
            enabled = bool(spec and (spec.get("sample_path")
                           or spec.get("use_default", True)))
            assigned = bool(spec and spec.get("sample_path"))
            if idx == selected:
                color, anim = C.COLOR_WHITE, C.ANIM_STATIC
            elif assigned:
                color, anim = C.COLOR_GREEN, C.ANIM_STATIC
            elif enabled:
                color, anim = C.COLOR_BLUE, C.ANIM_STATIC
            else:
                color, anim = C.COLOR_DARK_GRAY, C.ANIM_STATIC
            out[(col, row)] = (color, anim)
        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        buttons: dict[int, tuple[int, int]] = {}
        screen = self._screen()
        current = getattr(screen, "_tab", "sampler") if screen is not None else "sampler"
        for idx, item in enumerate(self._nav_items()):
            if idx >= len(C.BTN_UPPER_DISPLAY_CCS):
                break
            buttons[C.BTN_UPPER_DISPLAY_CCS[idx]] = (
                C.COLOR_GREEN if item.tab == current else C.COLOR_DARK_GRAY,
                C.ANIM_STATIC,
            )
        lower_colors = (
            C.COLOR_RED,
            C.COLOR_DARK_GRAY,
            C.COLOR_DARK_GRAY,
            C.COLOR_GREEN,
            C.COLOR_RED,
            C.COLOR_BLUE,
            C.COLOR_DARK_GRAY,
            C.COLOR_DARK_GRAY,
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
        track_idx = sampler_track_index(sess)
        pads = sampler_pad_specs(sess, track_idx)
        selected = getattr(screen, "_sampler_pad_idx", 0) if screen is not None else 0
        sample_path = ""
        message = ""
        if screen is not None:
            sample_path = screen._sampler_selected_sample()
            message = getattr(screen, "_sampler_message", "")
        d.text((10, 5), "SAMPLER", fill=(236, 240, 248), font=f_big)
        selected_name = "-"
        if 0 <= selected < len(pads):
            selected_name = pad_display_name(pads[selected], selected)
        d.text((118, 9), f"Pad {selected + 1}: {selected_name[:34]}",
               fill=(150, 166, 194), font=f_med)
        if message:
            d.text((430, 10), message[:48], fill=(174, 188, 222), font=f_sm)

        pad_w = 88
        pad_h = 24
        start_x = 10
        start_y = 38
        for idx in range(SAMPLER_PAD_COUNT):
            col = idx % 4
            row = idx // 4
            x = start_x + col * (pad_w + 6)
            y = start_y + row * (pad_h + 5)
            spec = pads[idx] if idx < len(pads) else None
            active = idx == selected
            assigned = bool(spec and spec.get("sample_path"))
            enabled = bool(spec and (spec.get("sample_path")
                           or spec.get("use_default", True)))
            bg = (34, 78, 68) if active else (
                (22, 42, 48) if assigned else (
                    (22, 26, 38) if enabled else (14, 16, 22)))
            edge = (90, 210, 170) if active else (54, 64, 86)
            d.rounded_rectangle((x, y, x + pad_w, y + pad_h),
                                radius=3, fill=bg, outline=edge, width=1)
            label = pad_display_name(spec, idx)
            d.text((x + 5, y + 5), f"{idx + 1} {label[:10]}",
                   fill=(232, 236, 244), font=f_sm)

        side_x = 390
        d.text((side_x, 42), "LIBRARY", fill=(174, 188, 222), font=f_med)
        d.text((side_x, 62), (sample_label(sample_path) or "-")[:34],
               fill=(232, 236, 244), font=f_sm)
        actions = ("STOP", "PREV", "NEXT", "ASSIGN", "CLEAR", "STARTER", "HOME", "CLIPS")
        for idx, label in enumerate(actions):
            x = side_x + (idx % 4) * 70
            y = 90 + (idx // 4) * 26
            d.text((x, y), label, fill=(150, 166, 194), font=f_sm)
        return img
