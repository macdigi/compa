"""Device Workspace — full-screen per-device control workspace.

Reached by tapping a card on the session screen. Shows device-specific
tabs (Control, Looper, DJ, Pattern, Record) with hardware-matching
layouts. Back button returns to session.

Layout (top to bottom):
- Header: Back + Tabs + Device name (36px)
- Oscilloscope: Full-width live waveform with BPM/transport overlay (~40% of content area)
- Controls: Device-specific parameter knobs with section tabs
- Bus selector (SP-404 only, bottom strip)
"""

import numpy as np
import pygame
from ui.components.piano_display import PianoDisplay, note_name
from .. import theme
from ..components.knob import Knob

import logging
log = logging.getLogger(__name__)


class DeviceWorkspaceScreen:
    """Full-screen workspace for a single device."""

    HEADER_H = 36

    def __init__(self, app):
        self.app = app
        self._device_key = ""
        self._device_profile = None
        self._device_color = theme.ACCENT
        self._tabs: list[tuple[str, str]] = []  # (key, label)
        self._current_tab = 0

        # SP-404 control state
        self._active_bus = 0
        self._fx_on = False
        self._fx_select_val = 0

        # P-6 control sections
        self._p6_section = 0  # 0=granular, 1=filter, 2=envelope, 3=mixer, 4=fx

        # Knobs shared across devices
        self._knobs: list[tuple[Knob, int, int]] = []  # (knob, cc, midi_channel)

        # Smoothed peak levels
        self._smooth_l = 0.0
        self._smooth_r = 0.0

        # Fullscreen oscilloscope toggle
        self._scope_fullscreen = False

        # Piano display for KEYS tab (built lazily in on_enter)
        self._piano_display: PianoDisplay | None = None
        # Touch-to-play note-off tracking
        self._touch_note: int = -1
        # Latch mode: notes stay on until tapped again
        self._keys_latch = False
        self._latched_notes: set[int] = set()

        # Pad selector for KEYS tab — pick which sound plays chromatically
        self._keys_bank = 0    # 0-indexed bank (A=0, B=1, ...)
        self._keys_pad = 0     # 0-indexed pad within bank
        self._keys_selected_name = ""  # display label for the selected pad

        # SP-404 chromatic workflow state
        self._sp404_chromatic_ready = False  # True after user does SHIFT+CHROMATIC

        # Keep chromatic keyboard active even when leaving the KEYS tab or
        # this workspace. When True, the user can navigate to any screen
        # while still playing notes through a connected MIDI keyboard.
        self._keys_persistent = False

    # ── Layout helpers (adapt to screen size) ────────────────────────

    @property
    def _content_top(self) -> int:
        return self.HEADER_H + 2

    @property
    def _content_h(self) -> int:
        return theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self._content_top

    @property
    def _scope_h(self) -> int:
        """Oscilloscope height. Fullscreen = entire content area, normal = ~40%."""
        if self._scope_fullscreen:
            return self._content_h - 4
        return max(80, min(160, int(self._content_h * 0.40)))

    @property
    def _controls_top(self) -> int:
        return self._content_top + self._scope_h + 4

    @property
    def _bus_bar_h(self) -> int:
        return 36 if self._device_key == "SP-404MKII" else 0

    @property
    def _controls_h(self) -> int:
        return self._content_h - self._scope_h - self._bus_bar_h - 8

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_enter(self):
        ctx = getattr(self.app, "_screen_context", {})
        dev_key = ctx.get("device", self.app.device_name)
        self.app._screen_context = {}

        if dev_key != self.app.device_name:
            self.app.switch_focus(dev_key)
        else:
            # Already focused — but Twister/recorder may not be retargeted yet
            # (happens on first boot when default focus matches dev_key)
            focused_midi = self.app._midi_connections.get(dev_key)
            if focused_midi:
                # Always update the Twister's target + rebuild pages so the
                # P-6 4x4 control layout renders regardless of whether the
                # Twister hardware is physically connected.
                self.app.twister.set_target(focused_midi)
                self.app.twister._rebuild_pages()

        self._device_key = dev_key
        self._device_profile = self.app.device
        self._device_color = theme.get_device_color(dev_key)

        self._build_tabs()
        self._current_tab = 0

        # Start monitoring
        if not self.app.recorder._monitoring:
            dev = self.app.device
            if dev and dev.audio_hint:
                self.app.recorder.switch_device(dev.audio_hint)
            self.app.recorder.start_monitoring()

        self._build_knobs()

        # Build piano display for the KEYS tab
        # Start at octave 2 so both playable octaves are visible
        # immediately (SP-404 chromatic is C2-C4, MIDI 36-60)
        piano_rect = pygame.Rect(
            10, self._controls_top + 30,
            theme.SCREEN_WIDTH - 20,
            self._controls_h - 40,
        )
        # Root note = C3 (MIDI 60) = the sample's natural pitch
        self._piano_display = PianoDisplay(piano_rect, octaves=2,
                                            start_octave=2, root_note=60)

    def on_exit(self):
        if not self.app.recorder.is_recording:
            self.app.recorder.stop_monitoring()
        # Disable chromatic mode + release all latched notes — unless the
        # user explicitly enabled KEEP ACTIVE so they can keep playing
        # notes while looking at another screen.
        if hasattr(self.app, 'chromatic_kb') and not self._keys_persistent:
            self.app.chromatic_kb.enabled = False
            self.app.chromatic_kb._all_notes_off()
            self._latched_notes.clear()
            self._keys_latch = False

    def _build_tabs(self):
        key = self._device_key
        if key == "SP-404MKII":
            tabs = [
                ("control", "CONTROL"),
                ("twister", "TWISTER"),
                ("keys", "KEYS"),
                ("sequence", "SEQUENCE"),
                ("looper", "LOOPER"),
                ("dj", "DJ"),
            ]
            if not self.app.twister.connected:
                tabs = [t for t in tabs if t[0] != "twister"]
            self._tabs = tabs
        elif key == "P-6":
            self._tabs = [
                ("control", "CONTROL"),
                ("keys", "KEYS"),
                ("pattern", "PATTERN"),
                ("chain", "CHAIN"),
            ]
        elif key == "Force":
            self._tabs = [("transfer", "TRANSFER")]
        else:
            self._tabs = [("control", "CONTROL")]

    # ── Knob Building ────────────────────────────────────────────────

    def _build_knobs(self):
        """Build knobs for the current device + section."""
        self._knobs = []
        if self._device_key == "SP-404MKII":
            self._build_sp404_knobs()
        elif self._device_key == "P-6":
            self._build_p6_knobs()

    def _build_sp404_knobs(self):
        """SP-404: 3x2 knob grid for active bus FX parameters."""
        from engine.sp404_effects import fx_name_for_tab, fx_count_for_tab
        from engine.device_profiles import cc_map_to_legacy

        bus_tab_keys = ["bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"]
        tab_key = bus_tab_keys[self._active_bus] if self._active_bus < len(bus_tab_keys) else "bus1_fx"

        dev = self._device_profile
        if not dev or not dev.cc_map:
            return

        cc_map = cc_map_to_legacy(dev.cc_map)
        params = cc_map.get(tab_key, [])

        # Layout: fill the control zone with a 3x2 grid
        ctrl_top = self._controls_top + 32  # room for section header
        ctrl_h = self._controls_h - 36
        knob_r = min(30, ctrl_h // 5)
        cols, rows = 3, 2
        col_gap = (theme.SCREEN_WIDTH - 40) // cols
        row_gap = max(knob_r * 3, ctrl_h // rows)
        start_x = 20 + col_gap // 2
        start_y = ctrl_top + row_gap // 2

        bus_ch = self._active_bus  # MIDI channel per bus

        for cc, name, lo, hi, default in params:
            if cc in (19, 83):  # FX On/Off and FX Select handled separately
                continue
            idx = len(self._knobs)
            if idx >= 6:
                break
            r, c = idx // cols, idx % cols
            knob = Knob(
                center=(start_x + c * col_gap, start_y + r * row_gap),
                radius=knob_r,
                min_val=float(lo), max_val=float(hi), value=float(default),
                label=name, int_mode=True,
                format_func=lambda v: f"{int(v)}",
            )
            self._knobs.append((knob, cc, bus_ch))

    def _build_p6_knobs(self):
        """P-6: knobs for the selected section (granular/filter/envelope/mixer/fx)."""
        dev = self._device_profile
        if not dev or not dev.cc_map:
            return

        sections = ["granular", "filter", "envelope", "mixer", "fx"]
        section = sections[self._p6_section] if self._p6_section < len(sections) else "granular"
        params = dev.cc_map.get(section, [])

        # MIDI channel for granular engine
        ch = dev.midi_channels.get(section, dev.midi_channels.get("granular", 3))

        # Layout: adaptive grid
        ctrl_top = self._controls_top + 28  # room for section tabs
        ctrl_h = self._controls_h - 32
        n = len(params)
        if n == 0:
            return

        # Determine grid size
        cols = min(4, n) if n <= 8 else min(5, n)
        rows = (n + cols - 1) // cols
        knob_r = min(26, (ctrl_h - 10) // (rows * 3))
        col_gap = (theme.SCREEN_WIDTH - 20) // cols
        row_gap = max(knob_r * 3, (ctrl_h - 10) // max(1, rows))
        start_x = 10 + col_gap // 2
        start_y = ctrl_top + row_gap // 2

        for i, midi_cc in enumerate(params):
            if i >= cols * rows:
                break
            r, c = i // cols, i % cols
            knob = Knob(
                center=(start_x + c * col_gap, start_y + r * row_gap),
                radius=knob_r,
                min_val=float(midi_cc.min_val), max_val=float(midi_cc.max_val),
                value=float(midi_cc.default),
                label=midi_cc.name[:10], int_mode=True,
                format_func=lambda v: f"{int(v)}",
            )
            self._knobs.append((knob, midi_cc.cc, ch))

    # ── Event Handling ───────────────────────────────────────────────

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Back button — large hit area covering top-left corner
            if mx < 80 and my < 40:
                session = self.app.screens.get("session")
                if session:
                    session._auto_expanded = False
                self.app.switch_screen("session")
                return

            # Oscilloscope tap → toggle fullscreen
            scope_rect = pygame.Rect(6, self._content_top, theme.SCREEN_WIDTH - 12, self._scope_h)
            if scope_rect.collidepoint(mx, my):
                self._scope_fullscreen = not self._scope_fullscreen
                return

            # REC button (top-right, next to device name)
            rec_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, 4, 50, 28)
            if rec_rect.collidepoint(mx, my):
                if self.app.recorder.is_recording:
                    self.app.recorder.stop_recording()
                else:
                    meta = {}
                    if self.app.p6:
                        meta["bpm_at_record"] = self.app.p6.state.bpm
                    self.app.recorder.start_recording(metadata=meta)
                return

            # RECALL button
            recall_rect = pygame.Rect(theme.SCREEN_WIDTH - 165, 4, 55, 28)
            if recall_rect.collidepoint(mx, my):
                if self.app.recorder.recall_seconds_available >= 1:
                    self.app.recorder.save_recall()
                    self.app.push_hud("Recall saved", theme.ACCENT)
                return

            # Tab buttons (skip if fullscreen scope)
            if self._scope_fullscreen:
                return
            for i, (key, label) in enumerate(self._tabs):
                if self._tab_rect(i).collidepoint(mx, my):
                    old_tab = self._tabs[self._current_tab][0] if self._tabs else ""
                    self._current_tab = i
                    new_tab = key
                    # Enable/disable chromatic keyboard based on tab
                    if hasattr(self.app, 'chromatic_kb'):
                        if new_tab == "keys":
                            self.app.chromatic_kb.enabled = True
                            # Retarget to THIS workspace's device
                            self._retarget_keys_for_device()
                        elif old_tab == "keys" and not self._keys_persistent:
                            # Only disable if the user hasn't marked it persistent
                            self.app.chromatic_kb.enabled = False
                            self.app.chromatic_kb._all_notes_off()
                            self._latched_notes.clear()
                            self._keys_latch = False
                    if new_tab == "control":
                        self._build_knobs()
                    return

            # Tab-specific controls
            tab_key = self._tabs[self._current_tab][0] if self._tabs else ""

            if tab_key == "control":
                if self._device_key == "SP-404MKII":
                    self._handle_sp404_clicks(mx, my)
                elif self._device_key == "P-6":
                    self._handle_p6_clicks(mx, my)
            elif tab_key == "twister":
                self._handle_twister_grid_clicks(mx, my)
            elif tab_key in ("pattern", "sequence"):
                self._handle_pattern_clicks(mx, my)
            elif tab_key == "chain":
                self._handle_chain_tab_clicks(mx, my)
            elif tab_key == "dj":
                self._handle_dj_clicks(mx, my)
            elif tab_key == "keys":
                self._handle_keys_clicks(mx, my)

        # Touch-to-play note-off on MOUSEBUTTONUP (KEYS tab)
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            tab_key = self._tabs[self._current_tab][0] if self._tabs else ""
            if tab_key == "keys" and self._touch_note >= 0:
                kb = getattr(self.app, 'chromatic_kb', None)
                if kb and kb._target_midi:
                    kb._forward_note_off(self._touch_note)
                    kb.active_notes.pop(self._touch_note, None)
                self._touch_note = -1

        # Knob drag handling (all events, but not if in header area)
        tab_key = self._tabs[self._current_tab][0] if self._tabs else ""
        if tab_key == "control":
            for knob, cc, ch in self._knobs:
                if knob.handle_event(event):
                    if self.app.p6:
                        self.app.p6.send_cc(cc, int(knob.value), channel=ch)

    def _handle_sp404_clicks(self, mx, my):
        from engine.sp404_effects import fx_count_for_tab

        # Bus selector
        bus_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self._bus_bar_h
        for i in range(5):
            r = pygame.Rect(40 + i * (theme.SCREEN_WIDTH - 80) // 5, bus_y, (theme.SCREEN_WIDTH - 80) // 5 - 4, 28)
            if r.collidepoint(mx, my):
                self._active_bus = i
                self._build_knobs()
                # Sync Twister bus
                if hasattr(self.app, 'twister'):
                    self.app.twister.active_bus = i
                return

        fx_y = self._controls_top + 2

        # Twister FX Page buttons (vertical column, right side)
        tw = self.app.twister
        if tw.connected and tw.page_count > 1:
            n_pages = tw.page_count
            pg_w = 40
            pg_h = min(36, (self._controls_h - 40) // n_pages - 3)
            pg_x = theme.SCREEN_WIDTH - pg_w - 8
            pg_start_y = fx_y + 30
            for p in range(n_pages):
                r = pygame.Rect(pg_x, pg_start_y + p * (pg_h + 3), pg_w, pg_h)
                if r.collidepoint(mx, my):
                    tw.switch_page(p)
                    return

        # FX On/Off
        toggle_r = pygame.Rect(theme.SCREEN_WIDTH - 160, fx_y, 70, 26)
        if toggle_r.collidepoint(mx, my):
            self._fx_on = not self._fx_on
            val = 127 if self._fx_on else 0
            if self.app.p6:
                self.app.p6.send_cc(19, val, channel=self._active_bus)
            self.app.live_cc[self._active_bus][19] = val
            return

        # FX Select
        sel_r = pygame.Rect(theme.SCREEN_WIDTH - 82, fx_y, 74, 26)
        if sel_r.collidepoint(mx, my):
            bus_tab_keys = ["bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"]
            tab_key = bus_tab_keys[self._active_bus]
            max_fx = fx_count_for_tab(tab_key) - 1
            self._fx_select_val = (self._fx_select_val + 1) % (max_fx + 1)
            if self.app.p6:
                self.app.p6.send_cc(83, self._fx_select_val, channel=self._active_bus)
            self.app.live_cc[self._active_bus][83] = self._fx_select_val
            return

    def _handle_p6_clicks(self, mx, my):
        # Twister page selector (vertical column, right side) — clickable
        # via touchscreen regardless of whether the Twister hardware is
        # connected.
        tw = self.app.twister
        if tw.page_count > 1:
            fx_y = self._controls_top + 2
            n_pages = tw.page_count
            pg_w = 40
            pg_h = min(36, (self._controls_h - 40) // n_pages - 3)
            pg_x = theme.SCREEN_WIDTH - pg_w - 8
            pg_start_y = fx_y + 28
            for p in range(n_pages):
                r = pygame.Rect(pg_x, pg_start_y + p * (pg_h + 3), pg_w, pg_h)
                if r.collidepoint(mx, my):
                    tw.switch_page(p)
                    return

    def _tab_rect(self, idx: int) -> pygame.Rect:
        n = len(self._tabs)
        tab_w = min(100, (theme.SCREEN_WIDTH - 80) // max(1, n))
        return pygame.Rect(70 + idx * (tab_w + 3), 4, tab_w, 28)

    def update(self):
        rec = self.app.recorder
        if rec._monitoring:
            pl, pr = rec.peak_levels
            decay = 0.85
            self._smooth_l = max(pl, self._smooth_l * decay)
            self._smooth_r = max(pr, self._smooth_r * decay)
        else:
            self._smooth_l *= 0.95
            self._smooth_r *= 0.95
        # Decay piano display notes for fade-out animation
        # But don't decay latched notes — they stay at full brightness
        if self._piano_display:
            self._piano_display.decay_active()
            # Re-apply full velocity to latched notes so they don't fade
            for note in self._latched_notes:
                self._piano_display._active_notes[note] = 127

        # Sync SP-404 live CC values into workspace knobs + FX state
        if self._device_key == "SP-404MKII":
            live = self.app.live_cc.get(self._active_bus, {})
            # Update FX on/off from CC19
            if 19 in live:
                self._fx_on = live[19] >= 64
            # Update FX select from CC83
            if 83 in live:
                self._fx_select_val = live[83]
            # Update knob values from live CCs
            for knob, cc, ch in self._knobs:
                if cc in live:
                    knob.value = float(live[cc])

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_large = theme.font("large")
        f_hero = theme.font("hero")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # ── Header ───────────────────────────────────────────────────
        self._draw_header(surface, f_large, f_small)

        # ── Oscilloscope ─────────────────────────────────────────────
        midi = self.app._midi_connections.get(self._device_key)
        self._draw_oscilloscope(surface, f_hero, f_small, f_tiny, midi)

        # ── Tab Content (hidden when scope is fullscreen) ────────────
        if self._scope_fullscreen:
            # Just show HUD over fullscreen scope
            self._draw_hud(surface, f_small)
            return

        tab_key = self._tabs[self._current_tab][0] if self._tabs else ""

        if tab_key == "control":
            if self._device_key == "SP-404MKII":
                self._draw_sp404_control(surface, f_med, f_small, f_tiny)
            elif self._device_key == "P-6":
                self._draw_p6_control(surface, f_med, f_small, f_tiny)
            else:
                self._draw_generic_control(surface, f_med, f_small)
        elif tab_key == "twister":
            self._draw_twister_grid(surface, f_med, f_small, f_tiny)
        elif tab_key in ("pattern", "sequence"):
            self._draw_pattern_grid(surface, f_med, f_small, f_tiny)
        elif tab_key == "chain":
            self._draw_chain_tab(surface, f_med, f_small, f_tiny)
        elif tab_key == "looper":
            self._draw_looper(surface, f_large, f_med, f_small)
        elif tab_key == "dj":
            self._draw_dj(surface, f_large, f_med, f_small)
        elif tab_key == "keys":
            self._draw_keyboard_tab(surface, f_med, f_small, f_tiny)
        else:
            y = self._controls_top + 20
            surf = f_med.render(f"{tab_key.upper()}", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=y))

        # ── HUD Overlay (top-right, fades after 2s) ─────────────────
        self._draw_hud(surface, f_small)

    def _draw_hud(self, surface, f_small):
        """Draw HUD notification overlay — recent Twister/MIDI activity."""
        import time
        now = time.monotonic()
        hud_lifetime = 2.0
        msgs = self.app._hud_messages

        # Prune expired
        msgs[:] = [(t, c, ts) for t, c, ts in msgs if now - ts < hud_lifetime]

        if not msgs:
            return

        hud_x = theme.SCREEN_WIDTH - 10
        hud_y = self.HEADER_H + 6

        for text, color, ts in reversed(msgs):
            age = now - ts
            # Fade out in last 0.5s
            alpha = min(1.0, (hud_lifetime - age) / 0.5)
            if alpha <= 0:
                continue

            surf = f_small.render(text, True, color)
            w = surf.get_width() + 16
            h = 24
            x = hud_x - w
            # Semi-transparent background
            bg = pygame.Surface((w, h), pygame.SRCALPHA)
            a = int(180 * alpha)
            bg.fill((10, 10, 18, a))
            surface.blit(bg, (x, hud_y))
            # Accent bar on left edge
            bar_color = (*color[:3], a) if len(color) >= 3 else (*color, a)
            bar = pygame.Surface((3, h), pygame.SRCALPHA)
            bar.fill(bar_color)
            surface.blit(bar, (x, hud_y))
            # Text
            text_surf = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
            text_surf.blit(surf, (0, 0))
            text_surf.set_alpha(int(255 * alpha))
            surface.blit(text_surf, (x + 8, hud_y + 3))

            hud_y += h + 3

    def _draw_header(self, surface, f_large, f_small):
        # Back button (larger for touchscreen)
        back = pygame.Rect(4, 2, 68, 34)
        pygame.draw.rect(surface, theme.BUTTON_BG, back, border_radius=6)
        surf = f_small.render("< BACK", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=back.center))

        # Tabs
        for i, (key, label) in enumerate(self._tabs):
            rect = self._tab_rect(i)
            active = i == self._current_tab
            bg = self._device_color if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, rect, border_radius=5)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # REC button
        is_rec = self.app.recorder.is_recording
        rec_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, 4, 50, 28)
        rec_bg = theme.RED if is_rec else theme.BUTTON_BG
        pygame.draw.rect(surface, rec_bg, rec_rect, border_radius=5)
        rec_label = f"REC" if not is_rec else f"{self.app.recorder.duration:.0f}s"
        surf = theme.font("tiny").render(rec_label, True, theme.TEXT_BRIGHT if is_rec else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=rec_rect.center))

        # RECALL button
        recall_secs = self.app.recorder.recall_seconds_available
        recall_rect = pygame.Rect(theme.SCREEN_WIDTH - 165, 4, 55, 28)
        recall_bg = theme.ACCENT if recall_secs >= 1 else theme.BUTTON_BG
        pygame.draw.rect(surface, recall_bg, recall_rect, border_radius=5)
        surf = theme.font("tiny").render(f"RCL {int(recall_secs)}s", True,
                                          theme.BG if recall_secs >= 1 else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=recall_rect.center))

        # Device name + connection dot
        midi = self.app._midi_connections.get(self._device_key)
        connected = midi and midi.connected
        dot_color = theme.GREEN if connected else theme.RED
        name_surf = f_small.render(self._device_key, True, self._device_color)
        nx = theme.SCREEN_WIDTH - name_surf.get_width() - 8
        surface.blit(name_surf, (nx, 10))
        pygame.draw.circle(surface, dot_color, (nx - 6, 18), 3)

        # Accent line
        pygame.draw.line(surface, self._device_color,
                        (0, self.HEADER_H), (theme.SCREEN_WIDTH, self.HEADER_H), 2)

    def _draw_oscilloscope(self, surface, f_hero, f_small, f_tiny, midi):
        """Full-width oscilloscope with filled waveform, meters, BPM overlay."""
        pad = 6
        scope_y = self._content_top
        scope_w = theme.SCREEN_WIDTH - pad * 2
        scope_h = self._scope_h
        scope_rect = pygame.Rect(pad, scope_y, scope_w, scope_h)

        # Background with subtle gradient feel
        pygame.draw.rect(surface, (8, 8, 14), scope_rect, border_radius=4)

        center_y = scope_rect.centery
        half_h = (scope_h - 10) // 2

        # Grid lines (horizontal)
        for frac in (0.25, 0.75):
            gy = scope_rect.y + int(scope_h * frac)
            pygame.draw.line(surface, (18, 18, 26),
                            (scope_rect.x + 2, gy), (scope_rect.right - 28, gy))
        # Center line
        pygame.draw.line(surface, (22, 22, 32),
                        (scope_rect.x + 2, center_y), (scope_rect.right - 28, center_y))

        # Meter area (right 24px)
        meter_w = 22
        wave_w = scope_w - meter_w - 10

        rec = self.app.recorder
        if rec._monitoring:
            buf = rec._recall_buf
            wpos = rec._recall_write_pos
            display_frames = min(2048, len(buf))

            if wpos >= display_frames:
                recent = buf[wpos - display_frames:wpos]
            else:
                recent = np.concatenate([buf[-(display_frames - wpos):], buf[:wpos]])

            if len(recent) > 0 and float(np.max(np.abs(recent))) > 0.001:
                mono = recent.mean(axis=1) if recent.ndim > 1 else recent

                step = max(1, len(mono) // wave_w)
                points = []
                dc = self._device_color

                for px in range(wave_w):
                    si = px * step
                    if si < len(mono):
                        val = max(-1.0, min(1.0, float(mono[si]) * 3.0))
                        py = center_y - int(val * half_h)
                        points.append((scope_rect.x + 4 + px, py))

                if len(points) > 1:
                    # Filled waveform
                    dim = (dc[0] // 5, dc[1] // 5, dc[2] // 5)
                    for px_x, py in points:
                        if py != center_y:
                            pygame.draw.line(surface, dim, (px_x, center_y), (px_x, py))
                    pygame.draw.lines(surface, dc, False, points, 2)
            else:
                # Silent — dim center line
                pygame.draw.line(surface, (35, 35, 48),
                               (scope_rect.x + 4, center_y),
                               (scope_rect.x + 4 + wave_w, center_y))

            # Status label (top-right of waveform area)
            if rec.is_recording:
                # Blinking REC
                dur = rec.duration
                surf = f_tiny.render(f"REC {dur:.0f}s", True, theme.RED)
            else:
                recall = rec.recall_seconds_available
                surf = f_tiny.render(f"buf:{int(recall)}s", True, theme.TEXT_DIM)
            surface.blit(surf, (scope_rect.x + wave_w - surf.get_width() - 2, scope_rect.y + 3))
        else:
            surf = f_small.render("No audio", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=(scope_rect.centerx, center_y)))

        # ── L/R Level Meters ─────────────────────────────────────────
        mx = scope_rect.right - meter_w - 2
        mh = scope_h - 16
        my = scope_rect.y + 4

        for i, (level, label) in enumerate([(self._smooth_l, "L"), (self._smooth_r, "R")]):
            bar_x = mx + i * (meter_w // 2 + 1)
            bar_w = meter_w // 2 - 1
            pygame.draw.rect(surface, (16, 16, 24), (bar_x, my, bar_w, mh))
            fill = int(level * mh)
            if fill > 0:
                color = theme.RED if level > 0.9 else (theme.YELLOW if level > 0.7 else self._device_color)
                pygame.draw.rect(surface, color, (bar_x, my + mh - fill, bar_w, fill))

        # ── BPM + Transport (bottom-left overlay) ────────────────────
        if midi:
            bpm = midi.state.bpm
            bpm_y = scope_rect.bottom - 24
            bpm_surf = f_hero.render(f"{bpm:.0f}", True, self._device_color)
            surface.blit(bpm_surf, (scope_rect.x + 6, bpm_y - 14))
            bw = bpm_surf.get_width()

            surf = f_tiny.render("BPM", True, theme.TEXT_DIM)
            surface.blit(surf, (scope_rect.x + bw + 10, bpm_y))

            # Transport indicator — measure its width so Pattern can clear it
            tx = scope_rect.x + bw + 40
            if midi.state.playing:
                pygame.draw.polygon(surface, theme.GREEN,
                    [(tx, bpm_y - 4), (tx, bpm_y + 8), (tx + 10, bpm_y + 2)])
                transport_w = 12  # triangle + small gap
            else:
                transport_surf = f_tiny.render("STOP", True, theme.TEXT_DIM)
                surface.blit(transport_surf, (tx, bpm_y))
                transport_w = transport_surf.get_width()

            # Pattern — positioned after transport with generous spacing
            pat = midi.state.active_pattern + 1
            pat_max = getattr(self._device_profile, "pattern_count", 0)
            if pat_max > 0:
                surf = f_tiny.render(f"Ptn {pat}/{pat_max}", True, theme.TEXT_DIM)
                surface.blit(surf, (tx + transport_w + 10, bpm_y))

        # Border
        pygame.draw.rect(surface, (28, 28, 38), scope_rect, 1, border_radius=4)

    # ── SP-404 Control ───────────────────────────────────────────────

    def _draw_sp404_control(self, surface, f_med, f_small, f_tiny):
        from engine.sp404_effects import fx_name_for_tab

        bus_tab_keys = ["bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"]
        tab_key = bus_tab_keys[self._active_bus]

        # FX header row
        fx_y = self._controls_top + 2
        bus_labels = ["BUS 1", "BUS 2", "BUS 3", "BUS 4", "INPUT"]
        surf = f_small.render(bus_labels[self._active_bus], True, self._device_color)
        surface.blit(surf, (10, fx_y + 3))

        fx_name = fx_name_for_tab(tab_key, self._fx_select_val)

        # FX On/Off
        toggle_r = pygame.Rect(theme.SCREEN_WIDTH - 160, fx_y, 70, 26)
        toggle_bg = theme.GREEN if self._fx_on else theme.BUTTON_BG
        toggle_tc = theme.BG if self._fx_on else theme.TEXT_DIM
        pygame.draw.rect(surface, toggle_bg, toggle_r, border_radius=5)
        surf = f_tiny.render("FX ON" if self._fx_on else "FX OFF", True, toggle_tc)
        surface.blit(surf, surf.get_rect(center=toggle_r.center))

        # FX Select
        sel_r = pygame.Rect(theme.SCREEN_WIDTH - 82, fx_y, 74, 26)
        pygame.draw.rect(surface, theme.ACCENT_DIM, sel_r, border_radius=5)
        pygame.draw.rect(surface, self._device_color, sel_r, 1, border_radius=5)
        surf = f_tiny.render(fx_name[:10], True, self._device_color)
        surface.blit(surf, surf.get_rect(center=sel_r.center))

        # ── Twister FX Pages (vertical column, right side) ───────────
        tw = self.app.twister
        if tw.connected and tw.page_count > 1:
            n_pages = tw.page_count
            pg_w = 40
            pg_h = min(36, (self._controls_h - 40) // n_pages - 3)
            pg_x = theme.SCREEN_WIDTH - pg_w - 8
            pg_start_y = fx_y + 30
            surf = f_tiny.render("FX", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=pg_x + pg_w // 2, top=fx_y + 2))
            for p in range(n_pages):
                r = pygame.Rect(pg_x, pg_start_y + p * (pg_h + 3), pg_w, pg_h)
                active = p == tw.current_page
                bg = self._device_color if active else theme.BUTTON_BG
                tc = theme.BG if active else theme.TEXT_DIM
                pygame.draw.rect(surface, bg, r, border_radius=5)
                surf = f_small.render(f"P{p + 1}", True, tc)
                surface.blit(surf, surf.get_rect(center=r.center))

        # Knobs
        for knob, cc, ch in self._knobs:
            knob.draw(surface)

        if not self._knobs:
            y = self._controls_top + 60
            surf = f_small.render("No FX parameters", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=y))

        # Bus selector bar (bottom)
        bus_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self._bus_bar_h
        n_bus = 5
        btn_w = (theme.SCREEN_WIDTH - 80) // n_bus - 4
        for i in range(n_bus):
            r = pygame.Rect(40 + i * (btn_w + 4), bus_y + 2, btn_w, 28)
            active = i == self._active_bus
            bg = self._device_color if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, r, border_radius=5)
            labels = ["B1", "B2", "B3", "B4", "IN"]
            surf = f_tiny.render(labels[i], True, tc)
            surface.blit(surf, surf.get_rect(center=r.center))

    # ── P-6 Control ──────────────────────────────────────────────────

    def _draw_p6_control(self, surface, f_med, f_small, f_tiny):
        """P-6 control: shows Twister-mapped parameters with live knob feedback."""
        tw = self.app.twister

        # ── Page header ─────────────────────────────────────────────
        fx_y = self._controls_top + 2

        # Page label
        if tw.connected:
            page_label = f"Page {tw.current_page + 1}/{tw.page_count}"
        else:
            page_label = "P-6 Control"
        surf = f_small.render(page_label, True, self._device_color)
        surface.blit(surf, (10, fx_y + 3))

        # ── Page selector (vertical column, right side) ──────────────
        if tw.page_count > 1:
            n_pages = tw.page_count
            pg_w = 40
            pg_h = min(36, (self._controls_h - 40) // n_pages - 3)
            pg_x = theme.SCREEN_WIDTH - pg_w - 8
            pg_start_y = fx_y + 28
            surf = f_tiny.render("PAGE", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=pg_x + pg_w // 2, top=fx_y + 2))
            for p in range(n_pages):
                r = pygame.Rect(pg_x, pg_start_y + p * (pg_h + 3), pg_w, pg_h)
                active = p == tw.current_page
                bg = self._device_color if active else theme.BUTTON_BG
                tc = theme.BG if active else theme.TEXT_DIM
                pygame.draw.rect(surface, bg, r, border_radius=5)
                surf = f_small.render(f"P{p + 1}", True, tc)
                surface.blit(surf, surf.get_rect(center=r.center))

        # ── 4x4 Parameter knobs (from Twister's current P-6 page) ────
        # Layout is always available when targeting the P-6; the Twister
        # hardware just decorates page switching / live knob feedback.
        if tw.is_p6_mode and tw.slots:
            slots = tw.slots
            n = min(16, len(slots))
            live = self.app.live_cc.get(14, {})  # P-6 auto channel (ch15, idx 14)

            # 4x4 grid layout
            cols, rows = 4, 4
            ctrl_top = fx_y + 26
            ctrl_h = self._controls_h - 30
            knob_r = min(24, ctrl_h // (rows * 3))
            col_gap = (theme.SCREEN_WIDTH - 70) // cols  # room for page column
            row_gap = ctrl_h // rows
            start_x = 10 + col_gap // 2
            start_y = ctrl_top + row_gap // 2

            import math
            for i in range(n):
                slot = slots[i]
                r, c = i // cols, i % cols
                cx = start_x + c * col_gap
                cy = start_y + r * row_gap

                cc = getattr(slot, "_p6_cc", None)
                if cc is None:
                    continue

                val = float(live.get(cc, 64))

                # Knob background
                pygame.draw.circle(surface, theme.BG_LIGHTER, (cx, cy), knob_r)
                # Value arc
                filled = val / 127.0
                angle_start = 135
                end_angle = angle_start + filled * 270
                for a in range(int(angle_start), int(end_angle)):
                    rad = math.radians(a)
                    px = cx + int((knob_r - 3) * math.cos(rad))
                    py = cy + int((knob_r - 3) * math.sin(rad))
                    pygame.draw.circle(surface, self._device_color, (px, py), 2)

                # Value text
                surf = f_tiny.render(f"{int(val)}", True, self._device_color)
                surface.blit(surf, surf.get_rect(center=(cx, cy)))

                # Label below
                surf = f_tiny.render(slot.name[:8], True, theme.TEXT_DIM)
                surface.blit(surf, surf.get_rect(centerx=cx, top=cy + knob_r + 2))
        else:
            # Fallback: old section-based knobs
            for knob, cc, ch in self._knobs:
                knob.draw(surface)
            if not self._knobs:
                y = self._controls_top + 50
                surf = f_small.render("No parameters", True, theme.TEXT_DIM)
                surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=y))

    # ── Other tabs ───────────────────────────────────────────────────

    # ── Pattern / Sequence Grid ──────────────────────────────────────

    def _pattern_grid_layout(self):
        """Returns (cols, rows, cell_w, cell_h, start_x, start_y, pattern_count)."""
        dev = self._device_profile
        count = getattr(dev, "pattern_count", 16) if dev else 16
        if count <= 0:
            count = 16

        # Layout: 4x4 for ≤16, 8x4 for ≤32, 8x8 for 64
        if count <= 16:
            cols, rows = 4, 4
        elif count <= 32:
            cols, rows = 8, 4
        else:
            cols, rows = 8, 8

        top = self._controls_top + 36  # space for header
        avail_h = self._content_h - 36 - self._bus_bar_h - 4
        avail_w = theme.SCREEN_WIDTH - 20

        cell_w = (avail_w - (cols - 1) * 4) // cols
        cell_h = (avail_h - (rows - 1) * 4) // rows
        start_x = 10
        start_y = top
        return cols, rows, cell_w, cell_h, start_x, start_y, count

    def _draw_pattern_grid(self, surface, f_med, f_small, f_tiny):
        """Draw pattern launch grid for the focused device."""
        dev = self._device_profile
        if not dev:
            return

        # Header
        midi = self.app._midi_connections.get(self._device_key)
        current = midi.state.active_pattern + 1 if midi else 1
        max_pat = getattr(dev, "pattern_count", 0)
        title = "PATTERNS" if self._device_key == "P-6" else "SEQUENCES"

        hdr_y = self._controls_top + 4
        surf = f_med.render(title, True, self._device_color)
        surface.blit(surf, (10, hdr_y))

        if max_pat:
            surf = f_small.render(f"Active: {current}/{max_pat}", True, theme.TEXT_DIM)
            surface.blit(surf, (140, hdr_y + 4))

        # Transport buttons (right of header)
        if midi:
            playing = midi.state.playing
            play_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, hdr_y, 60, 26)
            stop_rect = pygame.Rect(theme.SCREEN_WIDTH - 155, hdr_y, 60, 26)
            pygame.draw.rect(surface, theme.GREEN if playing else theme.BUTTON_BG, play_rect, border_radius=5)
            surf = f_tiny.render("PLAY", True, theme.BG if playing else theme.TEXT)
            surface.blit(surf, surf.get_rect(center=play_rect.center))
            pygame.draw.rect(surface, theme.BUTTON_BG, stop_rect, border_radius=5)
            surf = f_tiny.render("STOP", True, theme.TEXT)
            surface.blit(surf, surf.get_rect(center=stop_rect.center))

        # Grid
        cols, rows, cell_w, cell_h, sx, sy, count = self._pattern_grid_layout()
        for i in range(count):
            r, c = i // cols, i % cols
            x = sx + c * (cell_w + 4)
            y = sy + r * (cell_h + 4)
            rect = pygame.Rect(x, y, cell_w, cell_h)
            active = (i == (current - 1))
            bg = self._device_color if active else theme.BG_PANEL
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            if not active:
                pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=4)
            # Number
            label = str(i + 1)
            f = f_med if cell_h >= 50 else f_small
            surf = f.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

    # ── Chain tab (P-6) ──────────────────────────────────────────────

    def _chain_state(self):
        """Return (chain_player, chain) from the standalone Pattern
        screen so the workspace tab and the full editor share state."""
        ps = self.app.screens.get("pattern")
        if ps is None:
            return None, None
        return getattr(ps, "chain_player", None), getattr(ps, "_chain", None)

    def _draw_chain_tab(self, surface, f_med, f_small, f_tiny):
        """P-6 chain tab — compact step list + transport. Steps and
        playback state are shared with the full Pattern screen, so
        edits made here show up there and vice versa."""
        cp, chain = self._chain_state()
        top = self._controls_top + 4

        # Header: chain name + step count + play/stop transport
        name = getattr(chain, "name", "—") if chain else "—"
        steps = list(getattr(chain, "steps", []) or [])
        playing = bool(getattr(cp, "playing", False)) if cp else False

        title = f"CHAIN  ·  {name}  ({len(steps)} steps)"
        surf = f_med.render(title, True, self._device_color)
        surface.blit(surf, (12, top))

        # Transport buttons (right side of header)
        play_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, top, 60, 26)
        stop_rect = pygame.Rect(theme.SCREEN_WIDTH - 155, top, 60, 26)
        add_rect  = pygame.Rect(theme.SCREEN_WIDTH - 90,  top, 78, 26)
        pygame.draw.rect(surface,
                         theme.GREEN if playing else theme.BUTTON_BG,
                         play_rect, border_radius=5)
        surf = f_tiny.render("PLAY", True,
                             theme.BG if playing else theme.TEXT)
        surface.blit(surf, surf.get_rect(center=play_rect.center))
        pygame.draw.rect(surface, theme.BUTTON_BG, stop_rect, border_radius=5)
        surface.blit(f_tiny.render("STOP", True, theme.TEXT),
                     f_tiny.render("STOP", True, theme.TEXT).get_rect(
                         center=stop_rect.center))
        pygame.draw.rect(surface, theme.ACCENT_DIM, add_rect, border_radius=5)
        surface.blit(f_tiny.render("+ STEP", True, theme.BG),
                     f_tiny.render("+ STEP", True, theme.BG).get_rect(
                         center=add_rect.center))

        # Step list — compact rows
        list_y = top + 32
        row_h = 22
        max_rows = max(1, (self._controls_h - 40) // row_h)
        if not steps:
            msg = f_small.render(
                "No steps — tap + STEP to start a chain  ·  "
                "open the Pattern screen for the full editor",
                True, theme.TEXT_DIM)
            surface.blit(msg, (16, list_y + 8))
            return

        cur_idx = int(getattr(cp, "step_index", 0)) if cp else -1
        for i, step in enumerate(steps[:max_rows]):
            y = list_y + i * row_h
            row_rect = pygame.Rect(12, y, theme.SCREEN_WIDTH - 24, row_h - 2)
            is_current = playing and i == cur_idx
            if is_current:
                pygame.draw.rect(surface, theme.ACCENT_DIM,
                                 row_rect, border_radius=3)
            pygame.draw.rect(surface, theme.BORDER,
                             row_rect, 1, border_radius=3)
            marker = ">" if is_current else " "
            color = theme.GREEN if is_current else theme.TEXT_DIM
            surf = f_small.render(f"{marker}{i + 1:2d}", True, color)
            surface.blit(surf, (18, y + 4))
            pat = getattr(step, "pattern", 0) + 1
            bars = getattr(step, "bars", 4)
            multi = getattr(step, "device_patterns", None)
            if multi:
                pat_text = "  ·  ".join(
                    f"{k}:{v + 1}" for k, v in sorted(multi.items()))
            else:
                pat_text = f"P{pat:02d}"
            surf = f_small.render(pat_text, True, theme.TEXT)
            surface.blit(surf, (60, y + 4))
            surf = f_tiny.render(f"{bars} bars", True, theme.ACCENT)
            surface.blit(surf, (row_rect.right - 92, y + 5))
            # Per-row delete button.
            del_rect = pygame.Rect(row_rect.right - 28, y + 3,
                                   22, row_h - 8)
            pygame.draw.rect(surface, theme.RED, del_rect, border_radius=3)
            surf = f_tiny.render("X", True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=del_rect.center))

        if len(steps) > max_rows:
            more = f_tiny.render(
                f"... +{len(steps) - max_rows} more  ·  Pattern screen "
                "for full editor",
                True, theme.TEXT_DIM)
            surface.blit(more, (16, list_y + max_rows * row_h + 4))

    def _handle_chain_tab_clicks(self, mx, my):
        cp, chain = self._chain_state()
        if cp is None or chain is None:
            return
        top = self._controls_top + 4
        play_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, top, 60, 26)
        stop_rect = pygame.Rect(theme.SCREEN_WIDTH - 155, top, 60, 26)
        add_rect  = pygame.Rect(theme.SCREEN_WIDTH - 90,  top, 78, 26)

        if play_rect.collidepoint(mx, my):
            if cp.playing:
                cp.stop()
                return
            # Re-wire chain player to the focused device every time we
            # press play — the P-6 might have been disconnected at
            # screen-init time, so the standalone Pattern screen'"'"'s
            # original wiring may be stale.
            p6 = self.app.p6
            if p6 is not None:
                try:
                    cp.on_pattern_change = p6.send_program_change
                    cp._midi_out = p6
                except Exception:
                    pass
                try:
                    cp._device_midi = dict(self.app._midi_connections)
                except Exception:
                    pass
                # Tick advancement: chain ticks need MIDI clock from
                # the focused device. Hook it now.
                try:
                    p6.on_clock_tick = cp.on_tick
                except Exception:
                    pass
                # Get the device actually playing so it sends clock and
                # the patterns themselves are audible.
                try:
                    if hasattr(p6, "send_start"):
                        p6.send_start()
                except Exception:
                    pass
            cp.start()
            return
        if stop_rect.collidepoint(mx, my):
            if cp.playing:
                cp.stop()
            # Don'"'"'t auto-stop the device — user may want the current
            # pattern to keep looping. They can stop the device on its
            # own transport.
            return
        if add_rect.collidepoint(mx, my):
            from engine.p6_chain import ChainStep
            current_pat = 0
            try:
                midi = self.app._midi_connections.get(self._device_key)
                current_pat = midi.state.active_pattern if midi else 0
            except Exception:
                pass
            chain.steps.append(ChainStep(pattern=current_pat, bars=4))
            return

        # Per-row delete buttons. Recompute the same rects the draw
        # code used so a tap on an X removes that step.
        list_y = top + 32
        row_h = 22
        steps = list(getattr(chain, "steps", []) or [])
        max_rows = max(1, (self._controls_h - 40) // row_h)
        for i in range(min(len(steps), max_rows)):
            y = list_y + i * row_h
            row_right = theme.SCREEN_WIDTH - 12
            del_rect = pygame.Rect(row_right - 28, y + 3, 22, row_h - 8)
            if del_rect.collidepoint(mx, my):
                if cp.playing:
                    cp.stop()
                try:
                    chain.steps.pop(i)
                except Exception:
                    pass
                return

    def _handle_pattern_clicks(self, mx, my):
        midi = self.app._midi_connections.get(self._device_key)
        if not midi:
            return

        hdr_y = self._controls_top + 4
        # Play/Stop buttons
        play_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, hdr_y, 60, 26)
        if play_rect.collidepoint(mx, my):
            if midi.state.playing:
                midi.send_stop()
            else:
                midi.send_start()
            return
        stop_rect = pygame.Rect(theme.SCREEN_WIDTH - 155, hdr_y, 60, 26)
        if stop_rect.collidepoint(mx, my):
            midi.send_stop()
            return

        # Grid
        cols, rows, cell_w, cell_h, sx, sy, count = self._pattern_grid_layout()
        for i in range(count):
            r, c = i // cols, i % cols
            x = sx + c * (cell_w + 4)
            y = sy + r * (cell_h + 4)
            rect = pygame.Rect(x, y, cell_w, cell_h)
            if rect.collidepoint(mx, my):
                # Send Program Change to switch pattern
                pc_channel = getattr(self._device_profile, "pattern_pc_channel", 0)
                midi.send_program_change(i, channel=pc_channel)
                self.app.push_hud(f"Pattern {i + 1}", self._device_color)
                return

    def _handle_dj_clicks(self, mx, my):
        """Handle DJ mode button taps — send CCs to SP-404."""
        top = self._controls_top + 4
        ctrl_h = self._controls_h - 8
        half_w = (theme.SCREEN_WIDTH - 30) // 2
        btn_h = min(38, (ctrl_h - 60) // 3)
        btn_w = min(120, half_w // 2 - 8)

        for deck, ch in enumerate([0, 1]):
            dx = 14 + deck * (half_w + 6)
            by = top + 26
            buttons = [
                ("PLAY", 20, 127), ("CUE", 23, 127), ("SYNC", 22, 127),
                ("BEND+", 24, 127), ("BEND-", 25, 127),
            ]
            for i, (label, cc, val) in enumerate(buttons):
                col = i % 2
                row = i // 2
                x = dx + col * (btn_w + 6)
                y = by + row * (btn_h + 4)
                r = pygame.Rect(x, y, btn_w, btn_h)
                if r.collidepoint(mx, my):
                    if self.app.p6:
                        self.app.p6.send_cc(cc, val, channel=ch)
                    self.app.push_hud(f"DJ {label} Ch{ch+1}", self._device_color)
                    return

    # ── Twister Grid ──────────────────────────────────────────────────

    def _twister_grid_rects(self):
        """Calculate 4x4 grid cell rects for the Twister assignment view."""
        from engine.twister_genius import KNOB_CTRL2, KNOB_CTRL3, FX_KNOB_INDICES
        top = self._controls_top + 6
        h = self._controls_h - 10
        cell_w = (theme.SCREEN_WIDTH - 50) // 4
        cell_h = h // 4
        rects = {}
        for row in range(4):
            for col in range(4):
                phys = row * 4 + col
                x = 20 + col * (cell_w + 3)
                y = top + row * (cell_h + 2)
                rects[phys] = pygame.Rect(x, y, cell_w, cell_h)
        return rects

    def _draw_twister_grid(self, surface, f_med, f_small, f_tiny):
        """Draw the 4x4 Twister knob assignment grid."""
        from engine.twister_genius import KNOB_CTRL2, KNOB_CTRL3, FX_KNOB_INDICES
        from engine.sp404_effects import fx_list_for_tab
        tw = self.app.twister

        rects = self._twister_grid_rects()

        # Page selector (top-right column)
        if tw.page_count > 1:
            pg_x = theme.SCREEN_WIDTH - 48
            pg_start = self._controls_top + 8
            for p in range(tw.page_count):
                r = pygame.Rect(pg_x, pg_start + p * 34, 40, 30)
                active = p == tw.current_page
                bg = self._device_color if active else theme.BUTTON_BG
                tc = theme.BG if active else theme.TEXT_DIM
                pygame.draw.rect(surface, bg, r, border_radius=5)
                surf = f_small.render(f"P{p+1}", True, tc)
                surface.blit(surf, surf.get_rect(center=r.center))

        for phys, rect in rects.items():
            if phys == KNOB_CTRL2:
                # Dynamic Ctrl 2 / SHIFT
                pygame.draw.rect(surface, (30, 50, 50), rect, border_radius=6)
                pygame.draw.rect(surface, self._device_color, rect, 1, border_radius=6)
                surf = f_tiny.render("CTRL 2", True, self._device_color)
                surface.blit(surf, surf.get_rect(centerx=rect.centerx, top=rect.y + 4))
                surf = f_tiny.render("+ SHIFT", True, theme.TEXT_DIM)
                surface.blit(surf, surf.get_rect(centerx=rect.centerx, bottom=rect.bottom - 4))
            elif phys == KNOB_CTRL3:
                # Dynamic Ctrl 3
                pygame.draw.rect(surface, (30, 50, 50), rect, border_radius=6)
                pygame.draw.rect(surface, self._device_color, rect, 1, border_radius=6)
                surf = f_tiny.render("CTRL 3", True, self._device_color)
                surface.blit(surf, surf.get_rect(center=rect.center))
            elif phys in tw._phys_to_slot:
                # FX slot
                slot_idx = tw._phys_to_slot[phys]
                if slot_idx < len(tw.slots):
                    slot = tw.slots[slot_idx]
                    # Color from the slot's LED color (map to RGB approximation)
                    rgb = self._twister_color_to_rgb(slot.color)
                    dim_rgb = (rgb[0] // 4, rgb[1] // 4, rgb[2] // 4)
                    pygame.draw.rect(surface, dim_rgb, rect, border_radius=6)
                    pygame.draw.rect(surface, rgb, rect, 2, border_radius=6)
                    # Knob number
                    surf = f_tiny.render(f"K{phys+1}", True, theme.TEXT_DIM)
                    surface.blit(surf, (rect.x + 4, rect.y + 3))
                    # Effect name
                    name = slot.name
                    if len(name) > 12:
                        name = name[:11] + ".."
                    surf = f_small.render(name, True, rgb)
                    surface.blit(surf, surf.get_rect(center=rect.center))
                    # Active indicator
                    if slot.active:
                        surf = f_tiny.render("ACTIVE", True, theme.TEXT_BRIGHT)
                        surface.blit(surf, surf.get_rect(centerx=rect.centerx, bottom=rect.bottom - 3))
                else:
                    pygame.draw.rect(surface, theme.BUTTON_BG, rect, border_radius=6)
            else:
                pygame.draw.rect(surface, theme.BG_PANEL, rect, border_radius=6)

    def _handle_twister_grid_clicks(self, mx, my):
        """Handle taps on the Twister grid — cycle effect assignment."""
        from engine.twister_genius import KNOB_CTRL2, KNOB_CTRL3
        from engine.sp404_effects import fx_list_for_tab
        tw = self.app.twister

        # Page selector
        if tw.page_count > 1:
            pg_x = theme.SCREEN_WIDTH - 48
            pg_start = self._controls_top + 8
            for p in range(tw.page_count):
                r = pygame.Rect(pg_x, pg_start + p * 34, 40, 30)
                if r.collidepoint(mx, my):
                    tw.switch_page(p)
                    return

        rects = self._twister_grid_rects()
        for phys, rect in rects.items():
            if rect.collidepoint(mx, my):
                if phys in (KNOB_CTRL2, KNOB_CTRL3):
                    return  # Can't reassign dynamic knobs
                slot_idx = tw._phys_to_slot.get(phys)
                if slot_idx is None or slot_idx >= len(tw.slots):
                    return
                # Cycle to next effect on this bus
                fx = fx_list_for_tab(tw.bus_tab)
                fx_names = [name for _, name in fx if name != "(OFF)"]
                if not fx_names:
                    return
                current = tw.slots[slot_idx].name
                try:
                    idx = fx_names.index(current)
                    next_name = fx_names[(idx + 1) % len(fx_names)]
                except ValueError:
                    next_name = fx_names[0]
                tw.assign_effect(slot_idx, next_name)
                return

    @staticmethod
    def _twister_color_to_rgb(color_val: int) -> tuple:
        """Approximate Twister LED color wheel value (1-127) to RGB."""
        if color_val <= 0:
            return (60, 60, 60)
        # Simple HSV-like mapping: 1=red, ~21=orange, ~42=yellow, ~63=green,
        # ~84=cyan, ~105=blue, ~126=purple, 127=white
        if color_val >= 127:
            return (255, 255, 255)
        h = (color_val - 1) / 126.0  # 0.0 to 1.0
        # 6-segment color wheel
        i = int(h * 6) % 6
        f = h * 6 - int(h * 6)
        if i == 0: return (255, int(f * 255), 0)         # red → yellow
        if i == 1: return (int((1-f) * 255), 255, 0)     # yellow → green
        if i == 2: return (0, 255, int(f * 255))         # green → cyan
        if i == 3: return (0, int((1-f) * 255), 255)     # cyan → blue
        if i == 4: return (int(f * 255), 0, 255)         # blue → purple
        return (255, 0, int((1-f) * 255))                # purple → red

    def _draw_generic_control(self, surface, f_med, f_small):
        y = self._controls_top + 10
        surf = f_med.render(f"{self._device_key}", True, self._device_color)
        surface.blit(surf, (20, y))
        surf = f_small.render("No control parameters available", True, theme.TEXT_DIM)
        surface.blit(surf, (20, y + 28))

    def _draw_looper(self, surface, f_large, f_med, f_small):
        y = self._controls_top + 6
        btn_w = min(140, (theme.SCREEN_WIDTH - 60) // 3)
        btn_h = min(50, self._controls_h // 3)

        buttons = [
            ("REC", theme.RED), ("OVERDUB", theme.YELLOW), ("STOP", theme.BUTTON_BG),
            ("DELETE", (120, 40, 40)), ("UNDO", theme.ACCENT_DIM), ("REDO", theme.ACCENT_DIM),
        ]
        for i, (label, bg) in enumerate(buttons):
            r, c = i // 3, i % 3
            x = 20 + c * (btn_w + 8)
            rect = pygame.Rect(x, y + r * (btn_h + 8), btn_w, btn_h)
            pygame.draw.rect(surface, bg, rect, border_radius=8)
            pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=8)
            surf = f_med.render(label, True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=rect.center))

    def _draw_dj(self, surface, f_large, f_med, f_small):
        """SP-404 DJ Mode — dual deck controls with crossfader."""
        f_tiny = theme.font("tiny")
        top = self._controls_top + 4
        ctrl_h = self._controls_h - 8
        half_w = (theme.SCREEN_WIDTH - 30) // 2

        # ── Deck labels ─────────────────────────────────────────────
        surf = f_med.render("DECK A (Ch1)", True, self._device_color)
        surface.blit(surf, (14, top))
        surf = f_med.render("DECK B (Ch2)", True, self._device_color)
        surface.blit(surf, (half_w + 20, top))

        # ── Buttons per deck ────────────────────────────────────────
        btn_h = min(38, (ctrl_h - 60) // 3)
        btn_w = min(120, half_w // 2 - 8)

        for deck, ch in enumerate([0, 1]):
            dx = 14 + deck * (half_w + 6)
            by = top + 26

            buttons = [
                ("PLAY / PAUSE", 20, 127, theme.GREEN),
                ("CUE",          23, 127, theme.YELLOW),
                ("SYNC",         22, 127, theme.BLUE),
                ("BEND +",       24, 127, theme.ACCENT_DIM),
                ("BEND -",       25, 127, theme.ACCENT_DIM),
            ]
            for i, (label, cc, val, color) in enumerate(buttons):
                col = i % 2
                row = i // 2
                x = dx + col * (btn_w + 6)
                y = by + row * (btn_h + 4)
                r = pygame.Rect(x, y, btn_w, btn_h)
                pygame.draw.rect(surface, color, r, border_radius=6)
                surf = f_tiny.render(label, True, theme.BG)
                surface.blit(surf, surf.get_rect(center=r.center))

        # ── Crossfader (bottom, full width) ─────────────────────────
        xf_y = top + ctrl_h - 30
        xf_rect = pygame.Rect(14, xf_y, theme.SCREEN_WIDTH - 28, 24)
        pygame.draw.rect(surface, theme.BG_LIGHTER, xf_rect, border_radius=4)
        # Crossfade position from live CC
        xf_val = self.app.live_cc.get(0, {}).get(8, 64)
        xf_pos = int((xf_val / 127.0) * (xf_rect.width - 20))
        handle = pygame.Rect(xf_rect.x + xf_pos, xf_y - 2, 20, 28)
        pygame.draw.rect(surface, self._device_color, handle, border_radius=4)
        surf = f_tiny.render("A", True, theme.TEXT_DIM)
        surface.blit(surf, (xf_rect.x + 4, xf_y + 4))
        surf = f_tiny.render("CROSSFADE", True, theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(centerx=xf_rect.centerx, top=xf_y + 4))
        surf = f_tiny.render("B", True, theme.TEXT_DIM)
        surface.blit(surf, (xf_rect.right - 14, xf_y + 4))

    # ── KEYS tab (chromatic keyboard) ────────────────────────────────

    def _draw_keyboard_tab(self, surface, f_med, f_small, f_tiny):
        """Draw the chromatic keyboard tab: header + pad selector + piano."""
        kb = getattr(self.app, 'chromatic_kb', None)
        top = self._controls_top + 2

        # ── Row 1: controls bar ──────────────────────────────────────
        # Keyboard name (left)
        if kb and kb.connected:
            name = kb.device_name[:20]
        else:
            name = "touch to play"
        name_surf = f_tiny.render(name, True, self._device_color)
        surface.blit(name_surf, (10, top + 6))

        # Active notes text (center-left)
        if kb and kb.active_notes:
            note_strs = [note_name(n) for n in sorted(kb.active_notes.keys())]
            notes_text = "  ".join(note_strs[:6])
            nt_surf = f_small.render(notes_text, True, self._device_color)
            surface.blit(nt_surf, (160, top + 4))

        # KEEP button — if ON, keyboard keeps playing when you leave KEYS tab
        keep_rect = pygame.Rect(theme.SCREEN_WIDTH - 365, top, 60, 24)
        keep_bg = theme.GREEN if self._keys_persistent else theme.BUTTON_BG
        keep_tc = theme.BG if self._keys_persistent else theme.TEXT
        pygame.draw.rect(surface, keep_bg, keep_rect, border_radius=5)
        surface.blit(f_tiny.render("KEEP", True, keep_tc),
                     f_tiny.render("KEEP", True, keep_tc).get_rect(
                         center=keep_rect.center))

        # LATCH button
        latch_rect = pygame.Rect(theme.SCREEN_WIDTH - 300, top, 60, 24)
        latch_bg = theme.YELLOW if self._keys_latch else theme.BUTTON_BG
        latch_tc = theme.BG if self._keys_latch else theme.TEXT
        pygame.draw.rect(surface, latch_bg, latch_rect, border_radius=5)
        surface.blit(f_tiny.render("LATCH", True, latch_tc),
                     f_tiny.render("LATCH", True, latch_tc).get_rect(
                         center=latch_rect.center))

        # Octave shift
        oct_val = kb.octave_shift if kb else 0
        oct_label = f"OCT {oct_val:+d}" if oct_val != 0 else "OCT 0"
        minus_rect = pygame.Rect(theme.SCREEN_WIDTH - 210, top, 40, 24)
        oct_rect = pygame.Rect(theme.SCREEN_WIDTH - 166, top, 56, 24)
        plus_rect = pygame.Rect(theme.SCREEN_WIDTH - 106, top, 40, 24)
        for r in (minus_rect, plus_rect):
            pygame.draw.rect(surface, theme.BUTTON_BG, r, border_radius=5)
        pygame.draw.rect(surface, theme.BG_LIGHTER, oct_rect, border_radius=5)
        surface.blit(f_small.render("-", True, theme.TEXT),
                     f_small.render("-", True, theme.TEXT).get_rect(
                         center=minus_rect.center))
        surface.blit(f_tiny.render(oct_label, True, theme.TEXT_DIM),
                     f_tiny.render(oct_label, True, theme.TEXT_DIM).get_rect(
                         center=oct_rect.center))
        surface.blit(f_small.render("+", True, theme.TEXT),
                     f_small.render("+", True, theme.TEXT).get_rect(
                         center=plus_rect.center))

        # ── Row 2: Pad selector — bank buttons + pad grid ───────────
        pad_row_y = top + 28
        pad_row_h = 28

        # Device-specific bank/pad counts
        if self._device_key == "SP-404MKII":
            bank_count = 10
            pads_per_bank = 16
            bank_labels = [chr(ord("A") + i) for i in range(10)]
        elif self._device_key == "P-6":
            bank_count = 8
            pads_per_bank = 6
            bank_labels = [chr(ord("A") + i) for i in range(8)]
        else:
            bank_count = 4
            pads_per_bank = 16
            bank_labels = [chr(ord("A") + i) for i in range(4)]

        # Bank selector buttons (left side)
        bank_btn_w = min(28, (theme.SCREEN_WIDTH // 3) // bank_count)
        for bi in range(bank_count):
            r = pygame.Rect(10 + bi * (bank_btn_w + 2), pad_row_y,
                            bank_btn_w, pad_row_h)
            active = (bi == self._keys_bank)
            bg = self._device_color if active else theme.BG_LIGHTER
            tc = theme.BG if active else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, r, border_radius=4)
            lbl = f_tiny.render(bank_labels[bi], True, tc)
            surface.blit(lbl, lbl.get_rect(center=r.center))

        # Pad buttons (right side, filling remaining width)
        pad_start_x = 10 + bank_count * (bank_btn_w + 2) + 8
        pad_avail_w = theme.SCREEN_WIDTH - pad_start_x - 10
        pad_btn_w = min(36, (pad_avail_w - (pads_per_bank - 1) * 2) // pads_per_bank)
        for pi in range(pads_per_bank):
            r = pygame.Rect(pad_start_x + pi * (pad_btn_w + 2), pad_row_y,
                            pad_btn_w, pad_row_h)
            active = (pi == self._keys_pad and self._keys_bank == self._keys_bank)
            bg = self._device_color if active else theme.PAD_OFF
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, r, border_radius=4)
            pygame.draw.rect(surface, theme.BORDER, r, 1, border_radius=4)
            lbl = f_tiny.render(str(pi + 1), True, tc)
            surface.blit(lbl, lbl.get_rect(center=r.center))

        # Selected pad label
        bank_letter = bank_labels[self._keys_bank] if self._keys_bank < bank_count else "?"
        sel_text = f"{bank_letter}-{self._keys_pad + 1}"
        if self._keys_selected_name:
            sel_text += f"  {self._keys_selected_name}"
        sel_surf = f_tiny.render(sel_text, True, self._device_color)
        surface.blit(sel_surf, (pad_start_x, pad_row_y - 12))

        # ── Piano display (below pad selector) ──────────────────────
        piano_top = pad_row_y + pad_row_h + 6
        piano_h = self._controls_top + self._controls_h - piano_top - 18
        if self._piano_display:
            new_rect = pygame.Rect(10, piano_top,
                                    theme.SCREEN_WIDTH - 20, piano_h)
            if self._piano_display.rect != new_rect:
                self._piano_display.set_rect(new_rect)
            if kb:
                self._piano_display._active_notes = dict(kb.active_notes)
            self._piano_display.draw(surface)

        # Channel info + workflow hint (bottom)
        bottom_y = self._controls_top + self._controls_h - 14
        if self._device_key == "SP-404MKII":
            line1 = "On SP: select pad > SHIFT + PAD 4 (CHROMATIC) > play keys here"
            surface.blit(f_tiny.render(line1, True, theme.TEXT_DIM),
                         (10, bottom_y))
        elif self._device_key == "P-6":
            line1 = "On P-6: hold PATTERN + GRANULAR > select pad > play keys here"
            surface.blit(f_tiny.render(line1, True, theme.TEXT_DIM),
                         (10, bottom_y))
        elif kb and kb._target_midi:
            ch_text = f"MIDI Ch {kb._target_channel + 1}"
            if kb.enabled:
                ch_text += " · ACTIVE"
            else:
                ch_text += " · TAP KEY TO ENABLE"
            surface.blit(f_tiny.render(ch_text, True, theme.TEXT_DIM),
                         (10, bottom_y))
        else:
            surface.blit(f_tiny.render("No MIDI output target",
                                        True, theme.TEXT_DIM), (10, bottom_y))

    def _handle_keys_clicks(self, mx, my):
        """Handle clicks within the KEYS tab."""
        kb = getattr(self.app, 'chromatic_kb', None)
        if kb is None:
            return
        top = self._controls_top + 2

        # ── Row 1 controls ───────────────────────────────────────────

        # KEEP button
        keep_rect = pygame.Rect(theme.SCREEN_WIDTH - 365, top, 60, 24)
        if keep_rect.collidepoint(mx, my):
            self._keys_persistent = not self._keys_persistent
            return

        # LATCH button
        latch_rect = pygame.Rect(theme.SCREEN_WIDTH - 300, top, 60, 24)
        if latch_rect.collidepoint(mx, my):
            self._keys_latch = not self._keys_latch
            if not self._keys_latch:
                for note in list(self._latched_notes):
                    if kb._target_midi:
                        kb._forward_note_off(note)
                    kb.active_notes.pop(note, None)
                self._latched_notes.clear()
            return

        # Octave shift buttons
        minus_rect = pygame.Rect(theme.SCREEN_WIDTH - 210, top, 40, 24)
        plus_rect = pygame.Rect(theme.SCREEN_WIDTH - 106, top, 40, 24)
        if minus_rect.collidepoint(mx, my):
            kb.octave_shift = max(-3, kb.octave_shift - 1)
            if self._piano_display:
                self._piano_display.shift_octave(-1)
            return
        if plus_rect.collidepoint(mx, my):
            kb.octave_shift = min(3, kb.octave_shift + 1)
            if self._piano_display:
                self._piano_display.shift_octave(1)
            return

        # ── Row 2: pad selector ──────────────────────────────────────
        pad_row_y = top + 28
        pad_row_h = 28

        if self._device_key == "SP-404MKII":
            bank_count, pads_per_bank = 10, 16
        elif self._device_key == "P-6":
            bank_count, pads_per_bank = 8, 6
        else:
            bank_count, pads_per_bank = 4, 16

        bank_btn_w = min(28, (theme.SCREEN_WIDTH // 3) // bank_count)

        # Bank buttons
        for bi in range(bank_count):
            r = pygame.Rect(10 + bi * (bank_btn_w + 2), pad_row_y,
                            bank_btn_w, pad_row_h)
            if r.collidepoint(mx, my):
                self._keys_bank = bi
                # Sync to app-level state so external MIDI controllers
                # (Spectra etc) that use pad.trigger.* hit the right bank
                if hasattr(self.app, "current_bank"):
                    self.app.current_bank[self._device_key] = bi
                return

        # Pad buttons
        pad_start_x = 10 + bank_count * (bank_btn_w + 2) + 8
        pad_avail_w = theme.SCREEN_WIDTH - pad_start_x - 10
        pad_btn_w = min(36, (pad_avail_w - (pads_per_bank - 1) * 2) // pads_per_bank)
        for pi in range(pads_per_bank):
            r = pygame.Rect(pad_start_x + pi * (pad_btn_w + 2), pad_row_y,
                            pad_btn_w, pad_row_h)
            if r.collidepoint(mx, my):
                self._keys_pad = pi
                self._select_chromatic_pad(self._keys_bank, pi)
                return

        # ── Piano touch-to-play ──────────────────────────────────────
        if self._piano_display:
            note = self._piano_display.handle_event_at(mx, my)
            if note >= 0:
                if not kb.enabled:
                    kb.enabled = True

                if self._keys_latch:
                    if note in self._latched_notes:
                        if kb._target_midi:
                            kb._forward_note_off(note)
                        kb.active_notes.pop(note, None)
                        self._latched_notes.discard(note)
                    else:
                        if kb._target_midi:
                            kb._forward_note_on(note, 100)
                        kb.active_notes[note] = 100
                        self._latched_notes.add(note)
                    self._touch_note = -1
                else:
                    if kb._target_midi:
                        kb._forward_note_on(note, 100)
                        kb.active_notes[note] = 100
                        if kb.on_note_on:
                            kb.on_note_on(note, 100)
                    self._touch_note = note

    def _retarget_keys_for_device(self):
        """Point the chromatic keyboard at THIS workspace's device.

        The global focus might be on a different device (e.g. P-6 is
        focused but the user opened SP-404's workspace and switched to
        the KEYS tab). We need to retarget to the workspace device.
        """
        kb = getattr(self.app, 'chromatic_kb', None)
        if kb is None:
            return
        midi = self.app._midi_connections.get(self._device_key)
        if midi is None:
            return

        if self._device_key == "SP-404MKII":
            kb.set_target(midi, 15, pitchbend_mode=False)  # Ch16 chromatic
            print(f"KEYS: retargeted to SP-404 Ch16", flush=True)
        elif self._device_key == "P-6":
            ch_map = getattr(self._device_profile, "midi_channels", None)
            channel = ch_map.get("granular", 3) if ch_map else 3
            kb.set_target(midi, channel, pitchbend_mode=False)
            print(f"KEYS: retargeted to P-6 Ch{channel + 1}", flush=True)
        else:
            channel = getattr(midi, 'ch_sampler', 10)
            kb.set_target(midi, channel, pitchbend_mode=False)

    def _select_chromatic_pad(self, bank_idx: int, pad_idx: int):
        """Select a pad as the active sound for chromatic play.

        SP-404: Sets the pitch-bend-mode pad (bank channel + note).
                Sends a brief trigger so the user hears a preview.
                Subsequent piano keys use pitch bend on that channel.
        P-6:    Triggers the pad on the sampler channel so the granular
                engine picks it up. Ch4 chromatic play continues.
        """
        kb = getattr(self.app, 'chromatic_kb', None)
        if kb is None or kb._target_midi is None:
            return

        midi = kb._target_midi
        import threading

        if self._device_key == "SP-404MKII":
            # SP-404 pad numbering: pads 1-4 are the TOP row, 13-16 the
            # BOTTOM row. But MIDI notes 36-39 map to the BOTTOM row.
            #   note = 36 + (3 - sp_row) * 4 + col
            channel = bank_idx
            sp_row = pad_idx // 4
            col = pad_idx % 4
            midi_row = 3 - sp_row
            note = 36 + midi_row * 4 + col

            # Preview trigger — brief note so user hears the pad.
            # Chromatic target is set on the SP-404 via SHIFT + CHROMATIC.
            midi.send_note_on(note, 80, channel=channel)
            def _off():
                import time
                time.sleep(0.15)
                midi.send_note_off(note, channel=channel)
            threading.Thread(target=_off, daemon=True).start()

            # Reset chromatic-ready flag — user needs to SHIFT+CHROMATIC
            # on the SP-404 after selecting a new pad
            self._sp404_chromatic_ready = False

            bank_letter = chr(ord("A") + bank_idx)
            self._keys_selected_name = f"Bank {bank_letter} Pad {pad_idx + 1}"
            print(f"KEYS: SP-404 preview {bank_letter}-{pad_idx + 1} "
                  f"(Ch{channel + 1} note {note})", flush=True)

        elif self._device_key == "P-6":
            # P-6: trigger the pad on the sampler channel
            channel = midi.ch_sampler
            note = 48 + bank_idx * 6 + pad_idx
            midi.send_note_on(note, 80, channel=channel)
            def _off():
                import time
                time.sleep(0.12)
                midi.send_note_off(note, channel=channel)
            threading.Thread(target=_off, daemon=True).start()
            bank_letter = chr(ord("A") + bank_idx)
            self._keys_selected_name = f"Bank {bank_letter} Pad {pad_idx + 1}"
            print(f"KEYS: P-6 pad → {bank_letter}-{pad_idx + 1} "
                  f"(Ch{channel + 1} note {note})", flush=True)

        else:
            channel = getattr(midi, 'ch_sampler', 10)
            note = 36 + pad_idx
            midi.send_note_on(note, 80, channel=channel)
            def _off():
                import time
                time.sleep(0.12)
                midi.send_note_off(note, channel=channel)
            threading.Thread(target=_off, daemon=True).start()
            self._keys_selected_name = f"Pad {pad_idx + 1}"
