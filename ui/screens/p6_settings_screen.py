"""P-6 Settings Screen — configurable options with toggles and adjustments."""

import os
import subprocess
import pygame
from .. import theme

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class P6SettingsScreen:
    """Scrollable settings list with toggles, adjustments, and action buttons."""

    def __init__(self, app):
        self.app = app
        self._scroll_y = 0
        self._row_height = 36
        self._rows = []  # rebuilt each frame
        self._content_height = 0

    def on_enter(self):
        self._scroll_y = 0

    def on_exit(self):
        pass

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_threshold(self) -> int:
        """Get recording threshold level from config."""
        return int(self.app.config.get("REC_THRESHOLD", "30"))

    def _set_threshold(self, val: int):
        from ui.p6_app import save_config_key
        val = max(0, min(100, val))
        self.app.config["REC_THRESHOLD"] = str(val)
        save_config_key("REC_THRESHOLD", str(val))

    def _get_audio_device(self) -> str:
        return self.app.config.get("AUDIO_DEVICE_HINT", "default")

    def _get_resolution(self) -> str:
        return f"{theme.SCREEN_WIDTH}x{theme.SCREEN_HEIGHT}"

    def _get_p6_status(self) -> str:
        if self.app.p6 and self.app.p6.connected:
            return "Connected"
        return "Not found"

    def _toggle_mouse_mode(self):
        from ui.p6_app import save_config_key
        self.app.mouse_mode = not self.app.mouse_mode
        pygame.mouse.set_visible(self.app.mouse_mode)
        save_config_key("MOUSE_MODE", "1" if self.app.mouse_mode else "0")

    def _toggle_auto_record(self):
        from ui.p6_app import save_config_key
        self.app.auto_record = not self.app.auto_record
        save_config_key("P6_AUTO_RECORD", "1" if self.app.auto_record else "0")

    def _toggle_splash(self):
        from ui.p6_app import save_config_key
        current = self.app.config.get("SKIP_SPLASH", "0")
        new_val = "0" if current == "1" else "1"
        self.app.config["SKIP_SPLASH"] = new_val
        save_config_key("SKIP_SPLASH", new_val)

    def _run_calibrate(self):
        """Launch ts_calibrate with TSLIB environment variables."""
        env = os.environ.copy()
        env["TSLIB_TSDEVICE"] = env.get("TSLIB_TSDEVICE", "/dev/input/touchscreen")
        env["TSLIB_FBDEVICE"] = env.get("TSLIB_FBDEVICE", "/dev/fb0")
        env["TSLIB_CALIBFILE"] = "/etc/pointercal"
        try:
            subprocess.Popen(["sudo", "ts_calibrate"], env=env)
            print("Touch calibration started", flush=True)
        except Exception as e:
            print(f"Failed to start ts_calibrate: {e}", flush=True)

    # ── Settings row definitions ────────────────────────────────────

    def _build_rows(self):
        """Build the list of setting rows with current values."""
        threshold = self._get_threshold()
        splash_off = self.app.config.get("SKIP_SPLASH", "0") == "1"

        self._rows = [
            {"label": "Mouse Mode", "type": "toggle", "value": self.app.mouse_mode,
             "action": self._toggle_mouse_mode},
            {"label": "Auto-Record", "type": "toggle", "value": self.app.auto_record,
             "action": self._toggle_auto_record},
            {"label": "Threshold Level", "type": "adjust", "value": threshold,
             "action_dec": lambda: self._set_threshold(self._get_threshold() - 5),
             "action_inc": lambda: self._set_threshold(self._get_threshold() + 5)},
            {"label": "Splash Screen", "type": "toggle", "value": not splash_off,
             "action": self._toggle_splash},
            {"label": "", "type": "section", "value": "CONNECTED DEVICES"},
        ]

        # Dynamic device rows
        connected = self.app.device_manager.connected
        focus_key = self.app.device_manager.focus_key
        for short_name, profile in connected.items():
            midi_conn = self.app._midi_connections.get(short_name)
            is_focused = (short_name == focus_key)
            midi_status = "MIDI OK" if (midi_conn and midi_conn.connected) else "No MIDI"
            audio_info = f"{profile.audio_in_channels}in/{profile.audio_out_channels}out"
            rates = "/".join(str(r // 1000) + "k" for r in profile.supported_sample_rates)
            status = f"{midi_status} | {audio_info} {rates}"

            if is_focused:
                self._rows.append({
                    "label": f"► {profile.name}", "type": "info",
                    "value": f"FOCUSED  {status}",
                })
            else:
                self._rows.append({
                    "label": f"  {profile.name}", "type": "button",
                    "btn_label": "FOCUS",
                    "action": lambda sn=short_name: self.app.switch_focus(sn),
                    "value": status,
                })

        if not connected:
            self._rows.append({"label": "  No devices", "type": "info", "value": "—"})

        # Audio and display info
        self._rows.extend([
            {"label": "", "type": "section", "value": "AUDIO & DISPLAY"},
            {"label": "Audio Input", "type": "info", "value": self._get_audio_device()},
            {"label": "Display", "type": "info", "value": self._get_resolution()},
            {"label": "Touch Calibration", "type": "button", "btn_label": "CALIBRATE",
             "action": self._run_calibrate},
            {"label": "", "type": "section", "value": "ABOUT"},
            {"label": "Version", "type": "info",
             "value": "Compa v1.0 by RARE DATA / raredata.net"},
        ])
        self._content_height = len(self._rows) * self._row_height + 80

    # ── Event handling ──────────────────────────────────────────────

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.app.switch_screen("session")
                return
            elif event.key == pygame.K_UP:
                self._scroll_y = max(0, self._scroll_y - 40)
                return
            elif event.key == pygame.K_DOWN:
                self._scroll_y += 40
                return

        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos if hasattr(event, "pos") else (0, 0)

            # Scroll via mouse wheel
            if event.button == 4:
                self._scroll_y = max(0, self._scroll_y - 30)
                return
            elif event.button == 5:
                self._scroll_y += 30
                return

            if event.button != 1:
                return

            # HELP button (top-right)
            help_rect = pygame.Rect(theme.SCREEN_WIDTH - 80, 6, 70, 28)
            if help_rect.collidepoint(mx, my):
                self.app.switch_screen("help")
                return

            # Check row interactions
            content_y = 46
            row_h = self._row_height
            self._build_rows()

            for i, row in enumerate(self._rows):
                ry = content_y + i * row_h - self._scroll_y
                if ry < content_y - row_h or ry > theme.SCREEN_HEIGHT - theme.NAV_HEIGHT:
                    continue

                row_rect = pygame.Rect(0, ry, theme.SCREEN_WIDTH, row_h)
                if not row_rect.collidepoint(mx, my):
                    continue

                rtype = row["type"]
                ctrl_x = theme.SCREEN_WIDTH - 160

                if rtype == "toggle":
                    # Toggle button area (right side)
                    toggle_rect = pygame.Rect(ctrl_x, ry + 4, 60, row_h - 8)
                    if toggle_rect.collidepoint(mx, my):
                        row["action"]()
                    return

                elif rtype == "adjust":
                    # [-] button
                    dec_rect = pygame.Rect(ctrl_x, ry + 4, 32, row_h - 8)
                    # [+] button
                    inc_rect = pygame.Rect(ctrl_x + 80, ry + 4, 32, row_h - 8)
                    if dec_rect.collidepoint(mx, my):
                        row["action_dec"]()
                    elif inc_rect.collidepoint(mx, my):
                        row["action_inc"]()
                    return

                elif rtype == "button":
                    btn_rect = pygame.Rect(ctrl_x, ry + 4, 90, row_h - 8)
                    if btn_rect.collidepoint(mx, my):
                        row["action"]()
                    return

    def update(self):
        pass

    # ── Drawing ─────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # Header
        y_after = theme.draw_screen_header(surface, "SETTINGS", "")

        # HELP button (top-right)
        help_rect = pygame.Rect(theme.SCREEN_WIDTH - 80, 6, 70, 28)
        pygame.draw.rect(surface, theme.BUTTON_BG, help_rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, help_rect, 1, border_radius=6)
        surf = f_small.render("HELP", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=help_rect.center))

        # Build current row data
        self._build_rows()

        # Content area with clipping
        content_y = 46
        content_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - content_y
        content_rect = pygame.Rect(0, content_y, theme.SCREEN_WIDTH, content_h)
        clip = surface.get_clip()
        surface.set_clip(content_rect)

        row_h = self._row_height
        ctrl_x = theme.SCREEN_WIDTH - 160

        for i, row in enumerate(self._rows):
            ry = content_y + i * row_h - self._scroll_y
            if ry + row_h < content_y or ry > content_rect.bottom:
                continue

            # Alternating row backgrounds
            if i % 2 == 0:
                pygame.draw.rect(surface, theme.BG_PANEL,
                                 (8, ry, theme.SCREEN_WIDTH - 16, row_h),
                                 border_radius=4)
            else:
                pygame.draw.rect(surface, theme.BG,
                                 (8, ry, theme.SCREEN_WIDTH - 16, row_h))

            rtype = row["type"]

            # Section headers
            if rtype == "section":
                pygame.draw.line(surface, theme.BORDER, (20, ry + row_h // 2),
                                (theme.SCREEN_WIDTH - 20, ry + row_h // 2))
                sect_surf = f_tiny.render(row["value"], True, theme.ACCENT)
                bg_rect = sect_surf.get_rect(left=30, centery=ry + row_h // 2)
                bg_rect.inflate_ip(12, 4)
                pygame.draw.rect(surface, theme.BG, bg_rect)
                surface.blit(sect_surf, (30, ry + (row_h - sect_surf.get_height()) // 2))
                continue

            # Label
            label_surf = f_med.render(row["label"], True, theme.TEXT)
            surface.blit(label_surf, (20, ry + (row_h - label_surf.get_height()) // 2))

            if rtype == "toggle":
                # ON/OFF toggle button
                is_on = row["value"]
                toggle_rect = pygame.Rect(ctrl_x, ry + 4, 60, row_h - 8)
                if is_on:
                    pygame.draw.rect(surface, theme.GREEN, toggle_rect, border_radius=6)
                    lbl = f_small.render("ON", True, theme.BG)
                else:
                    pygame.draw.rect(surface, theme.BG_LIGHTER, toggle_rect, border_radius=6)
                    pygame.draw.rect(surface, theme.BORDER, toggle_rect, 1, border_radius=6)
                    lbl = f_small.render("OFF", True, theme.TEXT_DIM)
                surface.blit(lbl, lbl.get_rect(center=toggle_rect.center))

            elif rtype == "adjust":
                # [-] [value] [+]
                val = row["value"]
                dec_rect = pygame.Rect(ctrl_x, ry + 4, 32, row_h - 8)
                pygame.draw.rect(surface, theme.BUTTON_BG, dec_rect, border_radius=6)
                pygame.draw.rect(surface, theme.BORDER, dec_rect, 1, border_radius=6)
                lbl = f_med.render("-", True, theme.TEXT)
                surface.blit(lbl, lbl.get_rect(center=dec_rect.center))

                # Value display
                val_surf = f_med.render(str(val), True, theme.ACCENT)
                val_x = ctrl_x + 36 + (44 - val_surf.get_width()) // 2
                surface.blit(val_surf, (val_x, ry + (row_h - val_surf.get_height()) // 2))

                inc_rect = pygame.Rect(ctrl_x + 80, ry + 4, 32, row_h - 8)
                pygame.draw.rect(surface, theme.BUTTON_BG, inc_rect, border_radius=6)
                pygame.draw.rect(surface, theme.BORDER, inc_rect, 1, border_radius=6)
                lbl = f_med.render("+", True, theme.TEXT)
                surface.blit(lbl, lbl.get_rect(center=inc_rect.center))

            elif rtype == "button":
                btn_rect = pygame.Rect(ctrl_x, ry + 4, 90, row_h - 8)
                pygame.draw.rect(surface, theme.ACCENT_DIM, btn_rect, border_radius=6)
                lbl = f_small.render(row["btn_label"], True, theme.TEXT_BRIGHT)
                surface.blit(lbl, lbl.get_rect(center=btn_rect.center))

            elif rtype == "info":
                # Display value text on right side
                val_str = str(row["value"])
                val_surf = f_small.render(val_str, True, theme.TEXT_DIM)
                # Right-align within the control area
                vx = theme.SCREEN_WIDTH - 24 - val_surf.get_width()
                surface.blit(val_surf, (vx, ry + (row_h - val_surf.get_height()) // 2))

        surface.set_clip(clip)

        # Scrollbar
        total_h = len(self._rows) * row_h
        if total_h > content_h:
            bar_x = theme.SCREEN_WIDTH - 5
            thumb_h = max(20, int(content_h * content_h / total_h))
            thumb_y = content_y + int(
                (content_h - thumb_h) * self._scroll_y / max(1, total_h - content_h))
            thumb_y = max(content_y, min(thumb_y, content_y + content_h - thumb_h))
            pygame.draw.rect(surface, theme.BORDER,
                             (bar_x, content_y, 3, content_h), border_radius=1)
            pygame.draw.rect(surface, theme.ACCENT,
                             (bar_x, thumb_y, 3, thumb_h), border_radius=1)
