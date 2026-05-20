"""Push 2 Recorder mode for Compa Studio."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index
from engine.studio_modules import known_modules
from engine.studio_recorder import (
    active_clip_recordings,
    audio_track_indices,
    format_duration,
    recorder_status,
)
from .base import Mode


@dataclass(frozen=True)
class _NavItem:
    short_label: str
    tab: str


class StudioRecorderMode(Mode):
    name = "studio_recorder"

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
            "lower_display_1": screen._start_studio_recording,
            "lower_display_2": screen._stop_studio_recording,
            "lower_display_3": screen._recall_studio_buffer,
            "lower_display_4": screen._recall_and_continue_recording,
            "lower_display_5": lambda: screen._arm_recorder_clip_slot(sess),
            "lower_display_6": screen._capture_recorder_midi,
            "lower_display_7": lambda: screen._sp_pattern_record_assist(sess),
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
            screen._select_recorder_track_slot(
                sess, getattr(screen, "_recorder_track_choice_idx", 0) + delta)
        elif name == "track2":
            screen._cycle_recorder_scene(sess, delta)
        elif name == "track3":
            screen._cycle_recorder_length(delta)
        else:
            return False
        self.control.request_redraw()
        return True

    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        if not is_press:
            return True
        screen = self._screen()
        if screen is None:
            return False
        sess = self.control.session
        tracks = audio_track_indices(sess)
        if col < len(tracks):
            screen._select_recorder_track_slot(sess, col)
            screen._recorder_scene_idx = max(0, min(row, len(sess.scenes) - 1))
        self.control.request_redraw()
        return True

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        out: dict[tuple[int, int], tuple[int, int]] = {}
        sess = self.control.session
        tracks = audio_track_indices(sess)
        screen = self._screen()
        selected_slot = getattr(screen, "_recorder_track_choice_idx", 0) if screen else 0
        selected_scene = getattr(screen, "_recorder_scene_idx", 0) if screen else 0
        for col, track_idx in enumerate(tracks[:8]):
            track = sess.tracks[track_idx]
            color = track.color or track_color_index(track_idx)
            for row in range(min(8, len(track.clips))):
                clip = track.clips[row]
                active = col == selected_slot and row == selected_scene
                if active:
                    out[(col, row)] = (C.COLOR_WHITE, C.ANIM_STATIC)
                elif clip is not None:
                    out[(col, row)] = (color, C.ANIM_STATIC)
                else:
                    out[(col, row)] = (C.COLOR_DARK_GRAY, C.ANIM_STATIC)
        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        buttons: dict[int, tuple[int, int]] = {}
        screen = self._screen()
        current = getattr(screen, "_tab", "recorder") if screen is not None else "recorder"
        for idx, item in enumerate(self._nav_items()):
            if idx >= len(C.BTN_UPPER_DISPLAY_CCS):
                break
            buttons[C.BTN_UPPER_DISPLAY_CCS[idx]] = (
                C.COLOR_GREEN if item.tab == current else C.COLOR_DARK_GRAY,
                C.ANIM_STATIC,
            )
        rec = getattr(getattr(self.control, "host_app", None), "recorder", None)
        status = recorder_status(rec)
        lower = (
            C.COLOR_RED if status["recording"] else C.COLOR_GREEN,
            C.COLOR_RED,
            C.COLOR_BLUE,
            C.COLOR_GREEN,
            C.COLOR_BLUE,
            C.COLOR_GREEN,
            C.COLOR_GREEN,
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
        screen = self._screen()
        app = getattr(self.control, "host_app", None)
        status = recorder_status(getattr(app, "recorder", None))
        message = getattr(screen, "_recorder_message", "") if screen is not None else ""
        armed = active_clip_recordings(getattr(app, "clip_engine", None))
        d.text((10, 5), "RECORDER", fill=(236, 240, 248), font=f_big)
        d.text((128, 10), status["device"][:32],
               fill=(150, 166, 194), font=f_med)
        if message:
            d.text((500, 10), message[:42], fill=(174, 188, 222), font=f_sm)
        state = "RECORDING" if status["recording"] else "READY"
        d.text((10, 42), f"{state}  {format_duration(status['duration'])}",
               fill=(236, 112, 120) if status["recording"] else (96, 224, 156),
               font=f_big)
        d.text((10, 76),
               f"Recall {status['recall_seconds']:.0f}/{status['recall_capacity']}s  "
               f"Monitor {'on' if status['monitoring'] else 'off'}  "
               f"Armed clips {len(armed)}",
               fill=(232, 236, 244), font=f_sm)
        d.text((10, 104),
               "Lower: REC STOP RECALL +REC ARMCLIP CAPMIDI REC1X HOME",
               fill=(150, 166, 194), font=f_sm)
        d.text((10, 126), "Enc: track / scene / clip length",
               fill=(150, 166, 194), font=f_sm)
        return img
