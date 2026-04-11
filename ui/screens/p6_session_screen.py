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

    # ── ASCII art device names (playing card style) ────────────────
    # COMPA logo (keep the original that works)
    _COMPA_LOGO = [
        "  ___ ___  __  __ ___  _   ",
        " / __/ _ \\|  \\/  | _ \\/ \\  ",
        "| (_| (_) | |\\/| |  _/ _ \\ ",
        " \\___\\___/|_|  |_|_|/_/ \\_\\",
    ]

    # Device → theme color name for card accent
    _DEVICE_COLORS = {
        "P-6": (255, 230, 0),       # Yellow
        "SP-404": (0, 200, 180),     # Teal
        "Force": (220, 50, 50),      # Red
    }

    def draw(self, surface: pygame.Surface):
        f_title = theme.font("title")
        f_hero = theme.font("hero")
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")
        f_mono = theme.font("mono")

        # ── COMPA logo (top left) ────────────────────────────────────
        for i, line in enumerate(self._COMPA_LOGO):
            surf = f_mono.render(line, True, theme.ACCENT)
            surface.blit(surf, (12, 6 + i * 14))

        # Version
        surf = f_tiny.render("v1.0", True, theme.TEXT_DIM)
        surface.blit(surf, (240, 10))

        # ── Device playing cards (horizontal, max 3) ─────────────────
        connected = self.app.device_manager.connected
        focus_key = self.app.device_manager.focus_key
        devices = list(connected.items())[:3]  # Max 3 cards
        num_cards = len(devices)

        cards_y = 68
        cards_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50
        card_h = cards_bottom - cards_y
        card_gap = 10
        total_w = theme.SCREEN_WIDTH - 24
        card_w = (total_w - (num_cards - 1) * card_gap) // max(1, num_cards)

        for idx, (short_name, profile) in enumerate(devices):
            midi = self.app._midi_connections.get(short_name)
            is_focused = (short_name == focus_key)
            is_connected = midi and midi.connected
            device_color = self._DEVICE_COLORS.get(short_name, theme.ACCENT)

            card_x = 12 + idx * (card_w + card_gap)
            card_rect = pygame.Rect(card_x, cards_y, card_w, card_h)

            # Card background + border
            pygame.draw.rect(surface, theme.BG_PANEL, card_rect, border_radius=10)
            border_color = device_color if is_focused else theme.BORDER
            border_w = 2 if is_focused else 1
            pygame.draw.rect(surface, border_color, card_rect, border_w, border_radius=10)

            # Top accent stripe
            stripe = pygame.Rect(card_x + 1, cards_y + 1, card_w - 2, 4)
            pygame.draw.rect(surface, device_color, stripe,
                           border_radius=0)

            cx = card_x + 12
            cy = cards_y + 12

            # ── Device name (big, bold, in device color) ────────────
            surf = f_title.render(short_name, True, device_color)
            surface.blit(surf, (cx, cy))
            cy += surf.get_height() + 4

            # Connection dot
            if is_connected:
                pygame.draw.circle(surface, theme.GREEN, (cx + 5, cy + 6), 4)
                surf = f_tiny.render("connected", True, theme.GREEN)
            else:
                pygame.draw.circle(surface, theme.RED, (cx + 5, cy + 6), 4, 1)
                surf = f_tiny.render("offline", True, theme.RED)
            surface.blit(surf, (cx + 14, cy))
            cy += 18

            # ── BPM (big, in device color) ───────────────────────────
            if midi:
                bpm = midi.state.bpm
                surf = f_hero.render(f"{bpm:.0f}", True, device_color)
                surface.blit(surf, (cx, cy))
                bpm_w = surf.get_width()
                surf = f_small.render("BPM", True, theme.TEXT_DIM)
                surface.blit(surf, (cx + bpm_w + 4, cy + 16))
            else:
                surf = f_large.render("---", True, theme.TEXT_DIM)
                surface.blit(surf, (cx, cy + 4))
            cy += 42

            # ── Transport + Pattern ──────────────────────────────────
            if midi:
                if midi.state.playing:
                    pygame.draw.circle(surface, theme.GREEN, (cx + 5, cy + 7), 5)
                    surf = f_med.render("PLAYING", True, theme.GREEN)
                else:
                    pygame.draw.circle(surface, theme.TEXT_DIM, (cx + 5, cy + 7), 4, 1)
                    surf = f_med.render("STOPPED", True, theme.TEXT_DIM)
                surface.blit(surf, (cx + 16, cy))
                cy += 22

                pat = midi.state.active_pattern + 1
                pat_max = getattr(profile, "pattern_count", 0)
                if pat_max > 0:
                    surf = f_med.render(f"Ptn {pat}/{pat_max}", True, device_color)
                    surface.blit(surf, (cx, cy))
                cy += 22

            # ── Audio info ───────────────────────────────────────────
            audio = f"{profile.audio_in_channels}in/{profile.audio_out_channels}out"
            rates = "/".join(f"{r//1000}k" for r in profile.supported_sample_rates)
            surf = f_tiny.render(f"{audio} {rates}", True, theme.TEXT_DIM)
            surface.blit(surf, (cx, cy))
            cy += 16

            # ── Feature badges (bottom of card) ──────────────────────
            badge_y = card_rect.bottom - 28
            bx = cx
            badges = []
            if getattr(profile, "has_granular", False):
                badges.append("GRAN")
            if getattr(profile, "has_effects", False):
                badges.append("FX")
            if getattr(profile, "has_looper", False):
                badges.append("LOOP")
            if getattr(profile, "has_dj_mode", False):
                badges.append("DJ")

            for label in badges:
                bw = len(label) * 7 + 10
                br = pygame.Rect(bx, badge_y, bw, 16)
                pygame.draw.rect(surface, device_color, br, border_radius=3)
                surf = f_tiny.render(label, True, theme.BG)
                surface.blit(surf, surf.get_rect(center=br.center))
                bx += bw + 4

            # FOCUS tag (top right of card)
            if is_focused:
                tag_rect = pygame.Rect(card_rect.right - 58, cards_y + 10, 48, 16)
                pygame.draw.rect(surface, device_color, tag_rect, border_radius=3)
                surf = f_tiny.render("FOCUS", True, theme.BG)
                surface.blit(surf, surf.get_rect(center=tag_rect.center))

        # ── No devices fallback ──────────────────────────────────────
        if not devices:
            surf = f_large.render("No devices connected", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=160))
            surf = f_small.render("Plug in a USB device to get started", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=200))

        # ── Bottom info strip ────────────────────────────────────────
        bpm = self.app.p6.state.bpm if self.app.p6 else 120.0
        content_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50
        col_y = content_bottom

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
