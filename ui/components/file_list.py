"""Scrollable touch file browser list."""

import os
import pygame
from .. import theme
from engine.sample_loader import SampleLoader


class FileListItem:
    """Single item in the file list."""
    def __init__(self, name: str, path: str, is_dir: bool):
        self.name = name
        self.path = path
        self.is_dir = is_dir


class FileList:
    """Scrollable, touch-friendly file browser list."""

    ITEM_HEIGHT = 44
    SCROLL_DECEL = 0.92

    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.items: list[FileListItem] = []
        self.scroll_offset: float = 0.0
        self.selected_index: int = -1
        self._scroll_velocity: float = 0.0
        self._drag_start_y: int = 0
        self._drag_start_offset: float = 0.0
        self._dragging = False
        self._last_touch_y = 0
        self._touch_moved = False

    def set_rect(self, rect: pygame.Rect):
        self.rect = rect

    def load_directory(self, path: str):
        """Load directory listing into the file list."""
        self.items.clear()
        self.scroll_offset = 0
        self.selected_index = -1

        if not os.path.isdir(path):
            return

        try:
            entries = os.listdir(path)
        except PermissionError:
            return

        dirs = []
        files = []

        for entry in sorted(entries, key=str.lower):
            if entry.startswith("."):
                continue
            full_path = os.path.join(path, entry)
            if os.path.isdir(full_path):
                dirs.append(FileListItem(entry + "/", full_path, True))
            elif SampleLoader.is_audio_file(entry):
                files.append(FileListItem(entry, full_path, False))

        # Parent directory entry
        parent = os.path.dirname(path)
        if parent != path:
            self.items.append(FileListItem("../", parent, True))

        self.items.extend(dirs)
        self.items.extend(files)

    @property
    def total_height(self) -> int:
        return len(self.items) * self.ITEM_HEIGHT

    @property
    def max_scroll(self) -> float:
        return max(0, self.total_height - self.rect.height)

    def _clamp_scroll(self):
        self.scroll_offset = max(0, min(self.scroll_offset, self.max_scroll))

    def update(self):
        """Update scroll physics."""
        if not self._dragging and abs(self._scroll_velocity) > 0.5:
            self.scroll_offset += self._scroll_velocity
            self._scroll_velocity *= self.SCROLL_DECEL
            self._clamp_scroll()
        elif not self._dragging:
            self._scroll_velocity = 0

    def draw(self, surface: pygame.Surface):
        """Draw the file list with scrollbar."""
        # Clip to rect
        clip = surface.get_clip()
        surface.set_clip(self.rect)

        # Background
        pygame.draw.rect(surface, theme.BG, self.rect)

        f = theme.font("medium")
        f_small = theme.font("small")
        y_start = self.rect.y - int(self.scroll_offset)

        for i, item in enumerate(self.items):
            y = y_start + i * self.ITEM_HEIGHT
            if y + self.ITEM_HEIGHT < self.rect.y or y > self.rect.bottom:
                continue

            item_rect = pygame.Rect(self.rect.x, y, self.rect.width - 10, self.ITEM_HEIGHT)

            # Selection highlight
            if i == self.selected_index:
                pygame.draw.rect(surface, theme.BG_LIGHTER, item_rect)

            # Icon
            icon = "D " if item.is_dir else "~ "
            icon_color = theme.YELLOW if item.is_dir else theme.WAVEFORM_COLOR
            icon_surf = f.render(icon, True, icon_color)
            surface.blit(icon_surf, (item_rect.x + 8, item_rect.y + 10))

            # Name
            name_surf = f.render(item.name, True, theme.TEXT)
            surface.blit(name_surf, (item_rect.x + 36, item_rect.y + 10))

            # Separator
            pygame.draw.line(surface, theme.BORDER,
                           (item_rect.x, item_rect.bottom - 1),
                           (item_rect.right, item_rect.bottom - 1))

        # Scrollbar
        if self.total_height > self.rect.height:
            sb_height = max(20, int(self.rect.height * self.rect.height / self.total_height))
            sb_y = self.rect.y + int(self.scroll_offset / self.max_scroll * (self.rect.height - sb_height))
            sb_rect = pygame.Rect(self.rect.right - 6, sb_y, 4, sb_height)
            pygame.draw.rect(surface, theme.SCROLLBAR_THUMB, sb_rect, border_radius=2)

        # Restore clip
        surface.set_clip(clip)

    def handle_event(self, event: pygame.event.Event) -> FileListItem | None:
        """Handle touch events. Returns selected item on tap (not drag)."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self._dragging = True
                self._drag_start_y = event.pos[1]
                self._drag_start_offset = self.scroll_offset
                self._last_touch_y = event.pos[1]
                self._touch_moved = False
                self._scroll_velocity = 0
                return None

        elif event.type == pygame.MOUSEMOTION and self._dragging:
            dy = event.pos[1] - self._last_touch_y
            if abs(event.pos[1] - self._drag_start_y) > 8:
                self._touch_moved = True
            self._scroll_velocity = -dy
            self.scroll_offset = self._drag_start_offset - (event.pos[1] - self._drag_start_y)
            self._clamp_scroll()
            self._last_touch_y = event.pos[1]

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_dragging = self._dragging
            self._dragging = False

            if was_dragging and not self._touch_moved:
                # Tap — select item
                rel_y = event.pos[1] - self.rect.y + self.scroll_offset
                index = int(rel_y // self.ITEM_HEIGHT)
                if 0 <= index < len(self.items):
                    self.selected_index = index
                    return self.items[index]

        return None
