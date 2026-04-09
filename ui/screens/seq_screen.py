"""Sequencer screen — step-based pattern editor with transport controls."""

import pygame
import time
from .. import theme
from ..components.button import Button


class SeqScreen:
    """16-step x 16-pad pattern sequencer with transport and pattern management."""

    GRID_COLS = 16  # steps
    GRID_ROWS = 16  # pads
    NUM_PATTERNS = 16

    def __init__(self, app):
        self.app = app

        self.area_y = theme.HEADER_HEIGHT
        self.area_h = theme.SCREEN_HEIGHT - theme.HEADER_HEIGHT - theme.NAV_HEIGHT

        # Sequencer state
        self.bpm = 120.0
        self.swing = 0       # 0-100%
        self.step_count = 16
        self.current_pattern = 0
        self.current_step = -1  # -1 = stopped
        self.playing = False
        self.recording = False
        self.overdub = False

        # Pattern data: [pattern][pad][step] = velocity (0.0 = off)
        self.patterns = [
            [[0.0] * self.GRID_COLS for _ in range(self.GRID_ROWS)]
            for _ in range(self.NUM_PATTERNS)
        ]

        # Step cursor for editing
        self.selected_step = -1
        self.selected_pad = -1

        # Clipboard for copy/paste
        self._clipboard = None

        # Timing
        self._last_step_time = 0.0

        # --- Layout calculations ---
        # Right panel width
        self.right_panel_w = 160
        self.grid_area_w = theme.SCREEN_WIDTH - self.right_panel_w

        # Top bar height (BPM, swing, etc.)
        self.top_bar_h = 34
        self.top_bar_y = self.area_y

        # Grid area
        grid_top = self.top_bar_y + self.top_bar_h + 2
        # Bottom bar for position indicator
        self.bottom_bar_h = 18
        grid_bottom = self.area_y + self.area_h - self.bottom_bar_h
        grid_h = grid_bottom - grid_top

        self.grid_x = 36  # leave room for pad labels
        self.grid_y = grid_top
        self.cell_w = (self.grid_area_w - self.grid_x - 4) // self.GRID_COLS
        self.cell_h = grid_h // self.GRID_ROWS
        self.grid_w = self.cell_w * self.GRID_COLS
        self.grid_h = self.cell_h * self.GRID_ROWS

        # Top bar controls — BPM +/- buttons
        bpm_x = 80
        btn_h = 28
        self.bpm_minus = Button(
            pygame.Rect(bpm_x, self.top_bar_y + 3, 30, btn_h), "-",
            font_name="medium",
        )
        self.bpm_plus = Button(
            pygame.Rect(bpm_x + 80, self.top_bar_y + 3, 30, btn_h), "+",
            font_name="medium",
        )
        # Swing +/-
        swing_x = 250
        self.swing_minus = Button(
            pygame.Rect(swing_x, self.top_bar_y + 3, 30, btn_h), "-",
            font_name="medium",
        )
        self.swing_plus = Button(
            pygame.Rect(swing_x + 80, self.top_bar_y + 3, 30, btn_h), "+",
            font_name="medium",
        )

        # Right panel — transport buttons
        rp_x = self.grid_area_w + 8
        rp_w = self.right_panel_w - 16
        ty = self.area_y + 8
        btn_h_transport = 34
        gap = 6

        self.play_btn = Button(
            pygame.Rect(rp_x, ty, rp_w, btn_h_transport), "PLAY",
            color=theme.BUTTON_BG, active_color=theme.GREEN,
            font_name="medium", toggle=True,
        )
        ty += btn_h_transport + gap
        self.stop_btn = Button(
            pygame.Rect(rp_x, ty, rp_w, btn_h_transport), "STOP",
            color=theme.BUTTON_BG, font_name="medium",
        )
        ty += btn_h_transport + gap
        self.rec_btn = Button(
            pygame.Rect(rp_x, ty, rp_w, btn_h_transport), "REC",
            color=theme.BUTTON_BG, active_color=theme.RED,
            font_name="medium", toggle=True,
        )
        ty += btn_h_transport + gap
        self.overdub_btn = Button(
            pygame.Rect(rp_x, ty, rp_w, btn_h_transport), "OVERDUB",
            color=theme.BUTTON_BG, active_color=theme.YELLOW,
            font_name="small", toggle=True,
        )
        ty += btn_h_transport + gap + 8

        # Pattern select grid (4x4)
        pat_label_y = ty
        ty += 18
        pat_btn_size = (rp_w - 12) // 4
        self.pattern_buttons: list[Button] = []
        for row in range(4):
            for col in range(4):
                idx = row * 4 + col
                bx = rp_x + col * (pat_btn_size + 2)
                by = ty + row * (pat_btn_size + 2)
                btn = Button(
                    pygame.Rect(bx, by, pat_btn_size, pat_btn_size),
                    str(idx + 1),
                    color=theme.BUTTON_BG,
                    active_color=theme.ACCENT,
                    font_name="small",
                )
                if idx == 0:
                    btn.active = True
                self.pattern_buttons.append(btn)
        self.pat_label_y = pat_label_y

        # Utility buttons below pattern grid
        util_y = ty + 4 * (pat_btn_size + 2) + 8
        util_w = rp_w // 2 - 2
        self.quantize_btn = Button(
            pygame.Rect(rp_x, util_y, util_w, 28), "QUANT",
            font_name="small",
        )
        self.clear_btn = Button(
            pygame.Rect(rp_x + util_w + 4, util_y, util_w, 28), "CLEAR",
            color=theme.RED, font_name="small",
        )
        util_y += 34
        self.copy_btn = Button(
            pygame.Rect(rp_x, util_y, util_w, 28), "COPY",
            font_name="small",
        )
        self.paste_btn = Button(
            pygame.Rect(rp_x + util_w + 4, util_y, util_w, 28), "PASTE",
            font_name="small",
        )

    def on_enter(self):
        pass

    def on_exit(self):
        self.playing = False
        self.recording = False
        self.current_step = -1

    @property
    def _pattern(self):
        """Current pattern data."""
        return self.patterns[self.current_pattern]

    def _step_duration(self) -> float:
        """Duration of one step in seconds."""
        return 60.0 / self.bpm / 4.0  # 16th notes

    def update(self):
        """Advance sequencer if playing."""
        if not self.playing:
            return

        now = time.monotonic()
        dur = self._step_duration()

        # Apply swing to even steps
        actual_dur = dur
        if self.current_step >= 0 and self.current_step % 2 == 0 and self.swing > 0:
            actual_dur = dur * (1.0 + self.swing / 200.0)

        if now - self._last_step_time >= actual_dur:
            self._last_step_time = now
            self.current_step = (self.current_step + 1) % self.step_count

            # Trigger pads for this step
            pattern = self._pattern
            for pad_idx in range(self.GRID_ROWS):
                vel = pattern[pad_idx][self.current_step]
                if vel > 0:
                    pads = self.app.pad_bank.current_pads
                    if pad_idx < len(pads) and pads[pad_idx].has_sample:
                        if hasattr(self.app, 'audio_engine'):
                            self.app.audio_engine.trigger_pad(pads[pad_idx], velocity=vel)

    def draw(self, surface: pygame.Surface):
        """Draw the sequencer screen."""
        # Header
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, theme.HEADER_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)
        f = theme.font("large")
        title = f.render("SEQUENCER", True, theme.TEXT_BRIGHT)
        surface.blit(title, (12, 6))
        pygame.draw.line(surface, theme.BORDER,
                        (0, theme.HEADER_HEIGHT),
                        (theme.SCREEN_WIDTH, theme.HEADER_HEIGHT))

        # Top bar
        self._draw_top_bar(surface)

        # Step grid
        self._draw_grid(surface)

        # Bottom position indicator
        self._draw_position_bar(surface)

        # Right panel
        self._draw_right_panel(surface)

    def _draw_top_bar(self, surface: pygame.Surface):
        """Draw BPM, swing, step count, pattern number."""
        f_sm = theme.font("small")
        f_med = theme.font("medium")
        y = self.top_bar_y

        # Background
        bar_rect = pygame.Rect(0, y, self.grid_area_w, self.top_bar_h)
        pygame.draw.rect(surface, theme.BG_PANEL, bar_rect)

        # BPM
        bpm_label = f_sm.render("BPM", True, theme.TEXT_DIM)
        surface.blit(bpm_label, (12, y + 4))
        bpm_val = f_med.render(f"{self.bpm:.0f}", True, theme.TEXT_BRIGHT)
        surface.blit(bpm_val, (112, y + 6))
        self.bpm_minus.draw(surface)
        self.bpm_plus.draw(surface)

        # Swing
        sw_label = f_sm.render("SWING", True, theme.TEXT_DIM)
        surface.blit(sw_label, (180, y + 4))
        sw_val = f_med.render(f"{self.swing}%", True, theme.TEXT_BRIGHT)
        surface.blit(sw_val, (282, y + 6))
        self.swing_minus.draw(surface)
        self.swing_plus.draw(surface)

        # Steps
        steps_label = f_sm.render(f"STEPS: {self.step_count}", True, theme.TEXT_DIM)
        surface.blit(steps_label, (380, y + 10))

        # Pattern
        pat_label = f_sm.render(f"PAT: {self.current_pattern + 1}", True, theme.ACCENT)
        surface.blit(pat_label, (480, y + 10))

        pygame.draw.line(surface, theme.BORDER,
                        (0, y + self.top_bar_h),
                        (self.grid_area_w, y + self.top_bar_h))

    def _draw_grid(self, surface: pygame.Surface):
        """Draw the step grid."""
        f_sm = theme.font("small")
        pattern = self._pattern

        for row in range(self.GRID_ROWS):
            # Pad label on left
            label = f_sm.render(str(row + 1), True, theme.TEXT_DIM)
            label_rect = label.get_rect(
                right=self.grid_x - 4,
                centery=self.grid_y + row * self.cell_h + self.cell_h // 2,
            )
            surface.blit(label, label_rect)

            for col in range(self.GRID_COLS):
                cx = self.grid_x + col * self.cell_w
                cy = self.grid_y + row * self.cell_h
                cell_rect = pygame.Rect(cx + 1, cy + 1,
                                        self.cell_w - 2, self.cell_h - 2)

                vel = pattern[row][col]

                # Current step column highlight
                if col == self.current_step and self.playing:
                    highlight = pygame.Rect(cx, cy, self.cell_w, self.cell_h)
                    pygame.draw.rect(surface, (40, 40, 50), highlight)

                # Cell color
                if vel > 0:
                    # Active — brightness based on velocity
                    color = theme.velocity_color(vel)
                    pygame.draw.rect(surface, color, cell_rect, border_radius=2)
                else:
                    # Empty
                    pygame.draw.rect(surface, theme.PAD_OFF, cell_rect, border_radius=2)

                # Selected cell outline
                if row == self.selected_pad and col == self.selected_step:
                    pygame.draw.rect(surface, theme.PAD_SELECTED, cell_rect, 2,
                                    border_radius=2)

        # Grid border
        grid_rect = pygame.Rect(self.grid_x, self.grid_y,
                                self.grid_w, self.grid_h)
        pygame.draw.rect(surface, theme.BORDER, grid_rect, 1)

        # Step numbers at top (inside grid area)
        for col in range(self.GRID_COLS):
            cx = self.grid_x + col * self.cell_w + self.cell_w // 2
            num_color = theme.ACCENT if col == self.current_step else theme.TEXT_DIM
            num_surf = f_sm.render(str(col + 1), True, num_color)
            num_rect = num_surf.get_rect(centerx=cx, bottom=self.grid_y - 2)
            surface.blit(num_surf, num_rect)

    def _draw_position_bar(self, surface: pygame.Surface):
        """Draw the scrolling position indicator below the grid."""
        bar_y = self.grid_y + self.grid_h + 4
        bar_h = self.bottom_bar_h - 6

        # Track
        pygame.draw.rect(surface, theme.KNOB_TRACK,
                        (self.grid_x, bar_y, self.grid_w, bar_h),
                        border_radius=3)

        # Position marker
        if self.current_step >= 0 and self.playing:
            marker_w = self.cell_w - 2
            marker_x = self.grid_x + self.current_step * self.cell_w + 1
            pygame.draw.rect(surface, theme.ACCENT,
                            (marker_x, bar_y, marker_w, bar_h),
                            border_radius=3)

    def _draw_right_panel(self, surface: pygame.Surface):
        """Draw transport and pattern controls."""
        f_sm = theme.font("small")

        # Panel background
        panel_rect = pygame.Rect(self.grid_area_w, self.area_y,
                                 self.right_panel_w, self.area_h)
        pygame.draw.rect(surface, theme.BG_PANEL, panel_rect)
        pygame.draw.line(surface, theme.BORDER,
                        (self.grid_area_w, self.area_y),
                        (self.grid_area_w, self.area_y + self.area_h))

        # Transport buttons
        self.play_btn.active = self.playing
        self.rec_btn.active = self.recording
        self.overdub_btn.active = self.overdub

        self.play_btn.draw(surface)
        self.stop_btn.draw(surface)
        self.rec_btn.draw(surface)
        self.overdub_btn.draw(surface)

        # Pattern label
        pat_label = f_sm.render("PATTERN", True, theme.TEXT_DIM)
        surface.blit(pat_label, (self.grid_area_w + 8, self.pat_label_y))

        # Pattern buttons
        for btn in self.pattern_buttons:
            btn.draw(surface)

        # Utility buttons
        self.quantize_btn.draw(surface)
        self.clear_btn.draw(surface)
        self.copy_btn.draw(surface)
        self.paste_btn.draw(surface)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events."""
        # BPM buttons
        if self.bpm_minus.handle_event(event):
            self.bpm = max(30, self.bpm - 1)
            return True
        if self.bpm_plus.handle_event(event):
            self.bpm = min(300, self.bpm + 1)
            return True

        # Swing buttons
        if self.swing_minus.handle_event(event):
            self.swing = max(0, self.swing - 5)
            return True
        if self.swing_plus.handle_event(event):
            self.swing = min(100, self.swing + 5)
            return True

        # Transport
        if self.play_btn.handle_event(event):
            self.playing = self.play_btn.active
            if self.playing:
                self.current_step = -1
                self._last_step_time = time.monotonic()
            else:
                self.current_step = -1
            return True
        if self.stop_btn.handle_event(event):
            self.playing = False
            self.recording = False
            self.current_step = -1
            self.play_btn.active = False
            self.rec_btn.active = False
            return True
        if self.rec_btn.handle_event(event):
            self.recording = self.rec_btn.active
            return True
        if self.overdub_btn.handle_event(event):
            self.overdub = self.overdub_btn.active
            return True

        # Pattern select
        for i, btn in enumerate(self.pattern_buttons):
            if btn.handle_event(event):
                self.current_pattern = i
                for j, b in enumerate(self.pattern_buttons):
                    b.active = (j == i)
                return True

        # Utility buttons
        if self.quantize_btn.handle_event(event):
            return True
        if self.clear_btn.handle_event(event):
            self.patterns[self.current_pattern] = [
                [0.0] * self.GRID_COLS for _ in range(self.GRID_ROWS)
            ]
            return True
        if self.copy_btn.handle_event(event):
            self._clipboard = [row[:] for row in self._pattern]
            return True
        if self.paste_btn.handle_event(event):
            if self._clipboard is not None:
                self.patterns[self.current_pattern] = [
                    row[:] for row in self._clipboard
                ]
            return True

        # Grid tap — toggle steps
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            grid_rect = pygame.Rect(self.grid_x, self.grid_y,
                                    self.grid_w, self.grid_h)
            if grid_rect.collidepoint(event.pos):
                col = (event.pos[0] - self.grid_x) // self.cell_w
                row = (event.pos[1] - self.grid_y) // self.cell_h
                if 0 <= col < self.GRID_COLS and 0 <= row < self.GRID_ROWS:
                    pattern = self._pattern
                    if pattern[row][col] > 0:
                        pattern[row][col] = 0.0
                        self.selected_step = -1
                        self.selected_pad = -1
                    else:
                        pattern[row][col] = 0.78  # default velocity
                        self.selected_step = col
                        self.selected_pad = row
                    return True

        return False
