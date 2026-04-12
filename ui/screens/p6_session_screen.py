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
        # Start audio monitoring so meters work on this screen
        if not self.app.recorder._monitoring:
            # If recorder doesn't have a valid device, try the focused device
            if not self.app.recorder.available:
                dev = self.app.device
                if dev and dev.audio_hint:
                    self.app.recorder.switch_device(dev.audio_hint)
            self.app.recorder.start_monitoring()

    def on_exit(self):
        self._save_notes()
        # Stop monitoring if not recording (same as Record screen)
        if not self.app.recorder.is_recording:
            self.app.recorder.stop_monitoring()

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

            # Card transport buttons (priority over card tap)
            if hasattr(self, "_card_buttons"):
                for rect, dev_name, action in self._card_buttons:
                    if rect.collidepoint(mx, my):
                        self._handle_card_button(dev_name, action)
                        return

            # Tap card background → switch focus + start monitoring
            if hasattr(self, "_card_rects"):
                for rect, dev_name in self._card_rects:
                    if rect.collidepoint(mx, my):
                        self.app.switch_focus(dev_name)
                        # Switch recorder to this device and start monitoring
                        dev = self.app.device_manager.connected.get(dev_name)
                        if dev and dev.audio_hint:
                            self.app.recorder.stop_monitoring()
                            self.app.recorder.switch_device(dev.audio_hint)
                            import numpy as np
                            self.app.recorder._recall_buf[:] = 0
                            self.app.recorder._recall_write_pos = 0
                            self.app.recorder._recall_total_written = 0
                            self.app.recorder.start_monitoring()
                            self.app.route_monitor(dev_name)
                        elif not self.app.recorder._monitoring:
                            # No audio hint but try to start monitoring anyway
                            self.app.recorder.start_monitoring()
                        return

    def _handle_card_button(self, dev_name: str, action: str):
        """Handle transport button taps on device cards."""
        if action == "set_monitor":
            if self.app.monitor_output == dev_name:
                # Toggle off
                self.app.monitor_output = ""
                if self.app._monitor_route and self.app._monitor_route.is_active:
                    self.app._monitor_route.stop()
                print(f"Monitor output: OFF", flush=True)
            else:
                self.app.set_monitor_output(dev_name)
                # Re-route current focus through new monitor
                focus = self.app.device_manager.focus_key
                if focus and focus != dev_name:
                    self.app.route_monitor(focus)
            return

        midi = self.app._midi_connections.get(dev_name)
        if not midi:
            return
        if action == "play":
            midi.send_start()
        elif action == "stop":
            midi.send_stop()
            if self.app.recorder.is_recording:
                self.app.recorder.stop_recording()
        elif action == "rec":
            if self.app.recorder.is_recording:
                self.app.recorder.stop_recording()
            else:
                # Switch audio source to this device and start recording
                dev = self.app.device_manager.connected.get(dev_name)
                if dev and dev.audio_hint:
                    self.app.recorder.switch_device(dev.audio_hint)
                meta = {"bpm_at_record": midi.state.bpm,
                        "pattern_at_record": midi.state.active_pattern}
                self.app.recorder.start_recording(metadata=meta)

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

        # ── COMPA ASCII logo (top left) ──────────────────────────────
        for i, line in enumerate(self._COMPA_LOGO):
            surf = f_mono.render(line, True, theme.ACCENT)
            surface.blit(surf, (12, 6 + i * 14))

        # Version + subtitle
        surf = f_tiny.render("v1.0", True, theme.TEXT_DIM)
        surface.blit(surf, (240, 10))
        surf = f_tiny.render("raredata.net", True, theme.TEXT_DIM)
        surface.blit(surf, (240, 24))

        # Reset card rects each frame
        self._card_buttons = []
        self._card_rects = []  # (rect, short_name) for tap-to-focus

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

            # Store rect for tap-to-focus
            self._card_rects.append((card_rect, short_name))

            # Card background + border
            pygame.draw.rect(surface, theme.BG_PANEL, card_rect, border_radius=10)
            border_color = device_color if is_focused else theme.BORDER
            border_w = 2 if is_focused else 1
            pygame.draw.rect(surface, border_color, card_rect, border_w, border_radius=10)

            # Top accent stripe
            stripe = pygame.Rect(card_x + 1, cards_y + 1, card_w - 2, 4)
            pygame.draw.rect(surface, device_color, stripe,
                           border_radius=0)

            cx = card_x + 10
            cy = cards_y + 10
            inner_w = card_w - 20

            # ── Row 1: Device name + FOCUS tag ───────────────────────
            surf = f_large.render(short_name, True, device_color)
            surface.blit(surf, (cx, cy))
            if is_focused:
                tag_x = card_rect.right - 56
                tag_rect = pygame.Rect(tag_x, cy + 2, 46, 16)
                pygame.draw.rect(surface, device_color, tag_rect, border_radius=3)
                surf2 = f_tiny.render("FOCUS", True, theme.BG)
                surface.blit(surf2, surf2.get_rect(center=tag_rect.center))
            cy += 24

            # ── Row 2: Connection + audio specs + headphone button ────
            is_monitor_out = (short_name == self.app.monitor_output)
            if is_connected:
                pygame.draw.circle(surface, theme.GREEN, (cx + 4, cy + 5), 3)
            else:
                pygame.draw.circle(surface, theme.RED, (cx + 4, cy + 5), 3, 1)
            audio_info = f"{profile.audio_in_channels}in/{profile.audio_out_channels}out"
            rates = "/".join(f"{r//1000}k" for r in profile.supported_sample_rates)
            surf = f_tiny.render(f"{audio_info} {rates}", True, theme.TEXT_DIM)
            surface.blit(surf, (cx + 12, cy))

            # Headphone/monitor output button (right side of row 2)
            # Note: audio pass-through works best on Pi 4/5
            hp_rect = pygame.Rect(card_rect.right - 62, cy - 2, 52, 16)
            if is_monitor_out:
                pygame.draw.rect(surface, device_color, hp_rect, border_radius=3)
                surf = f_tiny.render("OUT *", True, theme.BG)
            else:
                pygame.draw.rect(surface, theme.BUTTON_BG, hp_rect, border_radius=3)
                pygame.draw.rect(surface, theme.BORDER, hp_rect, 1, border_radius=3)
                surf = f_tiny.render("OUT *", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=hp_rect.center))
            self._card_buttons.append((hp_rect, short_name, "set_monitor"))

            cy += 14

            # ── Row 3: Audio level meters (only for monitored device) ──
            meter_w = inner_w - 4
            meter_h = 6
            # Check if this device is the one being monitored
            rec_hint = self.app.recorder._device_hint
            # Check if this device is being monitored
            # Compare the recorder's actual device name against the profile
            rec_dev_name = self.app.recorder.device_name
            is_monitored = (self.app.recorder._monitoring and
                           rec_dev_name and profile.audio_hint and
                           (profile.audio_hint in rec_dev_name or
                            rec_dev_name in profile.audio_hint or
                            profile.short_name in rec_dev_name))
            peak_l = self._disp_peak_l if is_monitored else 0.0
            peak_r = self._disp_peak_r if is_monitored else 0.0

            # L meter
            pygame.draw.rect(surface, theme.WAVEFORM_BG,
                            (cx, cy, meter_w, meter_h), border_radius=2)
            fill = int(meter_w * min(1.0, peak_l))
            if fill > 0:
                mc = theme.RED if peak_l > 0.9 else (
                    theme.YELLOW if peak_l > 0.7 else device_color)
                pygame.draw.rect(surface, mc,
                                (cx, cy, fill, meter_h), border_radius=2)
            if is_monitored:
                surf = f_tiny.render("L", True, theme.TEXT_DIM)
                surface.blit(surf, (cx + meter_w + 2, cy - 1))
            cy += meter_h + 2
            # R meter
            pygame.draw.rect(surface, theme.WAVEFORM_BG,
                            (cx, cy, meter_w, meter_h), border_radius=2)
            fill = int(meter_w * min(1.0, peak_r))
            if fill > 0:
                mc = theme.RED if peak_r > 0.9 else (
                    theme.YELLOW if peak_r > 0.7 else device_color)
                pygame.draw.rect(surface, mc,
                                (cx, cy, fill, meter_h), border_radius=2)
            if is_monitored:
                surf = f_tiny.render("R", True, theme.TEXT_DIM)
                surface.blit(surf, (cx + meter_w + 2, cy - 1))
            elif is_connected:
                # Show "tap to monitor" hint on inactive cards
                surf = f_tiny.render("tap card to monitor", True, theme.TEXT_DIM)
                surface.blit(surf, (cx, cy - meter_h))
            cy += meter_h + 6

            # ── Row 4: BPM + Pattern ─────────────────────────────────
            if midi:
                bpm = midi.state.bpm
                surf = f_hero.render(f"{bpm:.0f}", True, device_color)
                surface.blit(surf, (cx, cy - 4))
                bpm_w = surf.get_width()
                surf = f_tiny.render("BPM", True, theme.TEXT_DIM)
                surface.blit(surf, (cx + bpm_w + 2, cy + 12))

                pat = midi.state.active_pattern + 1
                pat_max = getattr(profile, "pattern_count", 0)
                if pat_max > 0:
                    pat_text = f"Ptn {pat}/{pat_max}"
                    surf = f_small.render(pat_text, True, device_color)
                    surface.blit(surf, (card_rect.right - surf.get_width() - 14, cy + 6))
            else:
                surf = f_large.render("---", True, theme.TEXT_DIM)
                surface.blit(surf, (cx, cy))
            cy += 38

            # ── Row 5: Transport buttons ─────────────────────────────
            if midi:
                btn_w = min(50, (inner_w - 12) // 3)
                btn_h = 24

                # Play/Stop state indicator
                if midi.state.playing:
                    pygame.draw.circle(surface, theme.GREEN, (cx + 5, cy + btn_h // 2), 4)
                else:
                    pygame.draw.circle(surface, theme.TEXT_DIM, (cx + 5, cy + btn_h // 2), 3, 1)

                # Transport buttons
                bx = cx + 16
                play_rect = pygame.Rect(bx, cy, btn_w, btn_h)
                play_bg = theme.GREEN if midi.state.playing else theme.BUTTON_BG
                pygame.draw.rect(surface, play_bg, play_rect, border_radius=4)
                surf = f_tiny.render("PLAY", True, theme.BG if midi.state.playing else theme.TEXT)
                surface.blit(surf, surf.get_rect(center=play_rect.center))

                rec_rect = pygame.Rect(bx + btn_w + 4, cy, btn_w, btn_h)
                is_rec = self.app.recorder.is_recording
                rec_bg = theme.RED if is_rec else theme.BUTTON_BG
                pygame.draw.rect(surface, rec_bg, rec_rect, border_radius=4)
                surf = f_tiny.render("REC", True, theme.TEXT_BRIGHT if is_rec else theme.TEXT)
                surface.blit(surf, surf.get_rect(center=rec_rect.center))

                stop_rect = pygame.Rect(bx + 2 * (btn_w + 4), cy, btn_w, btn_h)
                pygame.draw.rect(surface, theme.BUTTON_BG, stop_rect, border_radius=4)
                surf = f_tiny.render("STOP", True, theme.TEXT)
                surface.blit(surf, surf.get_rect(center=stop_rect.center))

                # Store button rects for click handling
                self._card_buttons.append((play_rect, short_name, "play"))
                self._card_buttons.append((rec_rect, short_name, "rec"))
                self._card_buttons.append((stop_rect, short_name, "stop"))

            cy += 30

            # ── Row 6: Live waveform (monitored card) or static info ──
            wave_h = max(20, card_rect.bottom - cy - 26)
            wave_rect = pygame.Rect(cx, cy, inner_w, wave_h)
            pygame.draw.rect(surface, (15, 15, 22), wave_rect, border_radius=3)

            if is_monitored:
                # Oscilloscope — smooth line showing current audio shape
                import numpy as np
                peak_l, peak_r = self.app.recorder.peak_levels

                # Use the most recent audio from the recall buffer
                rec = self.app.recorder
                buf = rec._recall_buf
                wpos = rec._recall_write_pos
                display_frames = min(2048, len(buf))

                # Read the most recent chunk from the circular buffer
                if wpos >= display_frames:
                    recent = buf[wpos - display_frames:wpos]
                else:
                    recent = np.concatenate([buf[-(display_frames - wpos):], buf[:wpos]])

                if len(recent) > 0 and float(np.max(np.abs(recent))) > 0.001:
                    # Mono mix
                    if recent.ndim > 1:
                        mono = recent.mean(axis=1)
                    else:
                        mono = recent

                    # Downsample to display width
                    w = wave_rect.width - 4
                    step = max(1, len(mono) // w)
                    points = []
                    center_y = wave_rect.centery
                    half_h = (wave_rect.height - 8) // 2

                    for px in range(w):
                        si = px * step
                        if si < len(mono):
                            val = float(mono[si])
                            # Clamp and scale
                            val = max(-1.0, min(1.0, val * 3.0))  # Boost for visibility
                            py = center_y - int(val * half_h)
                            points.append((wave_rect.x + 2 + px, py))

                    if len(points) > 1:
                        pygame.draw.lines(surface, device_color, False, points, 2)
                else:
                    # Silent — flat center line
                    pygame.draw.line(surface, theme.BORDER,
                                   (wave_rect.x + 2, wave_rect.centery),
                                   (wave_rect.right - 2, wave_rect.centery))

                # Center line (faint, behind the waveform)
                pygame.draw.line(surface, (30, 30, 40),
                                (wave_rect.x + 2, wave_rect.centery),
                                (wave_rect.right - 2, wave_rect.centery))

                # Label
                if self.app.recorder.is_recording:
                    dur = self.app.recorder.duration
                    surf = f_tiny.render(f"REC {dur:.0f}s", True, theme.RED)
                else:
                    recall = self.app.recorder.recall_seconds_available
                    surf = f_tiny.render(f"LIVE  buf:{int(recall)}s", True, device_color)
                surface.blit(surf, (cx + 2, cy + 1))
            else:
                # Not monitored — show device info or "tap to monitor"
                pygame.draw.line(surface, theme.BORDER,
                                (wave_rect.x + 2, wave_rect.centery),
                                (wave_rect.right - 2, wave_rect.centery))
                surf = f_tiny.render("tap card to monitor", True, theme.TEXT_DIM)
                surface.blit(surf, surf.get_rect(center=wave_rect.center))

            cy += wave_h + 4

            # ── Feature badges (bottom of card) ──────────────────────
            badge_y = card_rect.bottom - 20
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
                bw = len(label) * 7 + 8
                br = pygame.Rect(bx, badge_y, bw, 14)
                pygame.draw.rect(surface, device_color, br, border_radius=3)
                surf = f_tiny.render(label, True, theme.BG)
                surface.blit(surf, surf.get_rect(center=br.center))
                bx += bw + 3

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
