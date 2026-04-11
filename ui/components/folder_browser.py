"""Touch-friendly folder browser — wraps TouchList with directory navigation.

Provides breadcrumb path display, back button, directory-first sorting,
file size display, and navigation history. Works with any directory tree.

Usage::

    browser = FolderBrowser(
        rect=pygame.Rect(0, 40, 600, 500),
        root_dir="/media/pi/Force_SSD/Samples",
        file_filter=lambda f: f.endswith(".wav"),
    )
    browser.navigate_to("/media/pi/Force_SSD/Samples")

    # In event loop:
    result = browser.handle_event(event)
    if result and result["type"] == "file":
        print("Selected:", result["path"])

    # In update/draw:
    browser.update()
    browser.draw(surface)
"""

import os
import pygame
from typing import Callable, Optional
from .touch_list import TouchList, TouchListItem
from .. import theme


def _human_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"


# File extension → icon mappings
_EXT_ICONS = {
    ".wav": ("~", None),
    ".aif": ("~", None),
    ".aiff": ("~", None),
    ".mp3": ("~", None),
    ".flac": ("~", None),
    ".xpm": ("K", (255, 180, 50)),   # Kit/program
    ".xpj": ("P", (100, 200, 255)),  # Project
    ".adg": ("A", (100, 255, 100)),  # Ableton
}

BREADCRUMB_HEIGHT = 32
BACK_BUTTON_WIDTH = 50


