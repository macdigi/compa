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

        # Grid layout
        self._grid_cols = 8
        self._grid_rows = 8
        self._grid_x = 16
        self._grid_y = 46
        self._cell_w = 88
        self._cell_h = 46
        self._cell_gap = 4

        # Chain player
        self.chain_player = ChainPlayer()
        self._chain = Chain(name="New Chain")
        self.chain_player.load(self._chain)

        # Chain editor state
        self._chain_scroll = 0
        self._chain_selected = -1  # selected step index
        self._chains_dir = os.path.join(
            app.config.get("P6_SESSIONS_DIR", "sessions"), "chains")

        # Wire chain player
        if self.app.p6:
            self.chain_player.on_pattern_change = self.app.p6.send_program_change

        # Pi-side step sequencer
        self.sequencer = PiSequencer(num_steps=16)
        if self.app.p6:
            self.sequencer.set_midi_out(self.app.p6)

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
        # Rewire chain player callback in case P-6 reconnected
        if self.app.p6:
            self.chain_player.on_pattern_change = self.app.p6.send_program_change

    def on_exit(self):
        if self.app.router:
            from engine.midi_router import Layer
            self.app.router.layer = Layer.PAD

    def handle_event(self, event):
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
                return

            if self._mode == "grid":
                self._handle_grid_click(mx, my)
            elif self._mode == "chain":
                self._handle_chain_click(mx, my)
            else:
                self._handle_seq_click(mx, my)

        # Scroll chain list
        if self._mode == "chain" and event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 4:
                self._chain_scroll = max(0, self._chain_scroll - 1)
            elif event.button == 5:
                max_scroll = max(0, len(self._chain.steps) - 6)
                self._chain_scroll = min(max_scroll, self._chain_scroll + 1)

    def _handle_grid_click(self, mx, my):
        for i in range(64):
            rect = self._cell_rect(i)
            if rect.collidepoint(mx, my):
                self._select_pattern(i)
                return

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

            # Pattern number (tap to set to current P-6 pattern)
            pat_rect = pygame.Rect(16, row_y + 4, 100, step_h - 10)
            if pat_rect.collidepoint(mx, my):
                if self.app.p6:
                    step.pattern = self.app.p6.state.active_pattern
                self._chain_selected = real_idx
                return

    def _cell_rect(self, index: int) -> pygame.Rect:
        row = index // self._grid_cols
        col = index % self._grid_cols
        x = self._grid_x + col * (self._cell_w + self._cell_gap)
        y = self._grid_y + row * (self._cell_h + self._cell_gap)
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
            self._select_pattern(min(63, current + self._grid_cols))

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
        elif self._mode == "chain":
            self._draw_chain(surface, f_small, f_med, f_mono)
        else:
            self._draw_seq(surface, f_small, f_med, f_mono)

    def _draw_grid(self, surface, f_small, f_mono):
        active = self.app.p6.state.active_pattern if self.app.p6 else 0

        # Panel behind grid area
        grid_panel = pygame.Rect(
            self._grid_x - 6, self._grid_y - 6,
            self._grid_cols * (self._cell_w + self._cell_gap) + 8,
            self._grid_rows * (self._cell_h + self._cell_gap) + 8)
        theme.draw_panel(surface, grid_panel, border=True)

        for i in range(64):
            rect = self._cell_rect(i)
            is_active = (i == active)

            if is_active:
                bg = theme.ACCENT
                text_color = theme.BG
                # Glow effect behind active cell
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

            name = self._pattern_names[i]
            if name:
                name_surf = f_small.render(name[:8], True, text_color)
                nr2 = name_surf.get_rect(centerx=rect.centerx, bottom=rect.bottom - 3)
                surface.blit(name_surf, nr2)

    def _draw_seq(self, surface, f_small, f_med, f_mono):
        """Draw the Pi-side step sequencer grid."""
        seq = self.sequencer
        grid_x = 100
        grid_y = 50
        num_steps = seq.num_steps
        num_pads = seq.num_pads
        cell_w = (theme.SCREEN_WIDTH - grid_x - 20) // num_steps
        cell_h = 50
        pad_gap = 6
        pad_labels = [f"PAD {i+1}" for i in range(num_pads)]

        # Pad labels on left
        for pad in range(num_pads):
            cy = grid_y + pad * (cell_h + pad_gap) + cell_h // 2
            surf = f_small.render(pad_labels[pad], True, theme.TEXT_DIM)
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

                if cell.active and is_current:
                    bg = theme.GREEN
                elif cell.active:
                    bg = theme.ACCENT
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
        surf = f_small.render(
            "Tap grid to toggle notes | Triggers P-6 pads via MIDI (SYNC=USB)",
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
