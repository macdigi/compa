"""P-6 Help Screen — searchable reference manual with sidebar navigation."""

import os
import pygame
from .. import theme


# Section icons/categories for visual grouping
SECTION_COLORS = {
    "GETTING STARTED": (100, 200, 120),
    "PADS AND BANKS": (255, 180, 80),
    "GRANULAR ENGINE": (140, 200, 255),
    "SEQUENCER": (255, 120, 180),
    "PATTERN EDITING": (255, 120, 180),
    "SAMPLING": (200, 160, 255),
    "AUTO-CHOP": (200, 160, 255),
    "FILTER": (140, 200, 255),
    "ENVELOPE": (140, 200, 255),
    "MIXER": (140, 200, 255),
    "EFFECTS": (140, 200, 255),
    "PERFORMANCE FX": (255, 180, 80),
    "USB BACKUP MODES": (255, 100, 100),
    "MIDI CHANNELS": (100, 220, 200),
    "MIDI CCS - GRANULAR": (100, 220, 200),
    "MIDI CCS - FILTER": (100, 220, 200),
    "MIDI CCS - ENVELOPE": (100, 220, 200),
    "MIDI CCS - MIXER": (100, 220, 200),
    "MIDI CCS - EFFECTS": (100, 220, 200),
    "MIDI NOTES": (100, 220, 200),
    "MENU NAVIGATION": (200, 200, 100),
    "MENU ITEMS": (200, 200, 100),
    "SYNC OPTIONS": (200, 200, 100),
    "FIRMWARE": (200, 200, 100),
    "TIPS AND TRICKS": (255, 220, 80),
    "TROUBLESHOOTING": (255, 100, 100),
}

SIDEBAR_WIDTH = 180


