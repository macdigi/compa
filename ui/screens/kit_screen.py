"""Kit screen — load, save, rename, and manage kits."""

import pygame
from .. import theme
from ..components.button import Button
from ..components.modal import Modal


class KitScreen:
    """Kit management: list saved kits, save/load/rename/new."""

    def __init__(self, app):
        self.app = app
        self.kit_list: list[str] = []
        self.selected_index: int = -1
        self.scroll_offset: int = 0

        ITEM_H = 40
        self.ITEM_H = ITEM_H

        # Action buttons
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50
        btn_w = 120
        self.save_btn = Button(
            pygame.Rect(12, btn_y, btn_w, 38), "SAVE",
            color=theme.ACCENT,
        )
        self.load_btn = Button(
            pygame.Rect(12 + btn_w + 8, btn_y, btn_w, 38), "LOAD",
            color=theme.GREEN,
        )
        self.rename_btn = Button(
            pygame.Rect(12 + (btn_w + 8) * 2, btn_y, btn_w, 38), "RENAME",
        )
        self.new_btn = Button(
            pygame.Rect(12 + (btn_w + 8) * 3, btn_y, btn_w, 38), "NEW KIT",
        )
        self.delete_btn = Button(
            pygame.Rect(theme.SCREEN_WIDTH - btn_w - 12, btn_y, btn_w, 38), "DELETE",
            color=theme.RED,
        )

        # Modal
        self.modal = Modal("", "", ["OK", "Cancel"])

        # List area
        self.list_rect = pygame.Rect(
            0, theme.HEADER_HEIGHT,
            theme.SCREEN_WIDTH,
            btn_y - theme.HEADER_HEIGHT - 8,
        )

    def on_enter(self):
        """Refresh kit list when entering screen."""
        self._refresh_list()

    def _refresh_list(self):
        self.kit_list = self.app.kit_manager.list_kits()
        # Try to select current kit
        current = self.app.pad_bank.kit_name
        if current in self.kit_list:
            self.selected_index = self.kit_list.index(current)
        elif self.kit_list:
            self.selected_index = 0
        else:
            self.selected_index = -1

    def update(self):
        pass

    def draw(self, surface: pygame.Surface):
        """Draw the kit management screen."""
        # Header
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, theme.HEADER_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)
        f = theme.font("large")
        title = f.render(f"Kits  |  Current: {self.app.pad_bank.kit_name}", True, theme.TEXT_BRIGHT)
        surface.blit(title, (12, 6))

        # Kit list
        clip = surface.get_clip()
        surface.set_clip(self.list_rect)

        f_med = theme.font("medium")
        f_sm = theme.font("small")

        for i, kit_name in enumerate(self.kit_list):
            y = self.list_rect.y + i * self.ITEM_H - self.scroll_offset
            if y + self.ITEM_H < self.list_rect.y or y > self.list_rect.bottom:
                continue

            item_rect = pygame.Rect(0, y, theme.SCREEN_WIDTH, self.ITEM_H)

            # Highlight
            if i == self.selected_index:
                pygame.draw.rect(surface, theme.BG_LIGHTER, item_rect)

            # Current kit indicator
            if kit_name == self.app.pad_bank.kit_name:
                pygame.draw.circle(surface, theme.ACCENT, (20, y + self.ITEM_H // 2), 5)

            # Kit name
            name_surf = f_med.render(kit_name, True, theme.TEXT)
            surface.blit(name_surf, (36, y + 10))

            # Separator
            pygame.draw.line(surface, theme.BORDER,
                           (0, y + self.ITEM_H - 1),
                           (theme.SCREEN_WIDTH, y + self.ITEM_H - 1))

        surface.set_clip(clip)

        # Bottom button bar
        btn_bar_y = self.save_btn.rect.y - 6
        pygame.draw.rect(surface, theme.BG_PANEL,
                        (0, btn_bar_y, theme.SCREEN_WIDTH, 50))
        pygame.draw.line(surface, theme.BORDER,
                        (0, btn_bar_y), (theme.SCREEN_WIDTH, btn_bar_y))

        self.save_btn.draw(surface)
        self.load_btn.draw(surface)
        self.rename_btn.draw(surface)
        self.new_btn.draw(surface)
        self.delete_btn.draw(surface)

        # Memory usage
        mem_mb = self.app.pad_bank.memory_usage_bytes() / (1024 * 1024)
        mem_text = f_sm.render(f"RAM: {mem_mb:.0f}MB / 600MB", True,
                              theme.RED if mem_mb > 500 else theme.TEXT_DIM)
        surface.blit(mem_text, (theme.SCREEN_WIDTH - 160, 10))

        # Modal
        self.modal.draw(surface)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events."""
        # Modal takes priority
        if self.modal.visible:
            result = self.modal.handle_event(event)
            if result and result != "__consumed__":
                self._handle_modal_result(result)
            return result is not None

        # List tap
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.list_rect.collidepoint(event.pos):
                rel_y = event.pos[1] - self.list_rect.y + self.scroll_offset
                idx = int(rel_y // self.ITEM_H)
                if 0 <= idx < len(self.kit_list):
                    self.selected_index = idx
                return True

        # List scroll
        if event.type == pygame.MOUSEWHEEL:
            self.scroll_offset -= event.y * 30
            max_scroll = max(0, len(self.kit_list) * self.ITEM_H - self.list_rect.height)
            self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))
            return True

        # Buttons
        if self.save_btn.handle_event(event):
            self._save_kit()
            return True
        if self.load_btn.handle_event(event):
            self._load_kit()
            return True
        if self.rename_btn.handle_event(event):
            self._rename_kit()
            return True
        if self.new_btn.handle_event(event):
            self._new_kit()
            return True
        if self.delete_btn.handle_event(event):
            self._delete_kit()
            return True

        return False

    def _save_kit(self):
        """Save current kit."""
        self.modal.show(
            "Save Kit",
            "Enter kit name:",
            input_mode=True,
            default_text=self.app.pad_bank.kit_name,
        )
        self._pending_action = "save"

    def _load_kit(self):
        """Load selected kit."""
        if self.selected_index < 0 or self.selected_index >= len(self.kit_list):
            return
        kit_name = self.kit_list[self.selected_index]
        self.modal.show("Load Kit", f"Load '{kit_name}'? Unsaved changes will be lost.")
        self._pending_action = "load"
        self._pending_kit = kit_name

    def _rename_kit(self):
        """Rename selected kit."""
        if self.selected_index < 0:
            return
        kit_name = self.kit_list[self.selected_index]
        self.modal.show(
            "Rename Kit",
            f"Rename '{kit_name}' to:",
            input_mode=True,
            default_text=kit_name,
        )
        self._pending_action = "rename"
        self._pending_kit = kit_name

    def _new_kit(self):
        """Create new empty kit."""
        self.modal.show("New Kit", "This will clear all pads. Continue?")
        self._pending_action = "new"

    def _delete_kit(self):
        """Delete selected kit."""
        if self.selected_index < 0:
            return
        kit_name = self.kit_list[self.selected_index]
        self.modal.show("Delete Kit", f"Delete '{kit_name}'? This cannot be undone.")
        self._pending_action = "delete"
        self._pending_kit = kit_name

    def _handle_modal_result(self, result: str):
        """Process modal button press."""
        action = getattr(self, "_pending_action", None)
        if not action:
            return

        if result == "Cancel":
            self._pending_action = None
            return

        if action == "save":
            name = self.modal.input_text.strip()
            if name:
                self.app.kit_manager.save_kit(self.app.pad_bank, name)
                self._refresh_list()

        elif action == "load":
            kit_name = getattr(self, "_pending_kit", "")
            if self.app.kit_manager.load_kit(self.app.pad_bank, kit_name):
                # Reload all samples
                self.app.reload_all_samples()
                self._refresh_list()

        elif action == "rename":
            old_name = getattr(self, "_pending_kit", "")
            new_name = self.modal.input_text.strip()
            if old_name and new_name:
                self.app.kit_manager.rename_kit(old_name, new_name)
                if self.app.pad_bank.kit_name == old_name:
                    self.app.pad_bank.kit_name = new_name
                self._refresh_list()

        elif action == "new":
            self.app.pad_bank.clear()
            self._refresh_list()

        elif action == "delete":
            kit_name = getattr(self, "_pending_kit", "")
            self.app.kit_manager.delete_kit(kit_name)
            self._refresh_list()

        self._pending_action = None
