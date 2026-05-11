"""P-6 Pattern Screen — pattern grid + chain/song mode + step sequencer."""

import json
import os
import pygame
from .. import theme
from engine.p6_chain import Chain, ChainStep, ChainPlayer, save_chain, load_chain, list_chains
from engine.p6_sequencer import PiSequencer


# Bar count options for cycling
BAR_OPTIONS = [1, 2, 4, 8, 16, 32]


class P6PatternScreen:
    """Pattern selection grid + chain mode with song sequencing."""

    def __init__(self, app):
        self.app = app
        self._pattern_names: list[str] = [""] * 64
        self._load_pattern_names()

        # Mode: "grid", "chain", or "seq"
        self._mode = "grid"

        # Help overlay (shown over grid mode when "?" tapped)
        self._show_record_help = False

        # Grid layout — adapts to device pattern count
        self._recalc_grid()

        # Chain player
        self.chain_player = ChainPlayer()
        self._chain = Chain(name="New Chain")
        self.chain_player.load(self._chain)

        # Chain editor state
        self._chain_scroll = 0
        self._chain_selected = -1  # selected step index
        self._chains_dir = os.path.join(
            app.config.get("P6_SESSIONS_DIR", "sessions"), "chains")

        # Wire chain player — focused device + all devices for multi-chain
        if self.app.p6:
            self.chain_player.on_pattern_change = self.app.p6.send_program_change
            self.chain_player._midi_out = self.app.p6
        self.chain_player._device_midi = dict(self.app._midi_connections)

        # Pi-side step sequencer
        self.sequencer = PiSequencer(num_steps=16)
        if self.app.p6:
            self.sequencer.set_midi_out(self.app.p6)

    def _recalc_grid(self):
        """Recalculate grid dimensions based on focused device's pattern count.

        On the 7" touchscreen (800×480) an 8×8 grid of 46px cells doesn't
        fit between the header (y=46) and the nav bar (bottom 52px). We
        use a scrollable viewport: the grid can be larger than the visible
        area, and `_grid_scroll` controls which row is at the top.
        """
        dev = getattr(self.app, "device", None)
        self._pattern_count = getattr(dev, "pattern_count", 64) if dev else 64
        if self._pattern_count <= 0:
            self._pattern_count = 64

        if self._pattern_count <= 16:
            self._grid_cols = 4
            self._cell_w = 170
            self._cell_h = 70
        elif self._pattern_count <= 32:
            self._grid_cols = 8
            self._cell_w = 88
            self._cell_h = 70
        else:
            self._grid_cols = 8
            self._cell_w = 88
            self._cell_h = 46

        self._grid_total_rows = (self._pattern_count + self._grid_cols - 1) // self._grid_cols
        self._grid_x = 16
        self._grid_y = 46
        self._cell_gap = 4

        # How many rows fit in the visible area — leave 60px at bottom
        # for the transport bar (PLAY / STOP / HELP)
        avail_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self._grid_y - 8 - 60
        self._grid_visible_rows = max(1, avail_h // (self._cell_h + self._cell_gap))

        # Scroll offset (in rows)
        if not hasattr(self, "_grid_scroll"):
            self._grid_scroll = 0
        self._grid_scroll = max(0, min(
            self._grid_scroll,
            self._grid_total_rows - self._grid_visible_rows))

    def on_focus_changed(self):
        """Called when the focused device changes — rebuild grid + re-wire MIDI."""
        self._recalc_grid()
        # Re-wire chain player and sequencer to new focused device
        if self.app.p6:
            self.chain_player.on_pattern_change = self.app.p6.send_program_change
            self.chain_player._midi_out = self.app.p6
            self.sequencer.set_midi_out(self.app.p6)
        # Reconfigure sequencer rows for device
        self.sequencer.configure_for_device(self.app.device_name)

    def _load_pattern_names(self):
        path = os.path.join(
            self.app.config.get("P6_SESSIONS_DIR", "sessions"),
            "pattern_names.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    names = json.load(f)
                    if isinstance(names, list) and len(names) == 64:
                        self._pattern_names = names
            except Exception:
                pass

    def _save_pattern_names(self):
        path = os.path.join(
            self.app.config.get("P6_SESSIONS_DIR", "sessions"),
            "pattern_names.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w") as f:
                json.dump(self._pattern_names, f)
        except Exception:
            pass

    def on_enter(self):
        if self.app.router:
            from engine.midi_router import Layer
            self.app.router.layer = Layer.PATTERN
        # Rewire chain player callback in case device reconnected
        if self.app.p6:
            self.chain_player.on_pattern_change = self.app.p6.send_program_change
            self.chain_player._midi_out = self.app.p6
            self.sequencer.set_midi_out(self.app.p6)
        self.chain_player._device_midi = dict(self.app._midi_connections)
        # Configure sequencer rows for current device
        self.sequencer.configure_for_device(self.app.device_name)
        self._recalc_grid()

    def on_exit(self):
        if self.app.router:
            from engine.midi_router import Layer
            self.app.router.layer = Layer.PAD

    def handle_event(self, event):
        # Help overlay swallows all clicks first
        if self._show_record_help:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._show_record_help = False
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Mode toggle buttons (top right)
            grid_btn = pygame.Rect(theme.SCREEN_WIDTH - 340, 8, 90, 30)
            chain_btn = pygame.Rect(theme.SCREEN_WIDTH - 240, 8, 90, 30)
            seq_btn = pygame.Rect(theme.SCREEN_WIDTH - 140, 8, 90, 30)
            if grid_btn.collidepoint(mx, my):
                self._mode = "grid"
                return
            if chain_btn.collidepoint(mx, my):
                self._mode = "chain"
                return
            if seq_btn.collidepoint(mx, my):
                self._mode = "seq"
                self.sequencer.configure_for_device(self.app.device_name)
                return

            if self._mode == "grid":
                # Transport bar buttons checked first (covers row above nav)
                if self._handle_grid_transport_click(mx, my):
                    return
                self._handle_grid_click(mx, my)
            elif self._mode == "chain":
                self._handle_chain_click(mx, my)
            else:
                self._handle_seq_click(mx, my)

        # Mouse wheel / touch-drag scroll
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 4:  # scroll up
                if self._mode == "grid":
                    self._grid_scroll = max(0, self._grid_scroll - 1)
                elif self._mode == "chain":
                    self._chain_scroll = max(0, self._chain_scroll - 1)
            elif event.button == 5:  # scroll down
                if self._mode == "grid":
                    max_scroll = max(0, self._grid_total_rows - self._grid_visible_rows)
                    self._grid_scroll = min(max_scroll, self._grid_scroll + 1)
                elif self._mode == "chain":
                    max_scroll = max(0, len(self._chain.steps) - 6)
                    self._chain_scroll = min(max_scroll, self._chain_scroll + 1)

    def _handle_grid_click(self, mx, my):
        # Only check patterns in visible rows
        first = self._grid_scroll * self._grid_cols
        last = min(self._pattern_count,
                   (self._grid_scroll + self._grid_visible_rows) * self._grid_cols)
        for i in range(first, last):
            rect = self._cell_rect(i)
            if rect.collidepoint(mx, my):
                self._select_pattern(i)
                return

    def _grid_transport_rects(self):
        """Layout for the GRID-mode transport bar (PLAY / STOP / HELP / status).

        Sits above the nav bar, below the pattern grid. Returns the dict so
        both draw and click handler use the same rects without drift."""
        bar_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50
        return {
            "bar_y": bar_y,
            "play": pygame.Rect(16, bar_y, 100, 40),
            "stop": pygame.Rect(124, bar_y, 100, 40),
            "help": pygame.Rect(theme.SCREEN_WIDTH - 116, bar_y, 100, 40),
        }

    def _handle_grid_transport_click(self, mx, my):
        rects = self._grid_transport_rects()
        if rects["play"].collidepoint(mx, my):
            if self.app.p6:
                self.app.p6.send_start()
            return True
        if rects["stop"].collidepoint(mx, my):
            if self.app.p6:
                self.app.p6.send_stop()
            return True
        if rects["help"].collidepoint(mx, my):
            self._show_record_help = True
            return True
        return False

    def _draw_grid_transport(self, surface, f_small, f_med):
        """Transport bar across the bottom: PLAY / STOP / status / HELP."""
        rects = self._grid_transport_rects()

        # Background strip
        bar_rect = pygame.Rect(8, rects["bar_y"] - 4,
                               theme.SCREEN_WIDTH - 16, 48)
        pygame.draw.rect(surface, theme.BG_PANEL, bar_rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, bar_rect, 1, border_radius=6)

        # PLAY
        play_rect = rects["play"]
        pygame.draw.rect(surface, theme.GREEN, play_rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, play_rect, 1, border_radius=6)
        ps = f_med.render("▶ PLAY", True, theme.BG)
        surface.blit(ps, ps.get_rect(center=play_rect.center))

        # STOP
        stop_rect = rects["stop"]
        pygame.draw.rect(surface, theme.BUTTON_BG, stop_rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, stop_rect, 1, border_radius=6)
        ss = f_med.render("■ STOP", True, theme.TEXT_BRIGHT)
        surface.blit(ss, ss.get_rect(center=stop_rect.center))

        # HELP
        help_rect = rects["help"]
        pygame.draw.rect(surface, theme.ACCENT_DIM, help_rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, help_rect, 1, border_radius=6)
        hs = f_med.render("? RECORD", True, theme.TEXT_BRIGHT)
        surface.blit(hs, hs.get_rect(center=help_rect.center))

        # Status text in the middle: active pattern + send hint
        active = self.app.p6.state.active_pattern if self.app.p6 else 0
        name = self._pattern_names[active] if active < len(self._pattern_names) else ""
        if name:
            mid = f"Pattern {active + 1}: {name}"
        else:
            mid = f"Pattern {active + 1}"
        ms = f_small.render(mid, True, theme.TEXT_DIM)
        surface.blit(ms, ms.get_rect(centerx=theme.SCREEN_WIDTH // 2,
                                     centery=play_rect.centery))

        # Tiny "MIDI Start/Stop" hint just below
        hint = f_small.render("PLAY/STOP send MIDI Start/Stop to the SP",
                              True, theme.TEXT_DIM)
        surface.blit(hint, hint.get_rect(centerx=theme.SCREEN_WIDTH // 2,
                                         top=play_rect.bottom + 2))

    def _draw_record_help_overlay(self, surface, f_small, f_med, f_large):
        """How-to-record overlay — tap anywhere to dismiss."""
        # Dim the background
        dim = pygame.Surface((theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT),
                             pygame.SRCALPHA)
        dim.fill((0, 0, 0, 180))
        surface.blit(dim, (0, 0))

        # Card
        card_w = min(720, theme.SCREEN_WIDTH - 40)
        card_h = min(440, theme.SCREEN_HEIGHT - 60)
        card_x = (theme.SCREEN_WIDTH - card_w) // 2
        card_y = (theme.SCREEN_HEIGHT - card_h) // 2
        card = pygame.Rect(card_x, card_y, card_w, card_h)
        pygame.draw.rect(surface, theme.BG_PANEL, card, border_radius=10)
        pygame.draw.rect(surface, theme.ACCENT, card, 2, border_radius=10)

        # Title
        title = f_large.render("How to record a pattern", True, theme.ACCENT)
        surface.blit(title, title.get_rect(centerx=card.centerx,
                                            top=card.top + 18))

        # Body lines
        lines = [
            ("Real-time recording", theme.TEXT_BRIGHT),
            ("  1. Tap an empty pattern slot in the grid to select it.", theme.TEXT),
            ("  2. On the SP-404: press REC, then PLAY to start recording.", theme.TEXT),
            ("  3. Perform on the pads in time with the metronome.", theme.TEXT),
            ("  4. Press REC again to stop — the pattern loops.", theme.TEXT),
            ("", theme.TEXT),
            ("Overdub onto an existing pattern", theme.TEXT_BRIGHT),
            ("  Select the pattern, press REC + PLAY together, then play.", theme.TEXT),
            ("", theme.TEXT),
            ("Step edit", theme.TEXT_BRIGHT),
            ("  On the SP: hold SHIFT + press PATTERN to enter step edit.", theme.TEXT),
            ("  Use VALUE knob to navigate; tap pads to add/remove hits.", theme.TEXT),
            ("", theme.TEXT),
            ("(Tap anywhere to dismiss)", theme.TEXT_DIM),
        ]
        ly = card.top + 60
        for text, color in lines:
            if not text:
                ly += 8
                continue
            font = f_med if color == theme.TEXT_BRIGHT else f_small
            ts = font.render(text, True, color)
            surface.blit(ts, (card.x + 24, ly))
            ly += ts.get_height() + 4

    def _handle_seq_click(self, mx, my):
        """Handle clicks in sequencer mode."""
        # Transport buttons
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 48
        play_rect = pygame.Rect(16, btn_y, 80, 36)
        stop_rect = pygame.Rect(108, btn_y, 80, 36)
        clear_rect = pygame.Rect(200, btn_y, 80, 36)

        if play_rect.collidepoint(mx, my):
            self.sequencer.start()
            if self.app.p6:
                self.app.p6.on_clock_tick = self.sequencer.on_tick
            return
        if stop_rect.collidepoint(mx, my):
            self.sequencer.stop()
            return
        if clear_rect.collidepoint(mx, my):
            self.sequencer.clear_all()
            return

        # Step grid clicks
        grid_x = 100
        grid_y = 50
        cell_w = (theme.SCREEN_WIDTH - grid_x - 20) // self.sequencer.num_steps
        cell_h = 50
        pad_gap = 6

        for pad in range(self.sequencer.num_pads):
            for step in range(self.sequencer.num_steps):
                cx = grid_x + step * cell_w
                cy = grid_y + pad * (cell_h + pad_gap)
                rect = pygame.Rect(cx, cy, cell_w - 2, cell_h)
                if rect.collidepoint(mx, my):
                    self.sequencer.toggle_step(pad, step)
                    return

    def _handle_chain_click(self, mx, my):
        # Chain transport buttons (bottom area)
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 52
        play_rect = pygame.Rect(16, btn_y, 80, 36)
        stop_rect = pygame.Rect(108, btn_y, 80, 36)
        loop_rect = pygame.Rect(200, btn_y, 80, 36)
        sync_rect = pygame.Rect(292, btn_y, 80, 36)
        add_rect = pygame.Rect(theme.SCREEN_WIDTH - 130, btn_y, 110, 36)
        snap_rect = pygame.Rect(theme.SCREEN_WIDTH - 370, btn_y, 100, 36)
        save_rect = pygame.Rect(theme.SCREEN_WIDTH - 250, btn_y, 100, 36)

        if play_rect.collidepoint(mx, my):
            self.chain_player.start()
            # Wire clock tick
            if self.app.p6:
                self._orig_clock_cb = self.app.p6.on_clock_tick
                self.app.p6.on_clock_tick = self.chain_player.on_tick
            return
        if stop_rect.collidepoint(mx, my):
            self.chain_player.stop()
            if self.app.p6 and hasattr(self, '_orig_clock_cb'):
                self.app.p6.on_clock_tick = self._orig_clock_cb
            return
        if loop_rect.collidepoint(mx, my):
            self._chain.loop = not self._chain.loop
            return
        if sync_rect.collidepoint(mx, my):
            self.chain_player.sync_transport = not self.chain_player.sync_transport
            return
        if add_rect.collidepoint(mx, my):
            # Add step with current P-6 pattern
            pat = self.app.p6.state.active_pattern if self.app.p6 else 0
            self._chain.steps.append(ChainStep(pattern=pat, bars=4))
            return
        if save_rect.collidepoint(mx, my):
            save_chain(self._chain, self._chains_dir)
            return
        if snap_rect.collidepoint(mx, my):
            self._snap_fx_to_step()
            return

        # Step list clicks
        list_y = 46
        step_h = 40
        visible = self._chain.steps[self._chain_scroll:self._chain_scroll + 7]
        for i, step in enumerate(visible):
            real_idx = self._chain_scroll + i
            row_y = list_y + i * step_h
            row_rect = pygame.Rect(16, row_y, theme.SCREEN_WIDTH - 32, step_h - 2)

            if not row_rect.collidepoint(mx, my):
                continue

            # Delete button (right edge)
            del_rect = pygame.Rect(row_rect.right - 40, row_y + 4, 36, step_h - 10)
            if del_rect.collidepoint(mx, my):
                self._chain.steps.pop(real_idx)
                return

            # Bar count (tap to cycle)
            bar_rect = pygame.Rect(row_rect.right - 140, row_y + 4, 80, step_h - 10)
            if bar_rect.collidepoint(mx, my):
                current = step.bars
                try:
                    idx = BAR_OPTIONS.index(current)
                    step.bars = BAR_OPTIONS[(idx + 1) % len(BAR_OPTIONS)]
                except ValueError:
                    step.bars = 4
                return

            # Pattern number (tap to set to current device's active pattern)
            pat_rect = pygame.Rect(16, row_y + 4, 100, step_h - 10)
            if pat_rect.collidepoint(mx, my):
                if self.app.p6:
                    step.pattern = self.app.p6.state.active_pattern
                self._chain_selected = real_idx
                return

            # Tap anywhere else on the row — just select it (for SNAP FX)
            self._chain_selected = real_idx
            return

    def _cell_rect(self, index: int) -> pygame.Rect:
        """Screen rect for pattern cell, accounting for scroll offset."""
        row = index // self._grid_cols
        col = index % self._grid_cols
        scroll_offset = getattr(self, "_grid_scroll", 0)
        x = self._grid_x + col * (self._cell_w + self._cell_gap)
        y = self._grid_y + (row - scroll_offset) * (self._cell_h + self._cell_gap)
        return pygame.Rect(x, y, self._cell_w, self._cell_h)

    def _select_pattern(self, index: int):
        if self.app.p6:
            self.app.p6.send_program_change(index)

    def on_up(self):
        if self.app.p6:
            current = self.app.p6.state.active_pattern
            self._select_pattern(max(0, current - self._grid_cols))

    def on_down(self):
        if self.app.p6:
            current = self.app.p6.state.active_pattern
            self._select_pattern(min(self._pattern_count - 1, current + self._grid_cols))

    def _snap_fx_to_step(self):
        """Capture current FX CC state and store in selected chain step."""
        if self._chain_selected < 0 or self._chain_selected >= len(self._chain.steps):
            print("SNAP FX: no step selected — tap a chain step first", flush=True)
            return
        if not self.app.p6:
            print("SNAP FX: no device connected", flush=True)
            return

        step = self._chain.steps[self._chain_selected]
        snapshot = {}
        dev = self.app.device

        if dev and dev.cc_map and dev.midi_channels:
            # Map category keys to MIDI channels
            ch_map = {
                "bus1_fx": dev.midi_channels.get("bus1", 0),
                "bus2_fx": dev.midi_channels.get("bus2", 1),
                "bus3_fx": dev.midi_channels.get("bus3", 2),
                "bus4_fx": dev.midi_channels.get("bus4", 3),
                "input_fx": dev.midi_channels.get("input_fx", 4),
            }
            for cat_key, params in dev.cc_map.items():
                ch = ch_map.get(cat_key)
                if ch is None:
                    continue  # Skip non-FX categories (looper, dj_mode)
                for mcc in params:
                    cc_num = mcc.cc if hasattr(mcc, "cc") else mcc[0]
                    val = self.app.p6.state.cc_values.get(cc_num, 64)
                    snapshot[(ch, cc_num)] = val
        else:
            # P-6 or generic — capture all tracked CCs on auto channel
            ch = self.app.p6.ch_auto
            for cc_num, val in self.app.p6.state.cc_values.items():
                snapshot[(ch, cc_num)] = val

        step.fx_snapshot = snapshot
        count = len(snapshot)
        print(f"SNAP FX: {count} CCs captured for step {self._chain_selected + 1}", flush=True)

    def update(self):
        pass

    def draw(self, surface: pygame.Surface):
        f_large = theme.font("large")
        f_small = theme.font("small")
        f_med = theme.font("medium")
        f_mono = theme.font("mono")

        # Header
        active = self.app.p6.state.active_pattern if self.app.p6 else 0
        active_text = f"Active: {active + 1}"
        name = self._pattern_names[active]
        if name:
            active_text += f" - {name}"
        theme.draw_screen_header(surface, "PATTERNS", active_text)

        # Mode toggle buttons (3 modes)
        modes = [
            (pygame.Rect(theme.SCREEN_WIDTH - 340, 8, 90, 26), "GRID", "grid"),
            (pygame.Rect(theme.SCREEN_WIDTH - 240, 8, 90, 26), "CHAIN", "chain"),
            (pygame.Rect(theme.SCREEN_WIDTH - 140, 8, 90, 26), "SEQ", "seq"),
        ]
        for rect, label, mode in modes:
            bg = theme.ACCENT if self._mode == mode else theme.BUTTON_BG
            tc = theme.BG if self._mode == mode else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        if self._mode == "grid":
            self._draw_grid(surface, f_small, f_mono)
            self._draw_grid_transport(surface, f_small, f_med)
        elif self._mode == "chain":
            self._draw_chain(surface, f_small, f_med, f_mono)
        else:
            self._draw_seq(surface, f_small, f_med, f_mono)

        # Help overlay (only meaningful in grid mode but harmless elsewhere)
        if self._show_record_help:
            self._draw_record_help_overlay(surface, f_small, f_med, f_large)

    def _draw_grid(self, surface, f_small, f_mono):
        active = self.app.p6.state.active_pattern if self.app.p6 else 0

        # Auto-scroll to keep active pattern visible
        active_row = active // self._grid_cols
        if active_row < self._grid_scroll:
            self._grid_scroll = active_row
        elif active_row >= self._grid_scroll + self._grid_visible_rows:
            self._grid_scroll = active_row - self._grid_visible_rows + 1

        # Clamp scroll
        max_scroll = max(0, self._grid_total_rows - self._grid_visible_rows)
        self._grid_scroll = max(0, min(self._grid_scroll, max_scroll))

        # Panel behind visible grid area
        vis_rows = min(self._grid_visible_rows, self._grid_total_rows)
        grid_panel = pygame.Rect(
            self._grid_x - 6, self._grid_y - 6,
            self._grid_cols * (self._cell_w + self._cell_gap) + 8,
            vis_rows * (self._cell_h + self._cell_gap) + 8)
        theme.draw_panel(surface, grid_panel, border=True)

        # Draw visible patterns only
        first = self._grid_scroll * self._grid_cols
        last = min(self._pattern_count,
                   (self._grid_scroll + self._grid_visible_rows) * self._grid_cols)

        for i in range(first, last):
            rect = self._cell_rect(i)
            is_active = (i == active)

            if is_active:
                bg = theme.ACCENT
                text_color = theme.BG
                glow_rect = rect.inflate(6, 6)
                pygame.draw.rect(surface, theme.ACCENT, glow_rect, border_radius=6)
            else:
                bg = theme.PAD_OFF
                text_color = theme.TEXT_DIM

            pygame.draw.rect(surface, bg, rect, border_radius=4)
            pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=4)

            num = f_mono.render(f"{i + 1}", True, text_color)
            nr = num.get_rect(centerx=rect.centerx, top=rect.top + 4)
            surface.blit(num, nr)

            name = self._pattern_names[i] if i < len(self._pattern_names) else ""
            if name:
                name_surf = f_small.render(name[:8], True, text_color)
                nr2 = name_surf.get_rect(centerx=rect.centerx, bottom=rect.bottom - 3)
                surface.blit(name_surf, nr2)

        # Scroll indicators — show arrows when there are patterns above/below
        f_tiny = theme.font("tiny")
        right_x = self._grid_x + self._grid_cols * (self._cell_w + self._cell_gap) + 12
        if self._grid_scroll > 0:
            up = f_small.render("▲", True, theme.ACCENT)
            surface.blit(up, (right_x, self._grid_y))
        if self._grid_scroll < max_scroll:
            down = f_small.render("▼", True, theme.ACCENT)
            surface.blit(down, (right_x, grid_panel.bottom - 24))

        # Row counter
        row_text = f"{self._grid_scroll + 1}-{self._grid_scroll + vis_rows}/{self._grid_total_rows}"
        row_surf = f_tiny.render(row_text, True, theme.TEXT_DIM)
        surface.blit(row_surf, (right_x, grid_panel.centery - 6))

    def _draw_seq(self, surface, f_small, f_med, f_mono):
        """Draw the Pi-side step sequencer grid."""
        seq = self.sequencer
        grid_x = 100
        grid_y = 50
        num_steps = seq.num_steps
        num_pads = seq.num_pads
        cell_w = (theme.SCREEN_WIDTH - grid_x - 20) // num_steps
        avail_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - grid_y - 60
        cell_h = max(24, min(50, (avail_h - (num_pads - 1) * 4) // num_pads))
        pad_gap = 4 if num_pads > 6 else 6
        # Pad labels from row configs (shows special row types)
        for pad in range(num_pads):
            cy = grid_y + pad * (cell_h + pad_gap) + cell_h // 2
            cfg = seq.row_configs[pad] if pad < len(seq.row_configs) else None
            label = cfg.label if cfg else f"PAD {pad+1}"
            label_color = theme.TEXT_DIM
            if cfg and cfg.color != (0, 0, 0):
                label_color = cfg.color
            surf = f_small.render(label, True, label_color)
            surface.blit(surf, (16, cy - 7))

        # Step number labels on top
        for step in range(num_steps):
            cx = grid_x + step * cell_w + cell_w // 2
            color = theme.TEXT_DIM
            # Highlight every 4th step
            if step % 4 == 0:
                color = theme.TEXT
            surf = f_small.render(f"{step+1}", True, color)
            surface.blit(surf, surf.get_rect(centerx=cx, bottom=grid_y - 2))

        # Grid cells
        for pad in range(num_pads):
            for step in range(num_steps):
                cx = grid_x + step * cell_w
                cy = grid_y + pad * (cell_h + pad_gap)
                rect = pygame.Rect(cx, cy, cell_w - 2, cell_h)

                cell = seq.grid[pad][step]
                is_current = (seq.playing and step == seq.current_step)
                cfg = seq.row_configs[pad] if pad < len(seq.row_configs) else None
                row_color = cfg.color if (cfg and cfg.color != (0, 0, 0)) else theme.ACCENT

                if cell.active and is_current:
                    bg = theme.GREEN
                elif cell.active:
                    bg = row_color
                elif is_current:
                    bg = (50, 60, 50)
                elif step % 4 == 0:
                    bg = (35, 35, 42)
                else:
                    bg = theme.PAD_OFF

                pygame.draw.rect(surface, bg, rect, border_radius=3)
                pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=3)

        # Current step indicator line
        if seq.playing:
            line_x = grid_x + seq.current_step * cell_w + cell_w // 2
            pygame.draw.line(surface, theme.GREEN,
                           (line_x, grid_y - 6),
                           (line_x, grid_y + num_pads * (cell_h + pad_gap)),
                           2)

        # Transport buttons
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 48

        buttons = [
            (pygame.Rect(16, btn_y, 80, 36), "PLAY",
             theme.GREEN if seq.playing else theme.BUTTON_BG),
            (pygame.Rect(108, btn_y, 80, 36), "STOP", theme.BUTTON_BG),
            (pygame.Rect(200, btn_y, 80, 36), "CLEAR", theme.BUTTON_BG),
        ]
        for rect, label, bg in buttons:
            tc = theme.BG if bg == theme.GREEN else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=6)
            surf = f_med.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Status
        if seq.playing:
            surf = f_small.render(f"Step {seq.current_step + 1}/{num_steps}",
                                 True, theme.GREEN)
            surface.blit(surf, (300, btn_y + 8))

        # Hint
        dev_name = self.app.device_name
        surf = f_small.render(
            f"Tap grid to toggle notes | Triggers {dev_name} pads via MIDI",
            True, theme.TEXT_DIM)
        surface.blit(surf, (16, btn_y + 40))

    def _draw_chain(self, surface, f_small, f_med, f_mono):
        # Step list
        list_y = 46
        step_h = 40
        visible_count = 7
        steps = self._chain.steps
        visible = steps[self._chain_scroll:self._chain_scroll + visible_count]

        if not steps:
            surf = f_med.render("No steps — tap ADD STEP to begin", True, theme.TEXT_DIM)
            surface.blit(surf, (16, list_y + 20))

        for i, step in enumerate(visible):
            real_idx = self._chain_scroll + i
            row_y = list_y + i * step_h
            row_rect = pygame.Rect(16, row_y, theme.SCREEN_WIDTH - 32, step_h - 2)

            # Highlight current playing step
            is_current = (self.chain_player.playing and
                         real_idx == self.chain_player.step_index)
            is_selected = (real_idx == self._chain_selected)

            if is_current:
                pygame.draw.rect(surface, theme.ACCENT_DIM, row_rect, border_radius=3)
            elif is_selected:
                pygame.draw.rect(surface, (40, 40, 50), row_rect, border_radius=3)

            pygame.draw.rect(surface, theme.BORDER, row_rect, 1, border_radius=3)

            # Step number
            marker = ">" if is_current else " "
            surf = f_mono.render(f"{marker}{real_idx + 1:2d}", True,
                                theme.GREEN if is_current else theme.TEXT_DIM)
            surface.blit(surf, (22, row_y + 10))

            # Pattern number + name
            pat_num = step.pattern + 1
            pat_name = self._pattern_names[step.pattern]
            pat_text = f"[{pat_num:2d}] {pat_name}" if pat_name else f"[{pat_num:2d}]"
            surf = f_med.render(pat_text, True, theme.TEXT)
            surface.blit(surf, (70, row_y + 8))

            # FX snapshot indicator
            if step.fx_snapshot:
                fx_count = len(step.fx_snapshot)
                surf = f_small.render(f"FX:{fx_count}", True, theme.YELLOW)
                surface.blit(surf, (row_rect.right - 210, row_y + 10))

            # Bar count
            bar_rect = pygame.Rect(row_rect.right - 140, row_y + 6, 80, step_h - 14)
            pygame.draw.rect(surface, theme.BUTTON_BG, bar_rect, border_radius=3)
            surf = f_med.render(f"{step.bars} bars", True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=bar_rect.center))

            # Delete button
            del_rect = pygame.Rect(row_rect.right - 44, row_y + 6, 36, step_h - 14)
            pygame.draw.rect(surface, theme.RED, del_rect, border_radius=3)
            surf = f_med.render("X", True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=del_rect.center))

        # ── Chain transport bar ──────────────────────────────────────
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 52

        # Position display
        if self.chain_player.playing:
            pos_text = self.chain_player.position_text
            surf = f_small.render(pos_text, True, theme.GREEN)
            surface.blit(surf, (16, btn_y - 22))

        # Progress bar
        if steps and self.chain_player.playing:
            prog_rect = pygame.Rect(16, btn_y - 8, theme.SCREEN_WIDTH - 32, 6)
            pygame.draw.rect(surface, theme.KNOB_BG, prog_rect, border_radius=2)
            total_bars = self._chain.total_bars()
            if total_bars > 0:
                elapsed = sum(s.bars for s in steps[:self.chain_player.step_index])
                elapsed += self.chain_player.bar_in_step
                frac = elapsed / total_bars
                fill_w = int(prog_rect.width * frac)
                if fill_w > 0:
                    pygame.draw.rect(surface, theme.ACCENT,
                                    (prog_rect.x, prog_rect.y, fill_w, 6),
                                    border_radius=2)

        # Buttons
        buttons = [
            (pygame.Rect(16, btn_y, 80, 36), "PLAY",
             theme.GREEN if self.chain_player.playing else theme.BUTTON_BG),
            (pygame.Rect(108, btn_y, 80, 36), "STOP", theme.BUTTON_BG),
            (pygame.Rect(200, btn_y, 80, 36),
             "LOOP" if self._chain.loop else "ONCE",
             theme.ACCENT if self._chain.loop else theme.BUTTON_BG),
            (pygame.Rect(292, btn_y, 80, 36),
             "SYNC" if self.chain_player.sync_transport else "FREE",
             theme.ACCENT if self.chain_player.sync_transport else theme.BUTTON_BG),
            (pygame.Rect(theme.SCREEN_WIDTH - 370, btn_y, 100, 36), "SNAP FX",
             theme.YELLOW if self._chain_selected >= 0 else theme.BUTTON_BG),
            (pygame.Rect(theme.SCREEN_WIDTH - 250, btn_y, 100, 36), "SAVE", theme.BUTTON_BG),
            (pygame.Rect(theme.SCREEN_WIDTH - 130, btn_y, 110, 36), "ADD STEP", theme.ACCENT),
        ]

        for rect, label, bg in buttons:
            tc = theme.BG if bg in (theme.GREEN, theme.ACCENT) else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=6)
            surf = f_med.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Chain name
        name_surf = f_small.render(f"Chain: {self._chain.name}", True, theme.TEXT_DIM)
        surface.blit(name_surf, (400, btn_y + 40))
