"""File Browser Screen — full Finder/Explorer-style file manager.

Browse the entire Compa file system: recordings, samples, kits, sessions.
Also browse files on peer Compas via the network link.
"""

import os
import shutil
import threading
import time
import pygame
from .. import theme
from ..components.folder_browser import FolderBrowser

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FileBrowserScreen:
    """Full file system browser with multi-location and peer navigation."""

    # Quick locations in the sidebar
    LOCATIONS = [
        ("Recordings", "recordings"),
        ("Samples",    "samples"),
        ("Kits",       "kits"),
        ("Sessions",   "sessions"),
        ("Network",    "network"),
    ]

    def __init__(self, app):
        self.app = app
        self._sidebar_w = 140
        self._toolbar_h = 36
        self._current_loc = "recordings"
        self._browser = None
        self._selected_file: str | None = None
        self._action_flash = 0
        self._action_msg = ""

        # Peer browsing state
        self._viewing_peer: dict | None = None  # peer dict if browsing remote
        self._peer_files: list[dict] = []
        self._peer_scroll = 0
        self._selected_peer_file: dict | None = None

        # Transfer progress
        self._transfer_active = False
        self._transfer_msg = ""
        self._transfer_progress = 0.0  # 0.0-1.0

        # Now-playing tracking
        self._playing_file: str | None = None

        # Network dual-pane state
        self._net_selected_peer: dict | None = None
        self._net_local_files: list[dict] = []
        self._net_peer_files: list[dict] = []
        self._net_local_selected: set[str] = set()
        self._net_peer_selected: set[str] = set()
        self._net_local_scroll = 0
        self._net_peer_scroll = 0
        self._net_status = ""

        self._build_browser()

    def on_enter(self):
        if self._browser:
            self._browser.refresh()

    def on_exit(self):
        # Stop any preview playback
        if hasattr(self.app, 'recorder'):
            self.app.recorder.stop_playback()

    def _location_path(self, key: str) -> str:
        """Get the absolute path for a location key."""
        if key == "recordings":
            return self.app.config.get("P6_RECORDING_DIR", os.path.join(PROJECT_ROOT, "recordings"))
        return os.path.join(PROJECT_ROOT, key)

    def _build_browser(self):
        rect = pygame.Rect(
            self._sidebar_w + 4,
            44 + self._toolbar_h,
            theme.SCREEN_WIDTH - self._sidebar_w - 8,
            theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 44 - self._toolbar_h - 4,
        )
        path = self._location_path(self._current_loc)
        self._browser = FolderBrowser(rect, root_dir=path, item_height=44)

    def _switch_location(self, loc: str):
        self._current_loc = loc
        self._viewing_peer = None
        self._selected_file = None
        self._selected_peer_file = None

        if loc == "network":
            # Load local files for the left pane
            self._load_network_local()
            # Auto-connect to first peer if available
            if hasattr(self.app, 'compa_browser'):
                peers = self.app.compa_browser.peers
                if peers:
                    self._net_selected_peer = peers[0]
                    self._load_network_peer()
        else:
            self._build_browser()

    def _load_network_local(self):
        """Load local recordings for the network dual-pane left side."""
        local_dir = self.app.config.get("P6_RECORDING_DIR")
        files = []
        if local_dir and os.path.isdir(local_dir):
            for fn in sorted(os.listdir(local_dir)):
                if fn.startswith(".") or fn.endswith(".meta.json"):
                    continue
                fp = os.path.join(local_dir, fn)
                if os.path.isfile(fp):
                    files.append({
                        "name": fn,
                        "size": os.path.getsize(fp),
                        "path": fp,
                    })
        self._net_local_files = files
        self._net_local_selected.clear()
        self._net_local_scroll = 0

    def _load_network_peer(self):
        """Load peer files in the background for the network right pane."""
        if not self._net_selected_peer:
            return
        from engine.compa_link import list_peer_files
        peer = self._net_selected_peer

        def _load():
            try:
                self._net_status = f"Connecting to {peer['name']}..."
                files = list_peer_files(peer, "recordings")
                self._net_peer_files = [f for f in files
                                        if not f["name"].startswith(".")
                                        and not f["name"].endswith(".meta.json")]
                self._net_status = f"Connected — {len(self._net_peer_files)} files"
            except Exception as e:
                self._net_status = f"Connection failed: {e}"

        import threading
        threading.Thread(target=_load, daemon=True).start()

    def _switch_to_peer(self, peer: dict):
        """Switch to viewing a peer's files."""
        self._viewing_peer = peer
        self._selected_peer_file = None
        self._peer_scroll = 0
        # Load files in background
        from engine.compa_link import list_peer_files

        def _load():
            try:
                self._action_msg = f"Loading {peer['name']}..."
                self._action_flash = 30
                files = list_peer_files(peer, "recordings")
                # Filter out hidden files
                self._peer_files = [f for f in files if not f["name"].startswith(".")]
                self._action_msg = f"{len(self._peer_files)} files on {peer['name']}"
                self._action_flash = 60
            except Exception as e:
                self._action_msg = f"Connection failed: {e}"
                self._action_flash = 90

        threading.Thread(target=_load, daemon=True).start()

    def _peer_list_rect(self) -> pygame.Rect:
        return pygame.Rect(
            self._sidebar_w + 4,
            44 + self._toolbar_h + 4,
            theme.SCREEN_WIDTH - self._sidebar_w - 8,
            theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 44 - self._toolbar_h - 8,
        )

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Sidebar locations (local)
            for i, (label, key) in enumerate(self.LOCATIONS):
                r = pygame.Rect(4, 44 + i * 44, self._sidebar_w - 8, 40)
                if r.collidepoint(mx, my):
                    self._switch_location(key)
                    return

            # Network pane clicks
            if self._current_loc == "network":
                self._handle_network_clicks(mx, my)
                return

            # Sidebar peer entries (below local locations)
            peer_y = 44 + len(self.LOCATIONS) * 44 + 12
            if hasattr(self.app, 'compa_browser'):
                peers = self.app.compa_browser.peers
                for i, peer in enumerate(peers):
                    r = pygame.Rect(4, peer_y + i * 36, self._sidebar_w - 8, 32)
                    if r.collidepoint(mx, my):
                        self._switch_to_peer(peer)
                        return

            # Toolbar buttons
            tb_y = 44
            btn_w = 90
            if self._viewing_peer:
                buttons = [
                    ("DOWNLOAD", self._download_selected_peer_file),
                    ("PULL ALL", self._pull_all_from_current_peer),
                    ("BACK",     lambda: self._switch_location(self._current_loc)),
                ]
            else:
                buttons = [
                    ("PLAY",   self._play_selected),
                    ("STOP",   self._stop_playback),
                    ("DELETE", self._delete_selected),
                ]
            for i, (label, action) in enumerate(buttons):
                r = pygame.Rect(self._sidebar_w + 8 + i * (btn_w + 4), tb_y, btn_w, self._toolbar_h - 4)
                if r.collidepoint(mx, my):
                    action()
                    return

            # Peer file list clicks
            if self._viewing_peer and self._peer_files:
                list_rect = self._peer_list_rect()
                if list_rect.collidepoint(mx, my):
                    item_h = 32
                    rel_y = my - list_rect.y + self._peer_scroll
                    idx = rel_y // item_h
                    if 0 <= idx < len(self._peer_files):
                        self._selected_peer_file = self._peer_files[idx]
                    return

            # Mouse wheel scroll
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 4:
                self._peer_scroll = max(0, self._peer_scroll - 32)
                return
            elif event.button == 5:
                self._peer_scroll += 32
                return

        # Pass to local browser
        if not self._viewing_peer and self._browser:
            result = self._browser.handle_event(event)
            if result and result.get("type") == "file":
                self._selected_file = result["path"]

    def update(self):
        if self._browser:
            self._browser.update()
        if self._action_flash > 0:
            self._action_flash -= 1

    def draw(self, surface: pygame.Surface):
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # Header
        surf = f_large.render("FILES", True, theme.ACCENT)
        surface.blit(surf, (10, 6))

        # Current path (right of header)
        if self._viewing_peer:
            label = f"{self._viewing_peer['name']} ({self._viewing_peer['ip']})"
            surf = f_small.render(f"📡 {label}", True, theme.BLUE)
            surface.blit(surf, (110, 14))
        elif self._browser:
            path = self._browser.current_path
            short = path.replace(PROJECT_ROOT, "~")
            if len(short) > 70:
                short = "..." + short[-67:]
            surf = f_small.render(short, True, theme.TEXT_DIM)
            surface.blit(surf, (110, 14))

        # Sidebar — local locations
        for i, (label, key) in enumerate(self.LOCATIONS):
            r = pygame.Rect(4, 44 + i * 44, self._sidebar_w - 8, 40)
            active = key == self._current_loc and not self._viewing_peer
            bg = theme.ACCENT if active else theme.BG_PANEL
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, r, border_radius=6)
            surf = f_med.render(label, True, tc)
            surface.blit(surf, (r.x + 12, r.y + 10))

        # Sidebar — peer Compas
        peer_y = 44 + len(self.LOCATIONS) * 44 + 12
        if hasattr(self.app, 'compa_browser'):
            peers = self.app.compa_browser.peers
            if peers:
                surf = f_tiny.render("PEERS", True, theme.TEXT_DIM)
                surface.blit(surf, (8, peer_y - 14))
                for i, peer in enumerate(peers):
                    r = pygame.Rect(4, peer_y + i * 36, self._sidebar_w - 8, 32)
                    active = self._viewing_peer and self._viewing_peer["name"] == peer["name"]
                    bg = theme.BLUE if active else theme.BG_PANEL
                    tc = theme.BG if active else theme.TEXT
                    pygame.draw.rect(surface, bg, r, border_radius=6)
                    surf = f_small.render(peer["name"][:16], True, tc)
                    surface.blit(surf, (r.x + 12, r.y + 9))

        # Toolbar
        tb_y = 44
        btn_w = 90
        if self._viewing_peer:
            buttons = [
                ("DOWNLOAD", theme.GREEN),
                ("PULL ALL", theme.BLUE),
                ("BACK",     theme.BUTTON_BG),
            ]
        else:
            buttons = [
                ("PLAY",   theme.GREEN),
                ("STOP",   theme.BUTTON_BG),
                ("DELETE", theme.RED),
            ]
        for i, (label, color) in enumerate(buttons):
            r = pygame.Rect(self._sidebar_w + 8 + i * (btn_w + 4), tb_y, btn_w, self._toolbar_h - 4)
            pygame.draw.rect(surface, color, r, border_radius=5)
            surf = f_small.render(label, True, theme.TEXT_BRIGHT)
            surface.blit(surf, surf.get_rect(center=r.center))

        # Status / selected file (right of toolbar)
        status_x = self._sidebar_w + 8 + len(buttons) * (btn_w + 4) + 8
        if self._action_flash > 0:
            surf = f_small.render(self._action_msg, True, theme.ACCENT)
            surface.blit(surf, (status_x, tb_y + 8))
        elif self._viewing_peer and self._selected_peer_file:
            name = self._selected_peer_file["name"]
            if len(name) > 30:
                name = name[:27] + "..."
            surf = f_small.render(f"Selected: {name}", True, theme.TEXT_DIM)
            surface.blit(surf, (status_x, tb_y + 8))
        elif self._selected_file:
            name = os.path.basename(self._selected_file)
            if len(name) > 30:
                name = name[:27] + "..."
            surf = f_small.render(f"Selected: {name}", True, theme.TEXT_DIM)
            surface.blit(surf, (status_x, tb_y + 8))

        # Now playing indicator (top of content area)
        rec = getattr(self.app, 'recorder', None)
        if rec and getattr(rec, 'is_playing_back', False):
            pf = getattr(rec, 'playback_file', '')
            if pf:
                np_y = 44 + self._toolbar_h + 4
                np_rect = pygame.Rect(self._sidebar_w + 4, np_y, theme.SCREEN_WIDTH - self._sidebar_w - 8, 22)
                pygame.draw.rect(surface, theme.GREEN, np_rect, border_radius=4)
                name = os.path.basename(pf)
                if len(name) > 50:
                    name = name[:47] + "..."
                surf = f_small.render(f"▶ Playing: {name}", True, theme.BG)
                surface.blit(surf, surf.get_rect(centery=np_rect.centery, left=np_rect.x + 8))

        # Content area
        if self._current_loc == "network":
            self._draw_network_panes(surface, f_med, f_small, f_tiny)
        elif self._viewing_peer:
            self._draw_peer_files(surface, f_small, f_tiny)
        elif self._browser:
            # Adjust browser rect if now-playing bar is showing
            if rec and getattr(rec, 'is_playing_back', False):
                br = pygame.Rect(
                    self._sidebar_w + 4, 44 + self._toolbar_h + 30,
                    theme.SCREEN_WIDTH - self._sidebar_w - 8,
                    theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 44 - self._toolbar_h - 32,
                )
                self._browser.set_rect(br)
            self._browser.draw(surface)

        # Transfer progress overlay
        if self._transfer_active:
            self._draw_progress_overlay(surface, f_med, f_small)

    def _draw_peer_files(self, surface, f_small, f_tiny):
        """Draw the peer files list (when viewing a peer)."""
        list_rect = self._peer_list_rect()
        pygame.draw.rect(surface, theme.BG_PANEL, list_rect, border_radius=4)

        if not self._peer_files:
            surf = f_small.render("Loading or empty...", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=list_rect.center))
            return

        item_h = 32
        clip = surface.get_clip()
        surface.set_clip(list_rect)
        y = list_rect.y - self._peer_scroll
        for i, f in enumerate(self._peer_files):
            row = pygame.Rect(list_rect.x, y, list_rect.width, item_h)
            if row.bottom < list_rect.y or row.y > list_rect.bottom:
                y += item_h
                continue
            selected = (self._selected_peer_file is not None and
                        f["name"] == self._selected_peer_file["name"])
            if selected:
                pygame.draw.rect(surface, theme.BLUE, row)
            elif i % 2 == 0:
                pygame.draw.rect(surface, (20, 20, 28), row)
            name = f["name"]
            if len(name) > 50:
                name = name[:47] + "..."
            surf = f_small.render(name, True, theme.BG if selected else theme.TEXT)
            surface.blit(surf, (row.x + 8, row.y + 8))
            size_kb = f["size"] / 1024
            size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
            surf = f_tiny.render(size_str, True, theme.BG if selected else theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(top=row.y + 9, right=row.right - 8))
            y += item_h
        surface.set_clip(clip)

    def _network_pane_rects(self):
        """Return (left_pane_rect, right_pane_rect) for the dual-pane view."""
        content_top = 44 + self._toolbar_h + 4
        content_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 4
        content_left = self._sidebar_w + 4
        content_right = theme.SCREEN_WIDTH - 4
        pane_w = (content_right - content_left - 100) // 2  # room for 100px center buttons
        left = pygame.Rect(content_left, content_top, pane_w, content_bottom - content_top)
        right = pygame.Rect(content_right - pane_w, content_top, pane_w, content_bottom - content_top)
        return left, right

    def _draw_network_panes(self, surface, f_med, f_small, f_tiny):
        """Dual-pane file transfer between local and peer Compa."""
        left, right = self._network_pane_rects()
        item_h = 28
        header_h = 28

        # Status bar (above panes)
        if self._net_status:
            surf = f_small.render(self._net_status, True, theme.ACCENT)
            surface.blit(surf, (left.x, left.y - 18))

        # ── LEFT PANE: This Compa ──────────────────────────────────
        pygame.draw.rect(surface, theme.BG_PANEL, left, border_radius=6)
        pygame.draw.rect(surface, self._device_color_for_self(), left, 2, border_radius=6)

        import socket
        my_name = socket.gethostname()
        surf = f_med.render(f"THIS ({my_name})", True, self._device_color_for_self())
        surface.blit(surf, (left.x + 10, left.y + 6))
        surf = f_tiny.render(f"{len(self._net_local_files)} files", True, theme.TEXT_DIM)
        surface.blit(surf, (left.x + 10, left.y + header_h))

        list_rect = pygame.Rect(left.x + 4, left.y + header_h + 20, left.width - 8,
                                 left.height - header_h - 24)
        clip = surface.get_clip()
        surface.set_clip(list_rect)
        y = list_rect.y - self._net_local_scroll
        for i, f in enumerate(self._net_local_files):
            row = pygame.Rect(list_rect.x, y, list_rect.width, item_h)
            if row.bottom >= list_rect.y and row.y <= list_rect.bottom:
                selected = f["name"] in self._net_local_selected
                bg = (60, 90, 140) if selected else ((20, 20, 28) if i % 2 == 0 else (15, 15, 22))
                pygame.draw.rect(surface, bg, row)
                name = f["name"]
                if len(name) > 38:
                    name = name[:35] + "..."
                txt_color = theme.TEXT_BRIGHT if selected else theme.TEXT
                surf = f_tiny.render(name, True, txt_color)
                surface.blit(surf, (row.x + 6, row.y + 8))
                size_kb = f["size"] / 1024
                size_str = f"{size_kb:.0f}K" if size_kb < 1024 else f"{size_kb / 1024:.1f}M"
                surf = f_tiny.render(size_str, True, theme.TEXT_DIM)
                surface.blit(surf, surf.get_rect(top=row.y + 8, right=row.right - 6))
            y += item_h
        surface.set_clip(clip)

        # ── CENTER: Transfer buttons ───────────────────────────────
        center_x = left.right + 8
        center_w = right.x - center_x - 8
        btn_h = 50

        has_peer = self._net_selected_peer is not None

        # Send right (local → peer)
        send_r_rect = pygame.Rect(center_x, left.centery - btn_h - 12, center_w, btn_h)
        can_send_r = bool(self._net_local_selected) and has_peer
        bg = theme.ACCENT if can_send_r else theme.BUTTON_BG
        pygame.draw.rect(surface, bg, send_r_rect, border_radius=8)
        surf = f_tiny.render("SEND >>", True, theme.BG if can_send_r else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=send_r_rect.center))

        # Pull left (peer → local)
        pull_l_rect = pygame.Rect(center_x, left.centery + 12, center_w, btn_h)
        can_pull = bool(self._net_peer_selected) and has_peer
        bg = theme.BLUE if can_pull else theme.BUTTON_BG
        pygame.draw.rect(surface, bg, pull_l_rect, border_radius=8)
        surf = f_tiny.render("<< PULL", True, theme.BG if can_pull else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=pull_l_rect.center))

        # ── RIGHT PANE: Peer Compa ─────────────────────────────────
        pygame.draw.rect(surface, theme.BG_PANEL, right, border_radius=6)
        pygame.draw.rect(surface, theme.BLUE if has_peer else theme.BORDER, right, 2, border_radius=6)

        if has_peer:
            peer_name = self._net_selected_peer["name"]
            surf = f_med.render(f"PEER ({peer_name})", True, theme.BLUE)
            surface.blit(surf, (right.x + 10, right.y + 6))
            surf = f_tiny.render(f"{len(self._net_peer_files)} files · {self._net_selected_peer['ip']}",
                                 True, theme.TEXT_DIM)
            surface.blit(surf, (right.x + 10, right.y + header_h))

            rlist_rect = pygame.Rect(right.x + 4, right.y + header_h + 20, right.width - 8,
                                     right.height - header_h - 24)
            surface.set_clip(rlist_rect)
            y = rlist_rect.y - self._net_peer_scroll
            for i, f in enumerate(self._net_peer_files):
                row = pygame.Rect(rlist_rect.x, y, rlist_rect.width, item_h)
                if row.bottom >= rlist_rect.y and row.y <= rlist_rect.bottom:
                    selected = f["name"] in self._net_peer_selected
                    bg = (60, 90, 140) if selected else ((20, 20, 28) if i % 2 == 0 else (15, 15, 22))
                    pygame.draw.rect(surface, bg, row)
                    name = f["name"]
                    if len(name) > 38:
                        name = name[:35] + "..."
                    txt_color = theme.TEXT_BRIGHT if selected else theme.TEXT
                    surf = f_tiny.render(name, True, txt_color)
                    surface.blit(surf, (row.x + 6, row.y + 8))
                    size_kb = f["size"] / 1024
                    size_str = f"{size_kb:.0f}K" if size_kb < 1024 else f"{size_kb / 1024:.1f}M"
                    surf = f_tiny.render(size_str, True, theme.TEXT_DIM)
                    surface.blit(surf, surf.get_rect(top=row.y + 8, right=row.right - 6))
                y += item_h
            surface.set_clip(clip)
        else:
            msg = "No peer selected" if not hasattr(self.app, 'compa_browser') or not self.app.compa_browser.peers else "Tap a peer button below"
            surf = f_small.render(msg, True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=right.center))

        # Peer selector buttons (bottom of right pane)
        if hasattr(self.app, 'compa_browser'):
            peers = self.app.compa_browser.peers
            if peers:
                peer_btn_y = right.bottom + 4
                pbw = 90
                for i, peer in enumerate(peers):
                    r = pygame.Rect(right.x + i * (pbw + 4), peer_btn_y, pbw, 20)
                    active = self._net_selected_peer and self._net_selected_peer["name"] == peer["name"]
                    bg = theme.BLUE if active else theme.BUTTON_BG
                    pygame.draw.rect(surface, bg, r, border_radius=4)
                    tc = theme.BG if active else theme.TEXT
                    surf = f_tiny.render(peer["name"][:10], True, tc)
                    surface.blit(surf, surf.get_rect(center=r.center))

    def _device_color_for_self(self) -> tuple:
        return theme.ACCENT

    def _draw_progress_overlay(self, surface, f_med, f_small):
        """Centered progress modal during transfers."""
        w, h = 400, 100
        x = (theme.SCREEN_WIDTH - w) // 2
        y = (theme.SCREEN_HEIGHT - h) // 2
        # Backdrop
        backdrop = pygame.Surface((theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT), pygame.SRCALPHA)
        backdrop.fill((0, 0, 0, 160))
        surface.blit(backdrop, (0, 0))
        # Modal
        modal = pygame.Rect(x, y, w, h)
        pygame.draw.rect(surface, theme.BG_PANEL, modal, border_radius=10)
        pygame.draw.rect(surface, theme.ACCENT, modal, 2, border_radius=10)
        # Message
        surf = f_med.render(self._transfer_msg or "Transferring...", True, theme.TEXT_BRIGHT)
        surface.blit(surf, surf.get_rect(centerx=modal.centerx, top=modal.y + 20))
        # Progress bar
        bar_w = w - 40
        bar_h = 16
        bar_x = x + 20
        bar_y = y + 60
        pygame.draw.rect(surface, theme.BG_LIGHTER, (bar_x, bar_y, bar_w, bar_h), border_radius=4)
        fill_w = int(bar_w * self._transfer_progress)
        if fill_w > 0:
            pygame.draw.rect(surface, theme.ACCENT, (bar_x, bar_y, fill_w, bar_h), border_radius=4)
        # Percentage
        pct = int(self._transfer_progress * 100)
        surf = f_small.render(f"{pct}%", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(centerx=modal.centerx, top=bar_y + bar_h + 4))

    # ── Actions ──────────────────────────────────────────────────────

    def _play_selected(self):
        if not self._selected_file:
            return
        if not self._selected_file.lower().endswith((".wav", ".mp3", ".flac", ".aif", ".aiff")):
            return
        # Open the full audio player modal
        if hasattr(self.app, 'audio_player'):
            self.app.audio_player.show(self._selected_file)
        elif hasattr(self.app, 'recorder'):
            self.app.recorder.play(self._selected_file)

    def _stop_playback(self):
        if hasattr(self.app, 'recorder'):
            self.app.recorder.stop_playback()
            self._flash("Stopped")

    def _delete_selected(self):
        if not self._selected_file:
            return
        try:
            os.remove(self._selected_file)
            # Also remove sidecar metadata if it exists
            meta = self._selected_file + ".meta.json"
            if os.path.exists(meta):
                os.remove(meta)
            self._flash(f"Deleted {os.path.basename(self._selected_file)}")
            self._selected_file = None
            if self._browser:
                self._browser.refresh()
        except Exception as e:
            self._flash(f"Delete failed: {e}")

    def _download_selected_peer_file(self):
        """Download a single file from the current peer."""
        if not self._viewing_peer or not self._selected_peer_file:
            return
        from engine.compa_link import download_peer_file
        peer = self._viewing_peer
        fname = self._selected_peer_file["name"]
        size = self._selected_peer_file.get("size", 0)
        local_dir = self.app.config.get("P6_RECORDING_DIR")

        def _dl():
            self._transfer_active = True
            self._transfer_msg = f"Downloading {fname[:30]}"
            self._transfer_progress = 0.1
            try:
                # We don't have streaming progress, so animate indeterminate
                path = download_peer_file(peer, "recordings", fname, local_dir)
                self._transfer_progress = 1.0
                if path:
                    self._action_msg = f"Downloaded {fname[:30]}"
                    self._action_flash = 90
                    # Also fetch sidecar metadata
                    meta_name = fname + ".meta.json"
                    download_peer_file(peer, "recordings", meta_name, local_dir, timeout=5)
                else:
                    self._action_msg = f"Download failed"
                    self._action_flash = 90
            except Exception as e:
                self._action_msg = f"Error: {e}"
                self._action_flash = 90
            finally:
                time.sleep(0.5)
                self._transfer_active = False

        threading.Thread(target=_dl, daemon=True).start()

    def _pull_all_from_current_peer(self):
        """Pull all new files from the current peer."""
        if not self._viewing_peer or not self._peer_files:
            return
        from engine.compa_link import download_peer_file
        peer = self._viewing_peer
        files = list(self._peer_files)
        local_dir = self.app.config.get("P6_RECORDING_DIR")

        def _pull():
            self._transfer_active = True
            existing = set(os.listdir(local_dir)) if os.path.isdir(local_dir) else set()
            todo = [f for f in files if f["name"] not in existing
                    and (f["name"].endswith(".wav") or f["name"].endswith(".json"))]
            total = len(todo)
            if total == 0:
                self._transfer_msg = "All files already local"
                self._transfer_progress = 1.0
                time.sleep(1)
                self._transfer_active = False
                return

            ok = 0
            for i, f in enumerate(todo):
                self._transfer_msg = f"[{i+1}/{total}] {f['name'][:30]}"
                self._transfer_progress = i / total
                try:
                    path = download_peer_file(peer, "recordings", f["name"], local_dir)
                    if path:
                        ok += 1
                except Exception as e:
                    print(f"  failed: {e}", flush=True)
            self._transfer_progress = 1.0
            self._transfer_msg = f"Pulled {ok}/{total} files"
            time.sleep(1.5)
            self._transfer_active = False
            self._action_msg = f"Pulled {ok} files from {peer['name']}"
            self._action_flash = 120

        threading.Thread(target=_pull, daemon=True).start()

    def _handle_network_clicks(self, mx, my):
        """Handle clicks in the dual-pane network transfer view."""
        left, right = self._network_pane_rects()
        item_h = 28
        header_h = 28

        # Peer selector buttons (bottom of right pane)
        if hasattr(self.app, 'compa_browser'):
            peers = self.app.compa_browser.peers
            if peers:
                peer_btn_y = right.bottom + 4
                pbw = 90
                for i, peer in enumerate(peers):
                    r = pygame.Rect(right.x + i * (pbw + 4), peer_btn_y, pbw, 20)
                    if r.collidepoint(mx, my):
                        self._net_selected_peer = peer
                        self._net_peer_selected.clear()
                        self._load_network_peer()
                        return

        # Transfer buttons
        center_x = left.right + 8
        center_w = right.x - center_x - 8
        btn_h = 50

        send_r_rect = pygame.Rect(center_x, left.centery - btn_h - 12, center_w, btn_h)
        if send_r_rect.collidepoint(mx, my):
            self._send_selected_to_peer()
            return

        pull_l_rect = pygame.Rect(center_x, left.centery + 12, center_w, btn_h)
        if pull_l_rect.collidepoint(mx, my):
            self._pull_selected_from_peer()
            return

        # Left pane — toggle local file selection
        list_rect = pygame.Rect(left.x + 4, left.y + header_h + 20, left.width - 8,
                                 left.height - header_h - 24)
        if list_rect.collidepoint(mx, my):
            idx = (my - list_rect.y + self._net_local_scroll) // item_h
            if 0 <= idx < len(self._net_local_files):
                name = self._net_local_files[idx]["name"]
                if name in self._net_local_selected:
                    self._net_local_selected.discard(name)
                else:
                    self._net_local_selected.add(name)
            return

        # Right pane — toggle peer file selection
        if self._net_selected_peer:
            rlist_rect = pygame.Rect(right.x + 4, right.y + header_h + 20, right.width - 8,
                                     right.height - header_h - 24)
            if rlist_rect.collidepoint(mx, my):
                idx = (my - rlist_rect.y + self._net_peer_scroll) // item_h
                if 0 <= idx < len(self._net_peer_files):
                    name = self._net_peer_files[idx]["name"]
                    if name in self._net_peer_selected:
                        self._net_peer_selected.discard(name)
                    else:
                        self._net_peer_selected.add(name)
                return

    def _send_selected_to_peer(self):
        """Send locally-selected files to the peer via HTTP upload.

        Requires a server-side upload endpoint. For now, shows a message
        that this direction requires the peer to pull.
        """
        self._net_status = "Send requires upload endpoint (use PULL from peer instead)"

    def _pull_selected_from_peer(self):
        """Pull selected files from the peer to local."""
        if not self._net_selected_peer or not self._net_peer_selected:
            self._net_status = "No files selected on peer"
            return
        from engine.compa_link import download_peer_file
        peer = self._net_selected_peer
        files = [f for f in self._net_peer_files if f["name"] in self._net_peer_selected]
        local_dir = self.app.config.get("P6_RECORDING_DIR")

        def _pull():
            self._transfer_active = True
            total = len(files)
            ok = 0
            for i, f in enumerate(files):
                self._transfer_msg = f"[{i+1}/{total}] {f['name'][:30]}"
                self._transfer_progress = i / total
                try:
                    path = download_peer_file(peer, "recordings", f["name"], local_dir)
                    if path:
                        ok += 1
                        # Also fetch metadata sidecar
                        download_peer_file(peer, "recordings", f["name"] + ".meta.json",
                                           local_dir, timeout=5)
                except Exception as e:
                    print(f"  {f['name']}: {e}", flush=True)
            self._transfer_progress = 1.0
            self._transfer_msg = f"Done — {ok}/{total} files"
            import time
            time.sleep(1.5)
            self._transfer_active = False
            self._net_peer_selected.clear()
            self._load_network_local()  # refresh local list
            self._net_status = f"Pulled {ok} files from {peer['name']}"

        import threading
        threading.Thread(target=_pull, daemon=True).start()

    def _flash(self, msg: str):
        self._action_msg = msg
        self._action_flash = 60
        print(msg, flush=True)
