"""Generic touch-friendly scrollable list component.

Drop-in replacement for manual scroll implementations across Compa.
Based on the proven patterns from FileList — drag-to-scroll with
momentum, tap-vs-drag detection, visible draggable scrollbar.

Usage::

    items = [TouchListItem("My File", subtext="2.4MB", data={"path": "/foo"})]
    tlist = TouchList(pygame.Rect(0, 0, 400, 500), multi_select=True)
    tlist.set_items(items)

    # In event loop:
    tapped = tlist.handle_event(event)
    if tapped:
        print(tapped.data)

    # In update:
    tlist.update()

    # In draw:
    tlist.draw(surface)
"""

import pygame
from dataclasses import dataclass, field
from typing import Any, Optional
from .. import theme


@dataclass
class TouchListItem:
    """One row in a TouchList."""
    text: str = ""              # Primary display text
    subtext: str = ""           # Right-aligned secondary info
    icon: str = ""              # Short icon label (1-2 chars)
    icon_color: tuple = (0, 0, 0)  # RGB for icon; (0,0,0) = use default
    data: Any = None            # Arbitrary payload for caller
    is_dir: bool = False        # Directory-style item (bold, folder color)
    selected: bool = False      # Multi-select checkbox state
    color: tuple = (0, 0, 0)    # Custom text color; (0,0,0) = use default