class FolderBrowser:
    """Touch-friendly folder browser with breadcrumb navigation."""

    def __init__(self, rect: pygame.Rect, root_dir: str = "",
                 file_filter: Optional[Callable[[str], bool]] = None,
                 item_height: int = 48, multi_select: bool = False):
        """
        Args:
            rect: Total area including breadcrumb bar.
            root_dir: Starting directory.
            file_filter: Optional function(filename) -> bool for filtering files.
            item_height: Height per row in the list.
            multi_select: Enable multi-select checkboxes.
        """
        self._full_rect = rect
        self._root_dir = root_dir
        self._file_filter = file_filter
        self._current_path = root_dir
        self._history: list[str] = []

        # TouchList occupies area below breadcrumb bar
        list_rect = pygame.Rect(
            rect.x, rect.y + BREADCRUMB_HEIGHT,
            rect.width, rect.height - BREADCRUMB_HEIGHT)
        self._list = TouchList(list_rect, item_height=item_height,
                               multi_select=multi_select)

        if root_dir and os.path.isdir(root_dir):
            self.navigate_to(root_dir)

    def set_rect(self, rect: pygame.Rect):
        self._full_rect = rect
        list_rect = pygame.Rect(
            rect.x, rect.y + BREADCRUMB_HEIGHT,
            rect.width, rect.height - BREADCRUMB_HEIGHT)
        self._list.set_rect(list_rect)

    @property
    def current_path(self) -> str:
        return self._current_path

    @property
    def selected_items(self) -> list[TouchListItem]:
        return self._list.selected_items()

    def selected_count(self) -> int:
        return self._list.selected_count()

    def clear_selection(self):
        self._list.clear_selection()

    def select_all(self):
        self._list.select_all()

    # ── Navigation ───────────────────────────────────────────────────

    def navigate_to(self, path: str):
        """Load a directory into the list."""
        if not os.path.isdir(path):
            return

        # Push current path to history (for back button)
        if self._current_path and self._current_path != path:
            self._history.append(self._current_path)

        self._current_path = path
        self._load_directory(path)

    def go_back(self):
        """Navigate to the previous directory in history."""
        if self._history:
            prev = self._history.pop()
            self._current_path = prev
            self._load_directory(prev)
        elif self._current_path != self._root_dir:
            # Go to parent
            parent = os.path.dirname(self._current_path)
            if parent and parent != self._current_path:
                self._current_path = parent
                self._load_directory(parent)

    def refresh(self):
        """Reload the current directory."""
        self._load_directory(self._current_path)

    def _load_directory(self, path: str):
        """Scan directory and populate the TouchList."""
        items: list[TouchListItem] = []

        # Parent directory entry (if not at root)
        parent = os.path.dirname(path)
        if parent and parent != path and path != self._root_dir:
            items.append(TouchListItem(
                text="..",
                subtext="Back",
                icon="<",
                icon_color=theme.TEXT_DIM,
                data={"type": "parent", "path": parent},
                is_dir=True,
            ))

        try:
            entries = sorted(os.listdir(path), key=str.lower)
        except PermissionError:
            entries = []

        dirs = []
        files = []

        for entry in entries:
            if entry.startswith("."):
                continue
            full_path = os.path.join(path, entry)

            if os.path.isdir(full_path):
                # Count items in subdirectory
                try:
                    count = len(os.listdir(full_path))
                    subtext = f"{count} items"
                except PermissionError:
                    subtext = ""

                dirs.append(TouchListItem(
                    text=entry,
                    subtext=subtext,
                    icon="D",
                    icon_color=theme.YELLOW,
                    data={"type": "dir", "path": full_path, "name": entry},
                    is_dir=True,
                ))
            else:
                ext = os.path.splitext(entry)[1].lower()
                # Apply file filter
                if self._file_filter and not self._file_filter(entry):
                    continue

                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    size = 0

                icon, icon_color = _EXT_ICONS.get(ext, ("", None))
                if icon_color is None:
                    icon_color = theme.ACCENT

                files.append(TouchListItem(
                    text=entry,
                    subtext=_human_size(size),
                    icon=icon or ext[1:3].upper() if ext else "?",
                    icon_color=icon_color,
                    data={"type": "file", "path": full_path, "name": entry,
                          "size": size, "ext": ext},
                    is_dir=False,
                ))

        items.extend(dirs)
        items.extend(files)
        self._list.set_items(items)

    # ── Event handling ───────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event) -> Optional[dict]:
        """Handle events. Returns data dict on file/dir selection.

        Returns:
            {"type": "file", "path": ..., "name": ...} for file taps
            {"type": "dir", "path": ...} for directory navigation (auto-handled)
            None for drag/scroll/no action
        """
        # Back button
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            back_rect = pygame.Rect(self._full_rect.x, self._full_rect.y,
                                     BACK_BUTTON_WIDTH, BREADCRUMB_HEIGHT)
            if back_rect.collidepoint(event.pos):
                self.go_back()
                return None

        # List events
        tapped = self._list.handle_event(event)
        if tapped and tapped.data:
            data = tapped.data
            if data.get("type") == "parent":
                self.go_back()
                return None
            elif data.get("type") == "dir":
                self.navigate_to(data["path"])
                return None
            else:
                return data  # File selected — return to caller

        return None

    def update(self):
        self._list.update()

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # ── Breadcrumb bar ───────────────────────────────────────────
        bc_rect = pygame.Rect(self._full_rect.x, self._full_rect.y,
                               self._full_rect.width, BREADCRUMB_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, bc_rect)
        pygame.draw.line(surface, theme.BORDER,
                        (bc_rect.x, bc_rect.bottom - 1),
                        (bc_rect.right, bc_rect.bottom - 1))

        # Back button
        back_rect = pygame.Rect(bc_rect.x, bc_rect.y,
                                 BACK_BUTTON_WIDTH, BREADCRUMB_HEIGHT)
        has_back = bool(self._history) or self._current_path != self._root_dir
        back_color = theme.TEXT if has_back else theme.TEXT_DIM
        pygame.draw.rect(surface, theme.BUTTON_BG, back_rect)
        pygame.draw.rect(surface, theme.BORDER, back_rect, 1)
        surf = f_small.render("<", True, back_color)
        surface.blit(surf, surf.get_rect(center=back_rect.center))

        # Path display
        display_path = self._current_path
        # Shorten if too long
        max_path_w = self._full_rect.width - BACK_BUTTON_WIDTH - 20
        path_surf = f_small.render(display_path, True, theme.ACCENT)
        while path_surf.get_width() > max_path_w and len(display_path) > 10:
            # Trim from the beginning
            parts = display_path.split("/")
            if len(parts) > 2:
                display_path = ".../" + "/".join(parts[-2:])
            else:
                display_path = "..." + display_path[-20:]
            path_surf = f_small.render(display_path, True, theme.ACCENT)

        surface.blit(path_surf, (bc_rect.x + BACK_BUTTON_WIDTH + 10,
                                  bc_rect.y + (BREADCRUMB_HEIGHT - path_surf.get_height()) // 2))

        # Item count
        count_text = f"{len(self._list.items)} items"
        count_surf = f_tiny.render(count_text, True, theme.TEXT_DIM)
        surface.blit(count_surf, (bc_rect.right - count_surf.get_width() - 8,
                                   bc_rect.y + (BREADCRUMB_HEIGHT - count_surf.get_height()) // 2))

        # ── File list ────────────────────────────────────────────────
        self._list.draw(surface)

        # Empty state
        if self._list.is_empty:
            empty_surf = f_small.render("Empty folder", True, theme.TEXT_DIM)
            center_y = self._list.rect.y + self._list.rect.height // 2
            surface.blit(empty_surf, empty_surf.get_rect(
                centerx=self._full_rect.centerx, centery=center_y))
