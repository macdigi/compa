"""Compa Updates screen.

Shown when the user taps the update pill in the nav bar (or navigates
manually via switch_screen("updates")). Surfaces what's coming in the
pending update in plain English, plus an "Update now" button that
pulls + restarts the service.

Source of release notes (in priority order):

  1. The "## Unreleased" section of CHANGELOG.md as it appears on
     origin/<branch>. Producer-friendly bullet points authored by the
     maintainer pre-push.
  2. Falls back to commit subjects (one-liners) of the commits the
     local is currently behind on.

The screen is intentionally minimal: a header, a scrollable bullet
list, and two actions at the bottom (Update Now / Later). No
toggling, no settings — those live in the Settings screen.
"""

from __future__ import annotations

import threading
import pygame

from .. import theme


class UpdatesScreen:
    """Shows pending update info + an Update Now action."""

    HEADER_H = 36

    def __init__(self, app) -> None:
        self.app = app
        self._scroll = 0  # vertical scroll offset for the bullet list
        self._status = ""  # transient status string ("Updating...", etc.)
        self._is_applying = False
        self._cached_bullets: list[str] | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_enter(self) -> None:
        self._scroll = 0
        self._cached_bullets = None
        self._status = ""
        # Kick off a fresh check so the screen reflects current state
        # rather than whatever the background poller had last time.
        try:
            self.app.updater.check_async(self._on_check_done)
        except Exception:
            pass

    def on_exit(self) -> None:
        pass

    def _on_check_done(self, _result: dict) -> None:
        # Force re-render of cached bullets next draw.
        self._cached_bullets = None

    # ── Bullet content ───────────────────────────────────────────────

    def _resolve_bullets(self) -> list[str]:
        if self._cached_bullets is not None:
            return self._cached_bullets
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
            # Strip conventional-commit prefixes for readability.
            cleaned = []
            for m in msgs:
                if ":" in m and len(m.split(":", 1)[0]) <= 30:
                    cleaned.append(m.split(":", 1)[1].strip())
                else:
                    cleaned.append(m)
            bullets = cleaned
        self._cached_bullets = bullets
        return bullets

    # ── Actions ──────────────────────────────────────────────────────

    def _apply(self) -> None:
        if self._is_applying:
            return
        self._is_applying = True
        self._status = "Pulling latest..."

        def _do():
            try:
                result = self.app.updater.apply(restart=True)
                self._status = (result.get("message") or "Update done")[:80]
            except Exception as e:
                self._status = f"Error: {e}"[:80]
            finally:
                self._is_applying = False

        threading.Thread(target=_do, daemon=True).start()

    def _close(self) -> None:
        # Return to settings — that's where Updates is conceptually
        # parked. If the user came from the nav-bar pill we still
        # land them in a sensible spot.
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
            0, self.HEADER_H,
            theme.SCREEN_WIDTH,
            theme.SCREEN_HEIGHT - self.HEADER_H - nav_h - 60,
        )

    def _action_buttons(self) -> tuple[pygame.Rect, pygame.Rect]:
        nav_h = (
            getattr(self.app, "_nav_rect", None).height
            if getattr(self.app, "_nav_rect", None) else theme.NAV_HEIGHT)
        bot_y = theme.SCREEN_HEIGHT - nav_h - 50
        update_rect = pygame.Rect(
            theme.SCREEN_WIDTH // 2 - 220, bot_y, 200, 40)
        later_rect = pygame.Rect(
            theme.SCREEN_WIDTH // 2 + 20, bot_y, 200, 40)
        return update_rect, later_rect

    # ── Event handling ───────────────────────────────────────────────

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button in (4, 5):
                # Wheel scroll
                self._scroll = max(
                    0,
                    self._scroll + (-30 if event.button == 4 else 30),
                )
                return True
            if event.button == 1:
                update_rect, later_rect = self._action_buttons()
                if update_rect.collidepoint(event.pos):
                    self._apply()
                    return True
                if later_rect.collidepoint(event.pos):
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

        title = "Compa updates"
        title_surf = f_title.render(title, True, theme.TEXT_BRIGHT)
        surface.blit(title_surf, (16, 4))

        # Update-meta line on the right of the header.
        try:
            updater = self.app.updater
            cur = updater.current_commit()
            behind = updater.commits_behind
            if behind > 0:
                meta = f"{behind} new change{'s' if behind != 1 else ''} available"
                meta_color = theme.ACCENT
            else:
                meta = f"Up to date · @ {cur}"
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

        # ── Content area ────────────────────────────────────────────
        content = self._content_rect()
        pygame.draw.rect(surface, theme.BG_PANEL, content, border_radius=6)
        pygame.draw.rect(
            surface, theme.BORDER, content, 1, border_radius=6)

        bullets = self._resolve_bullets()
        if not bullets:
            self._draw_empty_state(surface, content, f_med, f_small)
        else:
            self._draw_bullets(
                surface, content, bullets, f_med, f_small)

        # ── Status line + action buttons ────────────────────────────
        update_rect, later_rect = self._action_buttons()

        if self._status:
            ss = f_small.render(self._status, True, theme.ACCENT)
            surface.blit(
                ss,
                (theme.SCREEN_WIDTH // 2 - ss.get_width() // 2,
                 update_rect.y - 22))

        # Update Now button — primary, accent color.
        can_update = (
            getattr(self.app.updater, "update_available", False)
            and not self._is_applying)
        if can_update:
            pygame.draw.rect(
                surface, theme.ACCENT, update_rect, border_radius=8)
            label = "Update now"
            tc = theme.BG
        else:
            pygame.draw.rect(
                surface, theme.BG_LIGHTER, update_rect, border_radius=8)
            label = "Updating..." if self._is_applying else "Up to date"
            tc = theme.TEXT_DIM
        ls = f_med.render(label, True, tc)
        surface.blit(ls, ls.get_rect(center=update_rect.center))

        # Later button — secondary outline.
        pygame.draw.rect(
            surface, theme.BG_PANEL, later_rect, border_radius=8)
        pygame.draw.rect(
            surface, theme.BORDER, later_rect, 1, border_radius=8)
        ls = f_med.render("Later", True, theme.TEXT)
        surface.blit(ls, ls.get_rect(center=later_rect.center))

    # ── Draw helpers ─────────────────────────────────────────────────

    def _draw_empty_state(
        self, surface, rect, f_med, f_small
    ) -> None:
        try:
            cur = self.app.updater.current_commit()
            behind = self.app.updater.commits_behind
        except Exception:
            cur = ""
            behind = 0

        if behind > 0:
            line1 = "Update ready"
            line2 = (f"{behind} new change"
                     f"{'s' if behind != 1 else ''} are pending — release "
                     f"notes weren't found, but you can update now.")
        else:
            line1 = "You're up to date"
            line2 = (f"Compa is at the latest commit ({cur}). "
                     "We'll notify you when an update lands.")

        l1 = f_med.render(line1, True, theme.TEXT_BRIGHT)
        l2 = f_small.render(line2, True, theme.TEXT_DIM)
        cy = rect.centery
        surface.blit(
            l1, (rect.centerx - l1.get_width() // 2,
                 cy - l1.get_height() - 4))
        # word-wrap line 2 if needed
        if l2.get_width() > rect.width - 32:
            self._draw_wrapped(
                surface, line2, f_small, theme.TEXT_DIM,
                pygame.Rect(rect.x + 16, cy + 2, rect.width - 32, 60))
        else:
            surface.blit(
                l2, (rect.centerx - l2.get_width() // 2, cy + 2))

    def _draw_bullets(
        self, surface, rect, bullets, f_med, f_small
    ) -> None:
        # Sub-header inside the panel.
        sub = f_small.render("What's new", True, theme.TEXT_DIM)
        surface.blit(sub, (rect.x + 16, rect.y + 12))

        list_top = rect.y + 36
        list_bottom = rect.bottom - 12
        avail_h = list_bottom - list_top
        line_h = f_med.get_linesize() + 6

        # Apply scroll offset.
        max_scroll = max(0, len(bullets) * line_h - avail_h)
        self._scroll = max(0, min(self._scroll, max_scroll))

        # Clip to the list area so text doesn't overflow the panel.
        clip_rect = pygame.Rect(
            rect.x + 8, list_top, rect.width - 16, avail_h)
        surface.set_clip(clip_rect)

        x = rect.x + 18
        y = list_top - self._scroll
        for bullet in bullets:
            if y > list_bottom:
                break
            if y + line_h >= list_top:
                # Bullet marker
                pygame.draw.circle(
                    surface, theme.ACCENT, (x, y + line_h // 2 - 1), 3)
                # Wrap the text within the available width.
                text_x = x + 12
                text_w = rect.right - text_x - 16
                self._draw_wrapped_clipped(
                    surface, bullet, f_med, theme.TEXT,
                    text_x, y, text_w)
            y += self._wrapped_height(bullet, f_med, rect.width - 50) + 6

        surface.set_clip(None)

    def _draw_wrapped(self, surface, text, font, color, rect) -> None:
        words = text.split()
        line = ""
        y = rect.y
        for w in words:
            test = (line + " " + w).strip()
            if font.size(test)[0] > rect.width:
                if line:
                    surface.blit(
                        font.render(line, True, color), (rect.x, y))
                    y += font.get_linesize()
                line = w
            else:
                line = test
        if line:
            surface.blit(
                font.render(line, True, color), (rect.x, y))

    def _draw_wrapped_clipped(
        self, surface, text, font, color, x, y, max_w
    ) -> None:
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

    def _wrapped_height(self, text, font, max_w) -> int:
        words = text.split()
        line = ""
        lines = 0
        for w in words:
            test = (line + " " + w).strip()
            if font.size(test)[0] > max_w:
                if line:
                    lines += 1
                line = w
            else:
                line = test
        if line:
            lines += 1
        return max(1, lines) * font.get_linesize()
