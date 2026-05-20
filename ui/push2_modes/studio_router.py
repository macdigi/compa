"""Push 2 Mixer / Router mode for Compa Studio."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index
from engine.studio_modules import known_modules
from engine.studio_router import target_choices_for_track
from engine.studio_targets import target_for_track
from .base import Mode


@dataclass(frozen=True)
class _NavItem:
    short_label: str
    tab: str


class StudioRouterMode(Mode):
    name = "studio_router"

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

    def _selected_track(self) -> int:
        sess = self.control.session
        selected = int(getattr(self.control, "selected_track", 0) or 0)
        if not sess.tracks:
            return 0
        return max(0, min(len(sess.tracks) - 1, selected))

    def _select_track(self, idx: int) -> None:
        screen = self._screen()
        if screen is not None:
            screen._select_router_track(self.control.session, idx)
        else:
            self.control.selected_track = idx
        self.control.request_redraw()

    def _cycle_target(self) -> None:
        screen = self._screen()
        sess = self.control.session
        if screen is None or not sess.tracks:
            return
        idx = self._selected_track()
        choices = target_choices_for_track(sess.tracks[idx])
        current = target_for_track(sess.tracks[idx]).key
        keys = [choice.key for choice in choices]
        next_idx = (keys.index(current) + 1) % len(keys) if current in keys else 0
        screen._route_selected_track(sess, choices[next_idx].key)
        self.control.request_redraw()

    def on_encoder_turn(self, name: str, delta: int) -> bool:
        if not name.startswith("track") or delta == 0:
            return False
        try:
            idx = int(name[5:]) - 1
        except ValueError:
            return False
        sess = self.control.session
        if idx >= len(sess.tracks):
            return False
        screen = self._screen()
        if screen is not None:
            screen._select_router_track(sess, idx)
            field = "pan" if "shift" in self.control.modifiers else "volume"
            step = 0.05 if field == "pan" else 0.02
            screen._adjust_router_mix(sess, field, delta * step)
        else:
            track = sess.tracks[idx]
            if "shift" in self.control.modifiers:
                track.pan = max(-1.0, min(1.0, track.pan + delta * 0.05))
            else:
                track.volume = max(0.0, min(1.0, track.volume + delta * 0.02))
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
            "lower_display_1": lambda: screen._adjust_router_mix(sess, "mute"),
            "lower_display_2": lambda: screen._adjust_router_mix(sess, "solo"),
            "lower_display_3": lambda: screen._adjust_router_mix(sess, "arm"),
            "lower_display_4": lambda: self._cycle_target(),
            "lower_display_5": lambda: screen._adjust_router_mix(sess, "volume", -0.05),
            "lower_display_6": lambda: screen._adjust_router_mix(sess, "volume", 0.05),
            "lower_display_7": lambda: screen._clear_router_solos(sess),
            "lower_display_8": lambda: self._select_tab(0),
        }
        action = actions.get(name)
        if action is None:
            return False
        action()
        self.control.request_redraw()
        return True

    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        if not is_press:
            return True
        if col < len(self.control.session.tracks):
            self._select_track(col)
        return True

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        out: dict[tuple[int, int], tuple[int, int]] = {}
        sess = self.control.session
        selected = self._selected_track()
        for col, track in enumerate(sess.tracks[:8]):
            color = track.color or track_color_index(col)
            level = max(1, int(track.volume * 8))
            for row in range(level):
                out[(col, row)] = (
                    C.COLOR_RED if track.mute else color,
                    C.ANIM_STATIC,
                )
            if track.solo:
                out[(col, 7)] = (C.COLOR_GREEN, C.ANIM_PULSE_8TH)
            if col == selected:
                out[(col, 0)] = (C.COLOR_WHITE, C.ANIM_STATIC)
        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        buttons: dict[int, tuple[int, int]] = {}
        screen = self._screen()
        current = getattr(screen, "_tab", "mixer") if screen is not None else "mixer"
        for idx, item in enumerate(self._nav_items()):
            if idx >= len(C.BTN_UPPER_DISPLAY_CCS):
                break
            buttons[C.BTN_UPPER_DISPLAY_CCS[idx]] = (
                C.COLOR_GREEN if item.tab == current else C.COLOR_DARK_GRAY,
                C.ANIM_STATIC,
            )
        sess = self.control.session
        selected = self._selected_track()
        track = sess.tracks[selected] if sess.tracks else None
        lower = (
            C.COLOR_RED if track and track.mute else C.COLOR_DARK_GRAY,
            C.COLOR_GREEN if track and track.solo else C.COLOR_DARK_GRAY,
            C.COLOR_BLUE if track and track.arm else C.COLOR_DARK_GRAY,
            C.COLOR_GREEN,
            C.COLOR_DARK_GRAY,
            C.COLOR_DARK_GRAY,
            C.COLOR_RED,
            C.COLOR_DARK_GRAY,
        )
        for cc, color in zip(C.BTN_LOWER_DISPLAY_CCS, lower):
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
        sess = self.control.session
        selected = self._selected_track()
        message = ""
        screen = self._screen()
        if screen is not None:
            message = getattr(screen, "_router_message", "")
        d.text((10, 5), "MIXER / ROUTER", fill=(236, 240, 248), font=f_big)
        d.text((188, 10), "encoders volume, shift+encoder pan",
               fill=(150, 166, 194), font=f_sm)
        if message:
            d.text((480, 10), message[:42], fill=(174, 188, 222), font=f_sm)

        row_y = 38
        col_w = max(72, (w - 20) // 4)
        for idx, track in enumerate(sess.tracks[:8]):
            col = idx % 4
            row = idx // 4
            x = 10 + col * col_w
            y = row_y + row * 52
            active = idx == selected
            bg = (34, 74, 64) if active else (20, 23, 34)
            edge = (90, 210, 170) if active else (52, 60, 82)
            d.rounded_rectangle((x, y, x + col_w - 8, y + 44),
                                radius=4, fill=bg, outline=edge, width=1)
            target = target_for_track(track)
            flags = "".join((
                "M" if track.mute else "-",
                "S" if track.solo else "-",
                "A" if track.arm else "-",
            ))
            d.text((x + 6, y + 5), f"{idx + 1} {track.name[:14]}",
                   fill=(236, 240, 248), font=f_med)
            d.text((x + 6, y + 25),
                   f"{int(track.volume * 100)}% {track.pan:+.1f} {flags} {target.label[:14]}",
                   fill=(150, 166, 194), font=f_sm)
        return img
