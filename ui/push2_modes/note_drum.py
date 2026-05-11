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
        # Steps currently held for edit (multi-step batch). Each entry
        # is the absolute step number (page * 16 + step_in_page).
        self._held_steps: set[int] = set()
        # Latched note created on hold-down so encoder edits work even
        # for previously-empty steps (creates default note then edits).
        self._latched_notes: dict[int, "Note"] = {}

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
                        self.track_idx, 36 + pad_idx, velocity,
                        link_beat=self.control._beat())
                else:
                    self.control.engine.stop_note_live(
                        self.track_idx, 36 + pad_idx,
                        link_beat=self.control._beat())
                return True
            else:
                # Step seq pads — col 4..7, row 0..3 = 16 steps.
                # Press: hold the step for encoder editing AND toggle
                # whether the step exists on tap (released without an
                # encoder turn). Release: drop hold, finalize.
                step_in_page = (3 - row) * 4 + (col - 4)
                step = self.page * 16 + step_in_page
                if is_press:
                    self._held_steps.add(step)
                    # If step is empty, create a note immediately so
                    # encoder edits affect something. We remember
                    # whether we created it so a release-without-edit
                    # leaves the note in place (treats hold as "draw").
                    sess = self.control.session
                    scene = self.control.selected_scene or 0
                    clip = sess.get_clip(self.track_idx, scene)
                    from session.clip import MidiClip
                    if not isinstance(clip, MidiClip):
                        self._toggle_step(step)
                        return True
                    beat = step * self.step_resolution_beats
                    pitch = self.selected_drum_pitch
                    existing = next((n for n in clip.notes
                                      if abs(n.start_beat - beat) < 1e-3
                                      and n.pitch == pitch
                                      and not n.muted), None)
                    if existing is None:
                        from session.note import Note
                        new = Note(pitch=pitch, start_beat=beat,
                                    duration_beats=self.step_resolution_beats * 0.9,
                                    velocity=100)
                        clip.notes.append(new)
                        self._latched_notes[step] = new
                    else:
                        # Already-on step: hold = adjust, no toggle on
                        # release.
                        self._latched_notes[step] = existing
                    self.control.request_redraw()
                else:
                    self._held_steps.discard(step)
                    self._latched_notes.pop(step, None)
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
        # Mute + step pad = mute step in place (clear notes for held steps)
        # Delete + held drum / step = clear
        if name == "delete" and self._held_steps:
            sess = self.control.session
            scene = self.control.selected_scene or 0
            clip = sess.get_clip(self.track_idx, scene)
            from session.clip import MidiClip
            if isinstance(clip, MidiClip):
                pitch = self.selected_drum_pitch
                steps = sorted(self._held_steps)
                clip.notes = [
                    n for n in clip.notes
                    if not any(
                        abs(n.start_beat - s * self.step_resolution_beats) < 1e-3
                        and n.pitch == pitch
                        for s in steps
                    )
                ]
                self.control.request_redraw()
                return True
        return False

    # Encoders edit held steps' parameters: 1 nudge, 2 coarse-len,
    # 3 fine-len, 4 velocity, 5 chance, 6 vel-range.
    def on_encoder_turn(self, name: str, delta: int) -> bool:
        if not self._held_steps and not self._latched_notes:
            return False
        if not name.startswith("track"):
            return False
        try:
            i = int(name[5:]) - 1
        except ValueError:
            return False
        sess = self.control.session
        scene = self.control.selected_scene or 0
        clip = sess.get_clip(self.track_idx, scene)
        from session.clip import MidiClip
        if not isinstance(clip, MidiClip):
            return False
        pitch = self.selected_drum_pitch
        notes = []
        for s in self._held_steps:
            beat = s * self.step_resolution_beats
            for n in clip.notes:
                if (abs(n.start_beat - beat) < 1e-3
                        and n.pitch == pitch and not n.muted):
                    notes.append(n)
                    break
        if not notes:
            return False

        # Encoder mapping
        if i == 0:  # nudge: % offset within the step
            shift = delta * 0.005  # beats per tick
            for n in notes:
                n.start_beat = max(0.0, n.start_beat + shift)
        elif i == 1:  # coarse length: by step resolution
            for n in notes:
                n.duration_beats = max(0.05,
                    n.duration_beats + delta * self.step_resolution_beats * 0.5)
        elif i == 2:  # fine length: by 1/16 of step
            for n in notes:
                n.duration_beats = max(0.05,
                    n.duration_beats + delta * self.step_resolution_beats * 0.05)
        elif i == 3:  # velocity
            for n in notes:
                n.velocity = max(1, min(127, n.velocity + delta))
        elif i == 4:  # chance 0..1
            for n in notes:
                n.chance = max(0.0, min(1.0, n.chance + delta * 0.02))
        elif i == 5:  # velocity range 0..63
            for n in notes:
                n.velocity_range = max(0, min(63, n.velocity_range + delta))
        else:
            return False
        self.control.request_redraw()
        return True

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
