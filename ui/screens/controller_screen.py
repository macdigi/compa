"""MIDI Controller Mapping — touchscreen UI for ControllerMapper.

Tabbed screen showing every mappable Compa action with its current
MIDI source. Tap LEARN, wiggle a control on the hardware, Compa
captures the MIDI message and writes an override. Tap X to revert
to the shipped profile default.

Layout modeled on ui/screens/io_settings_screen.py (tabs at top,
drag-scroll content area, whole-row-tappable buttons).
"""

import pygame

from .. import theme
from engine import controller_actions


TABS = [
    ("ALL",       "all"),
    ("PADS",      "pad"),
    ("KNOBS",     "twister"),
    ("KEYS",      "keys"),
    ("TRANSPORT", "transport"),
    ("BANKS",     "bank"),
    ("NAV",       "navigation"),
]


class ControllerScreen:
    """Tabbed MIDI Learn UI."""

    def __init__(self, app):
        self.app = app
        self._tab = "all"
        self._scroll_y = 0
        self._row_h = 36
        self._rows: list[dict] = []

        # Drag scroll
        self._drag_start_y = 0
        self._drag_start_scroll = 0
        self._drag_active = False
        self._drag_moved = False
        self._pending_press = None

        # Which binding (controller) are we editing? Cycles via dropdown.
        self._binding_idx = 0

        # Flash feedback + learn state
        self._flash_key = None
        self._flash_until = 0

        # Action message toast
        self._action_msg = ""
        self._action_msg_until = 0

    # ── Lifecycle ────────────────────────────────────────────────

    def on_enter(self):
        self._scroll_y = 0
        # Cancel any stale learn from a previous visit
        mapper = getattr(self.app, "controller_mapper", None)
        if mapper:
            mapper.cancel_learn()

    def on_exit(self):
        mapper = getattr(self.app, "controller_mapper", None)
        if mapper:
            mapper.cancel_learn()

    # ── Helpers ──────────────────────────────────────────────────

    def _mapper(self):
        return getattr(self.app, "controller_mapper", None)

    def _active_binding(self):
        mapper = self._mapper()
        if mapper is None:
            return None
        bs = mapper.connected_controllers()
        if not bs:
            return None
        self._binding_idx = max(0, min(self._binding_idx, len(bs) - 1))
        return bs[self._binding_idx]

    def _set_msg(self, msg: str):
        self._action_msg = msg
        self._action_msg_until = pygame.time.get_ticks() + 2500

    # ── Row building ─────────────────────────────────────────────

    def _actions_for_tab(self, tab: str) -> list[str]:
        """Return action ids to display for the current tab."""
        all_actions = controller_actions.all_actions()
        if tab == "all":
            # Show in a sensible order
            order = []
            for cat in ("transport", "pad", "bank", "twister",
                        "navigation", "keys", "volume"):
                order.extend(controller_actions.actions_by_category(cat))
            return order
        return controller_actions.actions_by_category(tab)

    def _build_rows(self):
        binding = self._active_binding()
        self._rows.clear()

        if binding is None:
            self._rows.append({
                "type": "info",
                "text": "No controller connected.",
                "sub": "Plug in a USB MIDI controller and it will appear here.",
            })
            return

        prof = binding.profile

        # Pinned banner row: Network MIDI bypass toggle for THIS controller.
        # When ON, this controller's MIDI is sent over the network only
        # (no local pad triggers / focus-device control). rtpmidid handles
        # the broadcast — we just suppress local dispatch.
        self._rows.append({
            "type": "bypass",
            "label": "Bypass to Network MIDI",
            "sub": ("On — events go to network peers only"
                    if prof.network_bypass else
                    "Off — controls focused device locally"),
            "on": prof.network_bypass,
        })

        for action_id in self._actions_for_tab(self._tab):
            label = controller_actions.action_label(action_id)
            src = prof.sources_by_action.get(action_id)
            overridden = action_id in prof.overridden_actions

            self._rows.append({
                "type": "mapping",
                "action": action_id,
                "label": label,
                "source_text": src.describe() if src else "(unset)",
                "overridden": overridden,
            })

    # ── Layout rects ─────────────────────────────────────────────

    def _header_h(self) -> int:
        # Title row + controller row + tab row
        return 38 + 34 + 32

    def _tab_rects(self) -> list[tuple[pygame.Rect, str, str]]:
        rects = []
        top = 38 + 34
        w = theme.SCREEN_WIDTH // len(TABS)
        for i, (label, key) in enumerate(TABS):
            r = pygame.Rect(i * w, top, w, 32)
            rects.append((r, label, key))
        return rects

    def _content_rect(self) -> pygame.Rect:
        top = self._header_h()
        h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - top
        return pygame.Rect(0, top, theme.SCREEN_WIDTH, h)

    # ── Event handling ───────────────────────────────────────────

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.app.switch_screen("settings")
                return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            # Back button
            if pygame.Rect(8, 6, 70, 28).collidepoint(mx, my):
                self.app.switch_screen("settings")
                return

            # Controller dropdown (cycle)
            drop_rect = pygame.Rect(8, 42, 300, 26)
            if drop_rect.collidepoint(mx, my):
                mapper = self._mapper()
                if mapper:
                    bs = mapper.connected_controllers()
                    if len(bs) > 1:
                        self._binding_idx = (self._binding_idx + 1) % len(bs)
                        self._set_msg(
                            f"Controller {self._binding_idx + 1}/{len(bs)}")
                return

            # Reset overrides button
            reset_rect = pygame.Rect(theme.SCREEN_WIDTH - 168, 42, 160, 26)
            if reset_rect.collidepoint(mx, my):
                binding = self._active_binding()
                mapper = self._mapper()
                if binding and mapper:
                    mapper.reset_overrides(binding)
                    self._set_msg("Reverted to factory defaults")
                return

            # Tab bar
            for r, _lbl, key in self._tab_rects():
                if r.collidepoint(mx, my):
                    self._tab = key
                    self._scroll_y = 0
                    return

            # Start drag-or-tap tracking for content area
            if self._content_rect().collidepoint(mx, my):
                self._drag_start_y = my
                self._drag_start_scroll = self._scroll_y
                self._drag_active = True
                self._drag_moved = False
                self._pending_press = (mx, my)

        if event.type == pygame.MOUSEMOTION and self._drag_active:
            dy = event.pos[1] - self._drag_start_y
            if abs(dy) > 6:
                self._drag_moved = True
                self._scroll_y = max(0, self._drag_start_scroll - dy)

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._drag_active and not self._drag_moved:
                self._handle_content_tap(*event.pos)
            self._drag_active = False
            self._drag_moved = False
            self._pending_press = None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            self._scroll_y = max(0, self._scroll_y + (-30 if event.button == 4 else 30))

    def _handle_content_tap(self, mx: int, my: int):
        """Figure out which row's button was tapped."""
        cr = self._content_rect()
        if not cr.collidepoint(mx, my):
            return
        # Find the row index
        rel_y = my - cr.y + self._scroll_y
        idx = rel_y // self._row_h
        if not (0 <= idx < len(self._rows)):
            return
        row = self._rows[int(idx)]

        # Bypass-to-Network toggle: any tap on the banner row flips it
        if row.get("type") == "bypass":
            mapper = self._mapper()
            binding = self._active_binding()
            if mapper and binding:
                new_state = not row.get("on", False)
                mapper.set_network_bypass(binding, new_state)
                self._set_msg("Bypass ON — events sent to network only"
                              if new_state else
                              "Bypass OFF — local control restored")
            return

        if row.get("type") != "mapping":
            return

        # Button rects (right side)
        # Learn button: LEARN (or CANCEL if learn is active for this action)
        # Clear button: X (only when overridden)
        learn_rect = pygame.Rect(theme.SCREEN_WIDTH - 145,
                                  cr.y + int(idx) * self._row_h - self._scroll_y + 4,
                                  70, self._row_h - 8)
        clear_rect = pygame.Rect(theme.SCREEN_WIDTH - 68,
                                  cr.y + int(idx) * self._row_h - self._scroll_y + 4,
                                  56, self._row_h - 8)

        mapper = self._mapper()
        binding = self._active_binding()
        if mapper is None or binding is None:
            return

        if learn_rect.collidepoint(mx, my):
            # Toggle learn
            if mapper.learn_target == row["action"]:
                mapper.cancel_learn()
                self._set_msg("Learn cancelled")
            else:
                mapper.set_learn_target(row["action"],
                                         self._on_learn_captured)
                self._set_msg(f"Move a control for {row['label']}…")
            self._flash_key = row["action"]
            self._flash_until = pygame.time.get_ticks() + 150
            return

        if row["overridden"] and clear_rect.collidepoint(mx, my):
            mapper.clear_mapping(binding, row["action"])
            self._set_msg(f"Cleared override for {row['label']}")
            return

    def _on_learn_captured(self, src, binding, action_id):
        """Callback fired by the mapper when a MIDI message is captured
        while a learn target is armed.
        """
        label = controller_actions.action_label(action_id)
        self._set_msg(f"Mapped {label} → {src.describe()}")

    def update(self):
        pass

    # ── Drawing ──────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_title = theme.font("title")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # Header bar
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, 38)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)
        pygame.draw.line(surface, theme.BORDER,
                         (0, 38), (theme.SCREEN_WIDTH, 38))

        # Back button
        back = pygame.Rect(8, 6, 70, 28)
        pygame.draw.rect(surface, theme.BUTTON_BG, back, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, back, 1, border_radius=6)
        surface.blit(f_small.render("< BACK", True, theme.TEXT),
                     f_small.render("< BACK", True, theme.TEXT).get_rect(
                         center=back.center))

        # Title
        title_surf = f_title.render("MIDI CONTROLLER", True, theme.ACCENT)
        surface.blit(title_surf, (back.right + 14, 5))

        # Controller dropdown row
        drop_rect = pygame.Rect(8, 42, 300, 26)
        binding = self._active_binding()
        mapper = self._mapper()

        if binding is None:
            drop_label = "No controller connected"
            pygame.draw.rect(surface, theme.BG_LIGHTER, drop_rect,
                             border_radius=5)
            pygame.draw.rect(surface, theme.BORDER, drop_rect, 1,
                             border_radius=5)
        else:
            bs_count = len(mapper.connected_controllers())
            if bs_count > 1:
                drop_label = f"{binding.profile.name}  ▾  ({self._binding_idx + 1}/{bs_count})"
            else:
                drop_label = binding.profile.name
            pygame.draw.rect(surface, theme.ACCENT_DIM, drop_rect,
                             border_radius=5)
        surface.blit(f_small.render(drop_label, True, theme.TEXT_BRIGHT),
                     (drop_rect.x + 10, drop_rect.y + 5))

        # Reset button
        reset_rect = pygame.Rect(theme.SCREEN_WIDTH - 168, 42, 160, 26)
        pygame.draw.rect(surface, theme.BUTTON_BG, reset_rect, border_radius=5)
        pygame.draw.rect(surface, theme.BORDER, reset_rect, 1, border_radius=5)
        r_lbl = f_tiny.render("RESET OVERRIDES", True, theme.TEXT)
        surface.blit(r_lbl, r_lbl.get_rect(center=reset_rect.center))

        # Tab bar
        for r, label, key in self._tab_rects():
            active = (self._tab == key)
            bg = theme.ACCENT if active else theme.BG_PANEL
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, r)
            pygame.draw.line(surface, theme.BORDER, r.bottomleft, r.bottomright)
            if r.x > 0:
                pygame.draw.line(surface, theme.BORDER, r.topleft, r.bottomleft)
            lbl_surf = f_small.render(label, True, tc)
            surface.blit(lbl_surf, lbl_surf.get_rect(center=r.center))

        # Content area with clipping
        content_rect = self._content_rect()
        clip = surface.get_clip()
        surface.set_clip(content_rect)

        self._build_rows()
        row_h = self._row_h

        # Clamp scroll
        total_h = len(self._rows) * row_h
        max_scroll = max(0, total_h - content_rect.height)
        self._scroll_y = max(0, min(self._scroll_y, max_scroll))

        for i, row in enumerate(self._rows):
            ry = content_rect.y + i * row_h - self._scroll_y
            if ry + row_h < content_rect.y or ry > content_rect.bottom:
                continue

            # Alternating background
            bg_col = theme.BG_PANEL if i % 2 == 0 else theme.BG
            pygame.draw.rect(surface, bg_col,
                             (8, ry, theme.SCREEN_WIDTH - 16, row_h),
                             border_radius=4)

            if row["type"] == "info":
                surface.blit(f_small.render(row["text"], True, theme.TEXT),
                             (20, ry + 6))
                if row.get("sub"):
                    surface.blit(f_tiny.render(row["sub"], True,
                                               theme.TEXT_DIM),
                                 (20, ry + 22))
                continue

            if row["type"] == "bypass":
                # Header-style banner: label left, status sub-text below,
                # pill toggle right. Tap anywhere on row to toggle.
                is_on = row.get("on", False)
                surface.blit(f_small.render(row["label"], True,
                                            theme.TEXT_BRIGHT),
                             (20, ry + 4))
                surface.blit(f_tiny.render(row.get("sub", ""), True,
                                           theme.TEXT_DIM),
                             (20, ry + 22))
                pill_w, pill_h = 64, row_h - 12
                pill_rect = pygame.Rect(theme.SCREEN_WIDTH - pill_w - 16,
                                        ry + 6, pill_w, pill_h)
                pill_bg = theme.YELLOW if is_on else theme.BG_LIGHTER
                pygame.draw.rect(surface, pill_bg, pill_rect,
                                 border_radius=pill_h // 2)
                pygame.draw.rect(surface, theme.BORDER, pill_rect, 1,
                                 border_radius=pill_h // 2)
                pill_label = "ON" if is_on else "OFF"
                pill_tc = theme.BG if is_on else theme.TEXT
                pl = f_tiny.render(pill_label, True, pill_tc)
                surface.blit(pl, pl.get_rect(center=pill_rect.center))
                continue

            if row["type"] == "mapping":
                action_id = row["action"]
                is_learn = (mapper and mapper.learn_target == action_id)

                # Action label
                label_color = theme.TEXT if not is_learn else theme.ACCENT
                surface.blit(f_small.render(row["label"], True, label_color),
                             (20, ry + (row_h - 16) // 2))

                # Source description (middle)
                src_color = theme.TEXT_BRIGHT if row["overridden"] \
                    else theme.TEXT_DIM
                src_text = row["source_text"]
                if is_learn:
                    src_text = "Waiting for MIDI…"
                    src_color = theme.YELLOW
                src_surf = f_tiny.render(src_text[:40], True, src_color)
                surface.blit(src_surf,
                             (280, ry + (row_h - src_surf.get_height()) // 2))

                # LEARN button (or CANCEL if armed)
                learn_rect = pygame.Rect(theme.SCREEN_WIDTH - 145,
                                          ry + 4, 70, row_h - 8)
                if is_learn:
                    l_bg = theme.YELLOW
                    l_text = "CANCEL"
                    l_tc = theme.BG
                else:
                    l_bg = theme.ACCENT_DIM
                    l_text = "LEARN"
                    l_tc = theme.TEXT_BRIGHT
                pygame.draw.rect(surface, l_bg, learn_rect, border_radius=5)
                lbl = f_tiny.render(l_text, True, l_tc)
                surface.blit(lbl, lbl.get_rect(center=learn_rect.center))

                # X (clear) — only shown if overridden
                if row["overridden"]:
                    clear_rect = pygame.Rect(theme.SCREEN_WIDTH - 68,
                                              ry + 4, 56, row_h - 8)
                    pygame.draw.rect(surface, theme.BG_LIGHTER, clear_rect,
                                     border_radius=5)
                    pygame.draw.rect(surface, theme.BORDER, clear_rect, 1,
                                     border_radius=5)
                    surface.blit(f_tiny.render("CLEAR", True, theme.TEXT_DIM),
                                 f_tiny.render("CLEAR", True,
                                               theme.TEXT_DIM).get_rect(
                                     center=clear_rect.center))

        surface.set_clip(clip)

        # Scrollbar
        if total_h > content_rect.height:
            bar_x = theme.SCREEN_WIDTH - 5
            thumb_h = max(20, int(content_rect.height *
                                   content_rect.height / total_h))
            thumb_y = content_rect.y + int(
                (content_rect.height - thumb_h) * self._scroll_y
                / max(1, total_h - content_rect.height))
            pygame.draw.rect(surface, theme.BORDER,
                             (bar_x, content_rect.y, 3, content_rect.height),
                             border_radius=1)
            pygame.draw.rect(surface, theme.ACCENT,
                             (bar_x, thumb_y, 3, thumb_h),
                             border_radius=1)

        # Toast
        if pygame.time.get_ticks() < self._action_msg_until \
                and self._action_msg:
            toast = f_small.render(self._action_msg[:80], True,
                                    theme.TEXT_BRIGHT)
            tw = toast.get_width() + 18
            th = toast.get_height() + 10
            tx = (theme.SCREEN_WIDTH - tw) // 2
            ty = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - th - 8
            bg = pygame.Surface((tw, th), pygame.SRCALPHA)
            bg.fill((*theme.ACCENT_DIM, 230))
            surface.blit(bg, (tx, ty))
            pygame.draw.rect(surface, theme.ACCENT, (tx, ty, tw, th), 1,
                             border_radius=4)
            surface.blit(toast, (tx + 9, ty + 5))
