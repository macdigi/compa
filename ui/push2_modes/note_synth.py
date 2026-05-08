"""Note mode for synth tracks — chromatic / In-Key isomorphic layout.

Default 4ths layout: each row up = +5 semitones. In-key colors the
scale notes; chromatic mode lights all 12.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PIL import Image

from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index
from .base import Mode

if TYPE_CHECKING:
    from ui.push2_control import Push2Control


# 4ths-aligned scale shapes for highlighting in-key vs out-of-key.
SCALE_DEGREES = {
    "Major":             [0, 2, 4, 5, 7, 9, 11],
    "Minor":             [0, 2, 3, 5, 7, 8, 10],
    "Dorian":            [0, 2, 3, 5, 7, 9, 10],
    "Mixolydian":        [0, 2, 4, 5, 7, 9, 10],
    "Phrygian":          [0, 1, 3, 5, 7, 8, 10],
    "Lydian":            [0, 2, 4, 6, 7, 9, 11],
    "Pentatonic Minor":  [0, 3, 5, 7, 10],
    "Pentatonic Major":  [0, 2, 4, 7, 9],
    "Chromatic":         [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
}


class NoteSynthMode(Mode):
    name = "note_synth"

    def __init__(self, control: "Push2Control") -> None:
        super().__init__(control)
        self.root_note = 36         # C2
        self.scale = "Minor"
        self.in_key = True
        self.layout_offset = 5      # 4ths
        self._held_notes: dict[tuple[int, int], int] = {}

    @property
    def track_idx(self) -> int:
        return self.control.selected_track or 0

    def _pitch_at(self, col: int, row: int) -> int:
        return self.root_note + row * self.layout_offset + col

    def _is_in_scale(self, pitch: int) -> bool:
        degrees = SCALE_DEGREES.get(self.scale, SCALE_DEGREES["Chromatic"])
        return ((pitch - self.root_note) % 12) in degrees

    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        pitch = self._pitch_at(col, row)
        # In-Key: skip out-of-scale pads (pad lit dim, ignored input)
        if self.in_key and not self._is_in_scale(pitch):
            return True
        if is_press:
            self.control.engine.play_note_live(self.track_idx, pitch, velocity)
            self._held_notes[(col, row)] = pitch
        else:
            held = self._held_notes.pop((col, row), None)
            if held is not None:
                self.control.engine.stop_note_live(self.track_idx, held)
        return True

    def on_button(self, name: str, is_press: bool) -> bool:
        if not is_press:
            return False
        if name == "octave_up":
            self.root_note = min(96, self.root_note + 12)
            self.control.request_redraw()
            return True
        if name == "octave_down":
            self.root_note = max(0, self.root_note - 12)
            self.control.request_redraw()
            return True
        if name == "scale":
            scales = list(SCALE_DEGREES.keys())
            i = scales.index(self.scale)
            self.scale = scales[(i + 1) % len(scales)]
            self.control.request_redraw()
            return True
        return False

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        out: dict[tuple[int, int], tuple[int, int]] = {}
        sess = self.control.session
        track_color = (sess.tracks[self.track_idx].color
                       if self.track_idx < len(sess.tracks)
                       and sess.tracks[self.track_idx].color
                       else track_color_index(self.track_idx))
        for col in range(8):
            for row in range(8):
                pitch = self._pitch_at(col, row)
                if pitch < 0 or pitch > 127:
                    continue
                degree = (pitch - self.root_note) % 12
                if degree == 0:
                    out[(col, row)] = (C.COLOR_WHITE, C.ANIM_STATIC)
                elif self._is_in_scale(pitch):
                    out[(col, row)] = (track_color, C.ANIM_STATIC)
                elif not self.in_key:
                    out[(col, row)] = (C.COLOR_DARK_GRAY, C.ANIM_STATIC)
        # Held notes flash brighter
        for (c, r) in self._held_notes:
            out[(c, r)] = (C.COLOR_WHITE, C.ANIM_PULSE_8TH)
        return out

    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        from ui.push2_oled.frames.note_synth_frame import draw_note_synth_frame
        return draw_note_synth_frame(w, h, self.control, self)
