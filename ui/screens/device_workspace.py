"""Device Workspace — full-screen per-device control workspace.

Reached by tapping a card on the session screen. Shows device-specific
tabs (Control, Looper, DJ, Pattern, Record) with hardware-matching
layouts. Back button returns to session.

SP-404 Control layout matches the hardware:
- FX Select dropdown + On/Off toggle at top
- 3x2 knob grid matching physical knob positions
- Bus selector (1-4 + Input) at bottom
"""

import os
import time
import pygame
from .. import theme
from ..components.knob import Knob

import logging
log = logging.getLogger(__name__)


class DeviceWorkspaceScreen:
    """Full-screen workspace for a single device."""

    def __init__(self, app):
        self.app = app
        self._device_key = ""
        self._device_profile = None
        self._device_color = theme.ACCENT
        self._tabs: list[tuple[str, str]] = []  # (key, label)
        self._current_tab = 0

        # SP-404 control state
        self._active_bus = 0  # 0=Bus1, 1=Bus2, 2=Bus3, 3=Bus4, 4=InputFX
        self._bus_knobs: list[tuple[Knob, int]] = []  # (knob, cc)
        self._fx_on = False
        self._fx_select_val = 0

    def on_enter(self):
        ctx = getattr(self.app, "_screen_context", {})
        dev_key = ctx.get("device", self.app.device_name)
        self.app._screen_context = {}

        # Switch focus to this device
        if dev_key != self.app.device_name:
            self.app.switch_focus(dev_key)

        self._device_key = dev_key
        self._device_profile = self.app.device
        self._device_color = {
            "P-6": (255, 230, 0),
            "SP-404": (0, 200, 180),
            "Force": (220, 50, 50),
        }.get(dev_key, theme.ACCENT)

        # Build tabs based on device
        self._build_tabs()
        self._current_tab = 0

        # Start monitoring
        if not self.app.recorder._monitoring:
            dev = self.app.device
            if dev and dev.audio_hint:
                self.app.recorder.switch_device(dev.audio_hint)
            self.app.recorder.start_monitoring()

        # Build control knobs for current bus
        self._build_bus_knobs()

    def on_exit(self):
        if not self.app.recorder.is_recording:
            self.app.recorder.stop_monitoring()

    def _build_tabs(self):
        """Build device-specific tabs."""
        key = self._device_key
        if key == "SP-404":
            self._tabs = [
                ("control", "CONTROL"),
                ("looper", "LOOPER"),
                ("dj", "DJ MODE"),
            ]
        elif key == "P-6":
            self._tabs = [
                ("control", "CONTROL"),
                ("pattern", "PATTERN"),
            ]
        elif key == "Force":
            self._tabs = [
                ("transfer", "TRANSFER"),
            ]
        else:
            self._tabs = [("control", "CONTROL")]

    def _build_bus_knobs(self):
        """Build knobs for the active bus (SP-404 control tab)."""
        self._bus_knobs = []
        if self._device_key != "SP-404":
            return

        from engine.sp404_effects import fx_name_for_tab, fx_count_for_tab

        bus_tab_keys = ["bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"]
        tab_key = bus_tab_keys[self._active_bus] if self._active_bus < len(bus_tab_keys) else "bus1_fx"

        # CC map from device profile
        dev = self._device_profile
        if not dev or not dev.cc_map:
            return

        from engine.device_profiles import cc_map_to_legacy
        cc_map = cc_map_to_legacy(dev.cc_map)
        params = cc_map.get(tab_key, [])

        # Build 3x2 knob layout matching SP-404 hardware
        # Row 1: Ctrl 1, 2, 3 (top 3 knobs)
        # Row 2: Ctrl 4, 5, 6 (bottom 3 knobs)
        knob_r = 34
        start_x = 80
        start_y = 200
        col_gap = 160
        row_gap = 130

        for idx, (cc, name, lo, hi, default) in enumerate(params):
            if cc == 19:  # FX On/Off — handled as toggle, skip knob
                continue
            if cc == 83:  # FX Select — handled as selector, skip knob
                continue

            # Map remaining params to 3x2 grid
            knob_idx = len(self._bus_knobs)
            if knob_idx >= 6:
                break
            row = knob_idx // 3
            col = knob_idx % 3
            cx = start_x + col * col_gap
            cy = start_y + row * row_gap

            knob = Knob(
                center=(cx, cy),
                radius=knob_r,
                min_val=float(lo),
                max_val=float(hi),
                value=float(default),
                label=name,
                int_mode=True,
                format_func=lambda v: f"{int(v)}",
            )
            self._bus_knobs.append((knob, cc))

    # ── Event Handling ───────────────────────────────────────────────

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Back button
            back_rect = pygame.Rect(12, 6, 70, 30)
            if back_rect.collidepoint(mx, my):
                self.app.switch_screen("session")
                return

            # Tab buttons
            for i, (key, label) in enumerate(self._tabs):
                rect = self._tab_rect(i)
                if rect.collidepoint(mx, my):
                    self._current_tab = i
                    if key == "control":
                        self._build_bus_knobs()
                    return

            # Tab-specific handling
            tab_key = self._tabs[self._current_tab][0] if self._tabs else ""

            if tab_key == "control" and self._device_key == "SP-404":
                self._handle_sp404_control(mx, my, event)
                return

        # Pass all events to knobs for drag handling
        if self._tabs and self._tabs[self._current_tab][0] == "control":
            self._handle_knob_events(event)

    def _handle_sp404_control(self, mx, my, event):
        """Handle clicks in SP-404 control view."""
        from engine.sp404_effects import fx_count_for_tab

        # Bus selector buttons
        bus_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50
        for i in range(5):
            rect = pygame.Rect(60 + i * 90, bus_y, 80, 30)
            if rect.collidepoint(mx, my):
                self._active_bus = i
                self._build_bus_knobs()
                return

        # FX On/Off toggle
        toggle_rect = pygame.Rect(theme.SCREEN_WIDTH - 200, 80, 120, 40)
        if toggle_rect.collidepoint(mx, my):
            self._fx_on = not self._fx_on
            val = 127 if self._fx_on else 0
            bus_channels = [0, 1, 2, 3, 4]
            ch = bus_channels[self._active_bus]
            if self.app.p6:
                self.app.p6.send_cc(19, val, channel=ch)
            return

        # FX Select — tap to cycle
        sel_rect = pygame.Rect(theme.SCREEN_WIDTH - 200, 130, 120, 30)
        if sel_rect.collidepoint(mx, my):
            bus_tab_keys = ["bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"]
            tab_key = bus_tab_keys[self._active_bus]
            max_fx = fx_count_for_tab(tab_key) - 1
            self._fx_select_val = (self._fx_select_val + 1) % (max_fx + 1)
            ch = [0, 1, 2, 3, 4][self._active_bus]
            if self.app.p6:
                self.app.p6.send_cc(83, self._fx_select_val, channel=ch)
            return

    def _handle_knob_events(self, event):
        """Pass events to bus knobs."""
        for knob, cc in self._bus_knobs:
            if knob.handle_event(event):
                bus_channels = [0, 1, 2, 3, 4]
                ch = bus_channels[self._active_bus]
                if self.app.p6:
                    self.app.p6.send_cc(cc, int(knob.value), channel=ch)

    def _tab_rect(self, idx: int) -> pygame.Rect:
        n = len(self._tabs)
        tab_w = min(140, (theme.SCREEN_WIDTH - 120) // max(1, n))
        return pygame.Rect(90 + idx * (tab_w + 4), 6, tab_w, 30)

    def update(self):
        pass

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_title = theme.font("title")
        f_large = theme.font("large")
        f_hero = theme.font("hero")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # ── Header: Back + Device Name + Tabs ────────────────────────
        # Back button
        back_rect = pygame.Rect(12, 6, 70, 30)
        pygame.draw.rect(surface, theme.BUTTON_BG, back_rect, border_radius=6)
        surf = f_small.render("< BACK", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=back_rect.center))

        # Tabs
        for i, (key, label) in enumerate(self._tabs):
            rect = self._tab_rect(i)
            active = i == self._current_tab
            bg = self._device_color if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, rect, border_radius=6)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Device name + status (right side)
        midi = self.app._midi_connections.get(self._device_key)
        is_connected = midi and midi.connected
        conn_color = theme.GREEN if is_connected else theme.RED
        pygame.draw.circle(surface, conn_color,
                          (theme.SCREEN_WIDTH - 120, 20), 5)
        surf = f_large.render(self._device_key, True, self._device_color)
        surface.blit(surf, (theme.SCREEN_WIDTH - 110, 6))

        # Accent line below header
        pygame.draw.line(surface, self._device_color,
                        (0, 40), (theme.SCREEN_WIDTH, 40), 2)

        # ── Tab Content ──────────────────────────────────────────────
        tab_key = self._tabs[self._current_tab][0] if self._tabs else ""

        if tab_key == "control":
            if self._device_key == "SP-404":
                self._draw_sp404_control(surface, f_hero, f_large, f_med, f_small, f_tiny)
            else:
                self._draw_generic_control(surface, f_med, f_small)
        elif tab_key == "looper":
            self._draw_looper(surface, f_large, f_med, f_small)
        elif tab_key == "dj":
            self._draw_dj(surface, f_large, f_med, f_small)
        else:
            surf = f_med.render(f"Tab: {tab_key}", True, theme.TEXT_DIM)
            surface.blit(surf, (40, 100))

    def _draw_sp404_control(self, surface, f_hero, f_large, f_med, f_small, f_tiny):
        """Draw SP-404 control layout matching hardware."""
        from engine.sp404_effects import fx_name_for_tab, fx_count_for_tab

        bus_tab_keys = ["bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"]
        tab_key = bus_tab_keys[self._active_bus]

        # ── BPM display (top left) ───────────────────────────────────
        midi = self.app._midi_connections.get(self._device_key)
        if midi:
            bpm = midi.state.bpm
            surf = f_hero.render(f"{bpm:.0f}", True, self._device_color)
            surface.blit(surf, (20, 50))
            surf = f_small.render("BPM", True, theme.TEXT_DIM)
            surface.blit(surf, (20 + surf.get_width() + 60, 74))

            # Transport state
            if midi.state.playing:
                surf = f_small.render("PLAYING", True, theme.GREEN)
            else:
                surf = f_small.render("STOPPED", True, theme.TEXT_DIM)
            surface.blit(surf, (20, 90))

            # Pattern
            pat = midi.state.active_pattern + 1
            pat_max = getattr(self._device_profile, "pattern_count", 0)
            if pat_max > 0:
                surf = f_small.render(f"Ptn {pat}/{pat_max}", True, self._device_color)
                surface.blit(surf, (20, 108))

        # ── FX Select + On/Off (top right) ───────────────────────────
        fx_name = fx_name_for_tab(tab_key, self._fx_select_val)

        # On/Off toggle
        toggle_rect = pygame.Rect(theme.SCREEN_WIDTH - 200, 56, 120, 40)
        toggle_bg = theme.GREEN if self._fx_on else theme.BUTTON_BG
        toggle_tc = theme.BG if self._fx_on else theme.TEXT_DIM
        pygame.draw.rect(surface, toggle_bg, toggle_rect, border_radius=8)
        surf = f_med.render("FX ON" if self._fx_on else "FX OFF", True, toggle_tc)
        surface.blit(surf, surf.get_rect(center=toggle_rect.center))

        # FX Select
        sel_rect = pygame.Rect(theme.SCREEN_WIDTH - 200, 102, 120, 28)
        pygame.draw.rect(surface, theme.ACCENT_DIM, sel_rect, border_radius=4)
        pygame.draw.rect(surface, self._device_color, sel_rect, 1, border_radius=4)
        surf = f_small.render(fx_name[:14], True, self._device_color)
        surface.blit(surf, surf.get_rect(center=sel_rect.center))

        # FX number
        surf = f_tiny.render(f"#{self._fx_select_val}", True, theme.TEXT_DIM)
        surface.blit(surf, (theme.SCREEN_WIDTH - 200, 134))

        # ── 3x2 Knob Grid (center, matching hardware) ────────────────
        for knob, cc in self._bus_knobs:
            knob.draw(surface)

        if not self._bus_knobs:
            surf = f_med.render("No FX parameters", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=200))

        # ── Bus Selector (bottom) ────────────────────────────────────
        bus_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50
        bus_labels = ["BUS 1", "BUS 2", "BUS 3", "BUS 4", "INPUT"]
        bus_channels = [1, 2, 3, 4, 5]  # Display channels

        # Signal flow
        surf = f_tiny.render("Signal: B1+B2 (parallel) > B3 > B4/Master > OUT",
                            True, theme.TEXT_DIM)
        surface.blit(surf, (60, bus_y - 16))

        for i in range(5):
            rect = pygame.Rect(60 + i * 90, bus_y, 80, 30)
            active = i == self._active_bus
            bg = self._device_color if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, rect, border_radius=6)
            surf = f_small.render(f"Ch{bus_channels[i]} {bus_labels[i]}", True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

    def _draw_generic_control(self, surface, f_med, f_small):
        """Generic control view for P-6 and other devices."""
        surf = f_med.render(f"{self._device_key} Control", True, self._device_color)
        surface.blit(surf, (40, 60))
        surf = f_small.render("Use the CONTROL tab in the main nav for full parameter control",
                             True, theme.TEXT_DIM)
        surface.blit(surf, (40, 90))

    def _draw_looper(self, surface, f_large, f_med, f_small):
        """SP-404 Looper controls."""
        surf = f_large.render("LOOPER", True, self._device_color)
        surface.blit(surf, (40, 60))

        # Big looper buttons
        btn_w = 160
        btn_h = 60
        btn_y = 120
        buttons = [
            ("REC", theme.RED, 88, 127),
            ("OVERDUB", theme.YELLOW, 89, 127),
            ("STOP", theme.BUTTON_BG, 85, 127),
            ("DELETE", (120, 40, 40), 87, 127),
            ("UNDO", theme.ACCENT_DIM, 91, 127),
            ("REDO", theme.ACCENT_DIM, 91, 0),
        ]

        for i, (label, bg, cc, val) in enumerate(buttons):
            row = i // 3
            col = i % 3
            x = 40 + col * (btn_w + 16)
            y = btn_y + row * (btn_h + 16)
            rect = pygame.Rect(x, y, btn_w, btn_h)
            pygame.draw.rect(surface, bg, rect, border_radius=10)
            pygame.draw.rect(surface, theme.BORDER, rect, 2, border_radius=10)
            surf = f_large.render(label, True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=rect.center))

    def _draw_dj(self, surface, f_large, f_med, f_small):
        """SP-404 DJ Mode controls."""
        surf = f_large.render("DJ MODE", True, self._device_color)
        surface.blit(surf, (40, 60))
        surf = f_small.render("Put SP-404 in DJ Mode to use these controls",
                             True, theme.TEXT_DIM)
        surface.blit(surf, (40, 90))
