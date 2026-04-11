"""Transfer Screen — push/pull files between Compa and MPC/Force.

When an Akai device is in Computer Mode, its storage mounts via USB.
This screen lets you browse the device's files, push Compa recordings
and kits to it, and pull samples from it.

Uses TouchList and FolderBrowser for consistent touch-friendly navigation.
"""

import os
import shutil
import threading
import time
import pygame
from .. import theme
from ..components.touch_list import TouchList, TouchListItem
from ..components.folder_browser import FolderBrowser

import logging
log = logging.getLogger(__name__)


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


class TransferScreen:
    """File transfer between Compa and MPC/Force USB storage."""

    TAB_Y = 40
    TAB_H = 28
    CONTENT_Y = 72
    ACTION_H = 40

    def __init__(self, app):
        self.app = app
        self._mode = "push"  # push, pull, kits
        self._status = ""
        self._status_time = 0.0
        self._transferring = False

        # Active drive index
        self._active_drive = -1

        # Content area
        content_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self.CONTENT_Y - self.ACTION_H - 8
        content_rect = pygame.Rect(16, self.CONTENT_Y, theme.SCREEN_WIDTH - 32, content_h)

        # PUSH: multi-select list of local recordings
        self._push_list = TouchList(content_rect, item_height=48, multi_select=True)

        # PULL: folder browser for device storage
        self._pull_browser = FolderBrowser(
            content_rect, root_dir="",
            file_filter=lambda f: any(f.lower().endswith(e)
                                      for e in (".wav", ".aif", ".aiff", ".mp3",
                                                ".xpm", ".xpj", ".adg")),
            multi_select=True,
        )

        # KITS: list of exported kits
        self._kit_list = TouchList(content_rect, item_height=56)

    def on_enter(self):
        self._refresh_push_list()
        self._refresh_pull_browser()
        self._refresh_kit_list()

        # Check for cross-device workflow context
        ctx = getattr(self.app, "_screen_context", {})
        if ctx.get("mode") == "kits":
            self._mode = "kits"
            # Pre-highlight the specified kit
            kit_name = ctx.get("kit_name", "")
            if kit_name:
                for item in self._kit_list.items:
                    if item.data and item.data.get("name") == kit_name:
                        item.selected = True
                        self._set_status(f"Kit '{kit_name}' ready to push")
                        break
            self.app._screen_context = {}  # Consume

    def on_exit(self):
        pass

    def _set_status(self, text: str):
        self._status = text
        self._status_time = time.monotonic()

    # ── Data loading ─────────────────────────────────────────────────

    def _refresh_push_list(self):
        """Load local recordings into the push list."""
        rec_dir = self.app.config.get("P6_RECORDING_DIR", "recordings")
        items = []
        if os.path.isdir(rec_dir):
            for f in sorted(os.listdir(rec_dir), reverse=True):
                if f.endswith(".wav"):
                    path = os.path.join(rec_dir, f)
                    size = os.path.getsize(path)
                    items.append(TouchListItem(
                        text=f,
                        subtext=_human_size(size),
                        icon="~",
                        icon_color=theme.WAVEFORM_COLOR,
                        data={"path": path, "name": f, "size": size},
                    ))
        self._push_list.set_items(items)

    def _refresh_pull_browser(self):
        """Point the pull browser at the active drive."""
        storage = getattr(self.app, "akai_storage", None)
        if not storage or not storage.is_connected:
            return
        drives = storage.drives
        idx = self._active_drive if 0 <= self._active_drive < len(drives) else 0
        if idx >= len(drives):
            return
        d = drives[idx]
        # Use samples dir if available, otherwise mount root
        root = d.samples_dir if d.samples_dir else d.mount_point
        if root and os.path.isdir(root):
            self._pull_browser._root_dir = root
            self._pull_browser.navigate_to(root)

    def _refresh_kit_list(self):
        """Find exported kit directories."""
        sessions = self.app.config.get("P6_SESSIONS_DIR", "sessions")
        converted = os.path.join(sessions, "converted")
        items = []
        if os.path.isdir(converted):
            for name in sorted(os.listdir(converted)):
                kit_path = os.path.join(converted, name)
                if not os.path.isdir(kit_path):
                    continue
                has_xpm = any(f.endswith(".xpm") for f in os.listdir(kit_path))
                has_adg = any(f.endswith(".adg") for f in os.listdir(kit_path))
                num_wav = sum(1 for f in os.listdir(kit_path) if f.endswith(".wav"))

                badges = []
                if has_xpm:
                    badges.append("XPM")
                if has_adg:
                    badges.append("ADG")
                subtext = f"{' + '.join(badges)} | {num_wav} samples" if badges else f"{num_wav} samples"

                items.append(TouchListItem(
                    text=name,
                    subtext=subtext,
                    icon="K",
                    icon_color=theme.GREEN if has_xpm else theme.ACCENT,
                    data={"path": kit_path, "name": name,
                          "has_xpm": has_xpm, "has_adg": has_adg},
                ))
        self._kit_list.set_items(items)

    def _get_active_samples_dir(self) -> str:
        storage = getattr(self.app, "akai_storage", None)
        if not storage or not storage.is_connected:
            return ""
        drives = storage.drives
        if 0 <= self._active_drive < len(drives):
            d = drives[self._active_drive]
            if d.samples_dir:
                return d.samples_dir
            # Try to create Samples/ dir — may fail on read-only drives
            sdir = os.path.join(d.mount_point, "Samples")
            try:
                os.makedirs(sdir, exist_ok=True)
                d.samples_dir = sdir
                return sdir
            except PermissionError:
                # Fall back to mount point root
                return d.mount_point
        return storage.samples_dir

    # ── Event handling ───────────────────────────────────────────────

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Drive selector buttons
            if hasattr(self, "_drive_btn_rects"):
                for rect, drive_idx in self._drive_btn_rects:
                    if rect.collidepoint(mx, my):
                        self._active_drive = drive_idx
                        self._refresh_pull_browser()
                        return

            # Mode tabs
            tabs = [
                (pygame.Rect(16, self.TAB_Y, 100, self.TAB_H), "push"),
                (pygame.Rect(120, self.TAB_Y, 100, self.TAB_H), "pull"),
                (pygame.Rect(224, self.TAB_Y, 100, self.TAB_H), "kits"),
            ]
            for rect, mode in tabs:
                if rect.collidepoint(mx, my):
                    self._mode = mode
                    return

            # Action buttons (bottom)
            btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self.ACTION_H - 4
            if self._mode == "push":
                # SELECT ALL
                sel_rect = pygame.Rect(16, btn_y, 100, 36)
                if sel_rect.collidepoint(mx, my):
                    if self._push_list.selected_count() == len(self._push_list.items):
                        self._push_list.clear_selection()
                    else:
                        self._push_list.select_all()
                    return
                # PUSH
                push_rect = pygame.Rect(theme.SCREEN_WIDTH - 196, btn_y, 160, 36)
                if push_rect.collidepoint(mx, my):
                    self._do_push()
                    return

            elif self._mode == "pull":
                # SELECT ALL
                sel_rect = pygame.Rect(16, btn_y, 100, 36)
                if sel_rect.collidepoint(mx, my):
                    if self._pull_browser.selected_count() == len(self._pull_browser._list.items):
                        self._pull_browser.clear_selection()
                    else:
                        self._pull_browser.select_all()
                    return
                # PULL
                pull_rect = pygame.Rect(theme.SCREEN_WIDTH - 196, btn_y, 160, 36)
                if pull_rect.collidepoint(mx, my):
                    self._do_pull()
                    return

            elif self._mode == "kits":
                # PUSH KIT — check each kit row for button hit
                pass  # Handled by kit list tap below

        # Delegate to active list/browser
        if self._mode == "push":
            self._push_list.handle_event(event)
        elif self._mode == "pull":
            self._pull_browser.handle_event(event)
        else:
            tapped = self._kit_list.handle_event(event)
            if tapped and tapped.data:
                self._do_push_kit(tapped.data)

    # ── Transfer operations ──────────────────────────────────────────

    def _do_push(self):
        if self._transferring:
            return
        selected = self._push_list.selected_items()
        if not selected:
            self._set_status("Select files to push")
            return
        samples_dir = self._get_active_samples_dir()
        if not samples_dir:
            self._set_status("No device storage mounted")
            return

        self._transferring = True
        self._set_status(f"Pushing {len(selected)} file(s)...")
        files = [(s.data["path"], s.data["name"]) for s in selected]

        def worker():
            import subprocess
            dest = os.path.join(samples_dir, "Compa")
            try:
                os.makedirs(dest, exist_ok=True)
            except Exception as e:
                self._set_status(f"Can't create folder: {e}")
                self._transferring = False
                return
            ok = 0
            for path, name in files:
                try:
                    dest_path = os.path.join(dest, name)
                    shutil.copy2(path, dest_path)
                    # Verify the file actually landed
                    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                        ok += 1
                    else:
                        log.error("Push verify failed %s: file missing after copy", name)
                except (PermissionError, OSError) as e:
                    log.error("Push failed %s: %s", name, e)
                    self._set_status(f"Error: {e}")
                    self._transferring = False
                    return
            # Force flush to disk
            try:
                subprocess.run(["sync"], timeout=30)
            except Exception:
                pass
            self._set_status(f"Pushed {ok}/{len(files)} files")
            self._push_list.clear_selection()
            self._transferring = False

        threading.Thread(target=worker, daemon=True).start()

    def _do_pull(self):
        if self._transferring:
            return
        selected = self._pull_browser.selected_items()
        if not selected:
            self._set_status("Select files to pull")
            return

        dest = os.path.join(self.app.config.get("LOCAL_SAMPLE_CACHE", "samples"), "from_device")
        self._transferring = True
        self._set_status(f"Pulling {len(selected)} file(s)...")
        files = [(s.data["path"], s.data["name"]) for s in selected if s.data.get("type") == "file"]

        def worker():
            os.makedirs(dest, exist_ok=True)
            ok = 0
            for path, name in files:
                try:
                    shutil.copy2(path, os.path.join(dest, name))
                    ok += 1
                except Exception as e:
                    log.error("Pull failed %s: %s", name, e)
            self._set_status(f"Pulled {ok}/{len(files)} files")
            self._pull_browser.clear_selection()
            self._transferring = False

        threading.Thread(target=worker, daemon=True).start()

    def _do_push_kit(self, kit_data: dict):
        if self._transferring:
            return
        samples_dir = self._get_active_samples_dir()
        if not samples_dir:
            self._set_status("No device storage mounted")
            return

        self._transferring = True
        name = kit_data["name"]
        self._set_status(f"Pushing kit {name}...")

        def worker():
            import subprocess
            dest = os.path.join(samples_dir, "Compa Kits", name)
            try:
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(kit_data["path"], dest)
                # Verify
                if not os.path.isdir(dest):
                    self._set_status(f"Failed: directory not created")
                    self._transferring = False
                    return
                # Flush to disk
                subprocess.run(["sync"], timeout=30)
                self._set_status(f"Kit '{name}' pushed!")
            except (PermissionError, OSError) as e:
                self._set_status(f"Failed: {e}")
                log.error("Kit push failed: %s", e)
            self._transferring = False

        threading.Thread(target=worker, daemon=True).start()

    def update(self):
        storage = getattr(self.app, "akai_storage", None)
        if storage:
            storage.scan_and_mount()

        # Update active list physics
        if self._mode == "push":
            self._push_list.update()
        elif self._mode == "pull":
            self._pull_browser.update()
        else:
            self._kit_list.update()

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        storage = getattr(self.app, "akai_storage", None)
        connected = storage and storage.is_connected

        # Header + drive selector
        if connected:
            drives = storage.drives
            if self._active_drive < 0 or self._active_drive >= len(drives):
                for i, d in enumerate(drives):
                    if d.samples_dir and "ssd" in d.drive_type.lower():
                        self._active_drive = i
                        break
                else:
                    for i, d in enumerate(drives):
                        if d.samples_dir:
                            self._active_drive = i
                            break
                    else:
                        self._active_drive = 0

            active_d = drives[self._active_drive] if self._active_drive < len(drives) else None
            header_info = f"{active_d.label}: {active_d.free_gb}GB free" if active_d else ""
            theme.draw_screen_header(surface, "TRANSFER", header_info)

            # Drive selector buttons
            self._drive_btn_rects = []
            dx = theme.SCREEN_WIDTH - 16
            for i in range(len(drives) - 1, -1, -1):
                d = drives[i]
                is_active = (i == self._active_drive)
                label = f"{d.label} ({d.free_gb}G)"
                w = max(100, len(label) * 7 + 20)
                rect = pygame.Rect(dx - w, 6, w, 24)
                bg = theme.ACCENT if is_active else theme.BUTTON_BG
                tc = theme.BG if is_active else theme.TEXT_DIM
                pygame.draw.rect(surface, bg, rect, border_radius=4)
                surf = f_tiny.render(label, True, tc)
                surface.blit(surf, surf.get_rect(center=rect.center))
                self._drive_btn_rects.append((rect, i))
                dx = rect.x - 6
        else:
            theme.draw_screen_header(surface, "TRANSFER", "No device in Computer Mode")
            self._drive_btn_rects = []

        # Mode tabs
        modes = [
            (pygame.Rect(16, self.TAB_Y, 100, self.TAB_H), "PUSH ->", "push"),
            (pygame.Rect(120, self.TAB_Y, 100, self.TAB_H), "<- PULL", "pull"),
            (pygame.Rect(224, self.TAB_Y, 100, self.TAB_H), "KITS", "kits"),
        ]
        for rect, label, mode in modes:
            active = self._mode == mode
            bg = theme.ACCENT if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        if not connected:
            y = 120
            for line in [
                "Connect your MPC or Force via USB cable",
                "Put the device in Computer Mode:",
                "  Force: Menu > System > Computer Mode",
                "  MPC: Menu > Gear icon > Computer Mode",
                "Compa will auto-detect the storage",
            ]:
                surf = f_med.render(line, True, theme.TEXT_DIM)
                surface.blit(surf, (40, y))
                y += 28
            return

        # Content
        if self._mode == "push":
            self._push_list.draw(surface)
            self._draw_push_actions(surface, f_small, f_med)
        elif self._mode == "pull":
            self._pull_browser.draw(surface)
            self._draw_pull_actions(surface, f_small, f_med)
        else:
            self._kit_list.draw(surface)

        # Status
        if self._status:
            age = time.monotonic() - self._status_time
            if age < 8.0:
                color = theme.GREEN if "Pushed" in self._status or "Pulled" in self._status else theme.ACCENT
                surf = f_small.render(self._status, True, color)
                btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self.ACTION_H - 4
                surface.blit(surf, (130, btn_y + 10))

    def _draw_push_actions(self, surface, f_small, f_med):
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self.ACTION_H - 4
        count = self._push_list.selected_count()
        total = len(self._push_list.items)

        # SELECT ALL / NONE
        sel_rect = pygame.Rect(16, btn_y, 100, 36)
        pygame.draw.rect(surface, theme.BUTTON_BG, sel_rect, border_radius=6)
        label = "NONE" if count == total and total > 0 else "ALL"
        surf = f_small.render(label, True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=sel_rect.center))

        # PUSH button
        push_rect = pygame.Rect(theme.SCREEN_WIDTH - 196, btn_y, 160, 36)
        can = count > 0 and not self._transferring
        bg = theme.GREEN if can else theme.BUTTON_BG
        pygame.draw.rect(surface, bg, push_rect, border_radius=6)
        label = f"PUSH {count}" if count else "PUSH"
        surf = f_med.render(label, True, theme.BG if can else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=push_rect.center))

    def _draw_pull_actions(self, surface, f_small, f_med):
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self.ACTION_H - 4
        count = self._pull_browser.selected_count()

        # SELECT ALL / NONE
        sel_rect = pygame.Rect(16, btn_y, 100, 36)
        pygame.draw.rect(surface, theme.BUTTON_BG, sel_rect, border_radius=6)
        label = "NONE" if count > 0 else "ALL"
        surf = f_small.render(label, True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=sel_rect.center))

        # PULL button
        pull_rect = pygame.Rect(theme.SCREEN_WIDTH - 196, btn_y, 160, 36)
        can = count > 0 and not self._transferring
        bg = theme.ACCENT if can else theme.BUTTON_BG
        pygame.draw.rect(surface, bg, pull_rect, border_radius=6)
        label = f"PULL {count}" if count else "PULL"
        surf = f_med.render(label, True, theme.BG if can else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=pull_rect.center))
