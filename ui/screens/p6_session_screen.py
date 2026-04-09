"""P-6 Session Screen — dashboard with live status, notes, and P-6 backup."""

import json
import math
import os
import time
import pygame
from .. import theme
from ..components.text_area import TextArea
from ..components.modal import Modal
from engine.p6_image import P6ImageManager


class P6SessionScreen:
    """Main dashboard: transport, BPM, pattern, levels, and session notes."""

    def __init__(self, app):
        self.app = app
        self._meter_decay = 0.92
        self._disp_peak_l = 0.0
        self._disp_peak_r = 0.0

        # Session notes
        notes_dir = app.config.get("P6_SESSIONS_DIR",
                                    os.path.join(os.path.dirname(os.path.dirname(
                                        os.path.abspath(__file__))), "sessions"))
        os.makedirs(notes_dir, exist_ok=True)
        self._notes_path = os.path.join(notes_dir, "notes.json")

        # Text area in right column
        self._text_area = TextArea(
            pygame.Rect(420, 100, 360, 320),
            text=self._load_notes(),
        )

        self._last_save = time.monotonic()
        self._save_interval = 2.0

        # P-6 image backup/restore
        images_dir = os.path.join(notes_dir, "images")
        self._image_mgr = P6ImageManager(images_dir)
        self._backup_modal = Modal(
            "Backup P-6", "Name this backup:",
            buttons=["SAVE", "CANCEL"], width=400, height=190,
        )
        self._restore_modal = Modal(
            "Restore P-6", "This will overwrite P-6 contents!",
            buttons=["RESTORE", "CANCEL"], width=400, height=190,
        )
        self._restore_target: str | None = None
        self._backup_flash = 0

    @property
    def wants_keyboard(self) -> bool:
        """Tell the app to skip global shortcuts when text area is focused."""
        return self._text_area.focused

    def _load_notes(self) -> str:
        if os.path.exists(self._notes_path):
            try:
                with open(self._notes_path) as f:
                    data = json.load(f)
                    return data.get("text", "")
            except Exception:
                pass
        return ""

    def _save_notes(self):
        try:
            with open(self._notes_path, "w") as f:
                json.dump({"text": self._text_area.text}, f)
        except Exception:
            pass

    def on_enter(self):
        pass

    def on_exit(self):
        self._save_notes()

    def handle_event(self, event):
        # Modals first
        if self._backup_modal.visible:
            result = self._backup_modal.handle_event(event)
            if result == "SAVE":
                name = self._backup_modal.input_text.strip() or "backup"
                self._image_mgr.backup(name)
            return
        if self._restore_modal.visible:
            result = self._restore_modal.handle_event(event)
            if result == "RESTORE" and self._restore_target:
                self._image_mgr.restore(self._restore_target)
                self._restore_target = None
            return

        # Backup/restore buttons
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            backup_rect = pygame.Rect(16, 370, 100, 30)
            restore_rect = pygame.Rect(126, 370, 100, 30)
            if backup_rect.collidepoint(mx, my) and self._image_mgr.p6_mounted:
                self._backup_modal.show(input_mode=True, default_text="")
                return
            if restore_rect.collidepoint(mx, my) and self._image_mgr.p6_mounted:
                images = self._image_mgr.list_images()
                if images:
                    self._restore_target = images[0]["path"]
                    self._restore_modal.show(
                        message=f"Restore '{images[0]['name']}'? This overwrites P-6!")
                return

        self._text_area.handle_event(event)

    def update(self):
        # Smooth level meters
        peak_l, peak_r = (0.0, 0.0)
        if self.app.recorder.available:
            peak_l, peak_r = self.app.recorder.peak_levels
        self._disp_peak_l = max(peak_l, self._disp_peak_l * self._meter_decay)
        self._disp_peak_r = max(peak_r, self._disp_peak_r * self._meter_decay)

        # Auto-save notes
        if self._text_area.changed:
            self._last_save = time.monotonic()
        now = time.monotonic()
        if now - self._last_save < self._save_interval + 0.5 and now - self._last_save > self._save_interval:
            self._save_notes()

    def draw(self, surface: pygame.Surface):
        y = 8

        f_title = theme.font("title")
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")

        title = f_title.render("COMPA", True, theme.ACCENT)
        surface.blit(title, (16, y))
        y += 36

        # Connection status line
        p6_status = "CONNECTED" if (self.app.p6 and self.app.p6.connected) else "NOT CONNECTED"
        p6_color = theme.GREEN if (self.app.p6 and self.app.p6.connected) else theme.RED
        atom_status = "ATOM SQ" if (self.app.atom_sq and self.app.atom_sq.connected) else ""

        status_line = f"P-6: {p6_status}"
        if atom_status:
            status_line += f"  |  {atom_status}"
        surf = f_small.render(status_line, True, p6_color)
        surface.blit(surf, (16, y))
        y += 24

        pygame.draw.line(surface, theme.BORDER, (16, y), (theme.SCREEN_WIDTH - 16, y))
        y += 12

        # ── Left column: Status ──────────────────────────────────────
        left_x = 16
        col_y = y

        # Transport state
        if self.app.p6 and self.app.p6.state.playing:
            transport_text = "PLAYING"
            transport_color = theme.GREEN
        else:
            transport_text = "STOPPED"
            transport_color = theme.TEXT_DIM

        surf = f_large.render(transport_text, True, transport_color)
        surface.blit(surf, (left_x, col_y))
        col_y += 32

        # BPM
        bpm = self.app.p6.state.bpm if self.app.p6 else 120.0
        surf = f_title.render(f"{bpm:.0f} BPM", True, theme.TEXT)
        surface.blit(surf, (left_x, col_y))
        col_y += 40

        # Active pattern
        pattern = self.app.p6.state.active_pattern if self.app.p6 else 0
        surf = f_large.render(f"Pattern {pattern + 1}", True, theme.ACCENT)
        surface.blit(surf, (left_x, col_y))
        col_y += 36

        # Layer indicator
        if self.app.router:
            layer = self.app.router.layer.value.upper()
            surf = f_med.render(f"Layer: {layer}", True, theme.TEXT_DIM)
            surface.blit(surf, (left_x, col_y))
            col_y += 28

        # Auto-record status
        auto_text = "Auto-Record: ON" if self.app.auto_record else "Auto-Record: off"
        auto_color = theme.GREEN if self.app.auto_record else theme.TEXT_DIM
        surf = f_small.render(auto_text, True, auto_color)
        surface.blit(surf, (left_x, col_y))
        col_y += 22

        # Recording status
        if self.app.recorder.is_recording:
            dur = self.app.recorder.duration
            mins = int(dur) // 60
            secs = dur % 60
            rec_text = f"REC  {mins}:{secs:04.1f}"
            surf = f_large.render(rec_text, True, theme.RED)
            surface.blit(surf, (left_x, col_y))
            col_y += 32

        # ── Resample calculator ──────────────────────────────────────
        col_y += 6
        bpm = self.app.p6.state.bpm if self.app.p6 else 120.0
        from engine.p6_presets import resample_calc
        calc = resample_calc(bpm)
        surf = f_small.render(f"RESAMPLE @ {bpm:.0f} BPM", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x, col_y))
        col_y += 16
        for row in calc:
            bars = row["bars"]
            secs = row["seconds"]
            fits = row["fits"]
            marks = ""
            for rate in [44100, 22050, 14700, 11025]:
                r_label = f"{rate // 1000}k" if rate >= 1000 else str(rate)
                marks += f"{'OK' if fits[rate] else '--':>3} "
            text = f"{bars}bar={secs:5.1f}s  44k {marks}"
            # Simplified: just show which rates fit
            ok_rates = [f"{r//1000}k" for r, ok in fits.items() if ok]
            no_rates = [f"{r//1000}k" for r, ok in fits.items() if not ok]
            if no_rates:
                line = f" {bars} bar = {secs:.1f}s  {' '.join(ok_rates)}"
                surf = f_small.render(line, True, theme.TEXT_DIM)
            else:
                line = f" {bars} bar = {secs:.1f}s  ALL rates OK"
                surf = f_small.render(line, True, theme.GREEN)
            surface.blit(surf, (left_x, col_y))
            col_y += 15
        col_y += 4

        # Recall buffer indicator
        recall_secs = self.app.recorder.recall_seconds_available
        if recall_secs > 0:
            surf = f_small.render(f"Recall buffer: {int(recall_secs)}s", True, theme.ACCENT)
            surface.blit(surf, (left_x, col_y))
            col_y += 16

        # ── 6 P-6 pad slots ─────────────────────────────────────────
        col_y += 4
        surf = f_small.render("P-6 PADS", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x, col_y))
        col_y += 18

        pad_w = 55
        pad_h = 55
        pad_gap = 8
        for i in range(6):
            px = left_x + i * (pad_w + pad_gap)
            rect = pygame.Rect(px, col_y, pad_w, pad_h)
            pygame.draw.rect(surface, theme.PAD_OFF, rect, border_radius=4)
            pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=4)
            label = f_small.render(f"PAD {i + 1}", True, theme.TEXT_DIM)
            lr = label.get_rect(center=rect.center)
            surface.blit(label, lr)

        # ── P-6 Storage (below pads) ─────────────────────────────────
        storage_y = col_y + pad_h + 12
        surf = f_small.render("P-6 STORAGE", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x, storage_y))
        storage_y += 16

        mounted = self._image_mgr.p6_mounted
        mount_text = "USB: READY" if mounted else "USB: NOT MOUNTED"
        mount_color = theme.GREEN if mounted else theme.TEXT_DIM
        surf = f_small.render(mount_text, True, mount_color)
        surface.blit(surf, (240, storage_y + 6))

        backup_rect = pygame.Rect(16, storage_y, 100, 30)
        b_bg = theme.ACCENT if mounted else theme.BUTTON_BG
        b_tc = theme.BG if mounted else theme.TEXT_DIM
        pygame.draw.rect(surface, b_bg, backup_rect, border_radius=4)
        surf = f_small.render("BACKUP", True, b_tc)
        surface.blit(surf, surf.get_rect(center=backup_rect.center))

        restore_rect = pygame.Rect(126, storage_y, 100, 30)
        r_bg = theme.ACCENT if mounted else theme.BUTTON_BG
        r_tc = theme.BG if mounted else theme.TEXT_DIM
        pygame.draw.rect(surface, r_bg, restore_rect, border_radius=4)
        surf = f_small.render("RESTORE", True, r_tc)
        surface.blit(surf, surf.get_rect(center=restore_rect.center))

        # Instructions when not mounted
        if not mounted and not self._image_mgr.busy:
            hint_y = storage_y + 34
            surf = f_small.render("Hold [STOP]+power on = pattern backup", True, theme.TEXT_DIM)
            surface.blit(surf, (16, hint_y))
            surf = f_small.render("Hold [BANK]+power on = sample export", True, theme.TEXT_DIM)
            surface.blit(surf, (16, hint_y + 16))

        # Progress bar or status
        if self._image_mgr.busy:
            prog_y = storage_y + 34
            prog_rect = pygame.Rect(16, prog_y, 370, 12)
            pygame.draw.rect(surface, theme.KNOB_BG, prog_rect, border_radius=3)
            fill = int(prog_rect.width * self._image_mgr.progress)
            if fill > 0:
                pygame.draw.rect(surface, theme.GREEN,
                                (prog_rect.x, prog_rect.y, fill, 12), border_radius=3)
            surf = f_small.render(self._image_mgr.status, True, theme.TEXT)
            surface.blit(surf, (16, prog_y + 14))

        # Draw modals
        self._backup_modal.draw(surface)
        self._restore_modal.draw(surface)

        # ── Right column: Level meters + Notes ───────────────────────
        right_x = 420

        # Level meters
        surf = f_small.render("INPUT LEVEL", True, theme.TEXT_DIM)
        surface.blit(surf, (right_x, y))
        meter_y = y + 18

        meter_w = 360
        meter_h = 16
        self._draw_meter(surface, right_x, meter_y, meter_w, meter_h,
                        self._disp_peak_l, "L")
        meter_y += meter_h + 3
        self._draw_meter(surface, right_x, meter_y, meter_w, meter_h,
                        self._disp_peak_r, "R")

        # Notes label
        notes_label_y = meter_y + meter_h + 12
        surf = f_small.render("SESSION NOTES", True, theme.TEXT_DIM)
        surface.blit(surf, (right_x, notes_label_y))

        # Text area
        self._text_area.rect.y = notes_label_y + 18
        self._text_area.rect.height = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self._text_area.rect.y - 6
        self._text_area.draw(surface)

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
