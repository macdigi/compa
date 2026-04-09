"""Multi-line text area component with keyboard input."""

import pygame
from .. import theme


class TextArea:
    """Simple multi-line text editor for session notes.

    Supports typing, backspace, delete, arrow keys, enter for newlines.
    Auto-wraps text and scrolls to keep cursor visible.
    """

    def __init__(self, rect: pygame.Rect, text: str = "",
                 font_name: str = "small", max_chars: int = 2000):
        self.rect = rect
        self.text = text
        self.font_name = font_name
        self.max_chars = max_chars
        self.focused = False
        self.cursor_pos = len(text)
        self._scroll_y = 0
        self._cursor_timer = 0
        self._changed = False  # True if text modified since last check

    @property
    def changed(self) -> bool:
        """Check and clear the changed flag."""
        if self._changed:
            self._changed = False
            return True
        return False

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events. Returns True if event was consumed."""
        # Click to focus/unfocus
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.focused = True
                # Approximate cursor position from click
                self._click_to_cursor(event.pos)
                return True
            else:
                self.focused = False
                return False

        if not self.focused:
            return False

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                if self.cursor_pos > 0:
                    self.text = self.text[:self.cursor_pos - 1] + self.text[self.cursor_pos:]
                    self.cursor_pos -= 1
                    self._changed = True
            elif event.key == pygame.K_DELETE:
                if self.cursor_pos < len(self.text):
                    self.text = self.text[:self.cursor_pos] + self.text[self.cursor_pos + 1:]
                    self._changed = True
            elif event.key == pygame.K_LEFT:
                self.cursor_pos = max(0, self.cursor_pos - 1)
            elif event.key == pygame.K_RIGHT:
                self.cursor_pos = min(len(self.text), self.cursor_pos + 1)
            elif event.key == pygame.K_HOME:
                # Move to start of current line
                pos = self.text.rfind("\n", 0, self.cursor_pos)
                self.cursor_pos = pos + 1 if pos >= 0 else 0
            elif event.key == pygame.K_END:
                # Move to end of current line
                pos = self.text.find("\n", self.cursor_pos)
                self.cursor_pos = pos if pos >= 0 else len(self.text)
            elif event.key == pygame.K_RETURN:
                if len(self.text) < self.max_chars:
                    self.text = self.text[:self.cursor_pos] + "\n" + self.text[self.cursor_pos:]
                    self.cursor_pos += 1
                    self._changed = True
            elif event.key == pygame.K_TAB:
                pass  # Ignore tab
            elif event.unicode and event.unicode.isprintable():
                if len(self.text) < self.max_chars:
                    self.text = self.text[:self.cursor_pos] + event.unicode + self.text[self.cursor_pos:]
                    self.cursor_pos += 1
                    self._changed = True
            return True

        return False

    def _click_to_cursor(self, pos):
        """Approximate cursor position from mouse click."""
        f = theme.font(self.font_name)
        line_h = f.get_linesize()
        rel_x = pos[0] - self.rect.x - 6
        rel_y = pos[1] - self.rect.y - 4 + self._scroll_y

        lines = self._wrap_lines(f)
        target_line = min(int(rel_y / line_h), len(lines) - 1)
        target_line = max(0, target_line)

        # Find character offset for this line
        char_offset = 0
        for i in range(target_line):
            char_offset += len(lines[i])

        # Find horizontal position within line
        if target_line < len(lines):
            line = lines[target_line]
            best_pos = 0
            for j in range(len(line) + 1):
                w = f.size(line[:j])[0]
                if w <= rel_x:
                    best_pos = j
            char_offset += best_pos

        self.cursor_pos = min(char_offset, len(self.text))

    def _wrap_lines(self, font) -> list[str]:
        """Word-wrap text to fit within rect width."""
        max_w = self.rect.width - 12
        lines = []

        for paragraph in self.text.split("\n"):
            if not paragraph:
                lines.append("")
                continue

            words = paragraph.split(" ")
            current = ""
            for word in words:
                test = current + (" " if current else "") + word
                if font.size(test)[0] <= max_w:
                    current = test
                else:
                    if current:
                        lines.append(current + " ")
                    current = word
            lines.append(current + "\n" if paragraph != self.text.split("\n")[-1] else current)

        if not lines:
            lines = [""]

        return lines

    def draw(self, surface: pygame.Surface):
        f = theme.font(self.font_name)
        line_h = f.get_linesize()

        # Background
        bg_color = (30, 30, 40) if self.focused else (20, 20, 28)
        pygame.draw.rect(surface, bg_color, self.rect, border_radius=4)
        border_color = theme.ACCENT if self.focused else theme.BORDER
        pygame.draw.rect(surface, border_color, self.rect, 1, border_radius=4)

        # Clip to rect
        clip = surface.get_clip()
        inner = self.rect.inflate(-4, -4)
        surface.set_clip(inner)

        # Render lines
        lines = self._wrap_lines(f)
        total_h = len(lines) * line_h

        # Auto-scroll to keep cursor visible
        cursor_line = self._cursor_line(lines)
        cursor_y = cursor_line * line_h
        visible_h = self.rect.height - 8
        if cursor_y - self._scroll_y > visible_h - line_h:
            self._scroll_y = cursor_y - visible_h + line_h
        if cursor_y - self._scroll_y < 0:
            self._scroll_y = cursor_y
        self._scroll_y = max(0, min(self._scroll_y, max(0, total_h - visible_h)))

        x = self.rect.x + 6
        y = self.rect.y + 4 - self._scroll_y

        char_offset = 0
        for i, line in enumerate(lines):
            if y + line_h > self.rect.y and y < self.rect.bottom:
                display = line.rstrip("\n")
                text_surf = f.render(display, True, theme.TEXT)
                surface.blit(text_surf, (x, y))

                # Draw cursor
                if self.focused and char_offset <= self.cursor_pos <= char_offset + len(line):
                    self._cursor_timer += 1
                    if self._cursor_timer % 40 < 25:
                        cursor_in_line = self.cursor_pos - char_offset
                        cx = x + f.size(display[:cursor_in_line])[0]
                        pygame.draw.line(surface, theme.ACCENT,
                                        (cx, y + 1), (cx, y + line_h - 2), 2)

            char_offset += len(line)
            y += line_h

        # Placeholder text
        if not self.text and not self.focused:
            placeholder = f.render("Tap to add session notes...", True, theme.TEXT_DIM)
            surface.blit(placeholder, (self.rect.x + 6, self.rect.y + 4))

        # Restore clip
        surface.set_clip(clip)

    def _cursor_line(self, lines: list[str]) -> int:
        """Find which wrapped line the cursor is on."""
        char_offset = 0
        for i, line in enumerate(lines):
            if char_offset + len(line) >= self.cursor_pos:
                return i
            char_offset += len(line)
        return max(0, len(lines) - 1)
