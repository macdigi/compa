"""Touch-draggable rotary knob component."""

import math
import pygame
from .. import theme


class Knob:
    """Rotary knob controlled by vertical touch drag."""

    def __init__(self, center: tuple, radius: int = 28,
                 min_val: float = 0.0, max_val: float = 1.0,
                 value: float = 0.5, label: str = "",
                 format_func=None, int_mode: bool = False):
        self.center = center
        self.radius = radius
        self.min_val = min_val
        self.max_val = max_val
        self.value = value
        self.label = label
        self.format_func = format_func or (lambda v: f"{v:.0f}" if int_mode else f"{v:.2f}")
        self.int_mode = int_mode
        self._dragging = False
        self._drag_start_y = 0
        self._drag_start_val = 0.0
        self.sensitivity = 200.0  # pixels for full range

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(
            self.center[0] - self.radius,
            self.center[1] - self.radius - 12,
            self.radius * 2,
            self.radius * 2 + 30,
        )

    def draw(self, surface: pygame.Surface):
        """Draw the knob with arc indicator and label."""
        cx, cy = self.center
        r = self.radius

        # Background circle
        pygame.draw.circle(surface, theme.KNOB_BG, (cx, cy), r)
        pygame.draw.circle(surface, theme.BORDER, (cx, cy), r, 2)

        # Arc showing value
        start_angle = math.radians(225)  # 7:30 position
        end_angle = math.radians(225 - 270)  # Full sweep = 270 degrees

        normalized = (self.value - self.min_val) / max(0.001, self.max_val - self.min_val)
        normalized = max(0.0, min(1.0, normalized))
        sweep = normalized * 270.0

        # Draw track arc
        arc_rect = pygame.Rect(cx - r + 4, cy - r + 4, (r - 4) * 2, (r - 4) * 2)
        if arc_rect.width > 0 and arc_rect.height > 0:
            pygame.draw.arc(surface, theme.KNOB_TRACK, arc_rect,
                          math.radians(-45), math.radians(225), 3)
            # Value arc
            if sweep > 0:
                pygame.draw.arc(surface, theme.KNOB_FILL, arc_rect,
                              math.radians(225 - sweep), math.radians(225), 3)

        # Indicator dot
        angle = math.radians(225 - sweep)
        dot_r = r - 8
        dot_x = cx + int(dot_r * math.cos(angle))
        dot_y = cy - int(dot_r * math.sin(angle))
        pygame.draw.circle(surface, theme.ACCENT_BRIGHT, (dot_x, dot_y), 4)

        # Label above
        f = theme.font("small")
        label_surf = f.render(self.label, True, theme.TEXT_DIM)
        label_rect = label_surf.get_rect(centerx=cx, bottom=cy - r - 4)
        surface.blit(label_surf, label_rect)

        # Value below
        val_text = self.format_func(self.value)
        val_surf = f.render(val_text, True, theme.TEXT)
        val_rect = val_surf.get_rect(centerx=cx, top=cy + r + 4)
        surface.blit(val_surf, val_rect)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle touch drag. Returns True if value changed."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            # Check if touch is near knob
            dx = event.pos[0] - self.center[0]
            dy = event.pos[1] - self.center[1]
            if dx * dx + dy * dy <= (self.radius + 15) ** 2:
                self._dragging = True
                self._drag_start_y = event.pos[1]
                self._drag_start_val = self.value
                return False

        elif event.type == pygame.MOUSEMOTION and self._dragging:
            delta_y = self._drag_start_y - event.pos[1]  # Up = increase
            range_val = self.max_val - self.min_val
            delta_val = (delta_y / self.sensitivity) * range_val
            new_val = self._drag_start_val + delta_val
            new_val = max(self.min_val, min(self.max_val, new_val))

            if self.int_mode:
                new_val = round(new_val)

            if new_val != self.value:
                self.value = new_val
                return True

        elif event.type == pygame.MOUSEBUTTONUP:
            self._dragging = False

        return False

    def set_center(self, x: int, y: int):
        self.center = (x, y)
