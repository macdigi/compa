"""On-screen touchscreen keyboard.

Modal-style overlay component for text input on the 7" touchscreen when
no physical keyboard is attached. Pattern matches AudioPlayer: owned by
the app, drawn on top of everything when visible, consumes events first.

Usage:
    self.keyboard.show(title="WiFi Password",
                       default="",
                       password=True,
                       on_submit=lambda text: do_thing(text),
                       on_cancel=lambda: None)

Layout:
    ┌──────────────────────────────────────┐
    │ Title                           [X]  │
    │ ┌──────────────────────────────────┐ │
    │ │ ••••••••                       ▮ │ │  ← text field
    │ └──────────────────────────────────┘ │
    │ [1][2][3][4][5][6][7][8][9][0]       │
    │ [Q][W][E][R][T][Y][U][I][O][P]       │
    │ [A][S][D][F][G][H][J][K][L]          │
    │ [⇧][Z][X][C][V][B][N][M][⌫]          │
    │ [123] [       space       ] [↵ DONE]│
    └──────────────────────────────────────┘
"""

import pygame
from .. import theme


# Row definitions — lowercase default layout
_ROW_LOWER = [
    list("1234567890"),
    list("qwertyuiop"),
    list("asdfghjkl"),
    ["shift"] + list("zxcvbnm") + ["backspace"],
]

_ROW_UPPER = [
    list("1234567890"),
    list("QWERTYUIOP"),
    list("ASDFGHJKL"),
    ["shift"] + list("ZXCVBNM") + ["backspace"],
]

_ROW_SYMBOLS = [
    list("1234567890"),
    list("!@#$%^&*()"),
    list("-_=+[]{};:"),
    ["shift"] + list("'\",.<>/?") + ["backspace"],
]