class TouchList:
    """Touch-friendly scrollable list with momentum and optional multi-select.

    Handles drag-to-scroll, momentum inertia, tap detection, visible
    scrollbar, and multi-select checkboxes. Works with any data — not
    tied to file system.
    """

    DRAG_THRESHOLD = 8       # px before registering as drag (not tap)
    SCROLL_DECEL = 0.92      # Momentum decay per frame
    SCROLLBAR_WIDTH = 20     # Wide enough for finger taps
    CHECKBOX_SIZE = 28       # Multi-select checkbox size

    def __init__(self, rect: pygame.Rect, item_height: int = 48,
                 show_scrollbar: bool = True, multi_select: bool = False):
        self.rect = rect
        self.item_height = item_height
        self.show_scrollbar = show_scrollbar
        self.multi_select = multi_select

        self.items: list[TouchListItem] = []
        self.scroll_offset: float = 0.0
        self._scroll_velocity: float = 0.0

        # Touch state
        self._dragging = False
        self._drag_start_y = 0
        self._drag_start_offset = 0.0
        self._last_touch_y = 0
        self._touch_moved = False

        # Scrollbar drag state
        self._sb_dragging = False

    def set_rect(self, rect: pygame.Rect):
        self.rect = rect

    def set_items(self, items: list[TouchListItem]):
        """Replace the item list. Resets scroll position."""
        self.items = items
        self.scroll_offset = 0.0
        self._scroll_velocity = 0.0

    def clear_selection(self):
        """Deselect all items (multi-select mode)."""
        for item in self.items:
            item.selected = False

    def selected_items(self) -> list[TouchListItem]:
        """Get all selected items (multi-select mode)."""
        return [item for item in self.items if item.selected]

    def selected_count(self) -> int:
        return sum(1 for item in self.items if item.selected)

    def select_all(self):
        for item in self.items:
            item.selected = True

    # ── Geometry ─────────────────────────────────────────────────────

    @property
    def total_height(self) -> int:
        return len(self.items) * self.item_height

    @property
    def max_scroll(self) -> float:
        return max(0, self.total_height - self.rect.height)

    @property
    def content_width(self) -> int:
        """Usable width (minus scrollbar if shown)."""
        if self.show_scrollbar and self.total_height > self.rect.height:
            return self.rect.width - self.SCROLLBAR_WIDTH
        return self.rect.width

    def _clamp_scroll(self):
        self.scroll_offset = max(0.0, min(self.scroll_offset, self.max_scroll))

    # ── Update (momentum physics) ────────────────────────────────────

    def update(self):
        """Call every frame to apply momentum scrolling."""
        if not self._dragging and not self._sb_dragging:
            if abs(self._scroll_velocity) > 0.5:
                self.scroll_offset += self._scroll_velocity
                self._scroll_velocity *= self.SCROLL_DECEL
                self._clamp_scroll()
            else:
                self._scroll_velocity = 0.0

    # ── Event handling ───────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event) -> Optional[TouchListItem]:
        """Handle touch/mouse events.

        Returns the tapped item on a clean tap (no drag).
        In multi_select mode, toggles the item's selected state.
        """
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if not self.rect.collidepoint(event.pos):
                return None

            mx, my = event.pos

            # Scrollbar drag?
            if self.show_scrollbar and self.total_height > self.rect.height:
                sb_x = self.rect.right - self.SCROLLBAR_WIDTH
                if mx >= sb_x:
                    self._sb_dragging = True
                    self._update_scroll_from_sb(my)
                    return None

            # Content drag
            self._dragging = True
            self._drag_start_y = my
            self._drag_start_offset = self.scroll_offset
            self._last_touch_y = my
            self._touch_moved = False
            self._scroll_velocity = 0.0
            return None

        elif event.type == pygame.MOUSEMOTION:
            if self._sb_dragging:
                self._update_scroll_from_sb(event.pos[1])
                return None

            if self._dragging:
                dy = event.pos[1] - self._last_touch_y
                total_dy = abs(event.pos[1] - self._drag_start_y)
                if total_dy > self.DRAG_THRESHOLD:
                    self._touch_moved = True
                self._scroll_velocity = -dy
                self.scroll_offset = self._drag_start_offset - (event.pos[1] - self._drag_start_y)
                self._clamp_scroll()
                self._last_touch_y = event.pos[1]

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._sb_dragging:
                self._sb_dragging = False
                return None

            was_dragging = self._dragging
            self._dragging = False

            if was_dragging and not self._touch_moved:
                # Clean tap — identify which item
                rel_y = event.pos[1] - self.rect.y + self.scroll_offset
                index = int(rel_y // self.item_height)
                if 0 <= index < len(self.items):
                    item = self.items[index]
                    if self.multi_select:
                        item.selected = not item.selected
                    return item

        # Wheel scroll fallback (for mouse users)
        elif event.type == pygame.MOUSEBUTTONDOWN and self.rect.collidepoint(event.pos):
            if event.button == 4:
                self.scroll_offset = max(0, self.scroll_offset - self.item_height * 2)
            elif event.button == 5:
                self.scroll_offset = min(self.max_scroll, self.scroll_offset + self.item_height * 2)

        return None

    def _update_scroll_from_sb(self, mouse_y: int):
        """Update scroll from scrollbar drag position."""
        frac = (mouse_y - self.rect.y) / max(1, self.rect.height)
        frac = max(0.0, min(1.0, frac))
        self.scroll_offset = frac * self.max_scroll
        self._clamp_scroll()

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        """Render the list with clipping, scrollbar, and optional checkboxes."""
        clip = surface.get_clip()
        surface.set_clip(self.rect)

        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        y_start = self.rect.y - int(self.scroll_offset)
        cw = self.content_width

        for i, item in enumerate(self.items):
            y = y_start + i * self.item_height
            # Skip off-screen items
            if y + self.item_height < self.rect.y or y > self.rect.bottom:
                continue

            row_rect = pygame.Rect(self.rect.x, y, cw, self.item_height)

            # Alternating background
            if i % 2 == 0:
                pygame.draw.rect(surface, theme.BG_PANEL, row_rect)

            # Selection highlight (multi-select)
            if item.selected:
                pygame.draw.rect(surface, theme.ACCENT_DIM, row_rect)
                pygame.draw.rect(surface, theme.ACCENT, row_rect, 1)

            x = self.rect.x + 8

            # Checkbox (multi-select mode)
            if self.multi_select:
                cb_size = self.CHECKBOX_SIZE
                cb_y = y + (self.item_height - cb_size) // 2
                cb_rect = pygame.Rect(x, cb_y, cb_size, cb_size)
                if item.selected:
                    pygame.draw.rect(surface, theme.GREEN, cb_rect, border_radius=4)
                    check = f_med.render("v", True, theme.BG)
                    surface.blit(check, check.get_rect(center=cb_rect.center))
                else:
                    pygame.draw.rect(surface, theme.BORDER, cb_rect, 2, border_radius=4)
                x += cb_size + 8

            # Icon
            if item.icon:
                ic = item.icon_color if item.icon_color != (0, 0, 0) else (
                    theme.YELLOW if item.is_dir else theme.ACCENT)
                icon_surf = f_med.render(item.icon, True, ic)
                surface.blit(icon_surf, (x, y + (self.item_height - icon_surf.get_height()) // 2))
                x += icon_surf.get_width() + 6

            # Primary text
            text_color = item.color if item.color != (0, 0, 0) else (
                theme.TEXT_BRIGHT if item.is_dir else theme.TEXT)
            text_surf = f_med.render(item.text, True, text_color)
            text_y = y + (self.item_height - text_surf.get_height()) // 2
            if item.subtext:
                text_y = y + 4  # Push up if subtext present
            max_text_w = self.rect.x + cw - x - 10
            if text_surf.get_width() > max_text_w:
                # Truncate
                truncated = item.text
                while text_surf.get_width() > max_text_w and len(truncated) > 3:
                    truncated = truncated[:-4] + "..."
                    text_surf = f_med.render(truncated, True, text_color)
            surface.blit(text_surf, (x, text_y))

            # Subtext (right-aligned or second line)
            if item.subtext:
                sub_surf = f_small.render(item.subtext, True, theme.TEXT_DIM)
                sub_x = self.rect.x + cw - sub_surf.get_width() - 8
                sub_y = y + self.item_height - sub_surf.get_height() - 6
                surface.blit(sub_surf, (sub_x, sub_y))

            # Row separator
            sep_y = y + self.item_height - 1
            pygame.draw.line(surface, theme.BORDER,
                           (self.rect.x, sep_y), (self.rect.x + cw, sep_y))

        # ── Scrollbar ────────────────────────────────────────────────
        if self.show_scrollbar and self.total_height > self.rect.height:
            sb_x = self.rect.right - self.SCROLLBAR_WIDTH
            sb_h = self.rect.height

            # Track
            track_rect = pygame.Rect(sb_x, self.rect.y, self.SCROLLBAR_WIDTH, sb_h)
            pygame.draw.rect(surface, theme.BG_PANEL, track_rect)
            pygame.draw.line(surface, theme.BORDER,
                           (sb_x, self.rect.y), (sb_x, self.rect.bottom))

            # Thumb
            thumb_h = max(30, int(sb_h * self.rect.height / self.total_height))
            thumb_frac = self.scroll_offset / max(1, self.max_scroll)
            thumb_y = self.rect.y + int(thumb_frac * (sb_h - thumb_h))
            thumb_rect = pygame.Rect(sb_x + 3, thumb_y, self.SCROLLBAR_WIDTH - 6, thumb_h)

            thumb_color = theme.ACCENT if self._sb_dragging else theme.SCROLLBAR_THUMB
            pygame.draw.rect(surface, thumb_color, thumb_rect, border_radius=4)

        surface.set_clip(clip)

    # ── Utility ──────────────────────────────────────────────────────

    def scroll_to_top(self):
        self.scroll_offset = 0.0
        self._scroll_velocity = 0.0

    def scroll_to_bottom(self):
        self.scroll_offset = self.max_scroll
        self._scroll_velocity = 0.0

    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0
