"""Transfer Screen — push/pull files between Compa and MPC/Force.

When an Akai device is in Computer Mode, its storage mounts via USB.
This screen lets you browse the device's files, push Compa recordings
and kits to it, and pull samples from it.

Layout (1024x600):
  y=0-36:   Header with device storage info
  y=40-54:  Tab bar: PUSH | PULL | KITS
  y=58-500: Split view based on mode
  y=504-540: Action buttons + status
"""

import os
import threading
import time
import pygame
from .. import theme

import logging
log = logging.getLogger(__name__)


class TransferScreen:
    """File transfer between Compa and MPC/Force USB storage."""

    def __init__(self, app):
        self.app = app
        self._mode = "push"  # push, pull, kits
        self._status = ""
        self._status_time = 0.0

        # Push mode: list of local recordings to send
        self._local_files: list[dict] = []
        self._local_scroll = 0
        self._local_selected: set[int] = set()

        # Pull mode: list of device files to grab
        self._device_files: list[dict] = []
        self._device_scroll = 0
        self._device_selected: set[int] = set()

        # Kits mode: list of converted kits to push
        self._kit_dirs: list[str] = []
        self._kit_scroll = 0

        self._transferring = False
        self._max_visible = 14

        # Active drive index (which mounted drive to transfer to/from)
        self._active_drive = -1  # -1 = auto (first with Samples/)

    def on_enter(self):
        self._refresh_local_files()
        self._refresh_device_files()
        self._refresh_kits()

    def on_exit(self):
        pass

    def _set_status(self, text: str):
        self._status = text
        self._status_time = time.monotonic()

    def _refresh_local_files(self):
        """Load list of local recordings."""
        rec_dir = self.app.config.get("P6_RECORDING_DIR", "recordings")
        self._local_files = []
        if os.path.isdir(rec_dir):
            for f in sorted(os.listdir(rec_dir), reverse=True):
                if f.endswith(".wav"):
                    path = os.path.join(rec_dir, f)
                    size = os.path.getsize(path)
                    self._local_files.append({
                        "name": f,
                        "path": path,
                        "size": size,
                        "size_mb": round(size / (1024 * 1024), 1),
                    })

    def _refresh_device_files(self):
        """Load list of device samples from active drive."""
        storage = getattr(self.app, "akai_storage", None)
        if storage and storage.is_connected:
            self._device_files = storage.list_samples(self._active_drive)
        else:
            self._device_files = []

    def _get_active_samples_dir(self) -> str:
        """Get the Samples directory on the active drive."""
        storage = getattr(self.app, "akai_storage", None)
        if not storage or not storage.is_connected:
            return ""
        drives = storage.drives
        if 0 <= self._active_drive < len(drives):
            d = drives[self._active_drive]
            if d.samples_dir:
                return d.samples_dir
            # No Samples/ dir — create one
            sdir = os.path.join(d.mount_point, "Samples")
            os.makedirs(sdir, exist_ok=True)
            d.samples_dir = sdir
            return sdir
        return storage.samples_dir

    def _refresh_kits(self):
        """Find converted kit directories."""
        sessions = self.app.config.get("P6_SESSIONS_DIR", "sessions")
        converted = os.path.join(sessions, "converted")
        self._kit_dirs = []
        if os.path.isdir(converted):
            for name in sorted(os.listdir(converted)):
                kit_path = os.path.join(converted, name)
                if os.path.isdir(kit_path):
                    # Check if it has an XPM file
                    has_xpm = any(f.endswith(".xpm") for f in os.listdir(kit_path))
                    has_adg = any(f.endswith(".adg") for f in os.listdir(kit_path))
                    num_wav = sum(1 for f in os.listdir(kit_path) if f.endswith(".wav"))
                    self._kit_dirs.append({
                        "name": name,
                        "path": kit_path,
                        "has_xpm": has_xpm,
                        "has_adg": has_adg,
                        "num_wav": num_wav,
                    })

    # ── Event handling ──────────────────────────────────────────────

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Drive selector buttons
            if hasattr(self, "_drive_btn_rects"):
                for rect, drive_idx in self._drive_btn_rects:
                    if rect.collidepoint(mx, my):
                        self._active_drive = drive_idx
                        self._refresh_device_files()
                        return

            # Mode tabs
            tabs = [
                (pygame.Rect(16, 40, 100, 24), "push"),
                (pygame.Rect(120, 40, 100, 24), "pull"),
                (pygame.Rect(224, 40, 100, 24), "kits"),
            ]
            for rect, mode in tabs:
                if rect.collidepoint(mx, my):
                    self._mode = mode
                    return

            if self._mode == "push":
                self._handle_push_click(mx, my)
            elif self._mode == "pull":
                self._handle_pull_click(mx, my)
            else:
                self._handle_kits_click(mx, my)

        # Scroll
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 4:
                if self._mode == "push":
                    self._local_scroll = max(0, self._local_scroll - 1)
                elif self._mode == "pull":
                    self._device_scroll = max(0, self._device_scroll - 1)
                else:
                    self._kit_scroll = max(0, self._kit_scroll - 1)
            elif event.button == 5:
                if self._mode == "push":
                    self._local_scroll = min(max(0, len(self._local_files) - self._max_visible),
                                              self._local_scroll + 1)
                elif self._mode == "pull":
                    self._device_scroll = min(max(0, len(self._device_files) - self._max_visible),
                                               self._device_scroll + 1)
                else:
                    self._kit_scroll = min(max(0, len(self._kit_dirs) - self._max_visible),
                                            self._kit_scroll + 1)

    def _handle_push_click(self, mx, my):
        # File list — toggle selection
        list_y = 68
        visible = self._local_files[self._local_scroll:self._local_scroll + self._max_visible]
        for i, f in enumerate(visible):
            real_idx = self._local_scroll + i
            row = pygame.Rect(16, list_y + i * 28, theme.SCREEN_WIDTH - 200, 26)
            if row.collidepoint(mx, my):
                if real_idx in self._local_selected:
                    self._local_selected.discard(real_idx)
                else:
                    self._local_selected.add(real_idx)
                return

        # SELECT ALL button
        sel_all = pygame.Rect(16, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 46, 100, 30)
        if sel_all.collidepoint(mx, my):
            if len(self._local_selected) == len(self._local_files):
                self._local_selected.clear()
            else:
                self._local_selected = set(range(len(self._local_files)))
            return

        # PUSH button
        push_rect = pygame.Rect(theme.SCREEN_WIDTH - 180, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 46, 160, 30)
        if push_rect.collidepoint(mx, my) and self._local_selected:
            self._push_selected()
            return

    def _handle_pull_click(self, mx, my):
        list_y = 68
        visible = self._device_files[self._device_scroll:self._device_scroll + self._max_visible]
        for i, f in enumerate(visible):
            real_idx = self._device_scroll + i
            row = pygame.Rect(16, list_y + i * 28, theme.SCREEN_WIDTH - 200, 26)
            if row.collidepoint(mx, my):
                if real_idx in self._device_selected:
                    self._device_selected.discard(real_idx)
                else:
                    self._device_selected.add(real_idx)
                return

        # PULL button
        pull_rect = pygame.Rect(theme.SCREEN_WIDTH - 180, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 46, 160, 30)
        if pull_rect.collidepoint(mx, my) and self._device_selected:
            self._pull_selected()
            return

    def _handle_kits_click(self, mx, my):
        list_y = 68
        visible = self._kit_dirs[self._kit_scroll:self._kit_scroll + self._max_visible]
        for i, kit in enumerate(visible):
            # PUSH KIT button on each row
            btn_rect = pygame.Rect(theme.SCREEN_WIDTH - 160, list_y + i * 36 + 2, 120, 30)
            if btn_rect.collidepoint(mx, my):
                self._push_kit(kit)
                return

    # ── Transfer operations ─────────────────────────────────────────

    def _push_selected(self):
        if self._transferring:
            return
        storage = getattr(self.app, "akai_storage", None)
        if not storage or not storage.is_connected:
            self._set_status("No device storage mounted")
            return

        files = [self._local_files[i] for i in sorted(self._local_selected)]
        samples_dir = self._get_active_samples_dir()
        if not samples_dir:
            self._set_status("No Samples directory on selected drive")
            return
        self._transferring = True
        self._set_status(f"Pushing {len(files)} file(s)...")

        def worker():
            import shutil
            dest = os.path.join(samples_dir, "Compa")
            os.makedirs(dest, exist_ok=True)
            ok = 0
            for f in files:
                try:
                    shutil.copy2(f["path"], os.path.join(dest, f["name"]))
                    ok += 1
                except Exception as e:
                    log.error("Push failed %s: %s", f["name"], e)
            self._set_status(f"Pushed {ok}/{len(files)} files to device")
            self._local_selected.clear()
            self._transferring = False

        threading.Thread(target=worker, daemon=True).start()

    def _pull_selected(self):
        if self._transferring:
            return
        storage = getattr(self.app, "akai_storage", None)
        if not storage:
            return

        files = [self._device_files[i] for i in sorted(self._device_selected)]
        dest = os.path.join(self.app.config.get("LOCAL_SAMPLE_CACHE", "samples"), "from_device")
        self._transferring = True
        self._set_status(f"Pulling {len(files)} file(s)...")

        def worker():
            ok = 0
            for f in files:
                result = storage.pull_file(f["path"], dest)
                if result:
                    ok += 1
            self._set_status(f"Pulled {ok}/{len(files)} files from device")
            self._device_selected.clear()
            self._transferring = False

        threading.Thread(target=worker, daemon=True).start()

    def _push_kit(self, kit: dict):
        if self._transferring:
            return
        samples_dir = self._get_active_samples_dir()
        if not samples_dir:
            self._set_status("No Samples directory on selected drive")
            return

        self._transferring = True
        self._set_status(f"Pushing kit {kit['name']}...")

        def worker():
            import shutil
            dest = os.path.join(samples_dir, "Compa Kits", kit["name"])
            try:
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(kit["path"], dest)
                self._set_status(f"Kit '{kit['name']}' pushed to device!")
            except Exception as e:
                self._set_status(f"Failed: {e}")
                log.error("Kit push failed: %s", e)
            self._transferring = False

        threading.Thread(target=worker, daemon=True).start()

    def update(self):
        # Periodic device scan
        storage = getattr(self.app, "akai_storage", None)
        if storage:
            storage.scan_and_mount()

    # ── Drawing ─────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # Header
        storage = getattr(self.app, "akai_storage", None)
        connected = storage and storage.is_connected

        if connected:
            drives = storage.drives
            # Auto-select first drive with Samples/ if none selected
            if self._active_drive < 0 or self._active_drive >= len(drives):
                # Prefer SSD over SD card
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

            # Drive selector buttons (top right) — tap to switch target drive
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
            theme.draw_screen_header(surface, "TRANSFER",
                                      "No device in Computer Mode")
            self._drive_btn_rects = []

        # Mode tabs
        modes = [
            (pygame.Rect(16, 40, 100, 24), "PUSH →", "push"),
            (pygame.Rect(120, 40, 100, 24), "← PULL", "pull"),
            (pygame.Rect(224, 40, 100, 24), "KITS", "kits"),
        ]
        for rect, label, mode in modes:
            active = self._mode == mode
            bg = theme.ACCENT if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        if not connected:
            # Show instructions
            y = 120
            lines = [
                "Connect your MPC or Force via USB cable",
                "Put the device in Computer Mode:",
                "  Force: Menu → System → Computer Mode",
                "  MPC: Menu → Gear icon → Computer Mode",
                "Compa will auto-detect the storage",
            ]
            for line in lines:
                surf = f_med.render(line, True, theme.TEXT_DIM)
                surface.blit(surf, (40, y))
                y += 28
            return

        # Content based on mode
        if self._mode == "push":
            self._draw_push(surface, f_small, f_med, f_tiny)
        elif self._mode == "pull":
            self._draw_pull(surface, f_small, f_med, f_tiny)
        else:
            self._draw_kits(surface, f_small, f_med, f_tiny)

        # Status bar
        if self._status:
            age = time.monotonic() - self._status_time
            if age < 8.0:
                alpha = min(1.0, 1.0 - (age - 5.0) / 3.0) if age > 5.0 else 1.0
                color = theme.GREEN if "Pushed" in self._status or "Pulled" in self._status else theme.ACCENT
                surf = f_small.render(self._status, True, color)
                surface.blit(surf, (130, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 40))

    def _draw_push(self, surface, f_small, f_med, f_tiny):
        """Draw PUSH mode — local files to send to device."""
        list_y = 68
        visible = self._local_files[self._local_scroll:self._local_scroll + self._max_visible]

        if not self._local_files:
            surf = f_med.render("No recordings yet — record something first", True, theme.TEXT_DIM)
            surface.blit(surf, (40, list_y + 20))
            return

        # Column headers
        surf = f_tiny.render("SELECT FILES TO PUSH TO DEVICE", True, theme.TEXT_DIM)
        surface.blit(surf, (16, list_y - 12))
        surf = f_tiny.render(f"{len(self._local_selected)} selected", True, theme.ACCENT)
        surface.blit(surf, (300, list_y - 12))

        for i, f in enumerate(visible):
            real_idx = self._local_scroll + i
            selected = real_idx in self._local_selected
            y = list_y + i * 28
            row = pygame.Rect(16, y, theme.SCREEN_WIDTH - 200, 26)

            bg = theme.ACCENT_DIM if selected else (theme.BG_PANEL if i % 2 == 0 else theme.BG)
            pygame.draw.rect(surface, bg, row, border_radius=3)
            if selected:
                pygame.draw.rect(surface, theme.ACCENT, row, 1, border_radius=3)

            # Checkbox
            cb_rect = pygame.Rect(20, y + 4, 16, 16)
            if selected:
                pygame.draw.rect(surface, theme.GREEN, cb_rect, border_radius=2)
                surf = f_tiny.render("✓", True, theme.BG)
                surface.blit(surf, surf.get_rect(center=cb_rect.center))
            else:
                pygame.draw.rect(surface, theme.BORDER, cb_rect, 1, border_radius=2)

            # Filename
            surf = f_small.render(f["name"][:40], True, theme.TEXT)
            surface.blit(surf, (42, y + 4))

            # Size
            surf = f_tiny.render(f"{f['size_mb']}MB", True, theme.TEXT_DIM)
            surface.blit(surf, (row.right - 60, y + 6))

        # Bottom buttons
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 46
        sel_all = pygame.Rect(16, btn_y, 100, 30)
        pygame.draw.rect(surface, theme.BUTTON_BG, sel_all, border_radius=6)
        label = "NONE" if len(self._local_selected) == len(self._local_files) else "ALL"
        surf = f_small.render(label, True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=sel_all.center))

        push_rect = pygame.Rect(theme.SCREEN_WIDTH - 180, btn_y, 160, 30)
        can_push = len(self._local_selected) > 0 and not self._transferring
        push_bg = theme.GREEN if can_push else theme.BUTTON_BG
        pygame.draw.rect(surface, push_bg, push_rect, border_radius=6)
        label = f"PUSH {len(self._local_selected)}" if self._local_selected else "PUSH"
        surf = f_med.render(label, True, theme.BG if can_push else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=push_rect.center))

    def _draw_pull(self, surface, f_small, f_med, f_tiny):
        """Draw PULL mode — device files to grab."""
        list_y = 68
        visible = self._device_files[self._device_scroll:self._device_scroll + self._max_visible]

        if not self._device_files:
            surf = f_med.render("No samples found on device", True, theme.TEXT_DIM)
            surface.blit(surf, (40, list_y + 20))
            return

        surf = f_tiny.render(f"DEVICE SAMPLES ({len(self._device_files)} files)", True, theme.TEXT_DIM)
        surface.blit(surf, (16, list_y - 12))

        for i, f in enumerate(visible):
            real_idx = self._device_scroll + i
            selected = real_idx in self._device_selected
            y = list_y + i * 28
            row = pygame.Rect(16, y, theme.SCREEN_WIDTH - 200, 26)

            bg = theme.ACCENT_DIM if selected else (theme.BG_PANEL if i % 2 == 0 else theme.BG)
            pygame.draw.rect(surface, bg, row, border_radius=3)

            # Filename (relative path)
            surf = f_small.render(f["rel_path"][:50], True, theme.TEXT)
            surface.blit(surf, (20, y + 4))

            # Size + type
            size_kb = f["size"] / 1024
            label = f"{size_kb:.0f}KB" if size_kb < 1024 else f"{size_kb/1024:.1f}MB"
            surf = f_tiny.render(f"{label} {f['ext']}", True, theme.TEXT_DIM)
            surface.blit(surf, (row.right - 80, y + 6))

        # PULL button
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 46
        pull_rect = pygame.Rect(theme.SCREEN_WIDTH - 180, btn_y, 160, 30)
        can_pull = len(self._device_selected) > 0 and not self._transferring
        pull_bg = theme.ACCENT if can_pull else theme.BUTTON_BG
        pygame.draw.rect(surface, pull_bg, pull_rect, border_radius=6)
        label = f"PULL {len(self._device_selected)}" if self._device_selected else "PULL"
        surf = f_med.render(label, True, theme.BG if can_pull else theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=pull_rect.center))

    def _draw_kits(self, surface, f_small, f_med, f_tiny):
        """Draw KITS mode — push drum kits to device."""
        list_y = 68
        visible = self._kit_dirs[self._kit_scroll:self._kit_scroll + self._max_visible]

        if not self._kit_dirs:
            surf = f_med.render("No kits exported yet", True, theme.TEXT_DIM)
            surface.blit(surf, (40, list_y + 20))
            surf = f_small.render("Use Kit Builder → Export XPM to create kits", True, theme.TEXT_DIM)
            surface.blit(surf, (40, list_y + 48))
            return

        surf = f_tiny.render("EXPORTED KITS — push to device", True, theme.TEXT_DIM)
        surface.blit(surf, (16, list_y - 12))

        for i, kit in enumerate(visible):
            y = list_y + i * 36
            row = pygame.Rect(16, y, theme.SCREEN_WIDTH - 40, 34)
            bg = theme.BG_PANEL if i % 2 == 0 else theme.BG
            pygame.draw.rect(surface, bg, row, border_radius=3)

            # Kit name
            surf = f_med.render(kit["name"], True, theme.TEXT)
            surface.blit(surf, (24, y + 6))

            # Info badges
            bx = 300
            if kit["has_xpm"]:
                badge = pygame.Rect(bx, y + 8, 36, 16)
                pygame.draw.rect(surface, theme.GREEN, badge, border_radius=3)
                surf = f_tiny.render("XPM", True, theme.BG)
                surface.blit(surf, surf.get_rect(center=badge.center))
                bx += 42
            if kit["has_adg"]:
                badge = pygame.Rect(bx, y + 8, 36, 16)
                pygame.draw.rect(surface, theme.BLUE, badge, border_radius=3)
                surf = f_tiny.render("ADG", True, theme.BG)
                surface.blit(surf, surf.get_rect(center=badge.center))
                bx += 42
            surf = f_tiny.render(f"{kit['num_wav']} samples", True, theme.TEXT_DIM)
            surface.blit(surf, (bx, y + 10))

            # PUSH button
            btn_rect = pygame.Rect(theme.SCREEN_WIDTH - 160, y + 2, 120, 30)
            pygame.draw.rect(surface, theme.ACCENT, btn_rect, border_radius=6)
            surf = f_small.render("PUSH KIT", True, theme.BG)
            surface.blit(surf, surf.get_rect(center=btn_rect.center))
