"""Radio Screen -- internet radio streaming with capture for sampling.

Two-level genre browser: category -> sub-genre -> station list.
Search bar for quick filtering across all stations.
"""

import math
import os
import pygame
import numpy as np
from .. import theme
from ..components.modal import Modal
from engine.radio_stream import RadioStream, load_stations


# Category -> sub-genres mapping
CATEGORIES = {
    "MUSIC": ["jazz", "soul", "funk", "lofi", "hiphop", "metal", "rock",
              "blues", "country", "classical", "electronic", "reggaeton",
              "punk", "gospel", "ambient", "world"],
    "TALK":  ["news", "politics", "comedy", "spoken", "talk"],
    "WEIRD": ["paranormal", "vintage", "dark", "scanner", "experimental",
              "underground"],
    "ALL":   [],  # shows everything
}
CATEGORY_ORDER = ["MUSIC", "TALK", "WEIRD", "ALL"]

# Display labels for sub-genres
SUB_LABELS = {
    "jazz": "JAZZ", "soul": "SOUL", "funk": "FUNK", "lofi": "LOFI",
    "hiphop": "HIP HOP", "metal": "METAL", "rock": "ROCK", "blues": "BLUES",
    "country": "COUNTRY", "classical": "CLASSICAL", "electronic": "ELECTRO",
    "reggaeton": "LATIN", "punk": "PUNK", "gospel": "GOSPEL",
    "ambient": "AMBIENT", "world": "WORLD",
    "news": "NEWS", "politics": "POLITICS", "comedy": "COMEDY",
    "spoken": "SPOKEN", "talk": "TALK",
    "paranormal": "PARANORMAL", "vintage": "VINTAGE", "dark": "DARK",
    "scanner": "SCANNER", "experimental": "EXPRMNTL", "underground": "UNDER",
}


