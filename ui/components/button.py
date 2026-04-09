"""Touch button component — polished dark UI style."""

import pygame
from .. import theme


class Button:
    """Touchscreen button with depth, glow, and press feedback."""

    def __init__(self, rect: pygame.Rect, label: str,
                 color=None, active_color=None, text_color=None,
                 font_name: str = "medium", toggle: bool = False):
        self.rect = rect
        self.label = label
        self.color = color or theme.BUTTON_BG
        self.active_color = active_color or theme.ACCENT
        self.text_color = text_color or theme.BUTTON_TEXT
        self.font_name = font_name
        self.toggle = toggle
        self.active = False
        self.pressed = False

    def draw(self, surface: pygame.Surface):
        """Draw the button with depth and polish."""
        r = self.rect

        if self.active:
            # Active state: accent with glow
            glow = r.inflate(4, 4)
            glow_surf = pygame.Surface((glow.width, glow.height), pygame.SRCALPHA)
            pygame.draw.rect(glow_surf, (*self.active_color[:3], 35),
                            (0, 0, glow.width, glow.height),
                            border_radius=theme.BUTTON_RADIUS + 2)
            surface.blit(glow_surf, glow.topleft)

            bg = self.active_color
            tc = theme.BG
        elif self.pressed:
            bg = tuple(max(0, c - 15) for c in self.color)
            tc = self.text_color
        else:
            bg = self.color
            tc = self.text_color

        # Button body
        pygame.draw.rect(surface, bg, r, border_radius=theme.BUTTON_RADIUS)

        # Top edge highlight for depth (not on active)
        if not self.active and not self.pressed:
            hl_rect = pygame.Rect(r.x + 2, r.y + 1, r.width - 4, 1)
            hl_surf = pygame.Surface((hl_rect.width, 1), pygame.SRCALPHA)
            hl_surf.fill((255, 255, 255, 18))
            surface.blit(hl_surf, hl_rect.topleft)

        # Border
        border_color = self.active_color if self.active else theme.BORDER
        pygame.draw.rect(surface, border_color, r, 1, border_radius=theme.BUTTON_RADIUS)

        # Label
        f = theme.font(self.font_name)
        text_surf = f.render(self.label, True, tc)
        surface.blit(text_surf, text_surf.get_rect(center=r.center))

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
        self.rect.topleft = (x, y)
