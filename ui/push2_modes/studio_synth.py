"""Push 2 Synths mode for Compa Studio."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index
from engine.studio_modules import known_modules
from engine.studio_synth import (
    note_name,
    synth_params,
    synth_track_indices,
    synth_track_role,
)
from .base import Mode


@dataclass(frozen=True)
class _NavItem:
    short_label: str
    tab: str


class StudioSynthMode(Mode):
    name = "studio_synth"

    def __init__(self, control) -> None:
        super().__init__(control)
        self._held_notes: dict[tuple[int, int], int] = {}

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

    def _track_idx(self) -> int | None:
        screen = self._screen()
        if screen is not None:
            idx = screen._selected_synth_track_index(self.control.session)
            if idx is not None:
                return idx
        indices = synth_track_indices(self.control.session)
        return indices[0] if indices else None

    def _pitch_at(self, col: int, row: int) -> int:
        screen = self._screen()
        base = int(getattr(screen, "_synth_base_note", 48) if screen else 48)
        return base + row * 5 + col

    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        screen = self._screen()
        if screen is None:
            return False
        pitch = self._pitch_at(col, row)
        if is_press:
            screen._synth_note_on(self.control.session, pitch, velocity)
            self._held_notes[(col, row)] = pitch
        else:
            held = self._held_notes.pop((col, row), pitch)
            screen._synth_note_off(self.control.session, held)
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
            "lower_display_1": screen._stop_synth_notes,
            "lower_display_2": lambda: screen._cycle_synth_track(sess, -1),
            "lower_display_3": lambda: screen._cycle_synth_track(sess, 1),
            "lower_display_4": lambda: screen._set_synth_preset(sess, "bass"),
            "lower_display_5": lambda: screen._set_synth_preset(sess, "lead"),
            "lower_display_6": lambda: screen._set_synth_preset(sess, "pad"),
            "lower_display_7": lambda: screen._cycle_synth_waveform(sess),
            "lower_display_8": lambda: self._select_tab(0),
        }
        action = actions.get(name)
        if action is None:
            return False
        action()
        self.control.request_redraw()
        return True

    def on_encoder_turn(self, name: str, delta: int) -> bool:
        screen = self._screen()
        if screen is None or delta == 0:
            return False
        sess = self.control.session
        if name == "track1":
            screen._adjust_synth_param(sess, "cutoff_hz", delta * 250.0)
        elif name == "track2":
            screen._adjust_synth_param(sess, "attack", delta * 0.025)
        elif name == "track3":
            screen._adjust_synth_param(sess, "release", delta * 0.05)
        elif name == "track4":
            screen._adjust_synth_param(sess, "gain", delta * 0.05)
        elif name == "track5":
            screen._cycle_synth_track(sess, delta)
        elif name == "track6":
            screen._cycle_synth_waveform(sess)
        else:
            return False
        self.control.request_redraw()
        return True

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        out: dict[tuple[int, int], tuple[int, int]] = {}
        track_idx = self._track_idx() or 0
        track_color = track_color_index(track_idx)
        for col in range(8):
            for row in range(8):
                pitch = self._pitch_at(col, row)
                degree = pitch % 12
                if (col, row) in self._held_notes:
                    out[(col, row)] = (C.COLOR_WHITE, C.ANIM_PULSE_8TH)
                elif degree == 0:
                    out[(col, row)] = (C.COLOR_GREEN, C.ANIM_STATIC)
                elif degree in (3, 5, 7, 10):
                    out[(col, row)] = (track_color, C.ANIM_STATIC)
                else:
                    out[(col, row)] = (C.COLOR_DARK_GRAY, C.ANIM_STATIC)
        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        buttons: dict[int, tuple[int, int]] = {}
        screen = self._screen()
        current = getattr(screen, "_tab", "synth") if screen is not None else "synth"
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
            C.COLOR_GREEN,
            C.COLOR_GREEN,
            C.COLOR_BLUE,
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
        track_idx = self._track_idx()
        track_name = "No synth track"
        role = "-"
        params = synth_params(sess, track_idx)
        message = ""
        if screen is not None:
            message = getattr(screen, "_synth_message", "")
        if track_idx is not None and 0 <= track_idx < len(sess.tracks):
            track = sess.tracks[track_idx]
            track_name = track.name
            role = synth_track_role(track)
        d.text((10, 5), "SYNTHS", fill=(236, 240, 248), font=f_big)
        d.text((104, 9), f"{track_name[:24]}  {role}",
               fill=(150, 166, 194), font=f_med)
        if message:
            d.text((420, 10), message[:48], fill=(174, 188, 222), font=f_sm)
        base = int(getattr(screen, "_synth_base_note", 48) if screen else 48)
        d.text((10, 38), f"Pads: {note_name(base)} 4ths layout",
               fill=(174, 188, 222), font=f_med)
        lines = (
            f"Wave {params.get('waveform')}   Cut {float(params.get('cutoff_hz', 0)):.0f}Hz",
            f"Atk {float(params.get('attack', 0)):.2f}s   Rel {float(params.get('release', 0)):.2f}s   Gain {float(params.get('gain', 0)):.2f}",
            "Enc: cutoff / attack / release / gain / track / wave",
            "Lower: stop track- track+ bass lead pad wave home",
        )
        for idx, line in enumerate(lines):
            d.text((10, 64 + idx * 20), line, fill=(232, 236, 244), font=f_sm)
        return img
