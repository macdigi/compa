"""Session mode — 8x8 clip launcher grid."""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PIL import Image, ImageDraw

from session.clip import ClipState
from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index
from .base import Mode

if TYPE_CHECKING:
    from ui.push2_control import Push2Control


class SessionMode(Mode):
    name = "session"

    def __init__(self, control: "Push2Control") -> None:
        super().__init__(control)
        self.scene_offset = 0   # vertical scroll
        self.track_offset = 0   # horizontal scroll

    # ── Pad input — launches clips ─────────────────────────────────
    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        if not is_press:
            return True
        # Bottom row = row 0, top row = row 7. We display scenes top-down,
        # so map row → scene_idx (top = scene 0).
        scene_idx = (C.PAD_GRID_ROWS - 1 - row) + self.scene_offset
        track_idx = col + self.track_offset

        sess = self.control.session
        if track_idx >= len(sess.tracks) or scene_idx >= len(sess.scenes):
            return True

        mods = self.control.modifiers
        if "delete" in mods:
            sess.set_clip(track_idx, scene_idx, None)
            self.control.request_redraw()
            return True
        if "duplicate" in mods:
            self.control.duplicate_clip(track_idx, scene_idx)
            return True
        if "select" in mods:
            self.control.select_cell(track_idx, scene_idx)
            return True

        # Record + empty slot on an audio track = arm recording.
        # Captures from the recorder's input (SP-404 / P-6 / whatever
        # USB audio is monitored) for 4 bars, drops the result into
        # the slot as an AudioClip.
        if "record" in mods:
            from session.track import TrackType
            track = sess.tracks[track_idx]
            if track.type == TrackType.AUDIO:
                self.control.arm_recording(track_idx, scene_idx)
                return True

        clip = sess.get_clip(track_idx, scene_idx)
        if clip is None:
            # Tap on empty slot does nothing in v1 (no auto-seed garbage)
            return True
        # Shift = launch immediately (Live's retrigger-now behavior).
        immediate = "shift" in mods
        self.control.launch_clip(track_idx, scene_idx,
                                  immediate=immediate)
        return True

    # ── Buttons we handle in this mode ─────────────────────────────
    def on_button(self, name: str, is_press: bool) -> bool:
        if not is_press:
            return False
        # Add Track (CC 53) — appends new track. Shift = drum rack,
        # otherwise synth voice. Add Device (CC 52) is reserved for
        # the device chain editor (later).
        if name == "add_track":
            self.control.add_track(
                drum=("shift" in self.control.modifiers))
            return True
        # Delete + upper-display button = remove that track.
        if name.startswith("upper_display_"):
            try:
                col = int(name.split("_")[2]) - 1
            except ValueError:
                return False
            track_idx = col + self.track_offset
            if "delete" in self.control.modifiers:
                self.control.remove_track(track_idx)
                return True
            self.control.selected_track = track_idx
            self.control.request_redraw()
            return True
        # Scene-launch column (right of pad grid)
        if name.startswith("scene_"):
            try:
                row_top_to_bottom = int(name.split("_")[1]) - 1  # 0..7
            except ValueError:
                return False
            scene_idx = row_top_to_bottom + self.scene_offset
            self.control.launch_scene(scene_idx)
            return True
        # Lower display row = stop clip per track in Session
        if name.startswith("lower_display_"):
            try:
                col = int(name.split("_")[2]) - 1
            except ValueError:
                return False
            track_idx = col + self.track_offset
            self.control.stop_clip(track_idx)
            return True
        if name == "stop_clip":
            mods = self.control.modifiers
            if "shift" in mods:
                self.control.stop_all_clips()
            else:
                t = self.control.selected_track
                if t is not None:
                    self.control.stop_clip(t)
            return True
        if name == "octave_up":
            self.scene_offset = max(0, self.scene_offset - 8)
            self.control.request_redraw()
            return True
        if name == "octave_down":
            self.scene_offset = min(max(0, len(self.control.session.scenes) - 8),
                                    self.scene_offset + 8)
            self.control.request_redraw()
            return True
        if name == "page_left":
            self.track_offset = max(0, self.track_offset - 8)
            self.control.request_redraw()
            return True
        if name == "page_right":
            self.track_offset = min(max(0, len(self.control.session.tracks) - 8),
                                    self.track_offset + 8)
            self.control.request_redraw()
            return True
        # New button: Shift = Capture MIDI (Live 12.4 feature),
        # otherwise = add an empty scene at the end.
        if name == "new":
            if "shift" in self.control.modifiers:
                self.control.capture_midi_to_clip()
            else:
                self.control.add_scene()
            return True
        return False

    # ── Pad render ─────────────────────────────────────────────────
    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        out: dict[tuple[int, int], tuple[int, int]] = {}
        sess = self.control.session
        sched = self.control.engine.scheduler
        for col in range(C.PAD_GRID_COLS):
            for row in range(C.PAD_GRID_ROWS):
                track_idx = col + self.track_offset
                scene_idx = (C.PAD_GRID_ROWS - 1 - row) + self.scene_offset
                if (track_idx >= len(sess.tracks) or
                        scene_idx >= len(sess.scenes)):
                    continue
                track = sess.tracks[track_idx]
                clip = track.clips[scene_idx] if scene_idx < len(track.clips) else None
                base_color = (clip.color if (clip is not None and clip.color)
                              else (track.color if track.color
                                    else track_color_index(track_idx)))
                # Recording-armed shows red pulse even on empty slots
                if self.control.engine.is_recording(track_idx, scene_idx):
                    out[(col, row)] = (121, C.ANIM_PULSE_8TH)
                    continue
                if clip is None:
                    # Empty slot — leave off
                    continue
                state = sched.get_state(track_idx, scene_idx)
                if state == ClipState.PLAYING:
                    out[(col, row)] = (base_color, C.ANIM_PULSE_QUARTER)
                elif state == ClipState.QUEUED:
                    out[(col, row)] = (120, C.ANIM_BLINK_QUARTER)
                elif state == ClipState.RECORDING:
                    out[(col, row)] = (121, C.ANIM_PULSE_8TH)
                else:
                    out[(col, row)] = (base_color, C.ANIM_STATIC)
        return out

    # ── Button highlights ──────────────────────────────────────────
    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        out: dict[int, tuple[int, int]] = {}
        sess = self.control.session
        sched = self.control.engine.scheduler
        # Scene buttons
        for i, cc in enumerate(C.BTN_SCENE_LAUNCH_CCS):
            scene_idx = i + self.scene_offset
            if scene_idx >= len(sess.scenes):
                continue
            # Scene white when any clip in row is playing, dim white otherwise
            playing = any(
                sched.is_playing(t, scene_idx)
                for t in range(len(sess.tracks))
            )
            out[cc] = (C.COLOR_WHITE if playing else C.COLOR_DARK_GRAY,
                       C.ANIM_STATIC)
        # Lower display: track stop indicators
        for i, cc in enumerate(C.BTN_LOWER_DISPLAY_CCS):
            t = i + self.track_offset
            if t >= len(sess.tracks):
                continue
            color = sess.tracks[t].color or track_color_index(t)
            out[cc] = (color, C.ANIM_STATIC)
        # Upper display: selected track highlight
        for i, cc in enumerate(C.BTN_UPPER_DISPLAY_CCS):
            t = i + self.track_offset
            if t >= len(sess.tracks):
                continue
            if t == self.control.selected_track:
                out[cc] = (C.COLOR_WHITE, C.ANIM_STATIC)
            else:
                color = sess.tracks[t].color or track_color_index(t)
                out[cc] = (color, C.ANIM_STATIC)
        return out

    # ── OLED ──────────────────────────────────────────────────────
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        from ui.push2_oled.frames.session_frame import draw_session_frame
        return draw_session_frame(w, h, self.control, self)
