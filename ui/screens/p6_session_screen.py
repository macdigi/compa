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
        f_title = theme.font("title")
        f_hero = theme.font("hero")
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")
        f_mono = theme.font("mono")

        # ── Header bar ───────────────────────────────────────────────
        theme.draw_screen_header(surface, "COMPA", "v1.0")

        # ── Device status cards (full width) ─────────────────────────
        connected = self.app.device_manager.connected
        focus_key = self.app.device_manager.focus_key
        card_y = 44
        card_gap = 6
        num_devices = len(connected)
        content_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 60  # Room for bottom bar
        avail_h = content_bottom - card_y
        card_h = min(140, max(80, (avail_h - (num_devices - 1) * card_gap) // max(1, num_devices)))

        for short_name, profile in connected.items():
            midi = self.app._midi_connections.get(short_name)
            is_focused = (short_name == focus_key)
            is_connected = midi and midi.connected

            # Card background
            card_rect = pygame.Rect(12, card_y, theme.SCREEN_WIDTH - 24, card_h)
            if is_focused:
                pygame.draw.rect(surface, theme.BG_PANEL, card_rect, border_radius=8)
                pygame.draw.rect(surface, theme.ACCENT, card_rect, 2, border_radius=8)
            else:
                pygame.draw.rect(surface, theme.BG_PANEL, card_rect, border_radius=8)
                pygame.draw.rect(surface, theme.BORDER, card_rect, 1, border_radius=8)

            cx = card_rect.x + 16
            cy = card_rect.y + 8

            # ── Left: Device name + connection dot ───────────────────
            if is_connected:
                pygame.draw.circle(surface, theme.GREEN, (cx + 5, cy + 9), 5)
            else:
                pygame.draw.circle(surface, theme.RED, (cx + 5, cy + 9), 5, 2)

            name_color = theme.TEXT_BRIGHT if is_focused else theme.TEXT
            surf = f_large.render(profile.name, True, name_color)
            surface.blit(surf, (cx + 16, cy))

            if is_focused:
                tag_x = cx + 16 + surf.get_width() + 8
                tag_rect = pygame.Rect(tag_x, cy + 3, 50, 16)
                pygame.draw.rect(surface, theme.ACCENT, tag_rect, border_radius=3)
                surf = f_tiny.render("FOCUS", True, theme.BG)
                surface.blit(surf, surf.get_rect(center=tag_rect.center))

            # Audio info
            audio_text = f"{profile.audio_in_channels}in/{profile.audio_out_channels}out"
            rates = "/".join(f"{r//1000}k" for r in profile.supported_sample_rates)
            surf = f_tiny.render(f"{audio_text}  {rates}", True, theme.TEXT_DIM)
            surface.blit(surf, (cx + 16, cy + 22))

            # ── Center: BPM (big) ────────────────────────────────────
            bpm_x = card_rect.centerx - 80
            if midi:
                bpm = midi.state.bpm
                surf = f_hero.render(f"{bpm:.0f}", True, theme.TEXT_BRIGHT)
                surface.blit(surf, (bpm_x, cy - 2))
                bpm_w = surf.get_width()
                surf = f_small.render("BPM", True, theme.TEXT_DIM)
                surface.blit(surf, (bpm_x + bpm_w + 4, cy + 16))
            else:
                surf = f_large.render("---", True, theme.TEXT_DIM)
                surface.blit(surf, (bpm_x, cy + 4))

            # ── Right: Transport + Pattern ───────────────────────────
            rx = card_rect.right - 180

            if midi:
                # Transport state
                if midi.state.playing:
                    pygame.draw.circle(surface, theme.GREEN, (rx + 8, cy + 10), 6)
                    surf = f_med.render("PLAYING", True, theme.GREEN)
                else:
                    pygame.draw.circle(surface, theme.TEXT_DIM, (rx + 8, cy + 10), 5, 2)
                    surf = f_med.render("STOPPED", True, theme.TEXT_DIM)
                surface.blit(surf, (rx + 20, cy))

                # Pattern
                pat = midi.state.active_pattern + 1
                pat_max = getattr(profile, "pattern_count", 0)
                if pat_max > 0:
                    surf = f_med.render(f"Pattern {pat}/{pat_max}", True, theme.ACCENT)
                    surface.blit(surf, (rx, cy + 24))
            else:
                surf = f_med.render("NOT CONNECTED", True, theme.RED)
                surface.blit(surf, (rx, cy + 8))

            # ── Bottom row of card: recording status + features ──────
            if card_h > 60:
                by = card_rect.y + card_h - 24

                # Recording indicator
                if self.app.recorder.is_recording:
                    src = self.app.recorder.device_name
                    dur = self.app.recorder.duration
                    pygame.draw.circle(surface, theme.RED, (cx + 20, by + 7), 4)
                    surf = f_small.render(f"REC {dur:.0f}s [{src}]", True, theme.RED)
                    surface.blit(surf, (cx + 30, by))
                elif self.app.auto_record:
                    surf = f_small.render("AUTO-REC", True, theme.GREEN)
                    surface.blit(surf, (cx + 16, by))

                # Feature badges
                badge_x = rx
                badges = []
                if getattr(profile, "has_effects", False):
                    badges.append(("FX", theme.ACCENT))
                if getattr(profile, "has_looper", False):
                    badges.append(("LOOP", theme.BLUE))
                if getattr(profile, "has_dj_mode", False):
                    badges.append(("DJ", theme.GREEN))
                if getattr(profile, "has_granular", False):
                    badges.append(("GRAN", theme.YELLOW))

                for label, color in badges:
                    bw = len(label) * 7 + 10
                    badge_rect = pygame.Rect(badge_x, by, bw, 16)
                    pygame.draw.rect(surface, color, badge_rect, border_radius=3)
                    surf = f_tiny.render(label, True, theme.BG)
                    surface.blit(surf, surf.get_rect(center=badge_rect.center))
                    badge_x += bw + 4

            card_y += card_h + card_gap

        # ── No devices fallback ──────────────────────────────────────
        if not connected:
            surf = f_large.render("No devices connected", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=120))
            surf = f_small.render("Plug in a USB device to get started", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=160))

        # ── Bottom info bar ──────────────────────────────────────────
        bar_y = content_bottom
        bpm = self.app.p6.state.bpm if self.app.p6 else 120.0
        col_y = bar_y

        # ── Bottom info strip ─────────────────────────────────────────
        info_y = content_bottom + 4
        left_x = 16

        # Resample calc (compact inline)
        bpm = self.app.p6.state.bpm if self.app.p6 else 120.0
        from engine.p6_presets import resample_calc
        calc = resample_calc(bpm)
        surf = f_tiny.render(f"RESAMPLE @ {bpm:.0f}", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x, info_y))
        rx = left_x + surf.get_width() + 12
        for row in calc[:2]:  # Show first 2 rows only
            bars = row["bars"]
            secs = row["seconds"]
            fits = row["fits"]
            ok_rates = [f"{r//1000}k" for r, ok in fits.items() if ok]
            if len(ok_rates) == 4:
                text = f"{bars}bar={secs:.1f}s OK"
                color = theme.GREEN
            else:
                text = f"{bars}bar={secs:.1f}s {' '.join(ok_rates[:2])}"
                color = theme.TEXT_DIM
            surf = f_tiny.render(text, True, color)
            surface.blit(surf, (rx, info_y))
            rx += surf.get_width() + 16

        # Recording + buffer status
        info_y += 16
        if self.app.recorder.is_recording:
            dur = self.app.recorder.duration
            src = self.app.recorder.device_name
            pygame.draw.circle(surface, theme.RED, (left_x + 5, info_y + 6), 4)
            surf = f_small.render(f"REC {dur:.0f}s [{src}]", True, theme.RED)
            surface.blit(surf, (left_x + 14, info_y))
        else:
            if self.app.auto_record:
                surf = f_tiny.render("AUTO-REC ON", True, theme.GREEN)
                surface.blit(surf, (left_x, info_y))
            recall = self.app.recorder.recall_seconds_available
            if recall > 0:
                surf = f_tiny.render(f"Buffer: {int(recall)}s", True, theme.ACCENT)
                surface.blit(surf, (left_x + 100, info_y))

        # Level meters (inline, right side of bottom strip)
        meter_x = theme.SCREEN_WIDTH // 2 + 40
        meter_w = theme.SCREEN_WIDTH - meter_x - 20
        theme.draw_meter(surface, meter_x, info_y - 12, meter_w, 8,
                        self._disp_peak_l, "L")
        theme.draw_meter(surface, meter_x, info_y + 2, meter_w, 8,
                        self._disp_peak_r, "R")

        # Collapsed notepad (tap to expand — not implemented yet, just shows hint)
        notes_text = self._text_area.text.strip()
        if notes_text:
            info_y += 18
            preview = notes_text[:60].replace("\n", " ")
            surf = f_tiny.render(f"Notes: {preview}...", True, theme.TEXT_DIM)
            surface.blit(surf, (left_x, info_y))

        # Modals
        self._backup_modal.draw(surface)
        self._restore_modal.draw(surface)

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
