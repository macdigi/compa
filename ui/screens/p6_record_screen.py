"""P-6 Record Screen -- capture performances with waveform display.

Features:
- Record/stop with auto-record on P-6 transport
- RECALL button to save last 60 seconds from rolling buffer
- Level meters and scrolling waveform
- Recording list with tap for detail modal (play/rename/star/delete)
"""

import os
import pygame
import numpy as np
from .. import theme
from ..components.modal import Modal


class P6RecordScreen:
    """Performance recorder with waveform, meters, recall buffer, and file list."""

    def __init__(self, app):
        self.app = app
        self._meter_decay = 0.92
        self._disp_peak_l = 0.0
        self._disp_peak_r = 0.0
        self._scroll_offset = 0
        self._recall_flash = 0
        self._sort_key = "date"  # date, name, size, length
        self._sort_reverse = True  # newest first by default

        # Touch-friendly recording list
        from ui.components.touch_list import TouchList
        list_y = 182  # Below sort bar
        list_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - list_y - 4
        self._rec_list = TouchList(
            pygame.Rect(16, list_y, theme.SCREEN_WIDTH - 32, list_h),
            item_height=44,
        )

        # Detail modal for recording management
        self._detail_modal = Modal(
            "Recording", "", buttons=["PLAY", "STAR", "RENAME", "DELETE", "CLOSE"],
            width=680, height=220,
        )
        self._detail_rec: dict | None = None  # currently selected recording

        # Rename modal
        self._rename_modal = Modal(
            "Rename Recording", "Enter a name:",
            buttons=["SAVE", "CANCEL"], width=450, height=200,
        )

        # Delete confirmation modal
        self._delete_modal = Modal(
            "Delete Recording", "Are you sure?",
            buttons=["DELETE", "CANCEL"], width=350, height=180,
        )

    def on_enter(self):
        if not self.app.recorder._monitoring:
            self.app.recorder.start_monitoring()

    def on_exit(self):
        if not self.app.recorder.is_recording:
            self.app.recorder.stop_monitoring()

    def _cycle_audio_source(self):
        """Cycle the recorder's audio input through connected devices."""
        connected = self.app.device_manager.connected
        if len(connected) < 2:
            return  # Only one device, nothing to cycle

        # Build list of devices that have audio inputs
        audio_devs = [(sn, p) for sn, p in connected.items()
                      if p.audio_in_channels > 0 and p.audio_hint]
        if len(audio_devs) < 2:
            return

        # Find current source by matching recorder's device hint
        current_hint = self.app.recorder._device_hint
        current_idx = 0
        for i, (sn, p) in enumerate(audio_devs):
            if p.audio_hint in current_hint or current_hint in p.audio_hint:
                current_idx = i
                break

        # Cycle to next
        next_idx = (current_idx + 1) % len(audio_devs)
        next_sn, next_profile = audio_devs[next_idx]
        rate = next_profile.supported_sample_rates[0] if next_profile.supported_sample_rates else 44100
        self.app.recorder.switch_device(next_profile.audio_hint, rate)
        print(f"Recording source → {next_sn}", flush=True)

    def _any_modal_visible(self):
        return (self._detail_modal.visible or
                self._rename_modal.visible or
                self._delete_modal.visible)

    def handle_event(self, event):
        # Handle modals first (they consume events when visible)
        if self._rename_modal.visible:
            result = self._rename_modal.handle_event(event)
            if result == "SAVE" and self._detail_rec:
                meta = self.app.recorder.load_metadata(self._detail_rec["path"])
                meta["user_name"] = self._rename_modal.input_text.strip()
                self.app.recorder.save_metadata(self._detail_rec["path"], meta)
            elif result == "CANCEL":
                pass
            return

        if self._delete_modal.visible:
            result = self._delete_modal.handle_event(event)
            if result == "DELETE" and self._detail_rec:
                self.app.recorder.delete_recording(self._detail_rec["path"])
                self._detail_rec = None
            elif result == "CANCEL":
                pass
            return

        if self._detail_modal.visible:
            result = self._detail_modal.handle_event(event)
            if result == "PLAY" and self._detail_rec:
                self.app.recorder.play(self._detail_rec["path"])
                self._detail_modal.hide()
            elif result == "STAR" and self._detail_rec:
                meta = self.app.recorder.load_metadata(self._detail_rec["path"])
                meta["starred"] = not meta.get("starred", False)
                self.app.recorder.save_metadata(self._detail_rec["path"], meta)
                self._detail_modal.hide()
            elif result == "RENAME" and self._detail_rec:
                current_name = self._detail_rec.get("user_name", "")
                self._rename_modal.show(input_mode=True, default_text=current_name)
            elif result == "DELETE" and self._detail_rec:
                self._delete_modal.show(
                    message=f"Delete {self._detail_rec['filename']}?")
            elif result == "CLOSE":
                self._detail_rec = None
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Row 1 buttons (y=4, h=18)
            rec_rect = pygame.Rect(16, 4, 90, 18)
            if rec_rect.collidepoint(mx, my):
                if self.app.recorder.is_recording:
                    self.app.recorder.stop_recording()
                else:
                    meta = {}
                    if self.app.p6:
                        meta["bpm_at_record"] = self.app.p6.state.bpm
                        meta["pattern_at_record"] = self.app.p6.state.active_pattern
                    self.app.recorder.start_recording(metadata=meta)
                return

            recall_rect = pygame.Rect(112, 4, 90, 18)
            if recall_rect.collidepoint(mx, my):
                path = self.app.recorder.recall_buffer()
                if path:
                    self._recall_flash = 30
                return

            stop_rect = pygame.Rect(208, 4, 70, 18)
            if stop_rect.collidepoint(mx, my):
                self.app.recorder.stop_playback()
                return

            auto_rect = pygame.Rect(296, 4, 70, 18)
            if auto_rect.collidepoint(mx, my):
                self.app.auto_record = not self.app.auto_record
                from ui.p6_app import save_config_key
                save_config_key("P6_AUTO_RECORD", "1" if self.app.auto_record else "0")
                return

            # Input source selector (cycle through connected audio devices)
            src_rect = pygame.Rect(372, 4, 120, 18)
            if src_rect.collidepoint(mx, my):
                self._cycle_audio_source()
                return

            # Row 2 buttons (y=24, h=14)
            thresh_rect = pygame.Rect(16, 24, 70, 14)
            if thresh_rect.collidepoint(mx, my):
                self.app.recorder.toggle_threshold_mode()
                return

            th_down = pygame.Rect(90, 24, 30, 14)
            if th_down.collidepoint(mx, my):
                self.app.recorder.set_threshold(self.app.recorder._threshold - 0.005)
                return
            th_up = pygame.Rect(124, 24, 30, 14)
            if th_up.collidepoint(mx, my):
                self.app.recorder.set_threshold(self.app.recorder._threshold + 0.005)
                return

            # SLICE IT button (row 2 right side) — sends to slicer
            slice_rect = pygame.Rect(theme.SCREEN_WIDTH - 130, 24, 110, 14)
            if slice_rect.collidepoint(mx, my):
                self._send_to_slicer()
                return

            # Sort buttons (y=162, between waveform and list)
            sort_y = 162
            sort_btns = [
                (pygame.Rect(16, sort_y, 60, 16), "date"),
                (pygame.Rect(80, sort_y, 60, 16), "name"),
                (pygame.Rect(144, sort_y, 50, 16), "size"),
                (pygame.Rect(198, sort_y, 60, 16), "length"),
            ]
            for rect, sort_key in sort_btns:
                if rect.collidepoint(mx, my):
                    self._sort_recordings(sort_key)
                    return

        # TouchList handles ALL events (drag scroll, wheel, tap)
        # Must be outside the button-1 check so MOUSEMOTION/UP reach it
        tapped = self._rec_list.handle_event(event)
        if tapped and tapped.data:
            rec = tapped.data
            self._detail_rec = rec
            display_name = rec.get("user_name") or rec["filename"]
            dur = rec.get("duration", 0)
            bpm = rec.get("bpm_at_record", "?")
            pat = rec.get("pattern_at_record", "?")
            starred = "YES" if rec.get("starred") else "no"
            size = rec.get("size_mb", 0)
            msg = f"{display_name}  |  {dur:.1f}s  |  {size:.1f}MB"
            self._detail_modal.show(
                title=f"BPM:{bpm}  Pat:{pat}  Star:{starred}",
                message=msg,
            )

    def _sort_recordings(self, key: str):
        """Change sort order. Tap same key again to reverse."""
        if key == self._sort_key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = key
            self._sort_reverse = True if key == "date" else False
        self._last_rec_refresh = 0  # Force refresh
        # Force rebuild by clearing items
        self._rec_list.set_items([])
        print(f"Sort: {key} {'desc' if self._sort_reverse else 'asc'}", flush=True)

    def _send_to_slicer(self):
        """Send the most recent recording (or selected) to the slicer."""
        # Use detail_rec if one is selected, otherwise use the last recording
        path = None
        if self._detail_rec:
            path = self._detail_rec.get("path")
        else:
            recordings = self.app.recorder.list_recordings()
            if recordings:
                path = recordings[0].get("path")
        if path and os.path.isfile(path):
            self.app.switch_screen("sample", {"recording_path": path})
        else:
            print("SLICE IT: no recording to slice", flush=True)

    def _refresh_rec_list(self):
        """Populate the TouchList with current recordings, preserving scroll."""
        from ui.components.touch_list import TouchListItem
        import re
        recordings = self.app.recorder.list_recordings()

        # Don't rebuild if count hasn't changed (preserves scroll)
        if len(recordings) == len(self._rec_list.items) and len(recordings) > 0:
            return

        # Sort recordings
        sort_funcs = {
            "date": lambda r: r.get("filename", ""),  # Filename has timestamp
            "name": lambda r: (r.get("user_name") or r.get("filename", "")).lower(),
            "size": lambda r: r.get("size_mb", 0),
            "length": lambda r: r.get("duration", 0),
        }
        key_func = sort_funcs.get(self._sort_key, sort_funcs["date"])
        recordings = sorted(recordings, key=key_func, reverse=self._sort_reverse)

        saved_scroll = self._rec_list.scroll_offset
        items = []
        for rec in recordings:
            fname = rec.get("filename", "")
            user_name = rec.get("user_name", "")
            dur = rec.get("duration", 0)
            size = rec.get("size_mb", 0)
            starred = rec.get("starred", False)
            source = rec.get("source_device", "")

            # Smart display name
            display = user_name if user_name else fname
            # Remove extension
            display = display.rsplit(".", 1)[0] if "." in display else display

            # Parse date from filename (YYYYMMDD_HHMMSS pattern)
            date_str = ""
            m = re.search(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', fname)
            if m:
                date_str = f"{m.group(2)}/{m.group(3)} {m.group(4)}:{m.group(5)}"

            # Icon based on type
            if fname.startswith("recall_"):
                icon = "R"
                icon_color = theme.YELLOW
            elif fname.startswith("radio_"):
                icon = "~"
                icon_color = theme.BLUE
            elif starred:
                icon = "*"
                icon_color = theme.ACCENT
            else:
                icon = "~"
                icon_color = theme.WAVEFORM_COLOR

            # Subtext: date + duration + size + source
            parts = []
            if date_str:
                parts.append(date_str)
            parts.append(f"{dur:.1f}s")
            parts.append(f"{size:.1f}MB")
            if source:
                parts.append(f"[{source}]")
            subtext = "  ".join(parts)

            items.append(TouchListItem(
                text=display[:45],
                subtext=subtext,
                icon=icon,
                icon_color=icon_color,
                data=rec,
            ))
        self._rec_list.set_items(items)
        self._rec_list.scroll_offset = saved_scroll

    def update(self):
        peak_l, peak_r = (0.0, 0.0)
        if self.app.recorder.available:
            peak_l, peak_r = self.app.recorder.peak_levels
        self._disp_peak_l = max(peak_l, self._disp_peak_l * self._meter_decay)
        self._disp_peak_r = max(peak_r, self._disp_peak_r * self._meter_decay)

        if self._recall_flash > 0:
            self._recall_flash -= 1

        # Refresh recording list only every 2 seconds (not every frame!)
        import time
        now = time.monotonic()
        if now - getattr(self, "_last_rec_refresh", 0) > 2.0:
            self._last_rec_refresh = now
            self._refresh_rec_list()
        self._rec_list.update()

    def draw(self, surface: pygame.Surface):
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")

        # -- Header panel with two rows of buttons (y=0-38) ---------------
        theme.draw_panel(surface, pygame.Rect(0, 0, theme.SCREEN_WIDTH, 40))

        recording = self.app.recorder.is_recording
        playing = self.app.recorder.is_playing_back

        # Row 1 (y=4): [RECORD 90w] [RECALL 90w] [STOP 70w] gap [AUTO 70w]
        rec_rect = pygame.Rect(16, 4, 90, 18)
        rec_bg = theme.RED if recording else theme.BUTTON_BG
        rec_text = "STOP REC" if recording else "RECORD"
        pygame.draw.rect(surface, rec_bg, rec_rect, border_radius=4)
        surf = f_small.render(rec_text, True, theme.TEXT_BRIGHT)
        surface.blit(surf, surf.get_rect(center=rec_rect.center))

        recall_rect = pygame.Rect(112, 4, 90, 18)
        recall_secs = self.app.recorder.recall_seconds_available
        if self._recall_flash > 0:
            recall_bg = theme.GREEN
            recall_text = "SAVED!"
        elif recall_secs >= 1.0:
            recall_bg = theme.ACCENT
            recall_text = f"RECALL {int(recall_secs)}s"
        else:
            recall_bg = theme.BUTTON_BG
            recall_text = "RECALL"
        pygame.draw.rect(surface, recall_bg, recall_rect, border_radius=4)
        text_color = theme.BG if (self._recall_flash > 0 or recall_secs >= 1.0) else theme.TEXT_DIM
        surf = f_small.render(recall_text, True, text_color)
        surface.blit(surf, surf.get_rect(center=recall_rect.center))

        stop_rect = pygame.Rect(208, 4, 70, 18)
        stop_bg = theme.ACCENT if playing else theme.BUTTON_BG
        pygame.draw.rect(surface, stop_bg, stop_rect, border_radius=4)
        surf = f_small.render("STOP", True, theme.TEXT_BRIGHT)
        surface.blit(surf, surf.get_rect(center=stop_rect.center))

        auto_rect = pygame.Rect(296, 4, 70, 18)
        auto_on = self.app.auto_record
        auto_bg = theme.GREEN if auto_on else theme.BUTTON_BG
        auto_text_color = theme.BG if auto_on else theme.TEXT_DIM
        pygame.draw.rect(surface, auto_bg, auto_rect, border_radius=4)
        surf = f_small.render("AUTO", True, auto_text_color)
        surface.blit(surf, surf.get_rect(center=auto_rect.center))

        # Audio source selector — shows which device we're recording from
        src_rect = pygame.Rect(372, 4, 120, 18)
        src_name = self.app.recorder.device_name
        multi_device = len(self.app.device_manager.connected) > 1
        src_bg = theme.ACCENT_DIM if multi_device else theme.BG_PANEL
        pygame.draw.rect(surface, src_bg, src_rect, border_radius=4)
        if multi_device:
            pygame.draw.rect(surface, theme.ACCENT, src_rect, 1, border_radius=4)
        src_label = f"IN: {src_name}"
        surf = f_small.render(src_label, True, theme.TEXT if multi_device else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=src_rect.center))

        # Duration / status on far right of row 1
        if recording:
            dur = self.app.recorder.duration
            mins = int(dur) // 60
            secs = dur % 60
            dur_text = f"{mins}:{secs:04.1f}"
            surf = f_large.render(dur_text, True, theme.RED)
            surface.blit(surf, (theme.SCREEN_WIDTH - surf.get_width() - 16, 0))
        elif playing:
            pf = self.app.recorder.playback_file
            name = os.path.basename(pf) if pf else ""
            surf = f_small.render(f"Playing: {name[:20]}", True, theme.GREEN)
            surface.blit(surf, (theme.SCREEN_WIDTH - surf.get_width() - 16, 6))

        # Row 2 (y=24): [THRESH 70w] [-30w] [+30w] threshold value | status
        th_on = self.app.recorder.threshold_mode
        thresh_rect = pygame.Rect(16, 24, 70, 14)
        th_bg = theme.YELLOW if th_on else theme.BUTTON_BG
        th_tc = theme.BG if th_on else theme.TEXT_DIM
        pygame.draw.rect(surface, th_bg, thresh_rect, border_radius=3)
        surf = f_small.render("THRESH", True, th_tc)
        surface.blit(surf, surf.get_rect(center=thresh_rect.center))

        th_down = pygame.Rect(90, 24, 30, 14)
        pygame.draw.rect(surface, theme.BUTTON_BG, th_down, border_radius=3)
        surf = f_small.render("-", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=th_down.center))

        th_up = pygame.Rect(124, 24, 30, 14)
        pygame.draw.rect(surface, theme.BUTTON_BG, th_up, border_radius=3)
        surf = f_small.render("+", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=th_up.center))

        th_val = f_small.render(f"{self.app.recorder._threshold:.3f}", True, theme.TEXT_DIM)
        surface.blit(th_val, (160, 25))

        # SLICE IT button (row 2 right) — always visible when recordings exist
        slice_rect = pygame.Rect(theme.SCREEN_WIDTH - 130, 24, 110, 14)
        has_recs = len(self.app.recorder.list_recordings()) > 0
        if has_recs:
            pygame.draw.rect(surface, theme.BLUE, slice_rect, border_radius=3)
            surf = f_small.render("SLICE IT →", True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=slice_rect.center))

        # -- Level meters (y=42-56, each 6px tall) ------------------------
        meter_x = 16
        meter_w = theme.SCREEN_WIDTH - 32
        theme.draw_meter(surface, meter_x, 42, meter_w, 6,
                         self._disp_peak_l, "L")
        theme.draw_meter(surface, meter_x, 50, meter_w, 6,
                         self._disp_peak_r, "R")

        # -- Waveform display (y=60-158, 98px tall in panel) --------------
        wave_panel = pygame.Rect(12, 58, theme.SCREEN_WIDTH - 24, 102)
        theme.draw_panel(surface, wave_panel, border=True)
        wave_rect = pygame.Rect(16, 60, theme.SCREEN_WIDTH - 32, 98)
        pygame.draw.rect(surface, theme.WAVEFORM_BG, wave_rect, border_radius=4)

        waveform = self.app.recorder.waveform
        if waveform is not None and len(waveform) > 0:
            max_val = max(np.max(waveform), 0.001)
            points = []
            w = wave_rect.width
            h = wave_rect.height
            for i in range(min(w, len(waveform))):
                idx = (self.app.recorder._waveform_pos - w + i) % len(waveform)
                val = waveform[idx] / max_val
                px = wave_rect.x + i
                py = wave_rect.bottom - int(val * h * 0.9)
                points.append((px, py))

            if len(points) >= 2:
                pygame.draw.lines(surface, theme.WAVEFORM_COLOR, False, points, 1)

        cy = wave_rect.centery
        pygame.draw.line(surface, theme.BORDER,
                        (wave_rect.x, cy), (wave_rect.right, cy), 1)

        # -- Sort bar (y=160) ------------------------------------------------
        sort_y = 162
        sort_btns = [
            (pygame.Rect(16, sort_y, 60, 16), "date", "DATE"),
            (pygame.Rect(80, sort_y, 60, 16), "name", "NAME"),
            (pygame.Rect(144, sort_y, 50, 16), "size", "SIZE"),
            (pygame.Rect(198, sort_y, 60, 16), "length", "LENGTH"),
        ]
        for rect, key, label in sort_btns:
            active = self._sort_key == key
            bg = theme.ACCENT if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, rect, border_radius=3)
            arrow = " v" if active and self._sort_reverse else " ^" if active else ""
            surf = f_small.render(f"{label}{arrow}", True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Recording count
        count = len(self._rec_list.items)
        surf = f_small.render(f"{count} recordings", True, theme.TEXT_DIM)
        surface.blit(surf, (270, sort_y + 1))
        self._rec_list.draw(surface)

        # -- Draw modals on top -------------------------------------------
        self._detail_modal.draw(surface)
        self._rename_modal.draw(surface)
        self._delete_modal.draw(surface)

    def _draw_meter(self, surface, x, y, w, h, level, label):
        f = theme.font("small")
        lbl = f.render(label, True, theme.TEXT_DIM)
        surface.blit(lbl, (x, y))
        bar_x = x + 20
        bar_w = w - 20

        pygame.draw.rect(surface, theme.WAVEFORM_BG,
                        (bar_x, y, bar_w, h), border_radius=2)

        fill_w = int(bar_w * min(1.0, level))
        if fill_w > 0:
            color = theme.RED if level > 0.9 else theme.YELLOW if level > 0.7 else theme.GREEN
            pygame.draw.rect(surface, color,
                           (bar_x, y, fill_w, h), border_radius=2)
