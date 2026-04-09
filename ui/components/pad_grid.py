"""4x4 touch pad grid with velocity color feedback."""

import pygame
from .. import theme


class PadGrid:
    """4x4 pad grid for the main screen — touch triggering + visual feedback."""

    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.pad_rects: list[pygame.Rect] = []
        self._active_pads: dict[int, float] = {}  # index -> velocity (for glow)
        self._selected_pad: int = 0
        self._pad_labels: list[str] = [str(i + 1) for i in range(16)]
        self._has_sample: list[bool] = [False] * 16
        self._recalculate()

    def _recalculate(self):
        """Calculate pad rectangles from grid rect."""
        self.pad_rects.clear()
        spacing = theme.PAD_SPACING
        cols = theme.PAD_GRID_COLS
        rows = theme.PAD_GRID_ROWS

        total_spacing_x = spacing * (cols + 1)
        total_spacing_y = spacing * (rows + 1)
        pad_w = (self.rect.width - total_spacing_x) // cols
        pad_h = (self.rect.height - total_spacing_y) // rows

        for row in range(rows):
            for col in range(cols):
                x = self.rect.x + spacing + col * (pad_w + spacing)
                y = self.rect.y + spacing + row * (pad_h + spacing)
                self.pad_rects.append(pygame.Rect(x, y, pad_w, pad_h))

    def set_rect(self, rect: pygame.Rect):
        self.rect = rect
        self._recalculate()

    def set_active(self, index: int, velocity: float):
        """Mark a pad as actively playing with velocity."""
        self._active_pads[index] = velocity

    def clear_active(self, index: int):
        """Clear active state for a pad."""
        self._active_pads.pop(index, None)

    def set_selected(self, index: int):
        self._selected_pad = index

    def set_has_sample(self, index: int, has: bool):
        if 0 <= index < 16:
            self._has_sample[index] = has

    def update_sample_states(self, has_sample_list: list[bool]):
        self._has_sample = list(has_sample_list)

    def draw(self, surface: pygame.Surface):
        """Draw the full pad grid."""
        for i, rect in enumerate(self.pad_rects):
            # Determine color
            if i in self._active_pads:
                vel = self._active_pads[i]
                color = theme.velocity_color(vel)
            elif i == self._selected_pad:
                color = theme.PAD_SELECTED
            elif self._has_sample[i] if i < len(self._has_sample) else False:
                color = (60, 60, 72)
            else:
                color = theme.PAD_OFF

            # Draw pad
            pygame.draw.rect(surface, color, rect, border_radius=4)

            # Border
            border_color = theme.ACCENT if i == self._selected_pad else theme.BORDER
            pygame.draw.rect(surface, border_color, rect, 2, border_radius=4)

            # Label
            f = theme.font("medium")
            label = self._pad_labels[i] if i < len(self._pad_labels) else ""
            text = f.render(label, True, theme.TEXT_DIM)
            text_rect = text.get_rect(center=rect.center)
            surface.blit(text, text_rect)

    def handle_event(self, event: pygame.event.Event) -> int:
        """Handle touch/mouse. Returns pad index (0–15) if tapped, or -1."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(self.pad_rects):
                if rect.collidepoint(event.pos):
                    return i
        return -1

    def decay_active(self):
        """Reduce active pad glow over time. Call each frame."""
        to_remove = []
        for idx in list(self._active_pads):
            self._active_pads[idx] *= 0.92
            if self._active_pads[idx] < 0.05:
                to_remove.append(idx)
        for idx in to_remove:
            del self._active_pads[idx]
