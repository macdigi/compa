"""P-6 Control Screen — live parameter mirror + editor.

Shows all P-6 CC parameters as knobs that update in real-time
when you turn knobs on the P-6. Also sends CCs back when you
drag knobs on the Pi. Auto-switches to the tab of the parameter
being changed on the P-6.
"""

import os
import time
import pygame
from .. import theme
from ..components.knob import Knob
from ..components.modal import Modal
from engine.p6_midi import P6_CC_MAP, CC_LOOKUP
from engine.midi_clock import MidiClockSender
from engine.p6_presets import PresetManager, GranularPreset, GRANULAR_CCS

# Tab configuration
TABS = ["granular", "filter", "envelope", "mixer", "fx", "clock"]
TAB_LABELS = {
    "granular": "GRANULAR",
    "filter": "FILTER",
    "envelope": "ENVELOPE",
    "mixer": "MIXER",
    "fx": "FX",
    "clock": "CLOCK",
}

# Reverse lookup: cc_number -> tab index
CC_TO_TAB = {}
for _i, _tab in enumerate(TABS):
    for _cc, *_ in P6_CC_MAP.get(_tab, []):
        CC_TO_TAB[_cc] = _i


class P6ControlScreen:
    """Full-screen parameter control with live P-6 mirroring."""

    def __init__(self, app):
        self.app = app
        self._current_tab = 0
        self._knobs: dict[str, list[tuple[Knob, int]]] = {}  # tab -> [(Knob, cc_num)]
        self._tab_buttons: list[tuple[pygame.Rect, str]] = []

        # Last changed CC highlight
        self._last_cc = -1
        self._last_cc_time = 0.0
        self._highlight_duration = 1.5

        # Clock sender (created lazily when MIDI out is available)
        self._clock: MidiClockSender | None = None
        self._beat_flash = 0

        # Granular presets
        presets_dir = os.path.join(
            app.config.get("P6_SESSIONS_DIR", "sessions"), "presets")
        self._preset_mgr = PresetManager(presets_dir)
        self._save_modal = Modal("Save Preset", "Name this preset:",
                                 buttons=["SAVE", "CANCEL"], width=400, height=190)
        self._load_list: list[str] = []
        self._load_scroll = 0
        self._show_load = False
        self._preset_flash = 0

        self._build_tab_buttons()
        self._build_knobs()

    def _build_tab_buttons(self):
        """Create tab buttons across the top."""
        self._tab_buttons = []
        tab_w = 118
        tab_h = 30
        tab_gap = 5
        total = len(TABS) * tab_w + (len(TABS) - 1) * tab_gap
        start_x = (theme.SCREEN_WIDTH - total) // 2
        y = 44

        for i, tab_key in enumerate(TABS):
            rect = pygame.Rect(start_x + i * (tab_w + tab_gap), y, tab_w, tab_h)
            self._tab_buttons.append((rect, tab_key))

    def _build_knobs(self):
        """Create knobs for each tab based on P6_CC_MAP."""
        self._knobs = {}

        content_y = 80
        content_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - content_y - 10
        knob_r = 26
        cols = 7
        rows = 2
        cell_w = theme.SCREEN_WIDTH // cols
        cell_h = content_h // rows

        for tab_key in TABS:
            params = P6_CC_MAP.get(tab_key, [])
            knob_list = []

            for idx, (cc, name, lo, hi, default) in enumerate(params):
                row = idx // cols
                col = idx % cols
                if row >= rows:
                    break  # Max 14 knobs per tab

                cx = col * cell_w + cell_w // 2
                cy = content_y + row * cell_h + cell_h // 2

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
                knob_list.append((knob, cc))

            self._knobs[tab_key] = knob_list

    def on_enter(self):
        # Sync knob values with P-6 state
        self._sync_knobs()
        # Register CC callback for live updates
        if self.app.p6:
            self.app.p6.on_cc = self._on_p6_cc

    def on_exit(self):
        # Unregister CC callback
        if self.app.p6:
            self.app.p6.on_cc = None

    def _on_p6_cc(self, channel: int, cc: int, value: int):
        """Called from MIDI thread when P-6 sends a CC."""
        self._last_cc = cc
        self._last_cc_time = time.monotonic()

        # Auto-switch to the tab containing this CC
        if cc in CC_TO_TAB:
            self._current_tab = CC_TO_TAB[cc]

    def _sync_knobs(self):
        """Sync all knob positions from P-6 state."""
        if not self.app.p6:
            return
        for tab_key, knob_list in self._knobs.items():
            for knob, cc in knob_list:
                knob.value = float(self.app.p6.get_cc_value(cc))

    def _ensure_clock(self):
        """Create clock sender lazily when P-6 MIDI out is available."""
        if self._clock is None and self.app.p6 and self.app.p6._out:
            self._clock = MidiClockSender(self.app.p6._out)
            self._clock.on_beat = self._on_beat

    def _on_beat(self):
        """Called from clock thread on each beat."""
        self._beat_flash = 6  # ~200ms at 30fps

    def handle_event(self, event):
        # Save modal
        if self._save_modal.visible:
            result = self._save_modal.handle_event(event)
            if result == "SAVE" and self.app.p6:
                name = self._save_modal.input_text.strip() or "Preset"
                preset = self._preset_mgr.capture_from_state(
                    name, self.app.p6.state.cc_values)
                self._preset_mgr.save_preset(preset)
                self._preset_flash = 30
            return

        # Load list overlay
        if self._show_load and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            # Close button
            close_rect = pygame.Rect(580, 50, 60, 28)
            if close_rect.collidepoint(mx, my):
                self._show_load = False
                return
            # Preset list clicks
            list_y = 90
            for i, name in enumerate(self._load_list[self._load_scroll:self._load_scroll + 8]):
                row = pygame.Rect(220, list_y + i * 32, 360, 30)
                if row.collidepoint(mx, my):
                    preset = self._preset_mgr.load_preset(name)
                    if preset and self.app.p6:
                        self._preset_mgr.apply_preset(preset, self.app.p6)
                        self._sync_knobs()
                        self._preset_flash = 30
                    self._show_load = False
                    return
            # Scroll
            if event.button == 4:
                self._load_scroll = max(0, self._load_scroll - 1)
            elif event.button == 5:
                self._load_scroll = min(max(0, len(self._load_list) - 8), self._load_scroll + 1)
            return

        # Tab clicks
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, (rect, tab_key) in enumerate(self._tab_buttons):
                if rect.collidepoint(event.pos):
                    self._current_tab = i
                    return

        tab_key = TABS[self._current_tab]

        # Granular tab — preset buttons
        if tab_key == "granular" and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            content_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT
            save_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, content_bottom - 36, 100, 30)
            load_rect = pygame.Rect(theme.SCREEN_WIDTH - 110, content_bottom - 36, 100, 30)
            if save_rect.collidepoint(mx, my):
                self._save_modal.show(input_mode=True, default_text="")
                return
            if load_rect.collidepoint(mx, my):
                self._load_list = self._preset_mgr.list_presets()
                self._load_scroll = 0
                self._show_load = True
                return

        # Clock tab handling
        if tab_key == "clock" and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._ensure_clock()
            mx, my = event.pos
            # Tap tempo button
            tap_rect = pygame.Rect(280, 120, 240, 60)
            if tap_rect.collidepoint(mx, my) and self._clock:
                self._clock.tap()
                return
            # Start/stop
            start_rect = pygame.Rect(280, 290, 240, 50)
            if start_rect.collidepoint(mx, my) and self._clock:
                if self._clock.running:
                    self._clock.stop()
                else:
                    self._clock.start()
                return
            # Nudge buttons
            nudges = [(-10, 100), (-1, 190), (-0.1, 280), (0.1, 440), (1, 530), (10, 620)]
            for delta, bx in nudges:
                rect = pygame.Rect(bx, 210, 75, 40)
                if rect.collidepoint(mx, my) and self._clock:
                    self._clock.nudge(delta)
                    return
            return

        # Knob interaction — sends CC on both auto + granular channels
        for knob, cc in self._knobs.get(tab_key, []):
            if knob.handle_event(event):
                if self.app.p6:
                    from engine.p6_midi import CH_GRANULAR, CH_AUTO
                    val = int(knob.value)
                    self.app.p6.send_cc(cc, val, channel=CH_AUTO)
                    self.app.p6.send_cc(cc, val, channel=CH_GRANULAR)
                    self._last_cc = cc
                    self._last_cc_time = time.monotonic()
                if self.app.router:
                    self.app.router.focused_cc = cc

    def update(self):
        # Continuously sync knob values from P-6 state (live mirror)
        self._sync_knobs()
        if self._beat_flash > 0:
            self._beat_flash -= 1

    def draw(self, surface: pygame.Surface):
        f_small = theme.font("small")
        f_med = theme.font("medium")
        now = time.monotonic()

        # ── Header ──────────────────────────────────────────────────
        theme.draw_screen_header(surface, "CONTROL", "P-6 parameters")

        # ── Tab buttons (highlight if incoming CC is in that tab) ────
        for i, (rect, tab_key) in enumerate(self._tab_buttons):
            active = (i == self._current_tab)
            # Flash tab if it just received a CC
            flash = (active and self._last_cc in CC_TO_TAB
                     and CC_TO_TAB[self._last_cc] == i
                     and now - self._last_cc_time < 0.3)
            if flash:
                bg = theme.ACCENT_BRIGHT if hasattr(theme, 'ACCENT_BRIGHT') else (255, 220, 50)
            elif active:
                bg = theme.ACCENT
            else:
                bg = theme.BUTTON_BG
            text_color = theme.BG if active or flash else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            label = TAB_LABELS[tab_key]
            surf = f_small.render(label, True, text_color)
            lr = surf.get_rect(center=rect.center)
            surface.blit(surf, lr)

        # ── Tab content ──────────────────────────────────────────────
        tab_key = TABS[self._current_tab]

        if tab_key == "clock":
            self._draw_clock_tab(surface, f_small, f_med)
            return

        # ── Knobs (highlight the last-changed one) ───────────────────
        highlight_active = (now - self._last_cc_time < self._highlight_duration)

        for knob, cc in self._knobs.get(tab_key, []):
            knob.draw(surface)
            if highlight_active and cc == self._last_cc:
                alpha = max(0, 1.0 - (now - self._last_cc_time) / self._highlight_duration)
                ring_color = (
                    int(255 * alpha),
                    int(200 * alpha),
                    int(50 * alpha),
                )
                pygame.draw.circle(surface, ring_color, knob.center, knob.radius + 4, 3)

        # ── Active parameter display bar ─────────────────────────────
        bar_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 24
        if highlight_active and self._last_cc >= 0:
            cat, name = CC_LOOKUP.get(self._last_cc, ("", f"CC {self._last_cc}"))
            val = self.app.p6.get_cc_value(self._last_cc) if self.app.p6 else 0
            text = f"  {name.upper()} = {val}  "
            surf = f_med.render(text, True, theme.BG)
            rect = surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, centery=bar_y)
            # Background pill
            pill = rect.inflate(16, 6)
            pygame.draw.rect(surface, theme.ACCENT, pill, border_radius=10)
            surface.blit(surf, rect)
        elif self.app.router:
            cc = self.app.router.focused_cc
            cat, name = CC_LOOKUP.get(cc, ("", f"CC {cc}"))
            val = self.app.p6.get_cc_value(cc) if self.app.p6 else 0
            text = f"STRIP: {name} = {val}"
            surf = f_small.render(text, True, theme.ACCENT)
            surface.blit(surf, (8, bar_y))

        # ── Granular tab: preset buttons ─────────────────────────────
        if tab_key == "granular":
            content_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT
            save_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, content_bottom - 36, 100, 30)
            load_rect = pygame.Rect(theme.SCREEN_WIDTH - 110, content_bottom - 36, 100, 30)

            s_bg = theme.GREEN if self._preset_flash > 0 else theme.BUTTON_BG
            s_text = "SAVED!" if self._preset_flash > 0 else "SAVE"
            pygame.draw.rect(surface, s_bg, save_rect, border_radius=4)
            surf = f_small.render(s_text, True, theme.BG if self._preset_flash > 0 else theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=save_rect.center))

            pygame.draw.rect(surface, theme.ACCENT, load_rect, border_radius=4)
            surf = f_small.render("LOAD", True, theme.BG)
            surface.blit(surf, surf.get_rect(center=load_rect.center))

            if self._preset_flash > 0:
                self._preset_flash -= 1

            # Hint
            surf = f_small.render("Presets: save/load granular settings", True, theme.TEXT_DIM)
            surface.blit(surf, (8, content_bottom - 34))

        # ── Load preset overlay ──────────────────────────────────────
        if self._show_load:
            overlay = pygame.Surface((theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            surface.blit(overlay, (0, 0))

            panel = pygame.Rect(200, 44, 440, 360)
            pygame.draw.rect(surface, theme.MODAL_BG if hasattr(theme, 'MODAL_BG') else (35, 35, 45), panel, border_radius=8)
            pygame.draw.rect(surface, theme.BORDER, panel, 2, border_radius=8)

            title_surf = f_med.render("Load Granular Preset", True, theme.ACCENT)
            surface.blit(title_surf, (220, 52))

            close_rect = pygame.Rect(580, 50, 50, 28)
            pygame.draw.rect(surface, theme.BUTTON_BG, close_rect, border_radius=4)
            surf = f_small.render("X", True, theme.RED)
            surface.blit(surf, surf.get_rect(center=close_rect.center))

            if not self._load_list:
                surf = f_small.render("No presets saved yet", True, theme.TEXT_DIM)
                surface.blit(surf, (220, 100))
            else:
                list_y = 90
                visible = self._load_list[self._load_scroll:self._load_scroll + 8]
                for i, name in enumerate(visible):
                    row = pygame.Rect(220, list_y + i * 32, 360, 30)
                    pygame.draw.rect(surface, theme.BUTTON_BG, row, border_radius=4)
                    surf = f_med.render(name, True, theme.TEXT)
                    surface.blit(surf, (230, list_y + i * 32 + 5))

        # ── Save modal ──────────────────────────────────────────────
        self._save_modal.draw(surface)

    def _draw_clock_tab(self, surface, f_small, f_med):
        """Draw the CLOCK / tap tempo tab."""
        f_title = theme.font("title")
        f_large = theme.font("large")
        self._ensure_clock()

        bpm = self._clock.bpm if self._clock else 120.0
        running = self._clock.running if self._clock else False
        cx = 400  # Center x

        # Large BPM display
        bpm_text = f"{bpm:.1f}"
        surf = f_title.render(bpm_text, True, theme.TEXT)
        bpm_rect = surf.get_rect(centerx=cx, centery=80)
        surface.blit(surf, bpm_rect)
        label = f_small.render("BPM", True, theme.TEXT_DIM)
        surface.blit(label, label.get_rect(centerx=cx, top=bpm_rect.bottom + 4))

        # TAP TEMPO button
        tap_rect = pygame.Rect(cx - 120, 120, 240, 60)
        pygame.draw.rect(surface, theme.ACCENT, tap_rect, border_radius=10)
        surf = f_large.render("TAP TEMPO", True, theme.BG)
        surface.blit(surf, surf.get_rect(center=tap_rect.center))

        # Nudge buttons row
        nudge_y = 210
        nudge_data = [
            ("-10", -10, 100), ("-1", -1, 190), ("-0.1", -0.1, 280),
            ("+0.1", 0.1, 440), ("+1", 1, 530), ("+10", 10, 620),
        ]
        for label_text, delta, bx in nudge_data:
            rect = pygame.Rect(bx, nudge_y, 75, 40)
            pygame.draw.rect(surface, theme.BUTTON_BG, rect, border_radius=6)
            surf = f_med.render(label_text, True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # BPM display between nudge buttons
        surf = f_large.render(f"{bpm:.1f}", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(centerx=cx, centery=nudge_y + 20))

        # Start/Stop button
        start_rect = pygame.Rect(cx - 120, 290, 240, 50)
        if running:
            s_bg = theme.GREEN
            s_text = "STOP CLOCK"
        else:
            s_bg = theme.BUTTON_BG
            s_text = "START CLOCK"
        pygame.draw.rect(surface, s_bg, start_rect, border_radius=10)
        s_tc = theme.BG if running else theme.TEXT
        surf = f_med.render(s_text, True, s_tc)
        surface.blit(surf, surf.get_rect(center=start_rect.center))

        # Beat flash indicator
        flash_y = 390
        if self._beat_flash > 0:
            flash_size = 24 + self._beat_flash * 3
            pygame.draw.circle(surface, theme.GREEN, (cx, flash_y), flash_size)
        else:
            pygame.draw.circle(surface, theme.BORDER, (cx, flash_y), 24, 2)

        # Status
        if running:
            status = "Clock running — sending to P-6"
            status_color = theme.GREEN
        else:
            status = "Clock stopped"
            status_color = theme.TEXT_DIM
        surf = f_small.render(status, True, status_color)
        surface.blit(surf, surf.get_rect(centerx=cx, top=flash_y + 35))

        # Hint
        surf = f_small.render("Set P-6 SYNC = USB to receive clock", True, theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(centerx=cx, top=flash_y + 55))
