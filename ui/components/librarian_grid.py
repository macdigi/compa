"""Bank-selector + pad-grid component for device librarians.

Generic enough to serve both the P-6 (8 banks × 6 pads in a 3×2 grid)
and the SP-404 MK2 (10 banks × 16 pads in a 4×4 grid). Touch-friendly
tap-to-select interaction.

Usage:
    grid = LibrarianGrid(
        rect, banks=8, pads_per_bank=6, grid_cols=3, grid_rows=2,
    )
    grid.set_pads(my_48_pad_list)  # list of 48 (or 160) dicts or None
    grid.set_bank(0)                # 0-indexed bank

    result = grid.handle_event(event)
    if result is not None:
        # result is the global pad index that was tapped
        ...

    grid.draw(surface)
"""

from typing import Optional

import pygame
from .. import theme


class LibrarianGrid:
    """Bank selector row + pad grid with tap-to-select."""

    BANK_BAR_H = 36
    BANK_GAP = 3

    def __init__(self, rect: pygame.Rect, *,
                 banks: int, pads_per_bank: int,
                 grid_cols: int, grid_rows: int,
                 bank_labels: Optional[list[str]] = None):
        assert banks >= 1
        assert pads_per_bank == grid_cols * grid_rows, \
            "pads_per_bank must equal grid_cols * grid_rows"

        self._rect = rect
        self._banks = banks
        self._pads_per_bank = pads_per_bank
        self._cols = grid_cols
        self._rows = grid_rows

        # Bank labels A, B, C... or 1, 2, 3... if > 26 banks
        if bank_labels is not None:
            self._bank_labels = bank_labels
        else:
            self._bank_labels = [
                chr(ord("A") + i) if i < 26 else str(i + 1)
                for i in range(banks)
            ]

        self._current_bank = 0
        self._selected_global: int = -1  # -1 = nothing selected
        self._pads: list[Optional[dict]] = [None] * (banks * pads_per_bank)
        self._move_src: int = -1  # for "move" mode (SP-404)

    # ── Public API ───────────────────────────────────────────────────

    def set_rect(self, rect: pygame.Rect):
        self._rect = rect

    def set_pads(self, pads: list):
        expected = self._banks * self._pads_per_bank
        if len(pads) != expected:
            # Pad with None
            pads = (list(pads) + [None] * expected)[:expected]
        self._pads = pads

    @property
    def rect(self) -> pygame.Rect:
        return self._rect

    @property
    def pads(self) -> list[Optional[dict]]:
        return self._pads

    def set_bank(self, idx: int):
        if 0 <= idx < self._banks:
            self._current_bank = idx

    @property
    def current_bank(self) -> int:
        return self._current_bank

    def set_selected_pad(self, global_idx: int):
        self._selected_global = global_idx

    @property
    def selected_pad(self) -> int:
        """Currently selected global pad index, or -1."""
        return self._selected_global

    def set_move_src(self, global_idx: int):
        """Highlight a pad as the MOVE source (SP-404 move_pad flow)."""
        self._move_src = global_idx

    @property
    def move_src(self) -> int:
        return self._move_src

    def clear_move_src(self):
        self._move_src = -1

    # ── Event handling ───────────────────────────────────────────────

    def handle_event(self, event) -> Optional[int]:
        """Return the tapped global pad index if a pad was hit, else None."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return None

        mx, my = event.pos
        if not self._rect.collidepoint(mx, my):
            return None

        # Bank bar
        bank_rect = self._bank_bar_rect()
        if bank_rect.collidepoint(mx, my):
            total_w = bank_rect.width
            bw = (total_w - self.BANK_GAP * (self._banks - 1)) // self._banks
            for i in range(self._banks):
                bx = bank_rect.x + i * (bw + self.BANK_GAP)
                br = pygame.Rect(bx, bank_rect.y, bw, bank_rect.height)
                if br.collidepoint(mx, my):
                    self._current_bank = i
                    return None  # bank-select isn't a pad tap
            return None

        # Pad grid
        grid_rect = self._grid_rect()
        if grid_rect.collidepoint(mx, my):
            col_w = grid_rect.width / self._cols
            row_h = grid_rect.height / self._rows
            rel_x = mx - grid_rect.x
            rel_y = my - grid_rect.y
            col = int(rel_x // col_w)
            row = int(rel_y // row_h)
            if 0 <= col < self._cols and 0 <= row < self._rows:
                pad_in_bank = row * self._cols + col
                global_idx = self._current_bank * self._pads_per_bank + pad_in_bank
                self._selected_global = global_idx
                return global_idx

        return None

    # ── Drawing ──────────────────────────────────────────────────────

    def _bank_bar_rect(self) -> pygame.Rect:
        return pygame.Rect(
            self._rect.x, self._rect.y,
            self._rect.width, self.BANK_BAR_H,
        )

    def _grid_rect(self) -> pygame.Rect:
        return pygame.Rect(
            self._rect.x, self._rect.y + self.BANK_BAR_H + 6,
            self._rect.width,
            self._rect.height - self.BANK_BAR_H - 6,
        )

    def draw(self, surface: pygame.Surface):
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")
        f_med = theme.font("medium")

        # ── Bank bar ────────────────────────────────────────────
        bank_rect = self._bank_bar_rect()
        total_w = bank_rect.width
        bw = (total_w - self.BANK_GAP * (self._banks - 1)) // self._banks

        # Count loaded pads per bank for badges
        loaded_per_bank = [0] * self._banks
        for i, p in enumerate(self._pads):
            if p is not None:
                b = i // self._pads_per_bank
                if 0 <= b < self._banks:
                    loaded_per_bank[b] += 1

        for i in range(self._banks):
            bx = bank_rect.x + i * (bw + self.BANK_GAP)
            br = pygame.Rect(bx, bank_rect.y, bw, bank_rect.height)
            is_active = (i == self._current_bank)
            has_pads = loaded_per_bank[i] > 0

            if is_active:
                bg = theme.ACCENT
                tc = theme.BG
            elif has_pads:
                bg = theme.BG_LIGHTER
                tc = theme.TEXT
            else:
                bg = theme.BG_PANEL
                tc = theme.TEXT_DIM

            pygame.draw.rect(surface, bg, br, border_radius=5)
            if not is_active:
                pygame.draw.rect(surface, theme.BORDER, br, 1, border_radius=5)

            # Label
            lbl = f_med.render(self._bank_labels[i], True, tc)
            surface.blit(lbl, lbl.get_rect(center=(br.centerx, br.centery - 4)))

            # Badge: loaded count
            if has_pads:
                badge_color = theme.BG if is_active else theme.ACCENT
                cnt = f_tiny.render(f"{loaded_per_bank[i]}", True, badge_color)
                surface.blit(cnt, cnt.get_rect(
                    center=(br.centerx, br.bottom - 7)))

        # ── Pad grid ────────────────────────────────────────────
        grid_rect = self._grid_rect()
        gap = 4
        col_w = (grid_rect.width - gap * (self._cols - 1)) // self._cols
        row_h = (grid_rect.height - gap * (self._rows - 1)) // self._rows

        bank_start = self._current_bank * self._pads_per_bank
        bank_letter = self._bank_labels[self._current_bank]

        for row in range(self._rows):
            for col in range(self._cols):
                pad_in_bank = row * self._cols + col
                global_idx = bank_start + pad_in_bank
                pad = self._pads[global_idx] if global_idx < len(self._pads) else None

                px = grid_rect.x + col * (col_w + gap)
                py = grid_rect.y + row * (row_h + gap)
                pr = pygame.Rect(px, py, col_w, row_h)

                is_selected = (global_idx == self._selected_global)
                is_move_src = (global_idx == self._move_src)
                is_loaded = pad is not None

                # Background
                if is_selected:
                    bg = theme.ACCENT
                elif is_move_src:
                    bg = theme.BLUE
                elif is_loaded:
                    bg = theme.BG_LIGHTER
                else:
                    bg = theme.BG_PANEL
                pygame.draw.rect(surface, bg, pr, border_radius=6)

                # Border
                border_color = (theme.ACCENT if is_selected
                                else theme.BLUE if is_move_src
                                else theme.BORDER_LIGHT if is_loaded
                                else theme.BORDER)
                pygame.draw.rect(surface, border_color, pr, 1, border_radius=6)

                # Label — pad ID in corner
                pad_num = pad_in_bank + 1
                pad_id = f"{bank_letter}{pad_num:02d}" if len(bank_letter) == 1 \
                    else f"{bank_letter}-{pad_num}"
                id_color = theme.BG if is_selected else theme.TEXT_DIM
                id_surf = f_tiny.render(pad_id, True, id_color)
                surface.blit(id_surf, (pr.x + 5, pr.y + 4))

                # Filename (centered, truncated)
                if is_loaded:
                    filename = pad.get("filename", "?")
                    # Trim extension for readability
                    if filename.lower().endswith((".wav", ".smp")):
                        filename = filename[:-4]
                    # Truncate to fit
                    name_surf = None
                    for max_chars in range(len(filename), 0, -1):
                        trial = filename[:max_chars]
                        s = f_tiny.render(trial, True,
                                          theme.BG if is_selected else theme.TEXT)
                        if s.get_width() <= pr.width - 10:
                            name_surf = s
                            break
                    if name_surf:
                        surface.blit(name_surf, name_surf.get_rect(
                            center=(pr.centerx, pr.centery + 2)))

                    # Status badge in the lower-right corner.
                    is_pending = bool(pad.get("in_import"))
                    is_on_device = bool(pad.get("on_device"))
                    if is_pending:
                        badge_text = "PEND"
                        badge_bg = theme.ACCENT
                        badge_fg = theme.BG
                    elif is_on_device:
                        badge_text = "LIVE"
                        badge_bg = theme.BLUE
                        badge_fg = theme.TEXT_BRIGHT
                    else:
                        badge_text = "LOAD"
                        badge_bg = theme.BG
                        badge_fg = theme.ACCENT

                    badge_surf = f_tiny.render(badge_text, True, badge_fg)
                    badge_w = badge_surf.get_width() + 8
                    badge_h = badge_surf.get_height() + 2
                    badge_rect = pygame.Rect(
                        pr.right - badge_w - 5,
                        pr.bottom - badge_h - 5,
                        badge_w,
                        badge_h,
                    )
                    pygame.draw.rect(surface, badge_bg, badge_rect, border_radius=4)
                    if badge_bg == theme.BG:
                        pygame.draw.rect(surface, theme.BORDER_LIGHT, badge_rect, 1, border_radius=4)
                    surface.blit(badge_surf, badge_surf.get_rect(center=badge_rect.center))
                else:
                    # Empty — centered dash
                    dash = f_small.render("—", True, theme.TEXT_DIM)
                    surface.blit(dash, dash.get_rect(center=pr.center))