class OnScreenKeyboard:
    """Fullscreen-overlay touch keyboard for text entry."""

    def __init__(self, app):
        self.app = app
        self.visible = False
        self.title = ""
        self.text = ""
        self.password = False
        self._on_submit = None
        self._on_cancel = None
        self._layout = "lower"  # "lower", "upper", "symbols"
        self._caps_lock = False
        self._flash_key: str | None = None
        self._flash_until = 0

        # Rects rebuilt each draw call
        self._key_rects: dict[str, pygame.Rect] = {}
        self._close_rect = pygame.Rect(0, 0, 0, 0)
        self._field_rect = pygame.Rect(0, 0, 0, 0)

        # Blinking caret
        self._cursor_timer = 0

    # ── Public API ───────────────────────────────────────────────────

    def show(self, title: str = "Enter text", default: str = "",
             password: bool = False, on_submit=None, on_cancel=None):
        """Show the keyboard. on_submit(text), on_cancel()."""
        self.visible = True
        self.title = title
        self.text = default
        self.password = password
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._layout = "lower"
        self._caps_lock = False

    def hide(self):
        self.visible = False
        self._on_submit = None
        self._on_cancel = None

    # ── Event handling ───────────────────────────────────────────────

    def handle_event(self, event) -> bool:
        """Return True if the event was consumed."""
        if not self.visible:
            return False

        # Hardware keyboard fallback — nice for dev on Mac
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._cancel()
                return True
            if event.key == pygame.K_RETURN:
                self._submit()
                return True
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
                return True
            if event.unicode and event.unicode.isprintable():
                self.text += event.unicode
                return True
            return True  # swallow other keys too

        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button != 1:
                # Swallow scroll-wheel (4/5) and other buttons so nothing
                # behind the keyboard scrolls while it's up.
                return True

            mx, my = event.pos

            # Close button
            if self._close_rect.collidepoint(mx, my):
                self._cancel()
                return True

            # Key press
            for key_id, rect in self._key_rects.items():
                if rect.collidepoint(mx, my):
                    self._press_key(key_id)
                    return True

            return True  # swallow clicks outside keys so they don't leak

        if event.type in (pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            return True  # swallow release + drags

        # Raw finger events — just in case the keyboard is shown while
        # touch → mouse conversion is off (e.g. before on_enter runs).
        if event.type in (pygame.FINGERDOWN, pygame.FINGERUP, pygame.FINGERMOTION):
            return True

        return False

    def _press_key(self, key_id: str):
        """Handle a press on a virtual key."""
        self._flash_key = key_id
        self._flash_until = pygame.time.get_ticks() + 120

        if key_id == "backspace":
            self.text = self.text[:-1]
        elif key_id == "shift":
            if self._layout == "lower":
                self._layout = "upper"
            elif self._layout == "upper":
                self._layout = "lower"
            elif self._layout == "symbols":
                self._layout = "lower"
        elif key_id == "mode":
            self._layout = "symbols" if self._layout != "symbols" else "lower"
        elif key_id == "space":
            self.text += " "
        elif key_id == "done":
            self._submit()
        elif len(key_id) == 1:
            self.text += key_id
            # Auto-drop shift after one letter unless caps lock (long press would set it; not wiring yet)
            if self._layout == "upper" and not self._caps_lock:
                self._layout = "lower"

    def _submit(self):
        cb = self._on_submit
        text = self.text
        self.hide()
        if cb:
            try:
                cb(text)
            except Exception as e:
                print(f"Keyboard on_submit error: {e}", flush=True)

    def _cancel(self):
        cb = self._on_cancel
        self.hide()
        if cb:
            try:
                cb()
            except Exception as e:
                print(f"Keyboard on_cancel error: {e}", flush=True)

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        if not self.visible:
            return

        W, H = theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT

        # Dim background
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        surface.blit(overlay, (0, 0))

        f_title = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")

        # Panel fills most of the screen, leaving small margins
        margin = 8
        panel = pygame.Rect(margin, margin, W - margin * 2, H - margin * 2)
        pygame.draw.rect(surface, theme.BG_PANEL, panel, border_radius=10)
        pygame.draw.rect(surface, theme.ACCENT_DIM, panel, 2, border_radius=10)

        # Title bar
        title_h = 34
        title_bar = pygame.Rect(panel.x, panel.y, panel.width, title_h)
        pygame.draw.rect(surface, theme.BG_LIGHTER, title_bar,
                         border_top_left_radius=10, border_top_right_radius=10)
        pygame.draw.line(surface, theme.BORDER,
                         (panel.x, panel.y + title_h),
                         (panel.right, panel.y + title_h))

        title_surf = f_title.render(self.title, True, theme.ACCENT)
        surface.blit(title_surf, (panel.x + 14, panel.y + 6))

        # Close [X]
        close_size = 24
        self._close_rect = pygame.Rect(
            panel.right - close_size - 10,
            panel.y + 5,
            close_size, close_size,
        )
        pygame.draw.rect(surface, theme.BG, self._close_rect, border_radius=4)
        pygame.draw.rect(surface, theme.BORDER, self._close_rect, 1, border_radius=4)
        x_surf = f_small.render("X", True, theme.TEXT_DIM)
        surface.blit(x_surf, x_surf.get_rect(center=self._close_rect.center))

        # Text field
        field_y = panel.y + title_h + 10
        field_h = 38
        self._field_rect = pygame.Rect(
            panel.x + 14, field_y, panel.width - 28, field_h,
        )
        pygame.draw.rect(surface, theme.BG, self._field_rect, border_radius=6)
        pygame.draw.rect(surface, theme.ACCENT, self._field_rect, 2, border_radius=6)

        display = "•" * len(self.text) if self.password else self.text
        # Trim from the left if it overflows
        max_text_w = self._field_rect.width - 20
        txt_surf = f_med.render(display, True, theme.TEXT_BRIGHT)
        if txt_surf.get_width() > max_text_w:
            # Drop characters from the start until it fits
            start = 0
            while start < len(display):
                trimmed = display[start:]
                trimmed_surf = f_med.render(trimmed, True, theme.TEXT_BRIGHT)
                if trimmed_surf.get_width() <= max_text_w:
                    txt_surf = trimmed_surf
                    break
                start += 1
        surface.blit(
            txt_surf,
            (self._field_rect.x + 10,
             self._field_rect.y + (self._field_rect.height - txt_surf.get_height()) // 2),
        )

        # Blinking caret
        self._cursor_timer += 1
        if self._cursor_timer % 30 < 15:
            cx = self._field_rect.x + 10 + txt_surf.get_width() + 2
            cy1 = self._field_rect.y + 8
            cy2 = self._field_rect.bottom - 8
            if cx < self._field_rect.right - 4:
                pygame.draw.line(surface, theme.ACCENT, (cx, cy1), (cx, cy2), 2)

        # Keyboard area
        keys_top = field_y + field_h + 10
        keys_bottom = panel.bottom - 12
        keys_left = panel.x + 10
        keys_right = panel.right - 10

        self._draw_keys(surface, keys_left, keys_top, keys_right, keys_bottom,
                        f_small, f_med)

    def _draw_keys(self, surface, left: int, top: int, right: int, bottom: int,
                   f_small, f_med):
        """Draw the key grid. Builds _key_rects as a side effect."""
        self._key_rects.clear()

        if self._layout == "upper":
            rows = _ROW_UPPER
        elif self._layout == "symbols":
            rows = _ROW_SYMBOLS
        else:
            rows = _ROW_LOWER

        # 5 rows total: 4 character rows + 1 bottom control row
        total_rows = 5
        gap = 6
        avail_h = bottom - top
        key_h = (avail_h - gap * (total_rows - 1)) // total_rows
        if key_h < 30:
            key_h = 30

        # Each character row is a fixed grid. Max keys in a row = 10.
        grid_w = right - left
        key_w = (grid_w - gap * 9) // 10
        if key_w < 40:
            key_w = 40

        now = pygame.time.get_ticks()

        # Rows 0-3: character rows
        for row_idx, row_keys in enumerate(rows):
            row_y = top + row_idx * (key_h + gap)

            # Count fixed and flexible keys
            is_special = [k in ("shift", "backspace") for k in row_keys]

            # Shift/backspace take 1.5× width — adjust spacing to fit
            total_units = sum(1.5 if s else 1.0 for s in is_special)
            # Fit within grid_w
            unit_w = (grid_w - gap * (len(row_keys) - 1)) / total_units
            unit_w = min(unit_w, key_w * 1.1)

            # Center the row
            row_pixel_w = sum(
                (unit_w * 1.5 if s else unit_w) for s in is_special
            ) + gap * (len(row_keys) - 1)
            x = left + (grid_w - row_pixel_w) / 2

            for key_char, special in zip(row_keys, is_special):
                w = int(unit_w * 1.5 if special else unit_w)
                rect = pygame.Rect(int(x), int(row_y), w, key_h)
                self._key_rects[key_char] = rect
                self._draw_key(surface, rect, key_char, f_small, f_med, now)
                x += w + gap

        # Row 4: bottom controls [123] [ space ] [DONE]
        row_y = top + 4 * (key_h + gap)
        mode_w = int(grid_w * 0.18)
        done_w = int(grid_w * 0.22)
        space_w = grid_w - mode_w - done_w - gap * 2

        # [123] / [ABC] toggle
        mode_rect = pygame.Rect(left, row_y, mode_w, key_h)
        self._key_rects["mode"] = mode_rect
        self._draw_key(surface, mode_rect, "mode", f_small, f_med, now)

        # [space]
        space_rect = pygame.Rect(left + mode_w + gap, row_y, space_w, key_h)
        self._key_rects["space"] = space_rect
        self._draw_key(surface, space_rect, "space", f_small, f_med, now)

        # [DONE]
        done_rect = pygame.Rect(
            left + mode_w + gap + space_w + gap, row_y, done_w, key_h,
        )
        self._key_rects["done"] = done_rect
        self._draw_key(surface, done_rect, "done", f_small, f_med, now)

    def _draw_key(self, surface, rect, key_id, f_small, f_med, now):
        """Draw one key."""
        flashing = (self._flash_key == key_id and now < self._flash_until)

        # Background
        if key_id == "done":
            bg = theme.ACCENT_BRIGHT if flashing else theme.ACCENT
            tc = theme.BG
            label = "DONE"
            f = f_small
        elif key_id == "shift":
            shift_active = self._layout == "upper"
            if flashing:
                bg = theme.ACCENT_BRIGHT
            elif shift_active:
                bg = theme.ACCENT
            else:
                bg = theme.BG_LIGHTER
            tc = theme.BG if shift_active or flashing else theme.TEXT
            label = "^"
            f = f_med
        elif key_id == "backspace":
            bg = theme.ACCENT_BRIGHT if flashing else theme.BG_LIGHTER
            tc = theme.BG if flashing else theme.TEXT
            label = "<X"
            f = f_small
        elif key_id == "mode":
            mode_active = self._layout == "symbols"
            if flashing:
                bg = theme.ACCENT_BRIGHT
            elif mode_active:
                bg = theme.ACCENT
            else:
                bg = theme.BG_LIGHTER
            tc = theme.BG if mode_active or flashing else theme.TEXT
            label = "ABC" if mode_active else "123"
            f = f_small
        elif key_id == "space":
            bg = theme.ACCENT_BRIGHT if flashing else theme.BG_LIGHTER
            tc = theme.BG if flashing else theme.TEXT_DIM
            label = "space"
            f = f_small
        else:
            bg = theme.ACCENT_BRIGHT if flashing else theme.BUTTON_BG
            tc = theme.BG if flashing else theme.TEXT_BRIGHT
            label = key_id
            f = f_med

        pygame.draw.rect(surface, bg, rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=6)

        lbl = f.render(label, True, tc)
        surface.blit(lbl, lbl.get_rect(center=rect.center))
