"""Waveform display with draggable start/end markers."""

import numpy as np
import pygame
from .. import theme


class WaveformDisplay:
    """Displays a waveform with interactive start/end markers."""

    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.waveform_data: np.ndarray | None = None
        self.total_frames: int = 0
        self.start_frame: int = 0
        self.end_frame: int = 0
        self._dragging_start = False
        self._dragging_end = False
        self._surface: pygame.Surface | None = None
        self._dirty = True

    def set_rect(self, rect: pygame.Rect):
        self.rect = rect
        self._dirty = True

    def set_data(self, waveform_preview: np.ndarray | None,
                 total_frames: int, start: int, end: int):
        """Set waveform data and marker positions."""
        self.waveform_data = waveform_preview
        self.total_frames = total_frames
        self.start_frame = start
        self.end_frame = end if end > 0 else total_frames
        self._dirty = True

    def _frame_to_x(self, frame: int) -> int:
        """Convert a sample frame to pixel x position."""
        if self.total_frames <= 0:
            return self.rect.x
        ratio = frame / self.total_frames
        return self.rect.x + int(ratio * self.rect.width)

    def _x_to_frame(self, x: int) -> int:
        """Convert pixel x to sample frame."""
        if self.rect.width <= 0:
            return 0
        ratio = (x - self.rect.x) / self.rect.width
        ratio = max(0.0, min(1.0, ratio))
        return int(ratio * self.total_frames)

    def _render_waveform(self):
        """Pre-render waveform to cached surface."""
        self._surface = pygame.Surface((self.rect.width, self.rect.height))
        self._surface.fill(theme.WAVEFORM_BG)

        if self.waveform_data is None or len(self.waveform_data) == 0:
            self._dirty = False
            return

        w = self.rect.width
        h = self.rect.height
        mid_y = h // 2
        data = self.waveform_data

        # Resample to pixel width
        n_points = min(w, len(data))
        if n_points <= 0:
            self._dirty = False
            return

        indices = np.linspace(0, len(data) - 1, n_points).astype(int)
        display_data = data[indices]

        # Draw waveform bars
        for i in range(n_points):
            amplitude = display_data[i]
            bar_h = int(amplitude * mid_y * 0.9)
            if bar_h < 1:
                bar_h = 1

            # Dim outside start/end region
            frame = int((i / n_points) * self.total_frames)
            if frame < self.start_frame or frame > self.end_frame:
                color = (40, 80, 100)
            else:
                color = theme.WAVEFORM_COLOR

            pygame.draw.line(self._surface, color,
                           (i, mid_y - bar_h), (i, mid_y + bar_h))

        # Center line
        pygame.draw.line(self._surface, theme.BORDER, (0, mid_y), (w, mid_y), 1)

        self._dirty = False

    def draw(self, surface: pygame.Surface):
        """Draw the waveform display with markers."""
        if self._dirty or self._surface is None:
            self._render_waveform()

        # Blit cached waveform
        surface.blit(self._surface, self.rect)

        # Border
        pygame.draw.rect(surface, theme.BORDER, self.rect, 1)

        # Start marker
        sx = self._frame_to_x(self.start_frame)
        pygame.draw.line(surface, theme.GREEN,
                        (sx, self.rect.y), (sx, self.rect.bottom), 2)
        # Start handle
        handle_rect = pygame.Rect(sx - 6, self.rect.y, 12, 14)
        pygame.draw.rect(surface, theme.GREEN, handle_rect)
        f = theme.font("small")
        s_label = f.render("S", True, theme.BG)
        surface.blit(s_label, s_label.get_rect(center=handle_rect.center))

        # End marker
        ex = self._frame_to_x(self.end_frame)
        pygame.draw.line(surface, theme.RED,
                        (ex, self.rect.y), (ex, self.rect.bottom), 2)
        handle_rect = pygame.Rect(ex - 6, self.rect.y, 12, 14)
        pygame.draw.rect(surface, theme.RED, handle_rect)
        e_label = f.render("E", True, theme.BG)
        surface.blit(e_label, e_label.get_rect(center=handle_rect.center))

    def handle_event(self, event: pygame.event.Event) -> tuple[bool, int, int]:
        """Handle drag events for start/end markers.
        Returns (changed, start_frame, end_frame)."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                sx = self._frame_to_x(self.start_frame)
                ex = self._frame_to_x(self.end_frame)
                x = event.pos[0]

                # Check which marker is closer
                dist_s = abs(x - sx)
                dist_e = abs(x - ex)

                if dist_s < 20 and dist_s <= dist_e:
                    self._dragging_start = True
                elif dist_e < 20:
                    self._dragging_end = True
                elif x < (sx + ex) // 2:
                    self._dragging_start = True
                else:
                    self._dragging_end = True

        elif event.type == pygame.MOUSEMOTION:
            if self._dragging_start:
                frame = self._x_to_frame(event.pos[0])
                frame = max(0, min(frame, self.end_frame - 100))
                self.start_frame = frame
                self._dirty = True
                return (True, self.start_frame, self.end_frame)

            elif self._dragging_end:
                frame = self._x_to_frame(event.pos[0])
                frame = max(self.start_frame + 100, min(frame, self.total_frames))
                self.end_frame = frame
                self._dirty = True
                return (True, self.start_frame, self.end_frame)

        elif event.type == pygame.MOUSEBUTTONUP:
            was_dragging = self._dragging_start or self._dragging_end
            self._dragging_start = False
            self._dragging_end = False
            if was_dragging:
                return (True, self.start_frame, self.end_frame)

        return (False, self.start_frame, self.end_frame)
