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
from engine.device_profiles import cc_map_to_legacy, build_cc_lookup
from engine.sp404_effects import fx_name_for_tab, fx_count_for_tab
from engine.midi_lfo import MidiLFO, ALL_SHAPES, SHAPE_SINE

# P-6 5-page tab structure — each page holds 8 knobs in a 4×2 grid.
# Mirrors the Push 2 encoder page layout and the device-workspace
# control tab so knob assignments stay in sync across all three
# surfaces. Covers every documented P-6 CC (40 of 40).
_P6_4PAGE_CC_MAP = {
    "granular": [
        (23, "Grain Size",          0, 127, 64),
        (21, "Grains",              0, 127, 0),
        (19, "Head Position",       0, 127, 0),
        (20, "Head Speed",          0, 127, 64),
        (15, "Grain Shape",         0, 127, 0),
        (13, "Detune",              0, 127, 0),
        (25, "Spread",              0, 127, 0),
        (68, "Grain Jitter",        0, 127, 0),
    ],
    "granular_ext": [
        (18, "Fine Tune",           0, 127, 64),
        (76, "Coarse Tune",         0, 127, 64),
        (3,  "Reverse Prob",        0, 127, 0),
        (79, "Start Mode",          0, 127, 0),
        (88, "Sample Select",       0, 127, 0),
        (16, "Grain Time KF",       0, 127, 64),
        (26, "Cutoff KF",           0, 127, 64),
        (78, "Velocity Sens",       0, 127, 64),
    ],
    "filter_env": [
        (74, "Cutoff",              0, 127, 127),
        (71, "Resonance",           0, 127, 0),
        (12, "Filter Type",         0, 127, 0),
        (24, "Env Depth",           0, 127, 64),
        (73, "Attack",              0, 127, 0),
        (75, "Decay",               0, 127, 64),
        (30, "Sustain",             0, 127, 127),
        (72, "Release",             0, 127, 32),
    ],
    "env_mixer": [
        (28, "Amp Switch",          0, 127, 0),
        (29, "Env Mode",            0, 127, 0),
        (77, "Env Time KF",         0, 127, 64),
        (7,  "Level",               0, 127, 100),
        (10, "Pan",                 0, 127, 64),
        (9,  "Auto Pan",            0, 127, 0),
        (14, "Level Jitter",        0, 127, 0),
        (84, "Output Bus",          0, 127, 0),
    ],
    "fx_sends": [
        (85, "Send Delay",          0, 127, 0),
        (86, "Send Reverb",         0, 127, 0),
        (90, "Delay Time",          0, 127, 64),
        (92, "Delay Level",         0, 127, 0),
        (89, "Reverb Time",         0, 127, 64),
        (91, "Reverb Level",        0, 127, 0),
        (17, "Lo-fi Intensity",     0, 127, 0),
        (87, "Lo-fi Switch",        0, 127, 0),
    ],
}

# Default tab config (P-6 fallback) — 5-page layout, 8 knobs each.
_DEFAULT_TABS = ["granular", "granular_ext", "filter_env",
                 "env_mixer", "fx_sends"]
_DEFAULT_LABELS = {
    "granular": "GRANULAR",
    "granular_ext": "GRANULAR EXT",
    "filter_env": "FILTER + ENV",
    "env_mixer": "ENV EXT + MIXER",
    "fx_sends": "FX SENDS",
    # Legacy P-6 keys retained so device profiles that still expose
    # these by name keep rendering with a friendly label. Not in
    # _DEFAULT_TABS anymore.
    "filter": "FILTER", "envelope": "ENVELOPE", "mixer": "MIXER", "fx": "FX",
    "clock": "CLOCK", "lfo": "LFO",
    # SP-404 categories — 5 FX buses + looper + DJ
    "bus1_fx": "BUS 1", "bus2_fx": "BUS 2", "bus3_fx": "BUS 3",
    "bus4_fx": "BUS 4", "input_fx": "INPUT FX",
    "looper": "LOOPER", "dj_mode": "DJ",
    # Force categories
    "transport": "TRANSPORT",
}

# CCs that should be toggle buttons instead of knobs
# Value 0-63 = OFF, 64-127 = ON
_TOGGLE_CCS = {19}  # FX On/Off

