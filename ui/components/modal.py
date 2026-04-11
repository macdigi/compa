"""Popup dialog/modal component."""

import pygame
from .. import theme
from .button import Button


class Modal:
    """Simple modal dialog with title, message, and buttons."""

    def __init__(self, title: str, message: str,
                 buttons: list[str] = None,
                 width: int = 400, height: int = 200):
        self.title = title
        self.message = message
        self.visible = False
        self.result: str | None = None

        # Center on screen
        x = (theme.SCREEN_WIDTH - width) // 2
        y = (theme.SCREEN_HEIGHT - height) // 2
        self.rect = pygame.Rect(x, y, width, height)

        # Create buttons — auto-size to fit within modal width
        button_labels = buttons or ["OK"]
        self._buttons: list[Button] = []
        btn_gap = 10
        btn_h = 36
        max_btn_area = width - 32  # Padding on each side
        btn_w = min(100, (max_btn_area - (len(button_labels) - 1) * btn_gap) // max(1, len(button_labels)))
        total_btn_w = len(button_labels) * btn_w + (len(button_labels) - 1) * btn_gap
        start_x = self.rect.centerx - total_btn_w // 2
        btn_y = self.rect.bottom - 52

        for i, label in enumerate(button_labels):
            bx = start_x + i * (btn_w + btn_gap)
            color = theme.ACCENT if i == 0 else theme.BUTTON_BG
            btn = Button(
                pygame.Rect(bx, btn_y, btn_w, btn_h),
                label,
                color=color,
            )
            self._buttons.append(btn)

        # Text input (for rename etc.)
        self.input_mode = False
        self.input_text = ""
        self._cursor_visible = True
        self._cursor_timer = 0

    def show(self, title: str = None, message: str = None,
             input_mode: bool = False, default_text: str = ""):
        """Show the modal."""
        if title:
            self.title = title
        if message:
            self.message = message
        self.visible = True
        self.result = None
        self.input_mode = input_mode
        self.input_text = default_text

    def hide(self):
        self.visible = False

    def draw(self, surface: pygame.Surface):
        """Draw the modal overlay and dialog."""
        if not self.visible:
            return

        # Overlay
        overlay = pygame.Surface((theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        surface.blit(overlay, (0, 0))

        # Dialog box with glow
        glow = self.rect.inflate(8, 8)
        glow_surf = pygame.Surface((glow.width, glow.height), pygame.SRCALPHA)
        pygame.draw.rect(glow_surf, (0, 0, 0, 80), (0, 0, glow.width, glow.height),
                        border_radius=12)
        surface.blit(glow_surf, glow.topleft)
        pygame.draw.rect(surface, theme.MODAL_BG, self.rect, border_radius=10)
        pygame.draw.rect(surface, theme.ACCENT_DIM, self.rect, 1, border_radius=10)

        # Title bar
        title_bar = pygame.Rect(self.rect.x, self.rect.y, self.rect.width, 40)
        pygame.draw.rect(surface, theme.BG_LIGHTER, title_bar,
                        border_radius=10)
        # Clip bottom corners of title bar
        pygame.draw.rect(surface, theme.BG_LIGHTER,
                        (title_bar.x, title_bar.bottom - 10, title_bar.width, 10))
        pygame.draw.line(surface, theme.BORDER,
                        (self.rect.x, self.rect.y + 40),
                        (self.rect.right, self.rect.y + 40))

        f_title = theme.font("large")
        title_surf = f_title.render(self.title, True, theme.ACCENT)
        surface.blit(title_surf, (self.rect.x + 16, self.rect.y + 8))

        # Message
        f = theme.font("medium")
        msg_surf = f.render(self.message, True, theme.TEXT)
        surface.blit(msg_surf, (self.rect.x + 20, self.rect.y + 52))

        # Text input
        if self.input_mode:
            input_rect = pygame.Rect(
                self.rect.x + 20, self.rect.y + 82,
                self.rect.width - 40, 32
            )
            pygame.draw.rect(surface, theme.BG, input_rect)
            pygame.draw.rect(surface, theme.BORDER, input_rect, 1)
            text_surf = f.render(self.input_text, True, theme.TEXT)
            surface.blit(text_surf, (input_rect.x + 6, input_rect.y + 6))

            # Cursor
            self._cursor_timer += 1
            if self._cursor_timer % 30 < 15:
                cx = input_rect.x + 6 + text_surf.get_width() + 2
                pygame.draw.line(surface, theme.TEXT,
                               (cx, input_rect.y + 4), (cx, input_rect.bottom - 4), 2)

        # Buttons
        for btn in self._buttons:
            btn.draw(surface)

    def handle_event(self, event: pygame.event.Event) -> str | None:
        """Handle events. Returns button label if clicked, None otherwise."""
        if not self.visible:
            return None

        # Text input handling
        if self.input_mode and event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                self.input_text = self.input_text[:-1]
            elif event.key == pygame.K_RETURN:
                self.result = self._buttons[0].label
                self.visible = False
                return self.result
            elif event.unicode and event.unicode.isprintable():
                self.input_text += event.unicode
            return None

        # Button handling
        for btn in self._buttons:
            if btn.handle_event(event):
                self.result = btn.label
                self.visible = False
                return self.result

        # Consume all clicks within modal area
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            return "__consumed__"

        return None