class P6RadioScreen:
    """Internet radio browser with two-level genre navigation and capture."""

    def __init__(self, app):
        self.app = app

        # Radio engine
        recordings_dir = app.config.get("P6_RECORDING_DIR",
                                         os.path.join(os.path.dirname(os.path.dirname(
                                             os.path.abspath(__file__))), "recordings"))
        self._radio = RadioStream(recordings_dir)

        # Load stations
        docs_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "docs")
        self._all_stations = load_stations(os.path.join(docs_dir, "radio_stations.json"))

        # UI state
        self._category = "MUSIC"
        self._sub_genre = ""
        self._dragging_scrollbar = False
        self._scroll_float = 0.0
        self._search_text = ""
        self._search_active = False
        self._filtered: list[dict] = []
        self._scroll = 0
        self._selected = -1
        self._capture_flash = 0
        self._meter_decay = 0.92
        self._disp_peak_l = 0.0
        self._disp_peak_r = 0.0
        self._cursor_timer = 0

        # Custom URL modal
        self._url_modal = Modal("Custom Stream", "Enter stream URL:",
                                buttons=["PLAY", "CANCEL"], width=500, height=190)

        # Visualizer -- rolling peak history
        self._viz_width = 200
        self._viz_history = np.zeros(self._viz_width, dtype=np.float32)
        self._viz_pos = 0

        self._apply_filter()

    def _apply_filter(self):
        """Filter stations based on current category, sub-genre, and search."""
        if self._search_text:
            query = self._search_text.lower()
            self._filtered = [s for s in self._all_stations
                              if query in s.get("name", "").lower()
                              or query in s.get("desc", "").lower()
                              or query in s.get("genre", "").lower()]
        elif self._category == "ALL":
            self._filtered = list(self._all_stations)
        elif self._sub_genre:
            self._filtered = [s for s in self._all_stations
                              if s.get("genre") == self._sub_genre]
        else:
            # Show all stations in this category's sub-genres
            valid_genres = set(CATEGORIES.get(self._category, []))
            self._filtered = [s for s in self._all_stations
                              if s.get("genre") in valid_genres]
        self._scroll = 0

    def on_enter(self):
        self._apply_filter()

    def on_exit(self):
        pass

    @property
    def wants_keyboard(self) -> bool:
        return self._search_active

    def handle_event(self, event):
        # Scrollbar drag — smooth
        if event.type == pygame.MOUSEMOTION and self._dragging_scrollbar:
            browser_y = 100 if self._radio.is_playing else 42
            sub_y = browser_y + 28
            list_y = sub_y + 22
            item_h = 32
            list_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 20
            max_visible = (list_bottom - list_y) // item_h
            sb_h = max_visible * item_h
            total = len(self._filtered)
            if total > max_visible:
                frac = (event.pos[1] - list_y) / sb_h
                frac = max(0.0, min(1.0, frac))
                self._scroll_float = frac * (total - max_visible)
                self._scroll = int(self._scroll_float)
            return
        if event.type == pygame.MOUSEBUTTONUP:
            self._dragging_scrollbar = False

        # URL modal
        if self._url_modal.visible:
            result = self._url_modal.handle_event(event)
            if result == "PLAY":
                url = self._url_modal.input_text.strip()
                if url:
                    self._radio.play(url, station_name="Custom")
            return

        # Search keyboard input
        if self._search_active and event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._search_active = False
                self._search_text = ""
                self._apply_filter()
                return
            elif event.key == pygame.K_BACKSPACE:
                self._search_text = self._search_text[:-1]
                self._apply_filter()
                return
            elif event.unicode and event.unicode.isprintable():
                self._search_text += event.unicode
                self._apply_filter()
                return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            playing = self._radio.is_playing

            # ── Now-playing controls (when playing) ───────────────────
            if playing:
                ctrl_x = theme.SCREEN_WIDTH - 300

                # Stop button
                play_rect = pygame.Rect(ctrl_x, 6, 40, 40)
                if play_rect.collidepoint(mx, my):
                    self._radio.stop()
                    return

                # Record button
                rec_rect = pygame.Rect(ctrl_x + 48, 6, 40, 40)
                if rec_rect.collidepoint(mx, my):
                    if self._radio.is_recording:
                        self._radio.stop_recording()
                    else:
                        self._radio.start_recording()
                    return

                # Recall button
                capture_rect = pygame.Rect(ctrl_x + 96, 6, 80, 40)
                if capture_rect.collidepoint(mx, my):
                    path = self._radio.capture()
                    if path:
                        self._capture_flash = 30
                    return

                # Volume -/+
                vol_down = pygame.Rect(ctrl_x + 184, 28, 48, 18)
                vol_up = pygame.Rect(ctrl_x + 236, 28, 48, 18)
                if vol_down.collidepoint(mx, my):
                    self._radio.volume = max(0.0, self._radio.volume - 0.1)
                    return
                if vol_up.collidepoint(mx, my):
                    self._radio.volume = min(1.0, self._radio.volume + 0.1)
                    return

                # Search bar (when playing, inline with categories)
                search_rect = pygame.Rect(560, 100, 220, 24)
                if search_rect.collidepoint(mx, my):
                    self._search_active = True
                    return

                browser_y = 100
            else:
                # Search bar (not playing, in header)
                search_rect = pygame.Rect(480, 6, 300, 28)
                if search_rect.collidepoint(mx, my):
                    self._search_active = True
                    return
                elif self._search_active and my > 40:
                    self._search_active = False

                browser_y = 42

            # ── Category buttons ──────────────────────────────────────
            cat_y = browser_y
            for i, cat in enumerate(CATEGORY_ORDER):
                rect = pygame.Rect(16 + i * 125, cat_y, 118, 24)
                if rect.collidepoint(mx, my):
                    self._category = cat
                    self._sub_genre = ""
                    self._search_text = ""
                    self._search_active = False
                    self._apply_filter()
                    return

            # Sub-genre buttons
            sub_y = cat_y + 28
            subs = CATEGORIES.get(self._category, [])
            if subs and not self._search_text:
                bx = 16
                for genre in subs:
                    label = SUB_LABELS.get(genre, genre.upper())
                    btn_w = max(48, len(label) * 7 + 12)
                    rect = pygame.Rect(bx, sub_y, btn_w, 18)
                    if rect.collidepoint(mx, my):
                        if self._sub_genre == genre:
                            self._sub_genre = ""
                        else:
                            self._sub_genre = genre
                        self._apply_filter()
                        return
                    bx += btn_w + 3
                    if bx + btn_w > theme.SCREEN_WIDTH - 50:
                        break

            # ── Scrollbar ─────────────────────────────────────────────
            list_y = sub_y + 22
            item_h = 32
            list_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 20
            max_visible = (list_bottom - list_y) // item_h
            scrollbar_w = 30
            sb_x = theme.SCREEN_WIDTH - scrollbar_w - 2
            sb_h = max_visible * item_h
            sb_rect = pygame.Rect(sb_x, list_y, scrollbar_w + 2, sb_h)

            if sb_rect.collidepoint(mx, my):
                self._dragging_scrollbar = True
                total = len(self._filtered)
                if total > max_visible:
                    frac = (my - list_y) / sb_h
                    frac = max(0.0, min(1.0, frac))
                    self._scroll_float = frac * (total - max_visible)
                    self._scroll = int(self._scroll_float)
                return

            # ── Station list ──────────────────────────────────────────
            list_w = theme.SCREEN_WIDTH - 16 - scrollbar_w - 6
            visible = self._filtered[self._scroll:self._scroll + max_visible]
            for i, station in enumerate(visible):
                row_rect = pygame.Rect(16, list_y + i * item_h, list_w, item_h - 2)
                if row_rect.collidepoint(mx, my):
                    self._selected = self._scroll + i
                    self._radio.play(station["url"], station.get("name", ""))
                    return

            # ── Bottom controls (not playing) ─────────────────────────
            if not playing:
                bot_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 18
                thresh_rect = pygame.Rect(16, bot_y, 65, 16)
                if thresh_rect.collidepoint(mx, my):
                    self._radio.toggle_threshold_mode()
                    return
                url_rect = pygame.Rect(88, bot_y, 55, 16)
                if url_rect.collidepoint(mx, my):
                    self._url_modal.show(input_mode=True, default_text="https://")
                    return

        # Scroll
        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            max_s = max(0, len(self._filtered) - 7)
            if event.button == 4:
                self._scroll = max(0, self._scroll - 1)
            else:
                self._scroll = min(max_s, self._scroll + 1)

    def update(self):
        if self._radio.is_playing:
            peak_l, peak_r = self._radio.peak_levels
            self._disp_peak_l = max(peak_l, self._disp_peak_l * self._meter_decay)
            self._disp_peak_r = max(peak_r, self._disp_peak_r * self._meter_decay)
            # Feed visualizer
            avg = (self._disp_peak_l + self._disp_peak_r) * 0.5
            self._viz_history[self._viz_pos % self._viz_width] = avg
            self._viz_pos += 1
        else:
            self._disp_peak_l *= self._meter_decay
            self._disp_peak_r *= self._meter_decay

        if self._capture_flash > 0:
            self._capture_flash -= 1
        self._cursor_timer += 1

    def draw(self, surface: pygame.Surface):
        f_title = theme.font("title")
        f_large = theme.font("large")
        f_hero = theme.font("hero")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")
        playing = self._radio.is_playing
        cap_secs = self._radio.capture_seconds

        # ═══════════════════════════════════════════════════════════════
        # TOP ZONE: Now Playing / Station Info (y=0-95)
        # ═══════════════════════════════════════════════════════════════

        if playing:
            # Now playing panel — prominent
            theme.draw_panel(surface, pygame.Rect(0, 0, theme.SCREEN_WIDTH, 96))

            # Station name big
            pygame.draw.circle(surface, theme.GREEN, (22, 18), 6)
            name = self._radio.station_name
            surf = f_large.render(name[:35], True, theme.GREEN)
            surface.blit(surf, (34, 4))

            # Track title
            track = self._radio.track_title
            if track:
                surf = f_med.render(track[:55], True, theme.TEXT)
                surface.blit(surf, (34, 28))

            # ── Full-width visualizer (y=48, h=44) ────────────────────
            viz_rect = pygame.Rect(4, 48, theme.SCREEN_WIDTH - 8, 44)
            pygame.draw.rect(surface, theme.WAVEFORM_BG, viz_rect, border_radius=4)
            bar_w = max(1, viz_rect.width // self._viz_width)
            for i in range(self._viz_width):
                idx = (self._viz_pos - self._viz_width + i) % self._viz_width
                val = self._viz_history[idx]
                bh = int(val * viz_rect.height * 2.5)
                bh = min(bh, viz_rect.height - 2)
                if bh > 0:
                    px = viz_rect.x + i * bar_w
                    if val > 0.4:
                        color = theme.RED
                    elif val > 0.2:
                        color = theme.ACCENT
                    elif val > 0.1:
                        color = (210, 195, 40)
                    else:
                        color = theme.GREEN
                    cy = viz_rect.centery
                    half = bh // 2
                    pygame.draw.line(surface, color, (px, cy - half), (px, cy + half))
            pygame.draw.line(surface, (30, 30, 40),
                            (viz_rect.x, viz_rect.centery),
                            (viz_rect.right, viz_rect.centery), 1)

            # Controls overlaid on right of now-playing zone
            ctrl_x = theme.SCREEN_WIDTH - 300

            # ▶/⏹ Play/Stop (circle button)
            play_rect = pygame.Rect(ctrl_x, 6, 40, 40)
            pygame.draw.rect(surface, theme.GREEN if playing else theme.BUTTON_BG,
                            play_rect, border_radius=20)
            surf = f_large.render("\u25A0", True, theme.BG)  # ■ stop symbol
            surface.blit(surf, surf.get_rect(center=play_rect.center))

            # ⏺ Record (circle)
            rec_rect = pygame.Rect(ctrl_x + 48, 6, 40, 40)
            rec_bg = theme.RED if self._radio.is_recording else theme.BUTTON_BG
            pygame.draw.rect(surface, rec_bg, rec_rect, border_radius=20)
            pygame.draw.circle(surface, theme.RED if not self._radio.is_recording else theme.TEXT_BRIGHT,
                              rec_rect.center, 8)

            # RECALL
            capture_rect = pygame.Rect(ctrl_x + 96, 6, 80, 40)
            if self._capture_flash > 0:
                c_bg, c_text = theme.GREEN, "SAVED!"
            elif cap_secs >= 1:
                c_bg, c_text = theme.ACCENT, f"{int(cap_secs)}s"
            else:
                c_bg, c_text = theme.BUTTON_BG, "RECALL"
            pygame.draw.rect(surface, c_bg, capture_rect, border_radius=6)
            c_tc = theme.BG if c_bg != theme.BUTTON_BG else theme.TEXT_DIM
            surf = f_small.render(c_text, True, c_tc)
            surface.blit(surf, surf.get_rect(center=capture_rect.center))

            # Volume bar (slim, right edge)
            vol = self._radio.volume
            vol_rect = pygame.Rect(ctrl_x + 184, 10, 100, 14)
            pygame.draw.rect(surface, theme.KNOB_BG, vol_rect, border_radius=3)
            vf = int(vol_rect.width * vol)
            if vf > 0:
                pygame.draw.rect(surface, theme.ACCENT,
                                (vol_rect.x, vol_rect.y, vf, vol_rect.height), border_radius=3)
            surf = f_tiny.render(f"{int(vol*100)}%", True, theme.TEXT)
            surface.blit(surf, surf.get_rect(center=vol_rect.center))

            # Vol -/+
            vol_down = pygame.Rect(ctrl_x + 184, 28, 48, 18)
            vol_up = pygame.Rect(ctrl_x + 236, 28, 48, 18)
            pygame.draw.rect(surface, theme.BUTTON_BG, vol_down, border_radius=3)
            pygame.draw.rect(surface, theme.BUTTON_BG, vol_up, border_radius=3)
            surf = f_tiny.render("-VOL", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=vol_down.center))
            surf = f_tiny.render("+VOL", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=vol_up.center))

            # L/R meters below viz
            theme.draw_meter(surface, 4, 94, theme.SCREEN_WIDTH // 2 - 6, 4,
                            self._disp_peak_l)
            theme.draw_meter(surface, theme.SCREEN_WIDTH // 2 + 2, 94,
                            theme.SCREEN_WIDTH // 2 - 6, 4, self._disp_peak_r)

            browser_y = 100
        else:
            # Not playing — simple header
            theme.draw_screen_header(surface, "RADIO", "")

            # Search bar
            search_rect = pygame.Rect(480, 6, 300, 28)
            s_bg = theme.BG_INPUT if self._search_active else theme.BG_PANEL
            s_border = theme.ACCENT if self._search_active else theme.BORDER
            pygame.draw.rect(surface, s_bg, search_rect, border_radius=4)
            pygame.draw.rect(surface, s_border, search_rect, 1, border_radius=4)
            if self._search_text:
                surf = f_small.render(self._search_text, True, theme.TEXT)
                surface.blit(surf, (488, 12))
                if self._search_active and self._cursor_timer % 40 < 25:
                    cx = 488 + surf.get_width() + 2
                    pygame.draw.line(surface, theme.ACCENT, (cx, 10), (cx, 30), 2)
            else:
                surf = f_small.render("Search...", True, theme.TEXT_DIM)
                surface.blit(surf, (488, 12))

            browser_y = 42

        # ═══════════════════════════════════════════════════════════════
        # BROWSER ZONE: Categories + Station List
        # ═══════════════════════════════════════════════════════════════

        # Category row
        cat_y = browser_y
        for i, cat in enumerate(CATEGORY_ORDER):
            rect = pygame.Rect(16 + i * 125, cat_y, 118, 24)
            active = (cat == self._category and not self._search_text)
            bg = theme.ACCENT if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_small.render(cat, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Station count
        surf = f_tiny.render(f"{len(self._filtered)}", True, theme.TEXT_DIM)
        surface.blit(surf, (theme.SCREEN_WIDTH - surf.get_width() - 50, cat_y + 5))

        # Search (when playing, show inline)
        if playing:
            search_rect = pygame.Rect(560, cat_y, 220, 24)
            s_bg = theme.BG_INPUT if self._search_active else theme.BG_PANEL
            pygame.draw.rect(surface, s_bg, search_rect, border_radius=4)
            pygame.draw.rect(surface, theme.BORDER, search_rect, 1, border_radius=4)
            if self._search_text:
                surf = f_tiny.render(self._search_text[:20], True, theme.TEXT)
            else:
                surf = f_tiny.render("Search...", True, theme.TEXT_DIM)
            surface.blit(surf, (568, cat_y + 5))

        # Sub-genre pills
        sub_y = cat_y + 28
        subs = CATEGORIES.get(self._category, [])
        if subs and not self._search_text:
            bx = 16
            for genre in subs:
                label = SUB_LABELS.get(genre, genre.upper())
                btn_w = max(48, len(label) * 7 + 12)
                if bx + btn_w > theme.SCREEN_WIDTH - 50:
                    break
                rect = pygame.Rect(bx, sub_y, btn_w, 18)
                active = (genre == self._sub_genre)
                bg = theme.ACCENT if active else (35, 35, 48)
                tc = theme.BG if active else theme.TEXT_DIM
                pygame.draw.rect(surface, bg, rect, border_radius=3)
                surf = f_tiny.render(label, True, tc)
                surface.blit(surf, surf.get_rect(center=rect.center))
                bx += btn_w + 3

        # ── Station list ──────────────────────────────────────────────
        list_y = sub_y + 22
        item_h = 32
        list_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 20
        max_visible = (list_bottom - list_y) // item_h
        scrollbar_w = 30
        list_w = theme.SCREEN_WIDTH - 16 - scrollbar_w - 6
        visible = self._filtered[self._scroll:self._scroll + max_visible]

        for i, station in enumerate(visible):
            real_idx = self._scroll + i
            ry = list_y + i * item_h
            row_rect = pygame.Rect(16, ry, list_w, item_h - 2)

            is_now = (playing and self._radio.url == station.get("url"))

            if is_now:
                pygame.draw.rect(surface, (25, 55, 25), row_rect, border_radius=3)
                pygame.draw.rect(surface, theme.GREEN, row_rect, 1, border_radius=3)
                pygame.draw.circle(surface, theme.GREEN, (28, ry + item_h // 2), 4)
            elif real_idx == self._selected:
                pygame.draw.rect(surface, (40, 40, 52), row_rect, border_radius=3)
                pygame.draw.rect(surface, theme.BORDER, row_rect, 1, border_radius=3)
            else:
                if i % 2 == 1:
                    pygame.draw.rect(surface, theme.BG_LIGHTER, row_rect, border_radius=3)
                pygame.draw.rect(surface, theme.BORDER, row_rect, 1, border_radius=3)

            # Station name + desc on one line
            name_color = theme.GREEN if is_now else theme.TEXT
            name = station.get("name", "?")[:28]
            surf = f_small.render(name, True, name_color)
            surface.blit(surf, (38, ry + 2))

            desc = station.get("desc", "")[:38]
            surf = f_tiny.render(desc, True, theme.TEXT_DIM)
            surface.blit(surf, (38, ry + 17))

            # Genre pill on right
            genre = station.get("genre", "")
            if genre:
                g_surf = f_tiny.render(genre.upper(), True, theme.ACCENT)
                gx = list_w - g_surf.get_width() + 10
                surface.blit(g_surf, (gx, ry + 8))

        if not self._filtered:
            surf = f_med.render("No stations found", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=400, top=list_y + 40))

        # ── Scrollbar (wide, touch-friendly, no arrows) ───────────────
        total = len(self._filtered)
        sb_x = theme.SCREEN_WIDTH - scrollbar_w - 2
        sb_h = max_visible * item_h
        # Track
        pygame.draw.rect(surface, theme.BG_PANEL,
                        (sb_x, list_y, scrollbar_w, sb_h), border_radius=scrollbar_w // 2)

        if total > max_visible:
            thumb_h = max(36, int(sb_h * max_visible / total))
            max_scroll_val = max(1, total - max_visible)
            thumb_y = list_y + int((sb_h - thumb_h) * self._scroll / max_scroll_val)
            # Thumb — rounded pill
            pygame.draw.rect(surface, theme.ACCENT,
                            (sb_x + 4, thumb_y, scrollbar_w - 8, thumb_h),
                            border_radius=(scrollbar_w - 8) // 2)

        # ── Bottom bar: extra controls when not playing ───────────────
        bot_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 18
        if not playing:
            # THRESH + URL buttons
            thresh_rect = pygame.Rect(16, bot_y, 65, 16)
            th_on = self._radio.threshold_mode
            th_bg = theme.YELLOW if th_on else theme.BG_PANEL
            th_tc = theme.BG if th_on else theme.TEXT_DIM
            pygame.draw.rect(surface, th_bg, thresh_rect, border_radius=3)
            surf = f_tiny.render("THRESH", True, th_tc)
            surface.blit(surf, surf.get_rect(center=thresh_rect.center))

            url_rect = pygame.Rect(88, bot_y, 55, 16)
            pygame.draw.rect(surface, theme.BG_PANEL, url_rect, border_radius=3)
            surf = f_tiny.render("URL", True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=url_rect.center))

        # Modal
        self._url_modal.draw(surface)

    def _draw_meter(self, surface, x, y, w, h, level, label, font):
        lbl = font.render(label, True, theme.TEXT_DIM)
        surface.blit(lbl, (x, y))
        bar_x = x + 20
        bar_w = w - 20
        pygame.draw.rect(surface, theme.WAVEFORM_BG, (bar_x, y, bar_w, h), border_radius=2)
        fill_w = int(bar_w * min(1.0, level))
        if fill_w > 0:
            color = theme.RED if level > 0.9 else theme.YELLOW if level > 0.7 else theme.GREEN
            pygame.draw.rect(surface, color, (bar_x, y, fill_w, h), border_radius=2)
