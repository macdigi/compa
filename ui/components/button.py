"""Touch button component."""

import pygame
from .. import theme


class Button:
    """Touchscreen button with label, press state, and optional active/toggle."""

    def __init__(self, rect: pygame.Rect, label: str,
                 color=None, active_color=None, text_color=None,
                 font_name: str = "medium", toggle: bool = False):
        self.rect = rect
        self.label = label
        self.color = color or theme.BUTTON_BG
        self.active_color = active_color or theme.BUTTON_ACTIVE
        self.text_color = text_color or theme.BUTTON_TEXT
        self.font_name = font_name
        self.toggle = toggle
        self.active = False
        self.pressed = False
        self._press_time = 0

    def draw(self, surface: pygame.Surface):
        """Draw the button."""
        color = self.active_color if self.active else self.color
        if self.pressed:
            # Darken on press
            color = tuple(max(0, c - 30) for c in color)

        pygame.draw.rect(surface, color, self.rect, border_radius=theme.BUTTON_RADIUS)
        pygame.draw.rect(surface, theme.BORDER, self.rect, 1, border_radius=theme.BUTTON_RADIUS)

        # Label
        f = theme.font(self.font_name)
        text_surf = f.render(self.label, True, self.text_color)
        text_rect = text_surf.get_rect(center=self.rect.center)
        surface.blit(text_surf, text_rect)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle touch/mouse event. Returns True if button was clicked."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.pressed = True
                return False

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_pressed = self.pressed
            self.pressed = False
            if was_pressed and self.rect.collidepoint(event.pos):
                if self.toggle:
                    self.active = not self.active
                return True

        return False

    def set_pos(self, x: int, y: int):
        """Move button to new position."""
        self.rect.topleft = (x, y)