# Map CC category → MIDI channel for SP-404 multi-bus routing
_SP404_CATEGORY_CHANNELS = {
    "bus1_fx": 0,      # Ch1
    "bus2_fx": 1,      # Ch2
    "bus3_fx": 2,      # Ch3
    "bus4_fx": 3,      # Ch4
    "input_fx": 4,     # Ch5
    "looper": 0,       # Ch1
    "dj_mode": 0,      # Ch1 (volume on Ch1, crossfade on Ch1)
}

# Module-level fallback lookup (for P-6 when no device profile)
CC_TO_TAB = {}
for _i, _tab in enumerate(_DEFAULT_TABS):
    for _cc, *_ in _P6_4PAGE_CC_MAP.get(_tab, []):
        CC_TO_TAB[_cc] = _i


class P6ControlScreen:
    """Parameter control screen — adapts to connected device."""

    def __init__(self, app):
        self.app = app
        self._current_tab = 0
        self._knobs: dict[str, list[tuple[Knob, int]]] = {}
        self._tab_buttons: list[tuple[pygame.Rect, str]] = []

        # Resolve CC map from device profile or P-6 fallback (4-page).
        dev = getattr(app, "device", None)
        if dev and dev.cc_map:
            self._cc_map = cc_map_to_legacy(dev.cc_map)
            self._cc_lookup = build_cc_lookup(dev.cc_map)
            # Build tabs from device's CC categories + clock
            self._tabs = list(dev.cc_map.keys()) + ["clock"]
        else:
            self._cc_map = _P6_4PAGE_CC_MAP
            self._cc_lookup = CC_LOOKUP
            self._tabs = _DEFAULT_TABS + ["clock"]

        # Build per-instance CC-to-tab lookup
        self._cc_to_tab = {}
        for i, tab in enumerate(self._tabs):
            for cc, *_ in self._cc_map.get(tab, []):
                self._cc_to_tab[cc] = i

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

    def on_focus_changed(self):
        """Called when the focused device changes — rebuild everything."""
        self.rebuild_for_device()

    def rebuild_for_device(self):
        """Re-resolve CC map, tabs, and knobs for the focused device."""
        dev = getattr(self.app, "device", None)
        if dev and dev.cc_map:
            self._cc_map = cc_map_to_legacy(dev.cc_map)
            self._cc_lookup = build_cc_lookup(dev.cc_map)
            self._tabs = list(dev.cc_map.keys()) + ["clock", "lfo"]
        else:
            self._cc_map = _P6_4PAGE_CC_MAP
            self._cc_lookup = CC_LOOKUP
            self._tabs = _DEFAULT_TABS + ["clock", "lfo"]

        self._cc_to_tab = {}
        for i, tab in enumerate(self._tabs):
            for cc, *_ in self._cc_map.get(tab, []):
                self._cc_to_tab[cc] = i

        self._current_tab = 0
        self._last_cc = -1
        self._build_tab_buttons()
        self._build_knobs()

    def _build_tab_buttons(self):
        """Create tab buttons across the top — adapts to device categories."""
        self._tab_buttons = []
        n = len(self._tabs)
        tab_gap = 4
        tab_w = min(118, (theme.SCREEN_WIDTH - 20 - (n - 1) * tab_gap) // n)
        tab_h = 26
        total = n * tab_w + (n - 1) * tab_gap
        start_x = (theme.SCREEN_WIDTH - total) // 2
        y = 44

        for i, tab_key in enumerate(self._tabs):
            rect = pygame.Rect(start_x + i * (tab_w + tab_gap), y, tab_w, tab_h)
            self._tab_buttons.append((rect, tab_key))

    def _build_knobs(self):
        """Create knobs for each tab based on device CC map.

        P-6 uses a 4×2 grid of 8 larger knobs per tab (matches the
        4-page split). SP-404 / device-profile tabs keep the legacy
        7×2 grid (up to 14 knobs) since their bus tabs hold more
        params per page."""
        self._knobs = {}

        content_y = 80
        content_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - content_y - 10

        is_p6_layout = self._cc_map is _P6_4PAGE_CC_MAP
        if is_p6_layout:
            cols = 4
            rows = 2
            knob_r = 38
        else:
            cols = 7
            rows = 2
            knob_r = 26
        cell_w = theme.SCREEN_WIDTH // cols
        cell_h = content_h // rows

        for tab_key in self._tabs:
            params = self._cc_map.get(tab_key, [])
            knob_list = []

            for idx, (cc, name, lo, hi, default) in enumerate(params):
                row = idx // cols
                col = idx % cols
                if row >= rows:
                    break  # Max knobs per tab (8 for P-6, 14 for SP)

                cx = col * cell_w + cell_w // 2
                cy = content_y + row * cell_h + cell_h // 2

                # FX Select knob (CC#83) — limit range and show effect name
                if cc == 83 and tab_key in ("bus1_fx", "bus2_fx", "bus3_fx",
                                             "bus4_fx", "input_fx"):
                    max_fx = fx_count_for_tab(tab_key) - 1
                    tab_ref = tab_key  # capture for closure
                    knob = Knob(
                        center=(cx, cy),
                        radius=knob_r,
                        min_val=0.0,
                        max_val=float(max_fx),
                        value=0.0,
                        label="FX SELECT",
                        int_mode=True,
                        format_func=lambda v, t=tab_ref: fx_name_for_tab(t, int(v)),
                    )
                else:
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
        # Rebuild tabs/knobs for the focused device (fixes stale P-6 tabs
        # when SP-404 is focused but you navigated away and back)
        self.rebuild_for_device()
        # Sync knob values with device state
        self._sync_knobs()
        # Register CC callback for live updates
        if self.app.p6:
            self.app.p6.on_cc = self._on_p6_cc

    def on_exit(self):
        # Unregister CC callback
        if self.app.p6:
            self.app.p6.on_cc = None

    def _on_p6_cc(self, channel: int, cc: int, value: int):
        """Called from MIDI thread when device sends a CC."""
        self._last_cc = cc
        self._last_cc_time = time.monotonic()

        # Auto-switch to the tab containing this CC
        # Check instance lookup first, fall back to module-level
        if cc in self._cc_lookup:
            cat, _ = self._cc_lookup[cc]
            if cat in self._tabs:
                self._current_tab = self._tabs.index(cat)
        elif cc in CC_TO_TAB:
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

        tab_key = self._tabs[self._current_tab]

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

        # Looper tab — big button clicks
        if tab_key == "looper" and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._handle_looper_click(mx, my):
                return

        # DJ Mode tab — crossfader + button clicks
        if tab_key == "dj_mode" and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._handle_dj_click(mx, my):
                return

        # LFO tab — buttons
        if tab_key == "lfo" and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._handle_lfo_click(mx, my):
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

        # Toggle + Knob interaction
        for knob, cc in self._knobs.get(tab_key, []):
            # Toggle buttons (CC#19 FX On/Off)
            if cc in _TOGGLE_CCS and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                btn_rect = pygame.Rect(
                    knob.center[0] - knob.radius,
                    knob.center[1] - knob.radius // 2,
                    knob.radius * 2, knob.radius + 4)
                if btn_rect.collidepoint(event.pos):
                    # Toggle: was ON (>=64) → OFF (0), was OFF (<64) → ON (127)
                    new_val = 0 if knob.value >= 64 else 127
                    knob.value = float(new_val)
                    bus_ch = _SP404_CATEGORY_CHANNELS.get(tab_key)
                    if self.app.p6 and bus_ch is not None:
                        self.app.p6.send_cc(cc, new_val, channel=bus_ch)
                    elif self.app.p6:
                        from engine.p6_midi import CH_GRANULAR, CH_AUTO
                        self.app.p6.send_cc(cc, new_val, channel=CH_AUTO)
                    self._last_cc = cc
                    self._last_cc_time = time.monotonic()
                    return

            if knob.handle_event(event):
                if self.app.p6:
                    val = int(knob.value)
                    # SP-404: each bus tab has its own MIDI channel
                    bus_ch = _SP404_CATEGORY_CHANNELS.get(tab_key)
                    if bus_ch is not None:
                        self.app.p6.send_cc(cc, val, channel=bus_ch)
                    else:
                        # P-6 or unknown: send on auto + granular channels
                        from engine.p6_midi import CH_GRANULAR, CH_AUTO
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
        dev_label = self.app.device_name
        theme.draw_screen_header(surface, "CONTROL", f"{dev_label} parameters")

        # ── Tab buttons (highlight if incoming CC is in that tab) ────
        for i, (rect, tab_key) in enumerate(self._tab_buttons):
            active = (i == self._current_tab)
            # Flash tab if it just received a CC
            flash = (active and self._last_cc in self._cc_to_tab
                     and self._cc_to_tab[self._last_cc] == i
                     and now - self._last_cc_time < 0.3)
            if flash:
                bg = theme.ACCENT_BRIGHT if hasattr(theme, 'ACCENT_BRIGHT') else (255, 220, 50)
            elif active:
                bg = theme.ACCENT
            else:
                bg = theme.BUTTON_BG
            text_color = theme.BG if active or flash else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            label = _DEFAULT_LABELS.get(tab_key, tab_key.upper())
            surf = f_small.render(label, True, text_color)
            lr = surf.get_rect(center=rect.center)
            surface.blit(surf, lr)

        # ── Tab content ──────────────────────────────────────────────
        tab_key = self._tabs[self._current_tab]

        if tab_key == "clock":
            self._draw_clock_tab(surface, f_small, f_med)
            return

        if tab_key == "looper":
            self._draw_looper_tab(surface, f_small, f_med)
            return

        if tab_key == "dj_mode":
            self._draw_dj_tab(surface, f_small, f_med)
            return

        if tab_key == "lfo":
            self._draw_lfo_tab(surface, f_small, f_med)
            return

        # ── Knobs (highlight the last-changed one) ───────────────────
        highlight_active = (now - self._last_cc_time < self._highlight_duration)

        for knob, cc in self._knobs.get(tab_key, []):
            if cc in _TOGGLE_CCS:
                # Draw as toggle button instead of knob
                is_on = knob.value >= 64
                btn_rect = pygame.Rect(
                    knob.center[0] - knob.radius,
                    knob.center[1] - knob.radius // 2,
                    knob.radius * 2, knob.radius + 4)
                bg = theme.GREEN if is_on else theme.BUTTON_BG
                tc = theme.BG if is_on else theme.TEXT_DIM
                pygame.draw.rect(surface, bg, btn_rect, border_radius=8)
                pygame.draw.rect(surface, theme.BORDER, btn_rect, 1, border_radius=8)
                f_btn = theme.font("medium")
                surf = f_btn.render("ON" if is_on else "OFF", True, tc)
                surface.blit(surf, surf.get_rect(center=btn_rect.center))
                # Label above
                f_lbl = theme.font("small")
                surf = f_lbl.render(knob.label, True, theme.TEXT_DIM)
                surface.blit(surf, surf.get_rect(centerx=knob.center[0],
                                                  bottom=btn_rect.top - 4))
            else:
                knob.draw(surface)
            if highlight_active and cc == self._last_cc:
                alpha = max(0, 1.0 - (now - self._last_cc_time) / self._highlight_duration)
                ring_color = (
                    max(0, min(255, int(255 * alpha))),
                    max(0, min(255, int(200 * alpha))),
                    max(0, min(255, int(50 * alpha))),
                )
                if ring_color[0] > 0:
                    pygame.draw.circle(surface, ring_color, knob.center, knob.radius + 4, 3)

        # ── Bus signal flow bar for SP-404 ────────────────────────────
        if tab_key in ("bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"):
            flow_y = 74
            flow_h = 18
            f_flow = theme.font("tiny")
            cx = theme.SCREEN_WIDTH // 2

            # Draw: [PADS] → [B1]+[B2] → [B3] → [B4/MASTER] → [OUT]
            #                 [INPUT] → [INPUT FX] → [mix]
            bus_labels = [
                ("B1", "bus1_fx", 0), ("B2", "bus2_fx", 1),
                ("B3", "bus3_fx", 2), ("B4", "bus4_fx", 3),
                ("IN", "input_fx", 4),
            ]
            # Positions
            positions = {
                "bus1_fx": (cx - 220, flow_y),
                "bus2_fx": (cx - 130, flow_y),
                "bus3_fx": (cx - 10, flow_y),
                "bus4_fx": (cx + 110, flow_y),
                "input_fx": (cx + 230, flow_y),
            }
            for label, key, ch in bus_labels:
                px, py = positions[key]
                w = 60
                rect = pygame.Rect(px, py, w, flow_h)
                is_current = (key == tab_key)
                # Check if FX is enabled on this bus
                fx_on = False
                for knob, kcc in self._knobs.get(key, []):
                    if kcc == 19 and knob.value >= 64:
                        fx_on = True
                        break
                if is_current:
                    bg = theme.ACCENT
                elif fx_on:
                    bg = theme.GREEN
                else:
                    bg = theme.BUTTON_BG
                tc = theme.BG if is_current or fx_on else theme.TEXT_DIM
                pygame.draw.rect(surface, bg, rect, border_radius=3)
                surf = f_flow.render(f"Ch{ch+1} {label}", True, tc)
                surface.blit(surf, surf.get_rect(center=rect.center))

            # Arrows between buses
            arrow_color = theme.TEXT_DIM
            # B1/B2 → B3
            pygame.draw.line(surface, arrow_color,
                            (cx - 220 + 65, flow_y + flow_h // 2),
                            (cx - 10 - 5, flow_y + flow_h // 2))
            pygame.draw.line(surface, arrow_color,
                            (cx - 130 + 65, flow_y + flow_h // 2),
                            (cx - 10 - 5, flow_y + flow_h // 2))
            # B3 → B4
            pygame.draw.line(surface, arrow_color,
                            (cx - 10 + 65, flow_y + flow_h // 2),
                            (cx + 110 - 5, flow_y + flow_h // 2))

        # ── FX name banner for SP-404 bus tabs ────────────────────────
        if tab_key in ("bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"):
            # Find the FX Select knob (CC#83) value
            for knob, cc in self._knobs.get(tab_key, []):
                if cc == 83:
                    fx_val = int(knob.value)
                    fx_label = fx_name_for_tab(tab_key, fx_val)
                    if fx_label and fx_label != "(OFF)":
                        banner_y = 74
                        surf = f_med.render(f"▶ {fx_label}", True, theme.ACCENT)
                        surface.blit(surf, (16, banner_y))
                    break

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
        dev = self.app.device_name
        surf = f_small.render(f"Set {dev} SYNC = USB to receive clock", True, theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(centerx=cx, top=flash_y + 55))

    # ── Looper tab (big buttons for SP-404 looper) ───────────────────

    def _draw_looper_tab(self, surface, f_small, f_med):
        """Draw large performance buttons for the SP-404 looper."""
        f_large = theme.font("large")
        y_start = 90
        btn_w = 180
        btn_h = 70
        gap = 16
        cols = 3
        x_start = (theme.SCREEN_WIDTH - cols * btn_w - (cols - 1) * gap) // 2

        buttons = [
            ("REC", theme.RED, 88, 127),
            ("OVERDUB", theme.YELLOW, 89, 127),
            ("STOP", theme.BUTTON_BG, 85, 127),
            ("DELETE", (120, 40, 40), 87, 127),
            ("UNDO", theme.ACCENT_DIM, 91, 127),
            ("REDO", theme.ACCENT_DIM, 91, 0),
        ]

        self._looper_btn_rects = []
        for i, (label, bg, cc, val) in enumerate(buttons):
            row = i // cols
            col = i % cols
            x = x_start + col * (btn_w + gap)
            y = y_start + row * (btn_h + gap)
            rect = pygame.Rect(x, y, btn_w, btn_h)

            pygame.draw.rect(surface, bg, rect, border_radius=10)
            pygame.draw.rect(surface, theme.BORDER, rect, 2, border_radius=10)

            surf = f_large.render(label, True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=rect.center))

            self._looper_btn_rects.append((rect, cc, val))

        # BPM/Rate knob (CC#90) — draw as regular knob below buttons
        rate_y = y_start + 2 * (btn_h + gap) + 20
        knobs = self._knobs.get("looper", [])
        for knob, cc in knobs:
            if cc == 90:  # BPM/Play Rate
                knob.center = (theme.SCREEN_WIDTH // 2, rate_y + 40)
                knob.draw(surface)

        # Hint
        surf = f_small.render("SP-404 Looper — Ch1 | Tap buttons to trigger",
                             True, theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2,
                                          top=rate_y + 90))

    def _handle_looper_click(self, mx, my):
        """Handle clicks on looper buttons — send momentary CC triggers."""
        if not hasattr(self, "_looper_btn_rects"):
            return False
        for rect, cc, val in self._looper_btn_rects:
            if rect.collidepoint(mx, my):
                if self.app.p6:
                    ch = _SP404_CATEGORY_CHANNELS.get("looper", 0)
                    self.app.p6.send_cc(cc, val, channel=ch)
                return True
        return False

    # ── DJ Mode tab (crossfader + deck controls) ─────────────────────

    def _draw_dj_tab(self, surface, f_small, f_med):
        """Draw DJ mode controls — crossfader + transport buttons."""
        f_large = theme.font("large")
        cx = theme.SCREEN_WIDTH // 2

        # Crossfader — wide horizontal bar
        fader_y = 100
        fader_w = theme.SCREEN_WIDTH - 100
        fader_h = 40
        fader_rect = pygame.Rect(50, fader_y, fader_w, fader_h)
        pygame.draw.rect(surface, theme.KNOB_BG, fader_rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, fader_rect, 1, border_radius=6)

        # Crossfader thumb
        knobs = self._knobs.get("dj_mode", [])
        xfade_val = 64
        for knob, cc in knobs:
            if cc == 8:  # Crossfade
                xfade_val = int(knob.value)
        thumb_x = 50 + int((xfade_val / 127.0) * fader_w)
        thumb_rect = pygame.Rect(thumb_x - 15, fader_y - 5, 30, fader_h + 10)
        pygame.draw.rect(surface, theme.ACCENT, thumb_rect, border_radius=4)

        surf = f_small.render("CH1", True, theme.TEXT_DIM)
        surface.blit(surf, (55, fader_y - 20))
        surf = f_small.render("CH2", True, theme.TEXT_DIM)
        surface.blit(surf, (50 + fader_w - 30, fader_y - 20))
        surf = f_small.render("CROSSFADER", True, theme.ACCENT)
        surface.blit(surf, surf.get_rect(centerx=cx, top=fader_y + fader_h + 6))

        self._dj_fader_rect = fader_rect

        # Volume knobs (per deck)
        vol_y = fader_y + fader_h + 50
        for knob, cc in knobs:
            if cc == 7:  # Volume
                knob.center = (150, vol_y + 50)
                knob.label = "DECK VOL"
                knob.draw(surface)

        # Transport buttons
        btn_y = vol_y + 20
        btn_w = 110
        btn_h = 50
        btn_x = 350

        dj_buttons = [
            ("PLAY", theme.GREEN, 20, 127),
            ("PAUSE", theme.BUTTON_BG, 20, 0),
            ("SYNC", theme.BLUE, 22, 127),
            ("CUE", theme.YELLOW, 23, 127),
            ("BEND+", theme.BUTTON_BG, 24, 127),
            ("BEND-", theme.BUTTON_BG, 25, 127),
        ]

        self._dj_btn_rects = []
        for i, (label, bg, cc, val) in enumerate(dj_buttons):
            row = i // 3
            col = i % 3
            x = btn_x + col * (btn_w + 8)
            y = btn_y + row * (btn_h + 8)
            rect = pygame.Rect(x, y, btn_w, btn_h)

            pygame.draw.rect(surface, bg, rect, border_radius=8)
            pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=8)
            surf = f_med.render(label, True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=rect.center))

            self._dj_btn_rects.append((rect, cc, val))

        # Hint
        hint_y = btn_y + 2 * (btn_h + 8) + 16
        surf = f_small.render("SP-404 DJ Mode — decks on Ch1/Ch2",
                             True, theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(centerx=cx, top=hint_y))

    def _handle_dj_click(self, mx, my):
        """Handle clicks on DJ mode buttons and crossfader."""
        # Crossfader drag
        if hasattr(self, "_dj_fader_rect") and self._dj_fader_rect.collidepoint(mx, my):
            frac = (mx - self._dj_fader_rect.x) / self._dj_fader_rect.width
            val = int(max(0, min(127, frac * 127)))
            if self.app.p6:
                ch = _SP404_CATEGORY_CHANNELS.get("dj_mode", 0)
                self.app.p6.send_cc(8, val, channel=ch)
            # Update knob value
            for knob, cc in self._knobs.get("dj_mode", []):
                if cc == 8:
                    knob.value = float(val)
            return True

        # Buttons
        if hasattr(self, "_dj_btn_rects"):
            for rect, cc, val in self._dj_btn_rects:
                if rect.collidepoint(mx, my):
                    if self.app.p6:
                        ch = _SP404_CATEGORY_CHANNELS.get("dj_mode", 0)
                        self.app.p6.send_cc(cc, val, channel=ch)
                    return True
        return False

    # ── LFO Automation tab ───────────────────────────────────────────

    def _draw_lfo_tab(self, surface, f_small, f_med):
        """Draw LFO automation controls with waveform preview."""
        import math
        import time as _time
        f_large = theme.font("large")
        lfo = self.app.lfo
        running = lfo.is_running

        # ── Top: Start/Stop + status ─────────────────────────────────
        toggle_rect = pygame.Rect(16, 80, 120, 36)
        toggle_bg = theme.RED if running else theme.GREEN
        toggle_label = "STOP" if running else "START"
        pygame.draw.rect(surface, toggle_bg, toggle_rect, border_radius=8)
        surf = f_med.render(toggle_label, True, theme.BG)
        surface.blit(surf, surf.get_rect(center=toggle_rect.center))
        self._lfo_toggle_rect = toggle_rect

        status = f"LFO {'RUNNING' if running else 'STOPPED'} — {len(lfo.targets)} target(s)"
        surf = f_small.render(status, True, theme.GREEN if running else theme.TEXT_DIM)
        surface.blit(surf, (150, 88))

        # ── ADD button ───────────────────────────────────────────────
        add_rect = pygame.Rect(theme.SCREEN_WIDTH - 160, 80, 140, 36)
        pygame.draw.rect(surface, theme.ACCENT, add_rect, border_radius=8)
        surf = f_med.render("+ ADD", True, theme.BG)
        surface.blit(surf, surf.get_rect(center=add_rect.center))
        self._lfo_add_rect = add_rect

        # ── Target rows with inline waveform + editable params ───────
        targets = lfo.targets
        y = 126
        row_h = 56
        self._lfo_target_rects = []
        self._lfo_shape_rects = []
        self._lfo_rate_rects = []

        t_now = _time.monotonic() - getattr(lfo, "_start_time", 0) if running else 0

        for i, t in enumerate(targets):
            row_rect = pygame.Rect(16, y, theme.SCREEN_WIDTH - 32, row_h)
            bg = theme.BG_PANEL if i % 2 == 0 else theme.BG
            pygame.draw.rect(surface, bg, row_rect, border_radius=4)
            if not t.enabled:
                pygame.draw.rect(surface, theme.BORDER, row_rect, 1, border_radius=4)

            # ── Waveform preview (left, 120x40) ─────────────────────
            wave_rect = pygame.Rect(22, y + 6, 120, row_h - 12)
            pygame.draw.rect(surface, (20, 20, 30), wave_rect, border_radius=3)
            # Draw waveform
            points = []
            wave_color = theme.ACCENT if t.enabled else theme.BORDER
            for px in range(wave_rect.width):
                phase = (px / wave_rect.width + (t_now * t.rate_hz if running else 0)) % 1.0
                val = MidiLFO._compute_waveform(t.shape, phase, t)
                py = wave_rect.bottom - int(val * wave_rect.height)
                points.append((wave_rect.x + px, py))
            if len(points) > 1:
                pygame.draw.lines(surface, wave_color, False, points, 2)
            # Current position marker
            if running:
                cur_phase = (t_now * t.rate_hz) % 1.0
                marker_x = wave_rect.x + int(cur_phase * wave_rect.width)
                pygame.draw.line(surface, theme.GREEN,
                                (marker_x, wave_rect.top), (marker_x, wave_rect.bottom), 1)

            # ── Info text (middle) ───────────────────────────────────
            info_x = 152
            surf = f_small.render(f"Ch{t.channel+1}  CC#{t.cc}", True, theme.TEXT)
            surface.blit(surf, (info_x, y + 4))

            # Shape button (tappable to cycle)
            shape_rect = pygame.Rect(info_x, y + 22, 70, 22)
            pygame.draw.rect(surface, theme.BUTTON_BG, shape_rect, border_radius=4)
            pygame.draw.rect(surface, theme.ACCENT, shape_rect, 1, border_radius=4)
            surf = f_small.render(t.shape.upper()[:7], True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=shape_rect.center))
            self._lfo_shape_rects.append((shape_rect, i))

            # Rate (tappable to cycle)
            rate_rect = pygame.Rect(info_x + 80, y + 22, 70, 22)
            pygame.draw.rect(surface, theme.BUTTON_BG, rate_rect, border_radius=4)
            pygame.draw.rect(surface, theme.ACCENT, rate_rect, 1, border_radius=4)
            surf = f_small.render(f"{t.rate_hz:.2f}Hz", True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=rate_rect.center))
            self._lfo_rate_rects.append((rate_rect, i))

            # Range
            range_text = f"{t.min_val}–{t.max_val}"
            surf = f_small.render(range_text, True, theme.TEXT_DIM)
            surface.blit(surf, (info_x + 160, y + 26))

            # ── DEL button (right) ───────────────────────────────────
            del_rect = pygame.Rect(row_rect.right - 50, y + (row_h - 24) // 2, 40, 24)
            pygame.draw.rect(surface, theme.RED, del_rect, border_radius=4)
            surf = f_small.render("DEL", True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=del_rect.center))

            self._lfo_target_rects.append((row_rect, del_rect, i))
            y += row_h + 4

        if not targets:
            surf = f_med.render("No LFO targets — tap + ADD", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=y + 20))
            surf = f_small.render("Modulate FX knobs with sine, saw, random waveforms",
                                 True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=y + 46))

    def _handle_lfo_click(self, mx, my):
        """Handle clicks on LFO tab elements."""
        lfo = self.app.lfo

        # Toggle start/stop
        if hasattr(self, "_lfo_toggle_rect") and self._lfo_toggle_rect.collidepoint(mx, my):
            if lfo.is_running:
                lfo.stop()
            else:
                lfo.set_midi_out(self.app.p6)
                lfo.start()
            return True

        # Shape cycle (tap shape button to change waveform)
        if hasattr(self, "_lfo_shape_rects"):
            for rect, idx in self._lfo_shape_rects:
                if rect.collidepoint(mx, my) and idx < len(lfo._targets):
                    t = lfo._targets[idx]
                    shapes = ALL_SHAPES
                    cur = shapes.index(t.shape) if t.shape in shapes else 0
                    t.shape = shapes[(cur + 1) % len(shapes)]
                    return True

        # Rate cycle (tap rate button to adjust speed)
        if hasattr(self, "_lfo_rate_rects"):
            for rect, idx in self._lfo_rate_rects:
                if rect.collidepoint(mx, my) and idx < len(lfo._targets):
                    t = lfo._targets[idx]
                    rates = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
                    # Find closest rate and cycle to next
                    closest = min(range(len(rates)), key=lambda i: abs(rates[i] - t.rate_hz))
                    t.rate_hz = rates[(closest + 1) % len(rates)]
                    return True

        # Delete target
        if hasattr(self, "_lfo_target_rects"):
            for row_rect, del_rect, idx in self._lfo_target_rects:
                if del_rect.collidepoint(mx, my):
                    was_running = lfo.is_running
                    if was_running:
                        lfo.stop()
                    lfo.remove_target(idx)
                    if was_running and lfo.targets:
                        lfo.set_midi_out(self.app.p6)
                        lfo.start()
                    return True

        # Add target
        if hasattr(self, "_lfo_add_rect") and self._lfo_add_rect.collidepoint(mx, my):
            dev = self.app.device
            ch = 0
            if dev and dev.midi_channels:
                ch = dev.midi_channels.get("bus1", 0)
            num = len(lfo.targets)
            cc_options = [16, 17, 18, 80, 81, 82]
            cc = cc_options[num % len(cc_options)]
            shape = ALL_SHAPES[num % len(ALL_SHAPES)]
            rates = [0.25, 0.5, 1.0, 2.0, 0.1]
            rate = rates[num % len(rates)]

            was_running = lfo.is_running
            if was_running:
                lfo.stop()
            lfo.add_target(channel=ch, cc=cc, shape=shape, rate_hz=rate)
            if was_running:
                lfo.set_midi_out(self.app.p6)
                lfo.start()
            return True

        return False
