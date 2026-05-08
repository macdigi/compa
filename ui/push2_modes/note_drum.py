"""Note mode for drum tracks — Loop Selector layout.

Layout:
  - bottom-left 4×4: 16 drum-rack play pads (live triggering)
  - bottom-right 4×4: 16-step sequencer for the selected drum
  - top half (4×8): loop selector / page indicator (4 pages × 16 steps)
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from PIL import Image

from session.clip import MidiClip
from session.note import Note
from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index
from .base import Mode

if TYPE_CHECKING:
    from ui.push2_control import Push2Control


class NoteDrumMode(Mode):
    name = "note_drum"

    def __init__(self, control: "Push2Control") -> None:
        super().__init__(control)
        self.selected_pad = 0   # 0..15 (currently focused drum)
        self.page = 0           # 0..3 (which 16-step page of a 64-step clip)
        self.step_resolution_beats = 0.25  # 1/16 default (1 step)

    @property
    def selected_drum_pitch(self) -> int:
        return 36 + self.selected_pad

    @property
    def track_idx(self) -> int:
        return self.control.selected_track or 0

    # ── Pad input ──────────────────────────────────────────────────
    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        # Region: row 0–3 = bottom half (play + step). Row 4–7 = top half
        # (loop selector).
        # Row coords: row 0 = bottom of grid, row 7 = top.
        if row < 4:
            # Bottom 4 rows
            if col < 4:
                # Play pads — col 0..3, row 0..3
                pad_idx = (3 - row) * 4 + col
                if is_press:
                    mods = self.control.modifiers
                    if "select" in mods:
                        self.selected_pad = pad_idx
                        self.control.request_redraw()
                        return True
                    self.control.engine.play_note_live(
                        self.track_idx, 36 + pad_idx, velocity)
                else:
                    self.control.engine.stop_note_live(
                        self.track_idx, 36 + pad_idx)
                return True
            else:
                # Step seq pads — col 4..7, row 0..3 = 16 steps
                if is_press:
                    step_in_page = (3 - row) * 4 + (col - 4)
                    step = self.page * 16 + step_in_page
                    self._toggle_step(step)
                return True
        else:
            # Top 4 rows = loop selector / page nav. row 4..7
            if is_press:
                # 4 columns of 4 = 16 page slots; we use first 4 columns
                # for 4 pages, rest unused for v1.
                if col < 4:
                    self.page = col
                    self.control.request_redraw()
            return True

    def on_button(self, name: str, is_press: bool) -> bool:
        if not is_press:
            return False
        # Time-division buttons (scene-launch column doubles up)
        if name.startswith("scene_"):
            try:
                idx = int(name.split("_")[1]) - 1
            except ValueError:
                return False
            div = C.TIME_DIVISIONS[idx]
            self.step_resolution_beats = self._div_to_beats(div)
            self.control.request_redraw()
            return True
        return False

    def _div_to_beats(self, div: str) -> float:
        return {
            "1/32t": 1.0 / 12.0, "1/32": 0.125,
            "1/16t": 1.0 / 6.0, "1/16": 0.25,
            "1/8t": 1.0 / 3.0, "1/8": 0.5,
            "1/4t": 2.0 / 3.0, "1/4": 1.0,
        }.get(div, 0.25)

    # ── Step toggle ───────────────────────────────────────────────
    def _toggle_step(self, step: int) -> None:
        sess = self.control.session
        scene = self.control.selected_scene or 0
        clip = sess.get_clip(self.track_idx, scene)
        if clip is None:
            # Create a clip on this slot
            clip = MidiClip(name=f"clip {scene+1}", length_beats=4.0,
                            loop_start_beats=0.0, loop_end_beats=4.0)
            sess.set_clip(self.track_idx, scene, clip)
        if not isinstance(clip, MidiClip):
            return
        beat = step * self.step_resolution_beats
        pitch = self.selected_drum_pitch
        # Find existing note on this step+pitch
        for i, n in enumerate(clip.notes):
            if (abs(n.start_beat - beat) < 1e-3 and n.pitch == pitch
                    and not n.muted):
                clip.notes.pop(i)
                self.control.request_redraw()
                return
        clip.notes.append(Note(
            pitch=pitch,
            start_beat=beat,
            duration_beats=self.step_resolution_beats * 0.9,
            velocity=100,
        ))
        self.control.request_redraw()

    # ── Render ─────────────────────────────────────────────────────
    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        out: dict[tuple[int, int], tuple[int, int]] = {}
        sess = self.control.session
        track = sess.tracks[self.track_idx] if self.track_idx < len(sess.tracks) else None
        track_color = (track.color if track and track.color
                       else track_color_index(self.track_idx))

        # Bottom-left 4×4 play pads
        for r in range(4):
            for c in range(4):
                pad_idx = (3 - r) * 4 + c
                color = track_color
                if pad_idx == self.selected_pad:
                    color = C.COLOR_WHITE
                out[(c, r)] = (color, C.ANIM_STATIC)

        # Bottom-right 4×4 step sequencer
        scene = self.control.selected_scene or 0
        clip = sess.get_clip(self.track_idx, scene)
        playhead_step = self.control.playhead_step_for_clip(self.track_idx, scene,
                                                            self.step_resolution_beats)
        for r in range(4):
            for c in range(4, 8):
                step_in_page = (3 - r) * 4 + (c - 4)
                step = self.page * 16 + step_in_page
                lit = False
                if isinstance(clip, MidiClip):
                    beat = step * self.step_resolution_beats
                    for n in clip.notes:
                        if (abs(n.start_beat - beat) < 1e-3
                                and n.pitch == self.selected_drum_pitch
                                and not n.muted):
                            lit = True
                            break
                if step == playhead_step:
                    out[(c, r)] = (C.COLOR_WHITE, C.ANIM_STATIC)
                elif lit:
                    out[(c, r)] = (track_color, C.ANIM_STATIC)
                else:
                    out[(c, r)] = (C.COLOR_DARK_GRAY, C.ANIM_STATIC)

        # Top half loop selector (rows 4–7) — 4 pages on row 4, col 0..3
        for c in range(4):
            color = (C.COLOR_WHITE if c == self.page else C.COLOR_DARK_GRAY)
            out[(c, 4)] = (color, C.ANIM_STATIC)

        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        out: dict[int, tuple[int, int]] = {}
        # Highlight current time-division button
        current_div = None
        for d, beats in [("1/32t", 1/12), ("1/32", 0.125),
                         ("1/16t", 1/6), ("1/16", 0.25),
                         ("1/8t", 1/3), ("1/8", 0.5),
                         ("1/4t", 2/3), ("1/4", 1.0)]:
            if abs(self.step_resolution_beats - beats) < 1e-3:
                current_div = d
                break
        for i, cc in enumerate(C.BTN_SCENE_LAUNCH_CCS):
            div = C.TIME_DIVISIONS[i]
            if div == current_div:
                out[cc] = (C.COLOR_WHITE, C.ANIM_STATIC)
            else:
                out[cc] = (C.COLOR_DARK_GRAY, C.ANIM_STATIC)
        return out

    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        from ui.push2_oled.frames.note_drum_frame import draw_note_drum_frame
        return draw_note_drum_frame(w, h, self.control, self)
