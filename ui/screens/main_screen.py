"""Main screen — pad grid, side info panel, bank select, nav bar."""

import pygame
from .. import theme
from ..components.pad_grid import PadGrid
from ..components.button import Button


class MainScreen:
    """Default view: 4x4 pad grid with selected pad info and bank buttons."""

    def __init__(self, app):
        self.app = app

        # Pad grid — left portion
        grid_w = theme.SCREEN_WIDTH - theme.SIDE_PANEL_WIDTH
        grid_h = theme.SCREEN_HEIGHT - theme.HEADER_HEIGHT - theme.NAV_HEIGHT
        self.pad_grid = PadGrid(pygame.Rect(
            0, theme.HEADER_HEIGHT, grid_w, grid_h
        ))

        # Bank buttons in header
        self.bank_buttons: list[Button] = []
        bank_names = ["A", "B", "C", "D"]
        btn_w = 40
        btn_x = theme.SCREEN_WIDTH - len(bank_names) * (btn_w + 4) - 8
        for i, name in enumerate(bank_names):
            btn = Button(
                pygame.Rect(btn_x + i * (btn_w + 4), 2, btn_w, 30),
                name,
                color=theme.BUTTON_BG,
                active_color=theme.ACCENT,
                font_name="medium",
            )
            if name == "A":
                btn.active = True
            self.bank_buttons.append(btn)

        # Side panel rect
        self.side_rect = pygame.Rect(
            grid_w, theme.HEADER_HEIGHT,
            theme.SIDE_PANEL_WIDTH, grid_h
        )

    def update(self):
        """Update per-frame state."""
        self.pad_grid.decay_active()

        # Sync pad sample states
        pads = self.app.pad_bank.current_pads
        has_sample = [p.has_sample for p in pads]
        self.pad_grid.update_sample_states(has_sample)
        self.pad_grid.set_selected(self.app.pad_bank.selected_pad)

        # Sync bank button active states
        for i, btn in enumerate(self.bank_buttons):
            btn.active = (["A", "B", "C", "D"][i] == self.app.pad_bank.current_bank)

    def draw(self, surface: pygame.Surface):
        """Draw the main screen."""
        # Header
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, theme.HEADER_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)

        # Kit name
        f = theme.font("large")
        kit_surf = f.render(self.app.pad_bank.kit_name, True, theme.TEXT_BRIGHT)
        surface.blit(kit_surf, (12, 6))

        # Bank label
        f_sm = theme.font("small")
        bank_label = f_sm.render("Bank:", True, theme.TEXT_DIM)
        surface.blit(bank_label, (self.bank_buttons[0].rect.x - 50, 10))

        # Bank buttons
        for btn in self.bank_buttons:
            btn.draw(surface)

        # Pad grid
        self.pad_grid.draw(surface)

        # Side panel
        self._draw_side_panel(surface)

        # Header/grid separator
        pygame.draw.line(surface, theme.BORDER,
                        (0, theme.HEADER_HEIGHT), (theme.SCREEN_WIDTH, theme.HEADER_HEIGHT))

    def _draw_side_panel(self, surface: pygame.Surface):
        """Draw the selected pad info panel on the right."""
        pygame.draw.rect(surface, theme.BG_PANEL, self.side_rect)
        pygame.draw.line(surface, theme.BORDER,
                        (self.side_rect.x, self.side_rect.y),
                        (self.side_rect.x, self.side_rect.bottom))

        pad = self.app.pad_bank.selected
        x = self.side_rect.x + 12
        y = self.side_rect.y + 12
        f = theme.font("medium")
        f_sm = theme.font("small")
        line_h = 26

        # Pad number
        pad_idx = self.app.pad_bank.selected_pad + 1
        bank = self.app.pad_bank.current_bank
        header = f.render(f"Pad {pad_idx}{bank}", True, theme.ACCENT)
        surface.blit(header, (x, y))
        y += line_h + 4

        # Sample name
        label = f_sm.render("Sample:", True, theme.TEXT_DIM)
        surface.blit(label, (x, y))
        y += 18
        if pad.sample_path:
            import os
            name = os.path.basename(pad.sample_path)
            if len(name) > 22:
                name = name[:20] + ".."
        else:
            name = "(empty)"
        name_surf = f.render(name, True, theme.TEXT)
        surface.blit(name_surf, (x, y))
        y += line_h + 8

        # Volume bar
        label = f_sm.render("Vol:", True, theme.TEXT_DIM)
        surface.blit(label, (x, y))
        bar_x = x + 40
        bar_w = self.side_rect.width - 70
        bar_h = 12
        pygame.draw.rect(surface, theme.KNOB_TRACK,
                        (bar_x, y + 3, bar_w, bar_h))
        fill_w = int(bar_w * pad.volume)
        pygame.draw.rect(surface, theme.ACCENT,
                        (bar_x, y + 3, fill_w, bar_h))
        pct = f_sm.render(f"{int(pad.volume * 100)}%", True, theme.TEXT)
        surface.blit(pct, (bar_x + bar_w + 4, y))
        y += line_h

        # Pan
        label = f_sm.render("Pan:", True, theme.TEXT_DIM)
        surface.blit(label, (x, y))
        if pad.pan == 0:
            pan_text = "C"
        elif pad.pan < 0:
            pan_text = f"L{int(abs(pad.pan) * 100)}"
        else:
            pan_text = f"R{int(pad.pan * 100)}"
        pan_surf = f.render(pan_text, True, theme.TEXT)
        surface.blit(pan_surf, (x + 44, y))
        y += line_h

        # Mode
        label = f_sm.render("Mode:", True, theme.TEXT_DIM)
        surface.blit(label, (x, y))
        mode_text = pad.mode.value.replace("_", "-").upper()
        mode_surf = f.render(mode_text, True, theme.TEXT)
        surface.blit(mode_surf, (x + 50, y))
        y += line_h

        # Tune
        label = f_sm.render("Tune:", True, theme.TEXT_DIM)
        surface.blit(label, (x, y))
        tune_text = f"{pad.tune:+d} st" if pad.tune != 0 else "0 st"
        tune_surf = f.render(tune_text, True, theme.TEXT)
        surface.blit(tune_surf, (x + 50, y))
        y += line_h

        # Choke
        label = f_sm.render("Choke:", True, theme.TEXT_DIM)
        surface.blit(label, (x, y))
        choke_text = str(pad.choke_group) if pad.choke_group > 0 else "None"
        choke_surf = f.render(choke_text, True, theme.TEXT)
        surface.blit(choke_surf, (x + 56, y))

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events. Returns True if consumed."""
        # Bank buttons
        for i, btn in enumerate(self.bank_buttons):
            if btn.handle_event(event):
                bank = ["A", "B", "C", "D"][i]
                self.app.pad_bank.select_bank(bank)
                return True

        # Pad grid
        pad_idx = self.pad_grid.handle_event(event)
        if pad_idx >= 0:
            self.app.pad_bank.select_pad(pad_idx)
            # Trigger with fixed velocity for touchscreen
            pad = self.app.pad_bank.current_pads[pad_idx]
            if pad.has_sample:
                self.app.audio_engine.trigger_pad(pad, velocity=0.78)
                self.pad_grid.set_active(pad_idx, 0.78)
            return True

        return False

    def on_pad_trigger(self, pad_index: int, velocity: float):
        """Called from MIDI — visual feedback on the grid."""
        self.pad_grid.set_active(pad_index, velocity)

    def on_pad_release(self, pad_index: int):
        """Called from MIDI note-off."""
        pass  # Grid handles decay automatically