class P6HelpScreen:
    """Split-pane reference manual: sidebar TOC + scrollable content with search."""

    def __init__(self, app):
        self.app = app
        self._search_text = ""
        self._cursor_timer = 0
        self._scroll_y = 0
        self._sidebar_scroll = 0
        self._active_section = 0  # index into _sections

        # Load reference text
        docs_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "docs", "p6_reference.txt")
        try:
            with open(docs_path) as f:
                self._full_text = f.read()
        except Exception:
            self._full_text = "Reference file not found."

        self._sections = self._parse_sections()
        self._filtered: list[tuple[str, list[str]]] = list(self._sections)

    @property
    def wants_keyboard(self) -> bool:
        return True

    def _parse_sections(self) -> list[tuple[str, list[str]]]:
        sections = []
        current_title = ""
        current_lines: list[str] = []

        for line in self._full_text.split("\n"):
            if line.startswith("== ") and line.endswith(" =="):
                if current_title or current_lines:
                    sections.append((current_title, current_lines))
                current_title = line.strip("= ").strip()
                current_lines = []
            elif line.startswith("=== "):
                if current_title or current_lines:
                    sections.append((current_title, current_lines))
                current_title = line.strip("= ").strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_title or current_lines:
            sections.append((current_title, current_lines))
        return sections

    def _update_filter(self):
        query = self._search_text.lower().strip()
        if not query:
            self._filtered = list(self._sections)
            return

        self._filtered = []
        for title, lines in self._sections:
            title_match = query in title.lower()
            matching_lines = [l for l in lines if query in l.lower()]
            if title_match:
                self._filtered.append((title, lines))
            elif matching_lines:
                self._filtered.append((title, matching_lines))
        self._scroll_y = 0

    def on_enter(self):
        self._search_text = ""
        self._scroll_y = 0
        self._filtered = list(self._sections)

    def on_exit(self):
        pass

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.app.switch_screen("session")
                return
            elif event.key == pygame.K_BACKSPACE:
                self._search_text = self._search_text[:-1]
                self._update_filter()
            elif event.key in (pygame.K_UP,):
                self._scroll_y = max(0, self._scroll_y - 80)
            elif event.key in (pygame.K_DOWN,):
                self._scroll_y += 80
            elif event.key == pygame.K_PAGEUP:
                self._scroll_y = max(0, self._scroll_y - 300)
            elif event.key == pygame.K_PAGEDOWN:
                self._scroll_y += 300
            elif event.unicode and event.unicode.isprintable():
                self._search_text += event.unicode
                self._update_filter()

        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos if hasattr(event, 'pos') else (0, 0)

            # Sidebar click — jump to section
            if event.button == 1 and mx < SIDEBAR_WIDTH:
                sidebar_y = 42
                item_h = 24
                max_vis = (theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - sidebar_y) // item_h
                sections_to_show = self._filtered if self._search_text else self._sections
                visible = sections_to_show[self._sidebar_scroll:self._sidebar_scroll + max_vis]
                for i, (title, _) in enumerate(visible):
                    row_y = sidebar_y + i * item_h
                    if row_y <= my < row_y + item_h:
                        # Jump to this section in content
                        self._jump_to_section(title)
                        return

            # Scroll
            if event.button == 4:
                if mx < SIDEBAR_WIDTH:
                    self._sidebar_scroll = max(0, self._sidebar_scroll - 1)
                else:
                    self._scroll_y = max(0, self._scroll_y - 30)
            elif event.button == 5:
                if mx < SIDEBAR_WIDTH:
                    sections = self._filtered if self._search_text else self._sections
                    max_s = max(0, len(sections) - 15)
                    self._sidebar_scroll = min(max_s, self._sidebar_scroll + 1)
                else:
                    self._scroll_y += 30

    def _jump_to_section(self, target_title: str):
        """Scroll content to show the target section."""
        f_med = theme.font("medium")
        f_small = theme.font("small")
        line_h = 18
        y = 0
        for title, lines in self._filtered:
            if title == target_title:
                self._scroll_y = max(0, y - 10)
                return
            y += 28
            for line in lines:
                if not line.strip():
                    y += 8
                else:
                    y += line_h
            y += 8

    def update(self):
        self._cursor_timer += 1

    def draw(self, surface: pygame.Surface):
        f_title = theme.font("title")
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_mono = theme.font("mono")

        # ── Search bar (full width top) ──────────────────────────────
        search_rect = pygame.Rect(SIDEBAR_WIDTH + 8, 6, 460, 30)
        pygame.draw.rect(surface, theme.BG_PANEL, search_rect, border_radius=6)
        pygame.draw.rect(surface, theme.ACCENT if self._search_text else theme.BORDER,
                        search_rect, 1, border_radius=6)

        icon = f_small.render("SEARCH", True, theme.TEXT_DIM)
        surface.blit(icon, (search_rect.x + 10, search_rect.y + 8))

        search_surf = f_med.render(self._search_text if self._search_text else "",
                                   True, theme.TEXT)
        surface.blit(search_surf, (search_rect.x + 70, search_rect.y + 5))

        if self._cursor_timer % 40 < 25:
            cx = search_rect.x + 70 + search_surf.get_width() + 2
            pygame.draw.line(surface, theme.ACCENT,
                           (cx, search_rect.y + 5), (cx, search_rect.bottom - 5), 2)

        # Result count
        if self._search_text:
            ct = f"{len(self._filtered)} results"
            surf = f_small.render(ct, True, theme.ACCENT)
            surface.blit(surf, (search_rect.right + 10, 14))

        # Back button
        back_rect = pygame.Rect(theme.SCREEN_WIDTH - 70, 6, 60, 30)
        pygame.draw.rect(surface, theme.BUTTON_BG, back_rect, border_radius=4)
        surf = f_small.render("ESC", True, theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=back_rect.center))

        # ── Sidebar (left) ───────────────────────────────────────────
        sidebar_rect = pygame.Rect(0, 0, SIDEBAR_WIDTH, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, sidebar_rect)
        pygame.draw.rect(surface, theme.ACCENT, pygame.Rect(0, 0, SIDEBAR_WIDTH, 2))
        pygame.draw.line(surface, theme.BORDER,
                        (SIDEBAR_WIDTH, 0), (SIDEBAR_WIDTH, sidebar_rect.bottom))

        # Sidebar title
        surf = f_med.render("P-6 MANUAL", True, theme.ACCENT)
        surface.blit(surf, (10, 10))

        sidebar_y = 42
        item_h = 24
        max_vis = (sidebar_rect.height - sidebar_y) // item_h
        sections_to_show = self._filtered if self._search_text else self._sections

        visible_sections = sections_to_show[self._sidebar_scroll:self._sidebar_scroll + max_vis]

        for i, (title, _) in enumerate(visible_sections):
            row_y = sidebar_y + i * item_h
            color = SECTION_COLORS.get(title, theme.TEXT_DIM)

            # Highlight if this section is currently visible in content
            # (approximate based on scroll position)
            is_near = self._is_section_visible(title)
            if is_near:
                pygame.draw.rect(surface, (40, 40, 55),
                                (0, row_y, SIDEBAR_WIDTH, item_h))

            # Color dot
            pygame.draw.circle(surface, color, (12, row_y + item_h // 2), 4)

            # Truncated title
            display = title[:18]
            surf = f_small.render(display, True, theme.TEXT if is_near else theme.TEXT_DIM)
            surface.blit(surf, (22, row_y + 4))

        # ── Content (right) ──────────────────────────────────────────
        content_x = SIDEBAR_WIDTH + 6
        content_y = 42
        content_w = theme.SCREEN_WIDTH - content_x - 10
        content_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - content_y
        content_rect = pygame.Rect(content_x, content_y, content_w, content_h)

        clip = surface.get_clip()
        surface.set_clip(content_rect)

        y = content_y - self._scroll_y
        line_h = 18
        query = self._search_text.lower().strip()

        for title, lines in self._filtered:
            # Section header
            if y + 30 > content_y - 40 and y < content_rect.bottom + 40:
                if y > content_y - 30:
                    # Header background
                    hdr_rect = pygame.Rect(content_x, y, content_w, 26)
                    color = SECTION_COLORS.get(title, theme.ACCENT)
                    dark = (color[0] // 6, color[1] // 6, color[2] // 6)
                    pygame.draw.rect(surface, dark, hdr_rect, border_radius=3)

                    # Color bar on left
                    pygame.draw.rect(surface, color,
                                    (content_x, y, 4, 26), border_radius=2)

                    surf = f_med.render(title, True, color)
                    surface.blit(surf, (content_x + 12, y + 3))
            y += 30

            for line in lines:
                if y > content_rect.bottom + 40:
                    break
                if not line.strip():
                    y += 6
                    continue

                if y + line_h > content_y and y < content_rect.bottom:
                    stripped = line.strip()

                    # Highlight search matches
                    if query and query in stripped.lower():
                        hl = pygame.Rect(content_x, y, content_w, line_h)
                        pygame.draw.rect(surface, (70, 55, 15), hl)

                    # Format CC lines specially
                    if stripped.startswith("CC "):
                        parts = stripped.split(":", 1)
                        if len(parts) == 2:
                            cc_surf = f_mono.render(parts[0], True, (140, 200, 255))
                            surface.blit(cc_surf, (content_x + 8, y + 1))
                            desc_surf = f_small.render(parts[1].strip(), True, theme.TEXT)
                            surface.blit(desc_surf, (content_x + 8 + cc_surf.get_width() + 8, y + 1))
                        else:
                            surf = f_mono.render(stripped, True, (140, 200, 255))
                            surface.blit(surf, (content_x + 8, y + 1))
                    # Format key:value lines
                    elif ": " in stripped and not stripped.startswith(" ") and len(stripped.split(":")[0]) < 25:
                        key, _, val = stripped.partition(": ")
                        key_surf = f_small.render(key + ":", True, theme.TEXT)
                        val_surf = f_small.render(val, True, theme.ACCENT)
                        surface.blit(key_surf, (content_x + 8, y + 1))
                        surface.blit(val_surf, (content_x + 8 + key_surf.get_width() + 4, y + 1))
                    # Format menu items (4-char abbreviation + description)
                    elif len(stripped) > 6 and stripped[4] == " " and stripped[:4].replace(".", "").isalpha():
                        abbr = stripped[:4]
                        desc = stripped[4:].strip()
                        abbr_surf = f_mono.render(abbr, True, theme.ACCENT)
                        desc_surf = f_small.render(desc, True, theme.TEXT_DIM)
                        surface.blit(abbr_surf, (content_x + 8, y + 1))
                        surface.blit(desc_surf, (content_x + 52, y + 1))
                    # Warning/note lines
                    elif stripped.startswith("Note:") or stripped.startswith("Warning:"):
                        surf = f_small.render(stripped, True, theme.YELLOW)
                        surface.blit(surf, (content_x + 8, y + 1))
                    # Bullet-like lines
                    elif stripped.startswith("-") or stripped.startswith("*"):
                        surf = f_small.render(stripped, True, theme.TEXT)
                        surface.blit(surf, (content_x + 14, y + 1))
                    else:
                        surf = f_small.render(stripped, True, theme.TEXT_DIM)
                        surface.blit(surf, (content_x + 8, y + 1))

                y += line_h
            y += 10

        surface.set_clip(clip)

        # Content scrollbar
        total_h = y + self._scroll_y - content_y
        if total_h > content_h:
            bar_x = theme.SCREEN_WIDTH - 5
            thumb_h = max(20, int(content_h * content_h / total_h))
            thumb_y = content_y + int((content_h - thumb_h) * self._scroll_y / max(1, total_h - content_h))
            thumb_y = max(content_y, min(thumb_y, content_y + content_h - thumb_h))
            pygame.draw.rect(surface, theme.BORDER, (bar_x, content_y, 3, content_h), border_radius=1)
            pygame.draw.rect(surface, theme.ACCENT, (bar_x, thumb_y, 3, thumb_h), border_radius=1)

    def _is_section_visible(self, target_title: str) -> bool:
        """Check if a section is roughly in the visible content area."""
        line_h = 18
        y = 0
        for title, lines in self._filtered:
            section_start = y
            y += 30
            for line in lines:
                if not line.strip():
                    y += 6
                else:
                    y += line_h
            y += 10
            section_end = y
            if title == target_title:
                visible_top = self._scroll_y
                visible_bottom = self._scroll_y + 350
                return section_start < visible_bottom and section_end > visible_top
        return False
