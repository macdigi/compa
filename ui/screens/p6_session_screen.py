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
            # Storage buttons are dynamic position — use the panel area
            backup_rect = pygame.Rect(16, 370, 90, 22)
            restore_rect = pygame.Rect(116, 370, 90, 22)
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
        y = 6

        f_title = theme.font("title")
        f_hero = theme.font("hero")
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_mono = theme.font("mono")

        # ── ASCII-style logo header ──────────────────────────────────
        logo_lines = [
            "  ___ ___  __  __ ___  _   ",
            " / __/ _ \\|  \\/  | _ \\/ \\  ",
            "| (_| (_) | |\\/| |  _/ _ \\ ",
            " \\___\\___/|_|  |_|_|/_/ \\_\\",
        ]
        for i, line in enumerate(logo_lines):
            surf = f_mono.render(line, True, theme.ACCENT)
            surface.blit(surf, (12, y + i * 14))

        # Version + tagline next to logo
        surf = f_small.render("v1.0", True, theme.TEXT_DIM)
        surface.blit(surf, (240, y + 4))
        dev_name = self.app.device_name
        surf = f_small.render(f"{dev_name} Companion", True, theme.TEXT_DIM)
        surface.blit(surf, (240, y + 20))

        # Connection status (right of logo)
        p6_connected = self.app.p6 and self.app.p6.connected
        status_x = 240
        status_y = y + 40
        if p6_connected:
            pygame.draw.circle(surface, theme.GREEN, (status_x + 5, status_y + 7), 4)
            surf = f_small.render(f"{dev_name} connected", True, theme.GREEN)
        else:
            pygame.draw.circle(surface, theme.RED, (status_x + 5, status_y + 7), 4, 1)
            surf = f_small.render(f"{dev_name} not found", True, theme.RED)
        surface.blit(surf, (status_x + 14, status_y))

        y += 62
        pygame.draw.line(surface, theme.BORDER, (12, y), (theme.SCREEN_WIDTH - 12, y))
        y += 8

        # ── Left column ──────────────────────────────────────────────
        left_x = 16
        col_y = y

        # Transport + BPM + Pattern — compact row
        theme.draw_panel(surface, pygame.Rect(10, col_y - 2, 400, 90))

        if self.app.p6 and self.app.p6.state.playing:
            pygame.draw.circle(surface, theme.GREEN, (left_x + 6, col_y + 10), 5)
            surf = f_med.render("PLAYING", True, theme.GREEN)
        else:
            pygame.draw.circle(surface, theme.TEXT_DIM, (left_x + 6, col_y + 10), 4, 1)
            surf = f_med.render("STOPPED", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x + 16, col_y))

        # Recording indicator
        if self.app.recorder.is_recording:
            dur = self.app.recorder.duration
            surf = f_med.render(f"REC {dur:.0f}s", True, theme.RED)
            surface.blit(surf, (140, col_y))
        col_y += 22

        # BPM big + Pattern
        bpm = self.app.p6.state.bpm if self.app.p6 else 120.0
        f_hero = theme.font("hero")
        surf = f_hero.render(f"{bpm:.0f}", True, theme.TEXT)
        surface.blit(surf, (left_x, col_y))
        bpm_w = surf.get_width()
        surf = f_med.render("BPM", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x + bpm_w + 6, col_y + 14))

        pattern = self.app.p6.state.active_pattern if self.app.p6 else 0
        surf = f_large.render(f"Pattern {pattern + 1}", True, theme.ACCENT)
        surface.blit(surf, (220, col_y + 8))

        col_y += 50
        # Auto-record indicator
        if self.app.auto_record:
            surf = f_small.render("AUTO-REC ON", True, theme.GREEN)
            surface.blit(surf, (left_x, col_y))
        recall_secs = self.app.recorder.recall_seconds_available
        if recall_secs > 0:
            surf = f_small.render(f"Buffer: {int(recall_secs)}s", True, theme.ACCENT)
            surface.blit(surf, (140, col_y))
        col_y += 20

        # ── Resample calc — compact 2-line ───────────────────────────
        col_y += 4
        theme.draw_panel(surface, pygame.Rect(10, col_y - 2, 400, 68))
        surf = f_small.render(f"RESAMPLE @ {bpm:.0f} BPM", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x, col_y))
        col_y += 16
        from engine.p6_presets import resample_calc
        calc = resample_calc(bpm)
        for row in calc:
            bars = row["bars"]
            secs = row["seconds"]
            fits = row["fits"]
            ok_rates = [f"{r//1000}k" for r, ok in fits.items() if ok]
            if len(ok_rates) == 4:
                line = f"{bars}bar={secs:.1f}s ALL OK"
                color = theme.GREEN
            else:
                line = f"{bars}bar={secs:.1f}s {' '.join(ok_rates)}"
                color = theme.TEXT_DIM
            surf = f_small.render(line, True, color)
            surface.blit(surf, (left_x, col_y))
            col_y += 13
        col_y += 6

        # ── P-6 Storage — compact ────────────────────────────────────
        theme.draw_panel(surface, pygame.Rect(10, col_y - 2, 400, 42))
        storage_y = col_y

        mounted = self._image_mgr.p6_mounted
        mount_text = "USB: READY" if mounted else "USB: NOT MOUNTED"
        mount_color = theme.GREEN if mounted else theme.TEXT_DIM

        surf = f_small.render("STORAGE", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x, storage_y + 2))

        surf = f_small.render(mount_text, True, mount_color)
        surface.blit(surf, (240, storage_y + 2))

        backup_rect = pygame.Rect(left_x, storage_y + 16, 90, 22)
        b_bg = theme.ACCENT if mounted else theme.BUTTON_BG
        b_tc = theme.BG if mounted else theme.TEXT_DIM
        pygame.draw.rect(surface, b_bg, backup_rect, border_radius=4)
        surf = f_small.render("BACKUP", True, b_tc)
        surface.blit(surf, surf.get_rect(center=backup_rect.center))

        restore_rect = pygame.Rect(116, storage_y + 16, 90, 22)
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
        theme.draw_meter(surface, right_x, y, 360, 12,
                        self._disp_peak_l, "L")
        theme.draw_meter(surface, right_x, y + 16, 360, 12,
                        self._disp_peak_r, "R")
        meter_y = y + 16 + 12

        # Notes label
        notes_label_y = meter_y + 12
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
