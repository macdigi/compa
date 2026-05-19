"""Push 2 Studio hub mode."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from engine.studio_modules import (
    is_module_available,
    known_modules,
    module_availability_label,
)
from .base import Mode


@dataclass(frozen=True)
class StudioNavItem:
    key: str
    label: str
    short_label: str
    tab: str
    stage: str = "ready"
    internal_audio: bool = False
    min_pi_generation: int | None = None


class StudioMode(Mode):
    name = "studio"

    def _screen(self):
        app = getattr(self.control, "host_app", None)
        screens = getattr(app, "screens", {}) if app is not None else {}
        return screens.get("studio") or screens.get("clips")

    def _nav_items(self):
        home = StudioNavItem(
            key="home",
            label="Studio Home",
            short_label="HOME",
            tab="overview",
        )
        modules_by_tab = {module.tab: module for module in known_modules()}
        ordered_tabs = (
            "clips",
            "performer",
            "sampler",
            "drum_synth",
            "synth",
            "mixer",
            "recorder",
        )
        return (home, *(modules_by_tab[tab] for tab in ordered_tabs
                        if tab in modules_by_tab))[:8]

    @staticmethod
    def _is_available(item, pi_generation, audio_supported) -> bool:
        if item.key == "home":
            return True
        return is_module_available(
            item,
            pi_generation=pi_generation,
            studio_audio_enabled=audio_supported,
        )

    @staticmethod
    def _status_label(item, pi_generation, audio_supported) -> str:
        if item.key == "home":
            return "ready"
        return module_availability_label(
            item,
            pi_generation=pi_generation,
            studio_audio_enabled=audio_supported,
        )

    def _select_module(self, idx: int) -> bool:
        items = self._nav_items()
        if not (0 <= idx < len(items)):
            return False
        screen = self._screen()
        if screen is None:
            return False
        screen._set_tab(items[idx].tab)
        self.control.request_redraw()
        return True

    def on_button(self, name: str, is_press: bool) -> bool:
        if not is_press:
            return False
        if name.startswith("upper_display_") or name.startswith("lower_display_"):
            try:
                idx = int(name.rsplit("_", 1)[1]) - 1
            except ValueError:
                return False
            return self._select_module(idx)
        return False

    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        if not is_press:
            return True
        if row in (0, 7):
            self._select_module(col)
        return True

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        out: dict[tuple[int, int], tuple[int, int]] = {}
        screen = self._screen()
        current = getattr(screen, "_tab", "overview") if screen is not None else "overview"
        pi_generation = getattr(screen, "_pi_generation", lambda: None)()
        audio_supported = getattr(screen, "_studio_audio_supported", lambda: True)()
        for idx, item in enumerate(self._nav_items()):
            available = self._is_available(item, pi_generation, audio_supported)
            if item.tab == current:
                color, anim = C.COLOR_GREEN, C.ANIM_STATIC
            elif not available:
                color, anim = C.COLOR_RED, C.ANIM_STATIC
            elif item.stage == "planned":
                color, anim = C.COLOR_BLUE, C.ANIM_STATIC
            else:
                color, anim = C.COLOR_WHITE, C.ANIM_STATIC
            out[(idx, 0)] = (color, anim)
            out[(idx, 7)] = (color, anim)
        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        buttons: dict[int, tuple[int, int]] = {}
        screen = self._screen()
        current = getattr(screen, "_tab", "overview") if screen is not None else "overview"
        pi_generation = getattr(screen, "_pi_generation", lambda: None)()
        audio_supported = getattr(screen, "_studio_audio_supported", lambda: True)()
        for idx, item in enumerate(self._nav_items()):
            if idx >= len(C.BTN_UPPER_DISPLAY_CCS):
                break
            available = self._is_available(item, pi_generation, audio_supported)
            color = C.COLOR_GREEN if item.tab == current else (
                C.COLOR_RED if not available else C.COLOR_DARK_GRAY)
            buttons[C.BTN_UPPER_DISPLAY_CCS[idx]] = (color, C.ANIM_STATIC)
            buttons[C.BTN_LOWER_DISPLAY_CCS[idx]] = (color, C.ANIM_STATIC)
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
        current = getattr(screen, "_tab", "overview") if screen is not None else "overview"
        pi_generation = getattr(screen, "_pi_generation", lambda: None)()
        audio_supported = getattr(screen, "_studio_audio_supported", lambda: True)()
        d.text((10, 5), "STUDIO", fill=(236, 240, 248), font=f_big)
        d.text((100, 9), "upper/lower buttons select modules",
               fill=(130, 146, 176), font=f_sm)
        items = self._nav_items()
        card_w = max(76, (w - 20) // 4)
        card_h = 48
        for idx, item in enumerate(items):
            col = idx % 4
            row = idx // 4
            x = 10 + col * card_w
            y = 34 + row * (card_h + 8)
            active = item.tab == current
            available = self._is_available(item, pi_generation, audio_supported)
            bg = (32, 76, 66) if active else (20, 23, 34)
            edge = (90, 210, 170) if active else (
                (166, 76, 82) if not available else (52, 60, 82))
            d.rounded_rectangle((x, y, x + card_w - 6, y + card_h),
                                radius=4, fill=bg, outline=edge, width=1)
            d.text((x + 6, y + 6), item.short_label[:9],
                   fill=(236, 240, 248), font=f_med)
            status = self._status_label(item, pi_generation, audio_supported)
            d.text((x + 6, y + 26), status[:16],
                   fill=(150, 166, 194), font=f_sm)
        return img
