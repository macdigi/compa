"""File browser screen — navigate sample library, preview, and assign."""

import os
import pygame
from .. import theme
from ..components.file_list import FileList, FileListItem
from ..components.button import Button


DEFAULT_BROWSE_PATHS = ["/mnt/samples", os.path.expanduser("~/pi-sampler/samples")]


class BrowserScreen:
    """Sample file browser with preview and assign to pad."""

    def __init__(self, app):
        self.app = app
        self.current_path: str = ""

        # File list
        list_y = theme.HEADER_HEIGHT
        list_h = theme.SCREEN_HEIGHT - theme.HEADER_HEIGHT - theme.NAV_HEIGHT - 50
        self.file_list = FileList(pygame.Rect(
            0, list_y, theme.SCREEN_WIDTH, list_h
        ))

        # Bottom buttons
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 46
        self.assign_btn = Button(
            pygame.Rect(12, btn_y, 200, 38),
            "ASSIGN TO PAD",
            color=theme.ACCENT,
        )
        self.preview_btn = Button(
            pygame.Rect(224, btn_y, 120, 38),
            "PREVIEW",
            color=theme.GREEN,
        )
        self.cancel_btn = Button(
            pygame.Rect(theme.SCREEN_WIDTH - 120, btn_y, 108, 38),
            "CANCEL",
        )

        self._previewing = False

        # Find initial browse path
        self._init_path()

    def _init_path(self):
        """Find the first valid browse path."""
        # Check config for local cache
        local_cache = getattr(self.app, "local_cache_dir", None)
        paths = DEFAULT_BROWSE_PATHS[:]
        if local_cache:
            paths.insert(0, local_cache)

        for p in paths:
            if os.path.isdir(p):
                self.current_path = p
                break
        else:
            self.current_path = os.path.expanduser("~")

    def on_enter(self):
        """Called when screen becomes active."""
        if not self.current_path or not os.path.isdir(self.current_path):
            self._init_path()
        self.file_list.load_directory(self.current_path)

    def update(self):
        self.file_list.update()
        # Update assign button label with current pad
        pad_idx = self.app.pad_bank.selected_pad + 1
        bank = self.app.pad_bank.current_bank
        self.assign_btn.label = f"ASSIGN TO PAD {pad_idx}{bank}"

    def draw(self, surface: pygame.Surface):
        """Draw the browser screen."""
        # Header with current path
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, theme.HEADER_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)

        f = theme.font("medium")
        path_display = self.current_path
        if len(path_display) > 60:
            path_display = "..." + path_display[-57:]
        path_surf = f.render(path_display, True, theme.TEXT)
        surface.blit(path_surf, (12, 8))

        # File list
        self.file_list.draw(surface)

        # Bottom bar
        bottom_rect = pygame.Rect(
            0, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50,
            theme.SCREEN_WIDTH, 50,
        )
        pygame.draw.rect(surface, theme.BG_PANEL, bottom_rect)
        pygame.draw.line(surface, theme.BORDER,
                        (0, bottom_rect.y), (theme.SCREEN_WIDTH, bottom_rect.y))

        self.assign_btn.draw(surface)
        self.preview_btn.draw(surface)
        self.cancel_btn.draw(surface)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events."""
        # File list tap
        item = self.file_list.handle_event(event)
        if item is not None:
            if item.is_dir:
                self.current_path = item.path
                self.file_list.load_directory(self.current_path)
            return True

        # Assign button
        if self.assign_btn.handle_event(event):
            self._assign_selected()
            return True

        # Preview button
        if self.preview_btn.handle_event(event):
            self._preview_selected()
            return True

        # Cancel
        if self.cancel_btn.handle_event(event):
            self.app.audio_engine.stop_preview()
            self.app.switch_screen("main")
            return True

        return False

    def _get_selected_file(self) -> str | None:
        """Get the path of the currently selected audio file."""
        idx = self.file_list.selected_index
        if idx < 0 or idx >= len(self.file_list.items):
            return None
        item = self.file_list.items[idx]
        if item.is_dir:
            return None
        return item.path

    def _preview_selected(self):
        """Preview the selected audio file."""
        path = self._get_selected_file()
        if path is None:
            return
        audio_data = self.app.sample_loader.load_preview(path)
        if audio_data is not None:
            self.app.audio_engine.preview_sample(audio_data)

    def _assign_selected(self):
        """Assign selected file to the current pad."""
        path = self._get_selected_file()
        if path is None:
            return

        pad = self.app.pad_bank.selected
        self.app.sample_loader.load_sample(
            path, pad,
            on_complete=self._on_sample_loaded,
        )
        self.app.audio_engine.stop_preview()

    def _on_sample_loaded(self, pad, success):
        """Callback when sample finishes loading."""
        if success:
            self.app.switch_screen("main")
