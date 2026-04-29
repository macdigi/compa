"""Compa Updates screen.

Two views in one screen:

  1. PENDING (top) — when the local is behind origin. Shows the new
     entries about to land + an "Update now" action.
  2. HISTORY (below, always shown) — the full CHANGELOG.md as it
     exists on this device, so users have one place to scroll through
     everything that's ever shipped, written in producer terms.

Reachable two ways:
  - Tap the pulsing "UPDATE" pill in the nav bar (only when pending).
  - From Settings → Updates (always).
"""

from __future__ import annotations

import threading
import pygame

from .. import theme


class UpdatesScreen:
    HEADER_H = 36

    def __init__(self, app) -> None:
        self.app = app
        self._scroll = 0
        self._status = ""
        self._is_applying = False
        self._cached_pending: list[str] | None = None
        self._cached_history: list[tuple[str, list[str]]] | None = None
        self._is_checking = False

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_enter(self) -> None:
        self._scroll = 0
        self._cached_pending = None
        self._cached_history = None
        self._status = ""
        self._kick_check()

    def on_exit(self) -> None:
        pass

    def _kick_check(self) -> None:
        """Fire a fresh remote check so on-screen counts are live."""
        if self._is_checking:
            return
        self._is_checking = True
        self._status = "Checking..."

        def _on_done(_result: dict) -> None:
            self._cached_pending = None
            self._is_checking = False
            try:
                if self.app.updater.update_available:
                    n = self.app.updater.commits_behind
                    self._status = (
                        f"{n} new change{'s' if n != 1 else ''} ready")
                else:
                    self._status = "Up to date"
            except Exception:
                self._status = ""

        try:
            self.app.updater.check_async(_on_done)
        except Exception:
            self._is_checking = False
            self._status = ""

    # ── Content resolution ──────────────────────────────────────────

    def _pending_bullets(self) -> list[str]:
        if self._cached_pending is not None:
            return self._cached_pending
        bullets: list[str] = []
        try:
            bullets = list(
                self.app.updater.changelog_entries_pending() or [])
        except Exception:
            bullets = []
        if not bullets:
            try:
                msgs = list(
                    self.app.updater.commit_messages_behind() or [])
            except Exception:
                msgs = []
            cleaned = []
            for m in msgs:
                # Strip "feat(scope):" / "fix:" prefixes.
                if ":" in m and len(m.split(":", 1)[0]) <= 30:
                    cleaned.append(m.split(":", 1)[1].strip())
                else:
                    cleaned.append(m)
            bullets = cleaned
        self._cached_pending = bullets
        return bullets

    def _history(self) -> list[tuple[str, list[str]]]:
        if self._cached_history is not None:
            return self._cached_history
        try:
            sections = list(
                self.app.updater.changelog_history(max_sections=20)
                or [])
        except Exception:
            sections = []
        # If the topmost is "Unreleased" but nothing's pending on the
        # remote, drop it from history so we don't show "Unreleased"
        # entries that have already been pulled.
        self._cached_history = sections
        return sections

    # ── Actions ──────────────────────────────────────────────────────

    def _apply(self) -> None:
        if self._is_applying:
            return
        if not getattr(self.app.updater, "update_available", False):
            return
        self._is_applying = True
        self._status = "Pulling latest..."

        def _do() -> None:
            try:
                result = self.app.updater.apply(restart=True)
                self._status = (
                    result.get("message") or "Update done")[:80]
            except Exception as e:
                self._status = f"Error: {e}"[:80]
            finally:
                self._is_applying = False

        threading.Thread(target=_do, daemon=True).start()

    def _close(self) -> None:
        try:
            self.app.switch_screen("settings")
        except Exception:
            pass

    # ── Layout helpers ───────────────────────────────────────────────

    def _content_rect(self) -> pygame.Rect:
        nav_h = (
            getattr(self.app, "_nav_rect", None).height
            if getattr(self.app, "_nav_rect", None) else theme.NAV_HEIGHT)
        return pygame.Rect(
            12, self.HEADER_H + 8,
            theme.SCREEN_WIDTH - 24,
            theme.SCREEN_HEIGHT - self.HEADER_H - nav_h - 70,
        )

    def _action_buttons(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        """Returns (update_rect, check_rect, close_rect)."""
        nav_h = (
            getattr(self.app, "_nav_rect", None).height
            if getattr(self.app, "_nav_rect", None) else theme.NAV_HEIGHT)
        bot_y = theme.SCREEN_HEIGHT - nav_h - 50
        update_rect = pygame.Rect(
            theme.SCREEN_WIDTH // 2 - 280, bot_y, 180, 40)
        check_rect = pygame.Rect(
            theme.SCREEN_WIDTH // 2 - 90, bot_y, 180, 40)
        close_rect = pygame.Rect(
            theme.SCREEN_WIDTH // 2 + 100, bot_y, 180, 40)
        return update_rect, check_rect, close_rect

    # ── Event handling ───────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button in (4, 5):
                self._scroll = max(
                    0,
                    self._scroll + (-30 if event.button == 4 else 30),
                )
                return True
            if event.button == 1:
                update_rect, check_rect, close_rect = (
                    self._action_buttons())
                if update_rect.collidepoint(event.pos):
                    self._apply()
                    return True
                if check_rect.collidepoint(event.pos):
                    self._kick_check()
                    return True
                if close_rect.collidepoint(event.pos):
                    self._close()
                    return True
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                self._close()
                return True
            if event.key == pygame.K_RETURN:
                self._apply()
                return True
        return False

    # ── Draw ─────────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface) -> None:
        f_title = theme.font("title")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        surface.fill(theme.BG)

        # ── Header ──────────────────────────────────────────────────
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, self.HEADER_H)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)
        pygame.draw.line(
            surface, theme.BORDER,
            (0, self.HEADER_H), (theme.SCREEN_WIDTH, self.HEADER_H), 1)

        title_surf = f_title.render(
            "Compa updates", True, theme.TEXT_BRIGHT)
        surface.blit(title_surf, (16, 4))

        try:
            updater = self.app.updater
            cur = updater.current_commit()
            behind = updater.commits_behind
            if behind > 0:
                meta = (f"{behind} pending  ·  current @ {cur}")
                meta_color = theme.ACCENT
            else:
                meta = f"Current @ {cur}"
                meta_color = theme.TEXT_DIM
        except Exception:
            meta = ""
            meta_color = theme.TEXT_DIM
        if meta:
            ms = f_small.render(meta, True, meta_color)
            surface.blit(
                ms,
                (theme.SCREEN_WIDTH - ms.get_width() - 16,
                 self.HEADER_H // 2 - ms.get_height() // 2))

        # ── Content panel ───────────────────────────────────────────
        content = self._content_rect()
        pygame.draw.rect(surface, theme.BG_PANEL, content, border_radius=6)
        pygame.draw.rect(
            surface, theme.BORDER, content, 1, border_radius=6)

        # Build the rendered "doc" — pending block (if any) + every
        # history section. Each section has a sub-header + bullets.
        # Drawn into a clipped area with vertical scroll.
        clip_rect = content.inflate(-12, -12)
        surface.set_clip(clip_rect)

        cursor_y = clip_rect.y - self._scroll
        text_x = clip_rect.x + 6
        text_w = clip_rect.width - 12

        pending = self._pending_bullets()
        update_pending = bool(
            getattr(self.app.updater, "update_available", False))
        if update_pending and pending:
            cursor_y = self._draw_section(
                surface, text_x, cursor_y, text_w,
                title="Coming up",
                title_color=theme.ACCENT,
                bullets=pending,
                f_med=f_med, f_small=f_small,
            ) + 8

        history = self._history()
        if not history and not (update_pending and pending):
            self._draw_empty_state(
                surface, clip_rect, f_med, f_small)
            cursor_y = clip_rect.bottom

        for title, bullets in history:
            cursor_y = self._draw_section(
                surface, text_x, cursor_y, text_w,
                title=title,
                title_color=theme.TEXT_BRIGHT,
                bullets=bullets,
                f_med=f_med, f_small=f_small,
            ) + 14

        # Compute scroll bounds.
        total_height = cursor_y + self._scroll - clip_rect.y
        max_scroll = max(0, total_height - clip_rect.height)
        if self._scroll > max_scroll:
            self._scroll = max_scroll

        surface.set_clip(None)

        # ── Status line + actions ───────────────────────────────────
        update_rect, check_rect, close_rect = self._action_buttons()

        if self._status:
            ss = f_small.render(self._status, True, theme.ACCENT)
            surface.blit(
                ss,
                (theme.SCREEN_WIDTH // 2 - ss.get_width() // 2,
                 update_rect.y - 22))

        # Update Now (primary, only enabled when pending)
        can_update = update_pending and not self._is_applying
        if can_update:
            pygame.draw.rect(
                surface, theme.ACCENT, update_rect, border_radius=8)
            label = "Update now"
            tc = theme.BG
        else:
            pygame.draw.rect(
                surface, theme.BG_LIGHTER, update_rect, border_radius=8)
            label = "Updating..." if self._is_applying else "Update now"
            tc = theme.TEXT_DIM
        surface.blit(
            f_med.render(label, True, tc),
            f_med.render(label, True, tc).get_rect(
                center=update_rect.center))

        # Check now (secondary)
        check_label = "Checking..." if self._is_checking else "Check now"
        pygame.draw.rect(
            surface, theme.BG_PANEL, check_rect, border_radius=8)
        pygame.draw.rect(
            surface, theme.BORDER, check_rect, 1, border_radius=8)
        cs = f_med.render(check_label, True, theme.TEXT)
        surface.blit(cs, cs.get_rect(center=check_rect.center))

        # Close (back to settings)
        pygame.draw.rect(
            surface, theme.BG_PANEL, close_rect, border_radius=8)
        pygame.draw.rect(
            surface, theme.BORDER, close_rect, 1, border_radius=8)
        ls = f_med.render("Back", True, theme.TEXT)
        surface.blit(ls, ls.get_rect(center=close_rect.center))

    # ── Section drawing helpers ──────────────────────────────────────

    def _draw_section(
        self, surface, x, y, w, title, title_color,
        bullets, f_med, f_small,
    ) -> int:
        """Draw a single section (title + bullets). Returns the next
        y position so the caller can stack sections vertically."""
        ts = f_med.render(title, True, title_color)
        surface.blit(ts, (x, y))
        y += ts.get_height() + 4

        if not bullets:
            empty = f_small.render(
                "(no entries)", True, theme.TEXT_DIM)
            surface.blit(empty, (x + 14, y))
            y += empty.get_height()
            return y

        for bullet in bullets:
            # Bullet glyph
            pygame.draw.circle(
                surface, theme.ACCENT,
                (x + 4, y + f_small.get_linesize() // 2 + 1), 2)
            # Wrapped text
            consumed = self._draw_wrapped(
                surface, bullet, f_small, theme.TEXT,
                x + 14, y, w - 14)
            y += consumed + 4
        return y

    def _draw_wrapped(
        self, surface, text, font, color, x, y, max_w
    ) -> int:
        words = text.split()
        line = ""
        cy = y
        for w in words:
            test = (line + " " + w).strip()
            if font.size(test)[0] > max_w:
                if line:
                    surface.blit(font.render(line, True, color), (x, cy))
                    cy += font.get_linesize()
                line = w
            else:
                line = test
        if line:
            surface.blit(font.render(line, True, color), (x, cy))
            cy += font.get_linesize()
        return cy - y

    def _draw_empty_state(
        self, surface, rect, f_med, f_small
    ) -> None:
        line1 = "No changelog entries yet"
        line2 = (
            "When updates ship, release notes will appear here in "
            "plain language. You can pull the latest at any time "
            "with the Check now button below.")
        l1 = f_med.render(line1, True, theme.TEXT_BRIGHT)
        cy = rect.centery
        surface.blit(
            l1, (rect.centerx - l1.get_width() // 2,
                 cy - l1.get_height() - 6))
        # word-wrap line 2
        words = line2.split()
        line = ""
        y = cy + 4
        for w in words:
            test = (line + " " + w).strip()
            if f_small.size(test)[0] > rect.width - 32:
                if line:
                    s = f_small.render(line, True, theme.TEXT_DIM)
                    surface.blit(
                        s, (rect.centerx - s.get_width() // 2, y))
                    y += f_small.get_linesize()
                line = w
            else:
                line = test
        if line:
            s = f_small.render(line, True, theme.TEXT_DIM)
            surface.blit(
                s, (rect.centerx - s.get_width() // 2, y))
