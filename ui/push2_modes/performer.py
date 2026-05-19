"""Push 2 Performer mode for Compa Studio."""
from __future__ import annotations

from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from .base import Mode


class PerformerMode(Mode):
    name = "performer"
    ENCODER_PAGES = ("feel", "gen", "lanes", "takes")

    def __init__(self, control) -> None:
        super().__init__(control)
        self.encoder_page = "feel"

    def _screen(self):
        app = getattr(self.control, "host_app", None)
        screens = getattr(app, "screens", {}) if app is not None else {}
        screen = screens.get("studio") or screens.get("clips")
        if screen is not None and getattr(screen, "_tab", "") == "performer":
            return screen
        return None

    def on_encoder_turn(self, name: str, delta: int) -> bool:
        screen = self._screen()
        if screen is None or delta == 0:
            return False
        page = self.encoder_page
        if page == "feel" and name == "track1":
            screen._adjust_performer_feel("swing", delta * 5.0)
        elif page == "feel" and name == "track2":
            screen._adjust_performer_feel("humanize", delta * 10.0)
        elif page == "feel" and name == "track3":
            screen._adjust_performer_feel("gate", delta * 0.2)
        elif page == "feel" and name == "track4":
            screen._cycle_performer_genre()
        elif page == "feel" and name == "track5":
            screen._cycle_performer_take(self.control.session, delta)
        elif page == "gen" and name == "track1":
            screen._adjust_performer_generator("density", delta * 5.0)
        elif page == "gen" and name == "track2":
            screen._adjust_performer_generator("complexity", delta * 5.0)
        elif page == "gen" and name == "track3":
            screen._adjust_performer_generator("fill", delta * 5.0)
        elif page == "gen" and name == "track4":
            screen._adjust_performer_generator("bass_activity", delta * 5.0)
        elif page == "gen" and name == "track5":
            screen._adjust_performer_generator("variation", delta * 5.0)
        elif page == "gen" and name == "track6":
            screen._cycle_performer_genre()
        elif page == "lanes" and name == "track1":
            screen._cycle_performer_lane(delta)
        elif page == "lanes" and name == "track2":
            screen._adjust_performer_lane("gate", delta * 0.1)
        elif page == "lanes" and name == "track3":
            screen._adjust_performer_lane("level", delta * 0.1)
        elif page == "lanes" and name == "track4":
            screen._adjust_performer_lane("mute")
        elif page == "takes" and name == "track1":
            screen._cycle_performer_take(self.control.session, delta)
        elif page == "takes" and name == "track2":
            screen._cycle_performer_genre()
        else:
            return False
        self.control.request_redraw()
        return True

    def on_button(self, name: str, is_press: bool) -> bool:
        if not is_press:
            return False
        screen = self._screen()
        if screen is None:
            return False
        if name.startswith("upper_display_"):
            try:
                idx = int(name.split("_")[2]) - 1
            except ValueError:
                return False
            if idx < len(self.ENCODER_PAGES):
                self.encoder_page = self.ENCODER_PAGES[idx]
                self.control.request_redraw()
                return True
            return False
        sess = self.control.session
        actions = {
            "play": lambda: screen._play_sp_beat_bass(sess),
            "stop_clip": screen._stop_performer,
            "record": lambda: screen._capture_sp_pattern_once(sess),
            "lower_display_1": lambda: screen._play_sp_beat_bass(sess),
            "lower_display_2": screen._stop_performer,
            "lower_display_3": lambda: screen._generate_sp_variation(sess),
            "lower_display_4": lambda: screen._save_performer_take(sess),
            "lower_display_5": lambda: screen._load_performer_take(sess),
            "lower_display_6": lambda: screen._toggle_take_chain(sess),
            "lower_display_7": lambda: screen._capture_sp_pattern_once(sess),
            "lower_display_8": lambda: screen._export_performer_take_to_step_grid(sess),
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
        screen = self._screen()
        if screen is None:
            return False
        sess = self.control.session
        if row == 0:
            screen._select_performer_take(sess, col)
            self.control.request_redraw()
            return True
        if row == 1:
            screen._select_performer_take(sess, col)
            screen._load_performer_take(sess)
            self.control.request_redraw()
            return True
        return True

    def draw_pads(self) -> dict[tuple[int, int], tuple[int, int]]:
        screen = self._screen()
        if screen is None:
            return {}
        sess = self.control.session
        takes = screen._performer_takes(sess)
        selected = getattr(screen, "_performer_take_idx", 0)
        status = screen._performer_player().status()
        playing = status.get("pattern_slot") if status.get("running") else None
        queued = status.get("queued_slot")
        chain_slots = {
            slot for slot in status.get("sequence_slots", [])
            if slot is not None
        }
        out: dict[tuple[int, int], tuple[int, int]] = {}
        for col in range(8):
            saved = bool(takes[col])
            if col == queued:
                color, anim = C.COLOR_BLUE, C.ANIM_BLINK_8TH
            elif col == playing:
                color, anim = C.COLOR_GREEN, C.ANIM_PULSE_QUARTER
            elif col == selected:
                color, anim = C.COLOR_GREEN, C.ANIM_STATIC
            elif col in chain_slots:
                color, anim = C.COLOR_BLUE, C.ANIM_STATIC
            elif saved:
                color, anim = C.COLOR_BLUE, C.ANIM_STATIC
            else:
                color, anim = C.COLOR_DARK_GRAY, C.ANIM_STATIC
            out[(col, 0)] = (color, anim)
            out[(col, 1)] = (
                C.COLOR_BLUE if col == queued else (
                    C.COLOR_GREEN if saved else C.COLOR_DARK_GRAY),
                C.ANIM_BLINK_8TH if col == queued else C.ANIM_STATIC,
            )
        return out

    def draw_buttons(self) -> dict[int, tuple[int, int]]:
        screen = self._screen()
        if screen is None:
            return {}
        status = screen._performer_player().status()
        buttons = {}
        colors = [
            C.COLOR_GREEN if status["running"] else C.COLOR_DARK_GRAY,
            C.COLOR_RED,
            C.COLOR_BLUE,
            C.COLOR_BLUE,
            C.COLOR_GREEN,
            C.COLOR_GREEN if status.get("sequence_enabled") else C.COLOR_DARK_GRAY,
            C.COLOR_BLUE,
            C.COLOR_BLUE,
        ]
        for cc, color in zip(C.BTN_LOWER_DISPLAY_CCS, colors):
            buttons[cc] = (color, C.ANIM_STATIC)
        for idx, cc in enumerate(C.BTN_UPPER_DISPLAY_CCS):
            if idx >= len(self.ENCODER_PAGES):
                continue
            color = (
                C.COLOR_GREEN
                if self.encoder_page == self.ENCODER_PAGES[idx]
                else C.COLOR_DARK_GRAY
            )
            buttons[cc] = (color, C.ANIM_STATIC)
        return buttons

    def draw_oled(self, w: int, h: int) -> Optional[Image.Image]:
        screen = self._screen()
        img = Image.new("RGB", (w, h), color=(8, 9, 14))
        d = ImageDraw.Draw(img)
        try:
            f_big = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            f_med = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
            f_sm = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        except Exception:
            f_big = f_med = f_sm = ImageFont.load_default()
        if screen is None:
            d.text((14, 58), "PERFORMER", fill=(230, 232, 240), font=f_big)
            return img
        sess = self.control.session
        spec = screen._current_performer_spec()
        status = screen._performer_player().status()
        feel = screen._performer_feel()
        state = "PLAYING" if status["running"] else "STOPPED"
        if status.get("queued_pattern_name"):
            state = "QUEUED"
        elif status.get("sequence_enabled"):
            state = "CHAIN"
        playing = screen._slot_label(
            status.get("pattern_slot") if status.get("running") else None,
            status.get("pattern_label") or "")
        if not status.get("running"):
            playing = "-"
        next_slot = status.get("queued_slot")
        next_label = screen._slot_label(
            next_slot, status.get("queued_pattern_label") or "")
        if next_slot is None and status.get("sequence_enabled"):
            next_slot = status.get("sequence_next_slot")
            next_label = screen._slot_label(
                next_slot, status.get("sequence_next_label") or "")
        if not status.get("running") and next_slot is None:
            next_label = "-"
        d.text((12, 5), "PERFORMER", fill=(236, 240, 248), font=f_big)
        d.text((150, 10), f"{screen._performer_bpm(sess):.1f} BPM",
               fill=(158, 174, 206), font=f_med)
        d.text((12, 34), spec.name[:78], fill=(158, 174, 206),
               font=f_med)
        d.text((12, 56),
               f"{state}  Play {playing[:10]}  Next {next_label[:10]}  "
               f"Chain {screen._chain_label(status)}",
               fill=(218, 224, 238), font=f_med)
        progress = max(0.0, min(1.0, float(status.get("loop_progress") or 0.0)))
        d.rectangle((12, 76, w - 12, 82), outline=(48, 56, 78))
        if progress > 0.0:
            d.rectangle((12, 76, 12 + int((w - 24) * progress), 82),
                        fill=(88, 190, 150))
        remaining = float(status.get("loop_remaining") or 0.0)
        if status.get("running"):
            d.text((w - 120, 84), f"{remaining:.1f}s next",
                   fill=(144, 158, 190), font=f_sm)
        labels = [
            ("SWING", f"{feel['swing']:.0f}"),
            ("HUMAN", f"{feel['humanize']:.0f}"),
            ("GATE", f"{feel['gate'] * 100:.0f}%"),
            ("GENRE", screen._style_label(screen._performer_style())[:8]),
            ("TAKE", f"{getattr(screen, '_performer_take_idx', 0) + 1}"),
        ] if self.encoder_page == "feel" else []
        if self.encoder_page == "gen":
            gen = screen._performer_generator_controls()
            labels = [
                ("DENS", f"{gen['density']:.0f}"),
                ("COMPLX", f"{gen['complexity']:.0f}"),
                ("FILL", f"{gen['fill']:.0f}"),
                ("BASS", f"{gen['bass_activity']:.0f}"),
                ("VAR", f"{gen['variation']:.0f}"),
                ("GENRE", screen._style_label(screen._performer_style())[:8]),
            ]
        elif self.encoder_page == "lanes":
            lane = screen._performer_lane()
            ctrl = screen._performer_lane_controls()[lane]
            labels = [
                ("LANE", screen._lane_label(lane)[:8]),
                ("GATE", f"{ctrl['gate'] * 100:.0f}%"),
                ("LEVEL", f"{ctrl['level'] * 100:.0f}%"),
                ("MUTE", "ON" if ctrl["mute"] else "OFF"),
            ]
        elif self.encoder_page == "takes":
            labels = [
                ("TAKE", f"{getattr(screen, '_performer_take_idx', 0) + 1}"),
                ("GENRE", screen._style_label(screen._performer_style())[:8]),
                ("PLAY", playing[:8]),
                ("NEXT", next_label[:8]),
                ("CHAIN", screen._chain_label(status)),
            ]
        for i, (title, value) in enumerate(labels):
            x = 12 + i * 112
            d.rectangle((x, 94, x + 96, 128), outline=(46, 54, 78))
            d.text((x + 8, 98), title, fill=(144, 158, 190), font=f_sm)
            d.text((x + 8, 112), value, fill=(240, 242, 248), font=f_med)
        page_labels = ["FEEL", "GEN", "LANES", "TAKES"]
        for i, label in enumerate(page_labels):
            x = 12 + i * 96
            fill = (126, 238, 186) if self.encoder_page == self.ENCODER_PAGES[i] else (112, 126, 152)
            d.text((x, 84), label, fill=fill, font=f_sm)
        bottom = ["PLAY", "STOP", "GEN", "SAVE", "QUEUE", "CHAIN", "REC 1X", "STEP"]
        for i, label in enumerate(bottom):
            x = 12 + i * 116
            d.text((x, 134), label, fill=(178, 198, 236), font=f_sm)
        return img
