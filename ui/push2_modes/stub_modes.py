"""Lightweight modes that show 'coming soon' OLED but accept input.

These modes route their primary inputs to the underlying session
mutations (so e.g. Stop button still works) but don't expose all
their parameters yet.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from .base import Mode

if TYPE_CHECKING:
    from ui.push2_control import Push2Control


def _draw_stub_oled(w: int, h: int, title: str, subtitle: str = "") -> Image.Image:
    img = Image.new("RGB", (w, h), color=(0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        f_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        f_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except Exception:
        f_big = ImageFont.load_default()
        f_sm = ImageFont.load_default()
    d.text((20, 40), title, fill=(255, 255, 255), font=f_big)
    if subtitle:
        d.text((20, 80), subtitle, fill=(180, 180, 180), font=f_sm)
    return img


class DeviceMode(Mode):
    name = "device"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        t = self.control.selected_track or 0
        sess = self.control.session
        track_name = sess.tracks[t].name if t < len(sess.tracks) else ""
        return _draw_stub_oled(w, h, "DEVICE",
                                f"Track: {track_name} (chain editing soon)")


class BrowseMode(Mode):
    """Sample browser — lists files in samples/ + dropbox dirs."""
    name = "browse"

    def __init__(self, control) -> None:
        super().__init__(control)
        self._cursor = 0
        self._samples: list[str] = []
        self._refresh()

    def _refresh(self) -> None:
        from engine.clip_engine.sample_loader import list_samples
        import os
        dirs = [
            os.path.expanduser("~/compa/samples"),
            os.path.expanduser("~/.compa/samples"),
            "/home/pi/compa/samples",
        ]
        seen = []
        for d in dirs:
            for p in list_samples(d):
                if p not in seen:
                    seen.append(p)
        self._samples = seen

    def enter(self) -> None:
        self._refresh()
        self.control.request_redraw()

    def on_encoder_turn(self, name: str, delta: int) -> bool:
        if name == "track1" or name == "master":
            if self._samples:
                self._cursor = (self._cursor + delta) % len(self._samples)
                self.control.request_redraw()
                return True
        return False

    def on_pad(self, col: int, row: int, velocity: int, is_press: bool) -> bool:
        # Tap any pad: load the cursored sample into (col, row) clip slot.
        if not is_press or not self._samples:
            return True
        track_idx = col
        scene_idx = (8 - 1 - row)
        path = self._samples[self._cursor]
        from engine.clip_engine.sample_loader import load_sample
        from session.clip import AudioClip, LaunchQuantize
        loaded = load_sample(path)
        if loaded is None:
            return True
        data, sr = loaded
        sess = self.control.session
        if track_idx >= len(sess.tracks):
            return True
        clip = AudioClip(
            name=path.split("/")[-1].rsplit(".", 1)[0],
            length_beats=4.0, loop_end_beats=4.0, looping=True,
            launch_quantize=LaunchQuantize.GLOBAL,
            sample_rate=sr, original_bpm=sess.bpm,
            audio=data, audio_path=path,
            start_sample=0, end_sample=len(data),
            loop_start_sample=0, loop_end_sample=len(data),
        )
        sess.set_clip(track_idx, scene_idx, clip)
        self.control._persist()
        self.control.request_redraw()
        return True

    def draw_oled(self, w: int, h: int):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (w, h), color=(8, 8, 14))
        d = ImageDraw.Draw(img)
        try:
            f_big = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            f_med = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            f_sm = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        except Exception:
            f_big = f_med = f_sm = ImageFont.load_default()
        d.text((10, 6), "BROWSE", fill=(220, 220, 230), font=f_big)
        if not self._samples:
            d.text((10, 60),
                   "(no samples; drop into ~/.compa/samples/)",
                   fill=(180, 180, 200), font=f_med)
            return img
        d.text((10, 36),
               f"{self._cursor + 1} / {len(self._samples)}   "
               f"encoder = scroll, pad = drop",
               fill=(180, 200, 255), font=f_sm)
        # 5 visible items, cursor in middle
        n = len(self._samples)
        view_count = 5
        line_h = 20
        for i in range(view_count):
            idx = (self._cursor - 2 + i) % n
            sample = self._samples[idx].split("/")[-1]
            y = 56 + i * line_h
            color = (255, 255, 255) if i == 2 else (140, 140, 160)
            if i == 2:
                d.rectangle((4, y - 2, w - 4, y + 16), fill=(40, 40, 70))
            d.text((10, y), sample[:80], fill=color, font=f_med)
        return img


class ClipEditorMode(Mode):
    """Selected-clip editor — encoder edits clip params."""
    name = "clip_editor"

    def on_encoder_turn(self, name: str, delta: int) -> bool:
        t = self.control.selected_track or 0
        s = self.control.selected_scene or 0
        clip = self.control.session.get_clip(t, s)
        if clip is None or not name.startswith("track"):
            return False
        try:
            i = int(name[5:]) - 1
        except ValueError:
            return False
        if i == 0:  # length
            clip.length_beats = max(0.25, clip.length_beats + delta * 0.25)
            clip.loop_end_beats = clip.length_beats
            self.control.request_redraw()
            return True
        if i == 1:  # loop start
            clip.loop_start_beats = max(0.0,
                min(clip.length_beats - 0.25,
                    clip.loop_start_beats + delta * 0.25))
            self.control.request_redraw()
            return True
        if i == 2:  # loop end
            clip.loop_end_beats = max(clip.loop_start_beats + 0.25,
                                       clip.loop_end_beats + delta * 0.25)
            self.control.request_redraw()
            return True
        # AudioClip-only
        from session.clip import AudioClip
        if isinstance(clip, AudioClip):
            if i == 3:  # transpose
                clip.transpose_semitones = max(-24, min(24,
                    clip.transpose_semitones + delta))
                self.control.request_redraw()
                return True
            if i == 4:  # detune
                clip.detune_cents = max(-100, min(100,
                    clip.detune_cents + delta))
                self.control.request_redraw()
                return True
            if i == 5:  # gain
                clip.gain = max(0.0, min(2.0, clip.gain + delta * 0.05))
                self.control.request_redraw()
                return True
        # Follow action — encoders 7/8 (track7=action, track8=after_bars).
        from session.clip import FollowActionType
        if i == 6:
            actions = list(FollowActionType)
            try:
                idx = actions.index(clip.follow_action.type)
            except ValueError:
                idx = 0
            idx = (idx + delta) % len(actions)
            clip.follow_action.type = actions[idx]
            self.control._persist()
            self.control.request_redraw()
            return True
        if i == 7:
            clip.follow_action.after_bars = max(
                0.25, clip.follow_action.after_bars + delta * 0.25)
            self.control._persist()
            self.control.request_redraw()
            return True
        return False

    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        from PIL import Image, ImageDraw, ImageFont
        try:
            f_big = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            f_sm = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except Exception:
            f_big = f_sm = ImageFont.load_default()
        img = Image.new("RGB", (w, h), color=(8, 8, 14))
        d = ImageDraw.Draw(img)
        t = self.control.selected_track or 0
        s = self.control.selected_scene or 0
        clip = self.control.session.get_clip(t, s)
        if clip is None:
            d.text((10, 60), "(no clip selected)",
                   fill=(180, 180, 200), font=f_big)
            return img
        d.text((10, 6), f"CLIP · {clip.name or '(unnamed)'}",
               fill=(220, 220, 230), font=f_big)
        from session.clip import AudioClip, MidiClip
        kind = "audio" if isinstance(clip, AudioClip) else "midi"
        d.text((10, 36),
               f"{kind}   length {clip.length_beats:.2f} beats   "
               f"loop {clip.loop_start_beats:.2f}–{clip.loop_end_beats:.2f}",
               fill=(180, 200, 255), font=f_sm)
        if isinstance(clip, AudioClip):
            d.text((10, 60),
                   f"transpose {clip.transpose_semitones:+d}st   "
                   f"detune {clip.detune_cents:+d}c   "
                   f"gain {clip.gain:.2f}",
                   fill=(220, 220, 230), font=f_sm)
        elif isinstance(clip, MidiClip):
            d.text((10, 60),
                   f"{len(clip.notes)} notes",
                   fill=(220, 220, 230), font=f_sm)
        # Follow action display
        fa = clip.follow_action
        d.text((10, 86),
               f"follow {fa.type.value}   after {fa.after_bars:.2f} bars   "
               f"chance {int(fa.chance*100)}%",
               fill=(255, 200, 120), font=f_sm)
        d.text((10, h - 24),
               "1 length · 2 loop start · 3 loop end · "
               "4 transpose · 5 detune · 6 gain · "
               "7 follow action · 8 follow length",
               fill=(120, 130, 150), font=f_sm)
        return img


class MasterMode(Mode):
    name = "master"
    def on_encoder_turn(self, name: str, delta: int) -> bool:
        if name == "master":
            self.control.session.master_volume = max(
                0.0, min(1.0,
                          self.control.session.master_volume + delta * 0.02))
            self.control.request_redraw()
            return True
        return False
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        vol = self.control.session.master_volume
        return _draw_stub_oled(w, h, "MASTER", f"volume {int(vol*100)}%")


class SetupMode(Mode):
    name = "setup"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        return _draw_stub_oled(w, h, "SETUP", "device settings (more soon)")


class UserMode(Mode):
    name = "user"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        return _draw_stub_oled(w, h, "USER", "raw MIDI passthrough mode")


class OverviewMode(Mode):
    name = "overview"
    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        return _draw_stub_oled(w, h, "OVERVIEW", "Session map (Layout-held)")
