"""Transfer Screen — device-side sample management.

Hosts three device-type tabs accessible from Files → Device:

  AKAI    push/pull files between Compa and MPC/Force via USB Computer Mode
  P-6     librarian for the Roland AIRA Compact P-6: bank/pad view, load
          samples by tapping a pad then tapping local audio, clear,
          backup/restore
  SP-404  librarian for the Roland SP-404 MK2: bank/pad view, load samples
          (WAV→SMP conversion), move pads, clear, backup/restore

The AKAI path uses TouchList + FolderBrowser. The librarian paths use
LibrarianGrid (bank selector + pad grid) + a TouchList source picker.

Backup/restore is delegated to engine.p6_image.P6ImageManager through
P6Librarian / SP404Librarian — it runs in a background thread with
progress tracking that we poll here in update() / draw().
"""

import os
import shutil
import threading
import time
import pygame
from .. import theme
from ..components.touch_list import TouchList, TouchListItem
from ..components.folder_browser import FolderBrowser
from ..components.librarian_grid import LibrarianGrid
from ..components.modal import Modal

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

    # Layout constants — offsets within the transfer content surface
    DEVICE_TAB_Y = 0       # Device-type tab bar (AKAI / P-6 / SP-404)
    DEVICE_TAB_H = 32
    DRIVE_ROW_Y = 38       # AKAI: drive selector pills
    DRIVE_ROW_H = 24
    TAB_Y = 68             # AKAI mode sub-tabs (PUSH / PULL / KITS)
    TAB_H = 26
    CONTENT_Y = 100        # First content row after tabs
    ACTION_H = 40          # Bottom action strip height

    def __init__(self, app):
        self.app = app
        self._device_type = "akai"   # "akai" | "p6" | "sp404"
        self._mode = "push"          # akai sub-mode: push | pull | kits
        self._status = ""
        self._status_time = 0.0
        self._transferring = False

        # When embedded inside another screen (e.g. Files → Device), we
        # skip drawing our own top-level header since the host already
        # owns that area.
        self._embedded = False

        # Active drive index (AKAI)
        self._active_drive = -1

        # Click-target rects (populated each draw, consumed each click)
        self._device_tab_rects: list[tuple[pygame.Rect, str]] = []
        self._p6_action_rects: list[tuple[pygame.Rect, str]] = []
        self._sp404_action_rects: list[tuple[pygame.Rect, str]] = []

        content_rect = self._compute_content_rect()

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

        # ── P-6 librarian state ────────────────────────────────────
        # The grid+list rects get resized on every draw via _relayout_librarian
        placeholder = pygame.Rect(0, 0, 100, 100)
        self._p6_grid = LibrarianGrid(
            placeholder,
            banks=8, pads_per_bank=6, grid_cols=3, grid_rows=2,
        )
        self._p6_source_list = TouchList(placeholder, item_height=36)
        self._p6_cached_assignments: list = [None] * 48

        # ── SP-404 librarian state ─────────────────────────────────
        self._sp404_grid = LibrarianGrid(
            placeholder,
            banks=10, pads_per_bank=16, grid_cols=4, grid_rows=4,
        )
        self._sp404_source_list = TouchList(placeholder, item_height=36)
        self._sp404_projects: list[dict] = []
        self._sp404_project_idx = 0
        self._sp404_move_mode = False  # first tap = source, second = dest
        self._sp404_protocol_scanning = False
        self._sp404_protocol_scan_key: tuple[str, int] | None = None

        # Shared confirm modal for destructive actions
        self._confirm_modal = Modal(
            "Confirm", "", buttons=["OK", "CANCEL"],
            width=400, height=170,
        )
        self._confirm_action = None  # callable to run on OK

        # Debug panel state (tapped via the DEBUG button on P-6/SP-404)
        self._debug_panel_visible = False
        self._debug_close_rect = pygame.Rect(0, 0, 0, 0)
        self._debug_subtab = "drives"  # "drives" | "raw"
        self._debug_subtab_rects: list[tuple[pygame.Rect, str]] = []

    # ── Librarian accessors (lazy, reach into app) ──────────────────

    @property
    def p6_lib(self):
        return getattr(self.app, "p6_lib", None)

    @property
    def sp404_lib(self):
        return getattr(self.app, "sp404_lib", None)

    def _sp404_normal_mode(self) -> bool:
        lib = self.sp404_lib
        return bool(
            lib is not None
            and not lib.is_mounted()
            and hasattr(lib, "normal_mode_available")
            and lib.normal_mode_available()
        )

    def _sp404_current_project_entry(self) -> dict | None:
        if not self._sp404_projects:
            return None
        idx = max(0, min(self._sp404_project_idx, len(self._sp404_projects) - 1))
        return self._sp404_projects[idx]

    def _sp404_current_project_is_protocol(self) -> bool:
        lib = self.sp404_lib
        proj = self._sp404_current_project_entry()
        return bool(
            lib is not None and proj
            and hasattr(lib, "is_protocol_project")
            and lib.is_protocol_project(proj.get("path", ""))
        )

    def _compute_content_rect(self) -> pygame.Rect:
        """Content rect computed from current theme dimensions (may be shimmed)."""
        content_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - self.CONTENT_Y - self.ACTION_H - 8
        return pygame.Rect(16, self.CONTENT_Y, theme.SCREEN_WIDTH - 32, content_h)

    def relayout(self):
        """Recompute internal rects from current theme dimensions.

        The Files screen embeds this screen inside its content pane and
        temporarily shims theme.SCREEN_WIDTH / SCREEN_HEIGHT to that pane's
        size. Call this after the shim is active so the lists use the new
        bounds instead of the values baked in at __init__.
        """
        rect = self._compute_content_rect()
        self._push_list.set_rect(rect)
        self._pull_browser.set_rect(rect)
        self._kit_list.set_rect(rect)
        self._relayout_librarian()

    def _relayout_librarian(self):
        """Size the P-6/SP-404 grid + source list for the current viewport."""
        W = theme.SCREEN_WIDTH
        H = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT  # usable content height

        # Grid takes the left 60% of the width
        lib_top = self.DEVICE_TAB_H + 6
        lib_bottom = H - self.ACTION_H - 8
        lib_h = lib_bottom - lib_top

        grid_w = int(W * 0.60) - 20
        grid_rect = pygame.Rect(10, lib_top, grid_w, lib_h)
        src_x = grid_w + 20
        src_w = W - grid_w - 30
        detail_h = min(138, max(110, int(lib_h * 0.26)))
        list_gap = 18
        src_rect = pygame.Rect(src_x, lib_top + detail_h + list_gap,
                               src_w, lib_h - detail_h - list_gap)

        self._p6_grid.set_rect(grid_rect)
        self._sp404_grid.set_rect(grid_rect)
        self._p6_source_list.set_rect(src_rect)
        self._sp404_source_list.set_rect(src_rect)

    def _selected_pad_detail(self, device_type: str, mounted: bool = True) -> dict:
        """Return normalized selected-pad details for the active librarian."""
        if not mounted:
            device_label = "P-6" if device_type == "p6" else "SP-404"
            return {
                "selected": False,
                "title": f"{device_label} storage offline",
                "state": "OFFLINE",
                "lines": [
                    "USB storage must mount before pad contents are known.",
                    "Use DEBUG to inspect detected block devices.",
                    "Pad contents are unavailable until mounted.",
                ],
            }

        if device_type == "p6":
            selected = self._p6_grid.selected_pad
            pads = self._p6_cached_assignments
            labels = "ABCDEFGH"
            per_bank = 6
            fallback_state = "EMPTY"
        else:
            selected = self._sp404_grid.selected_pad
            pads = self._sp404_grid.pads
            labels = [chr(ord("A") + i) for i in range(10)]
            per_bank = 16
            fallback_state = "EMPTY"

        if selected < 0 or selected >= len(pads):
            return {
                "selected": False,
                "title": "No pad selected",
                "state": fallback_state,
                "lines": ["Tap a pad to inspect it."],
            }

        bank_idx = selected // per_bank
        pad_idx = selected % per_bank
        bank_label = labels[bank_idx] if bank_idx < len(labels) else str(bank_idx + 1)
        pad_label = f"{bank_label}-{pad_idx + 1}" if device_type == "p6" else f"{bank_label}{pad_idx + 1:02d}"
        pad = pads[selected]
        if not pad:
            if device_type == "sp404" and self._sp404_current_project_is_protocol():
                return {
                    "selected": True,
                    "title": pad_label,
                    "state": fallback_state,
                    "lines": [
                        "Pad is empty or not scanned yet.",
                        "Tap SCAN BANK to read the current SP bank.",
                        "Writes/imports are disabled in normal mode.",
                    ],
                }
            return {
                "selected": True,
                "title": pad_label,
                "state": fallback_state,
                "lines": ["Pad is empty.", "Tap local audio to load it here.", "Action: convert + load"],
            }

        filename = pad.get("filename") or "Unknown sample"
        state = "PENDING" if pad.get("in_import") else "LIVE" if pad.get("on_device") else "LOADED"
        lines = [filename]
        src_path = pad.get("path") or ""
        if src_path:
            lines.append(src_path)
        size = pad.get("size") or 0
        duration = pad.get("duration") or 0.0
        meta_bits = []
        if duration:
            meta_bits.append(f"{duration:.1f}s")
        if size:
            meta_bits.append(_human_size(size))
        if meta_bits:
            lines.append(" · ".join(meta_bits))
        if state == "PENDING":
            lines.append("Action: replace or clear pending import")
        elif state == "LIVE":
            if pad.get("read_only"):
                lines.append("Action: read-only; import/delete not decoded yet")
            else:
                lines.append("Action: queue replacement or clear import slot")
        else:
            lines.append("Action: replace, move, or clear")
        return {
            "selected": True,
            "title": pad_label,
            "state": state,
            "lines": lines,
        }

    def _p6_action_specs(self, mounted: bool = True) -> list[dict]:
        selected = self._p6_grid.selected_pad
        pad = (self._p6_cached_assignments[selected]
               if 0 <= selected < len(self._p6_cached_assignments) else None)
        return [
            {"id": "clear_pad", "label": "CLEAR PAD", "color": theme.BUTTON_BG,
             "enabled": mounted and selected >= 0 and pad is not None},
            {"id": "clear_bank", "label": "CLR BANK", "color": theme.BUTTON_BG,
             "enabled": mounted},
            {"id": "clear_all", "label": "CLR ALL", "color": theme.RED,
             "enabled": mounted},
            {"id": "backup", "label": "BACKUP", "color": theme.BLUE,
             "enabled": mounted},
            {"id": "restore", "label": "RESTORE", "color": theme.ACCENT_DIM,
             "enabled": mounted},
            {"id": "debug", "label": "DEBUG", "color": theme.BUTTON_BG,
             "enabled": True},
        ]

    def _sp404_action_specs(self, mounted: bool = True,
                            normal_mode: bool = False) -> list[dict]:
        selected = self._sp404_grid.selected_pad
        pads = self._sp404_grid.pads
        pad = pads[selected] if 0 <= selected < len(pads) else None
        if normal_mode and not mounted:
            return [
                {"id": "scan_bank", "label": "SCAN BANK", "color": theme.BLUE,
                 "enabled": not self._sp404_protocol_scanning},
                {"id": "debug", "label": "DEBUG", "color": theme.BUTTON_BG,
                 "enabled": True},
            ]
        return [
            {"id": "clear_pad", "label": "CLEAR PAD", "color": theme.BUTTON_BG,
             "enabled": mounted and selected >= 0 and pad is not None},
            {"id": "move", "label": "MOVE" if not self._sp404_move_mode else "CANCEL",
             "color": theme.BLUE if not self._sp404_move_mode else theme.RED,
             "enabled": mounted and (self._sp404_move_mode or pad is not None)},
            {"id": "clear_bank", "label": "CLR BANK", "color": theme.BUTTON_BG,
             "enabled": mounted},
            {"id": "backup", "label": "BACKUP", "color": theme.BLUE,
             "enabled": mounted},
            {"id": "restore", "label": "RESTORE", "color": theme.ACCENT_DIM,
             "enabled": mounted},
            {"id": "debug", "label": "DEBUG", "color": theme.BUTTON_BG,
             "enabled": True},
        ]

    def _draw_selected_pad_card(self, surface: pygame.Surface, detail: dict,
                                rect: pygame.Rect, f_small, f_tiny):
        """Draw the selected-pad detail card above the source browser."""
        pygame.draw.rect(surface, theme.BG_PANEL, rect, border_radius=8)
        pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=8)

        title = detail.get("title", "")
        state = detail.get("state", "")
        lines = detail.get("lines", [])

        title_surf = f_small.render(title[:28], True, theme.TEXT_BRIGHT)
        surface.blit(title_surf, (rect.x + 10, rect.y + 10))

        badge_bg = theme.BG
        badge_fg = theme.TEXT_DIM
        if state == "PENDING":
            badge_bg = theme.ACCENT
            badge_fg = theme.BG
        elif state == "LIVE":
            badge_bg = theme.BLUE
            badge_fg = theme.TEXT_BRIGHT
        elif state == "LOADED":
            badge_bg = theme.BG
            badge_fg = theme.ACCENT
        elif state == "EMPTY":
            badge_bg = theme.BG
            badge_fg = theme.TEXT_DIM
        elif state == "OFFLINE":
            badge_bg = theme.RED
            badge_fg = theme.TEXT_BRIGHT

        badge_surf = f_tiny.render(state, True, badge_fg)
        badge_rect = pygame.Rect(rect.right - badge_surf.get_width() - 18, rect.y + 9,
                                 badge_surf.get_width() + 8, badge_surf.get_height() + 4)
        pygame.draw.rect(surface, badge_bg, badge_rect, border_radius=4)
        pygame.draw.rect(surface, theme.BORDER_LIGHT, badge_rect, 1, border_radius=4)
        surface.blit(badge_surf, badge_surf.get_rect(center=badge_rect.center))

        y = rect.y + 34
        for line in lines[:4]:
            surf = f_tiny.render(str(line)[:52], True, theme.TEXT_DIM if line != lines[0] else theme.TEXT)
            surface.blit(surf, (rect.x + 10, y))
            y += 16

    def on_enter(self):
        self._refresh_push_list()
        self._refresh_pull_browser()
        self._refresh_kit_list()
        self._refresh_librarian_sources()

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

    # ── Librarian helpers ──────────────────────────────────────────────

    def _refresh_librarian_sources(self):
        """Populate both librarian source lists with local importable audio."""
        items = self._scan_local_audio()
        self._p6_source_list.set_items(items)
        # Fresh copies so selection state is independent
        self._sp404_source_list.set_items(self._scan_local_audio())

    def _scan_local_audio(self) -> list:
        """Return TouchListItem entries for importable local audio.

        Pulls from Compa's recording/sample/session folders plus common
        user drop locations. P-6 imports are converted on write; SP-404
        imports are converted to SMP, so source files can be WAV, AIFF,
        FLAC, MP3, or M4A.
        """
        roots: list[tuple[str, str]] = []
        seen_roots: set[str] = set()

        def add_root(tag: str, path: str):
            if not path:
                return
            root = os.path.abspath(os.path.expanduser(path))
            if root in seen_roots or not os.path.isdir(root):
                return
            seen_roots.add(root)
            roots.append((tag, root))

        rec_dir = self.app.config.get("P6_RECORDING_DIR", "")
        sample_dir = self.app.config.get("LOCAL_SAMPLE_CACHE", "")
        sessions_dir = self.app.config.get("P6_SESSIONS_DIR", "sessions")
        add_root("REC", rec_dir)
        add_root("SMP", sample_dir)
        add_root("SLC", os.path.join(sessions_dir, "slices"))
        add_root("KIT", os.path.join(sessions_dir, "converted"))
        add_root("BAK", os.path.join(sessions_dir, "device_images"))
        add_root("MNT", "/mnt/samples")
        add_root("MUS", "~/Music")
        add_root("DLD", "~/Downloads")

        items: list = []
        audio_ext = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a"}
        skip_dirs = {".git", "venv", "__pycache__", ".mypy_cache", ".pytest_cache"}
        for tag, root in roots:
            try:
                found: list[tuple[str, str, int]] = []
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
                    for fn in filenames:
                        ext = os.path.splitext(fn)[1].lower()
                        if ext not in audio_ext:
                            continue
                        path = os.path.join(dirpath, fn)
                        try:
                            size = os.path.getsize(path)
                        except Exception:
                            size = 0
                        rel_dir = os.path.relpath(dirpath, root)
                        found.append((path, rel_dir, size))
                for path, rel_dir, size in sorted(found,
                                                  key=lambda x: (x[1], os.path.basename(x[0]).lower())):
                    fn = os.path.basename(path)
                    ext = os.path.splitext(fn)[1].lower().lstrip(".").upper()
                    rel_label = "" if rel_dir in (".", "") else f" · {rel_dir}"
                    items.append(TouchListItem(
                        text=fn,
                        subtext=f"{tag}{rel_label} · {ext} · {size // 1024}KB",
                        icon="~",
                        icon_color=theme.WAVEFORM_COLOR,
                        data={"path": path, "name": fn, "size": size, "root": root,
                              "rel_dir": rel_dir},
                    ))
            except Exception as e:
                log.warning("scan %s failed: %s", root, e)
        return items[:512]

    def _p6_storage_summary(self) -> tuple[str, str]:
        """Return a title/subtitle pair for the P-6 storage row."""
        lib = self.p6_lib
        if lib is None:
            return ("P-6 librarian unavailable", "No P-6 library backend is attached.")
        if not lib.is_mounted():
            diag = lib.diagnostic()
            if "storage found" in diag:
                return ("P-6 storage detected, not mounted", diag)
            return (
                "P-6 not mounted",
                "Hold SAMPLING while powering on the P-6. Use DEBUG to confirm what Linux sees.",
            )

        pads = lib.read_assignments()
        total = sum(1 for p in pads if p is not None)
        on_device = sum(1 for p in pads if p and p.get("on_device"))
        pending = sum(1 for p in pads if p and p.get("in_import"))
        title = f"P-6 ready · {total}/48 visible · {lib.mount_path}"
        subtitle = f"{on_device} on device · {pending} pending · imports convert to 44.1k mono, max 6s"
        return (title, subtitle)

    def _sp404_storage_summary(self) -> tuple[str, str]:
        """Return a title/subtitle pair for the SP-404 storage row."""
        lib = self.sp404_lib
        if lib is None:
            return ("SP-404 librarian unavailable", "No SP-404 library backend is attached.")
        if not lib.is_mounted():
            diag = lib.diagnostic()
            if "storage found" in diag:
                return ("SP-404 storage detected, not mounted", diag)
            if "librarian port found" in diag:
                proj = self._sp404_current_project_entry()
                if proj and proj.get("mode") == "protocol":
                    scanned = proj.get("num_samples", 0)
                    scan = "scanning..." if self._sp404_protocol_scanning else "tap SCAN BANK"
                    return (
                        f"SP-404 normal mode · {proj['name']} · read-only",
                        f"{scanned} scanned pad(s) cached · {scan}; writes still disabled.",
                    )
                return (
                    "SP-404 connected · normal librarian mode",
                    "Read-only protocol access is available; tap SCAN BANK after project load.",
                )
            return (
                "SP-404 storage not mounted",
                "Use DEBUG to confirm USB state. Normal-mode SP access is not a Linux mount.",
            )

        if not self._sp404_projects:
            self._refresh_sp404_projects()
        proj = self._sp404_projects[self._sp404_project_idx] if self._sp404_projects else None
        if proj:
            title = f"SP-404 ready · {proj['name']} · {proj['num_samples']} samples · {lib.mount_path}"
            subtitle = f"{len(self._sp404_projects)} project(s) visible"
        else:
            title = f"SP-404 ready · {lib.mount_path}"
            subtitle = "No projects yet — Compa can create PROJECT_01 on first write."
        return (title, subtitle)

    def _refresh_p6_assignments(self):
        """Re-read pad assignments from the P-6."""
        lib = self.p6_lib
        if lib is None:
            self._p6_cached_assignments = [None] * 48
        else:
            self._p6_cached_assignments = lib.read_assignments()
        self._p6_grid.set_pads(self._p6_cached_assignments)

    def _refresh_sp404_projects(self):
        lib = self.sp404_lib
        if lib is None:
            self._sp404_projects = []
            return
        self._sp404_projects = lib.list_projects()
        if not self._sp404_projects:
            self._sp404_project_idx = 0
        elif self._sp404_project_idx >= len(self._sp404_projects):
            self._sp404_project_idx = 0

    def _current_sp404_project(self) -> str:
        if not self._sp404_projects:
            return ""
        idx = max(0, min(self._sp404_project_idx, len(self._sp404_projects) - 1))
        return self._sp404_projects[idx]["path"]

    def _refresh_sp404_assignments(self):
        lib = self.sp404_lib
        proj = self._current_sp404_project()
        if lib is None or not proj:
            self._sp404_grid.set_pads([None] * 160)
            return
        pads = lib.read_project_pads(proj)
        self._sp404_grid.set_pads(pads)

    def _scan_sp404_current_bank(self):
        lib = self.sp404_lib
        if lib is None:
            self._set_status("SP-404 librarian unavailable")
            return
        if self._sp404_protocol_scanning:
            self._set_status("SP-404 bank scan already running")
            return

        bank_idx = self._sp404_grid.current_bank
        bank_letter = chr(ord("A") + bank_idx)
        self._sp404_protocol_scanning = True
        self._set_status(f"Preparing SP-404 bank {bank_letter} scan...")

        def _worker():
            try:
                proj = self._current_sp404_project()
                if not proj:
                    self._set_status("Loading SP-404 projects...")
                    self._refresh_sp404_projects()
                    proj = self._current_sp404_project()
                if not proj:
                    err = getattr(lib, "last_error", "") or "No SP-404 project selected"
                    self._set_status(err[:80])
                    return
                if not (hasattr(lib, "is_protocol_project")
                        and lib.is_protocol_project(proj)):
                    self._refresh_sp404_assignments()
                    return

                self._sp404_protocol_scan_key = (proj, bank_idx)
                self._set_status(f"Scanning SP-404 bank {bank_letter}...")
                pads = lib.read_project_bank_pads(proj, bank_idx)
                self._sp404_grid.set_pads(pads)
                self._refresh_sp404_projects()
                loaded = sum(
                    1 for p in pads[bank_idx * 16:(bank_idx + 1) * 16]
                    if p is not None
                )
                self._set_status(f"SP-404 bank {bank_letter}: {loaded}/16 loaded")
            finally:
                self._sp404_protocol_scanning = False

        threading.Thread(target=_worker, daemon=True).start()

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
        # Debug panel takes highest priority
        if self._debug_panel_visible:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                # Close button
                if self._debug_close_rect.collidepoint(mx, my):
                    self._debug_panel_visible = False
                    return
                # Sub-tab clicks (DRIVES / RAW)
                for rect, key in self._debug_subtab_rects:
                    if rect.collidepoint(mx, my):
                        self._debug_subtab = key
                        return
                # USE / MOUNT buttons
                for rect, action, payload in getattr(self, "_debug_use_rects", []):
                    if rect.collidepoint(mx, my):
                        self._handle_debug_action(action, payload)
                        return
            return

        # Debug panel cleared all input; fall through normally below

        # Confirm modal takes priority
        if self._confirm_modal.visible:
            result = self._confirm_modal.handle_event(event)
            if result == "OK":
                action = self._confirm_action
                self._confirm_action = None
                if action:
                    try:
                        action()
                    except Exception as e:
                        log.error("Confirm action failed: %s", e)
            elif result == "CANCEL":
                self._confirm_action = None
            return

        # Device-type tab bar — always checked first
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for rect, dtype in self._device_tab_rects:
                if rect.collidepoint(mx, my):
                    if dtype != self._device_type:
                        self._device_type = dtype
                        if dtype == "p6":
                            self._refresh_p6_assignments()
                        elif dtype == "sp404":
                            self._refresh_sp404_projects()
                            self._refresh_sp404_assignments()
                    return

        # Dispatch to the active device handler
        if self._device_type == "akai":
            self._handle_event_akai(event)
        elif self._device_type == "p6":
            self._handle_event_p6(event)
        elif self._device_type == "sp404":
            self._handle_event_sp404(event)

    def _handle_event_akai(self, event):
        """Original AKAI push/pull/kits handler — unchanged behaviour."""
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

    def _handle_event_p6(self, event):
        """P-6 librarian: tap a pad, tap a sample, assign."""
        # Action bar buttons
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for rect, action_id, enabled in self._p6_action_rects:
                if rect.collidepoint(mx, my):
                    if not enabled:
                        self._set_status("Select a loaded pad first")
                        return
                    self._do_p6_action(action_id)
                    return

        # Grid tap → selects a pad
        tapped_pad = self._p6_grid.handle_event(event)
        if tapped_pad is not None:
            return

        # Source list tap → assign to selected pad
        tapped = self._p6_source_list.handle_event(event)
        if tapped and tapped.data:
            selected = self._p6_grid.selected_pad
            if selected < 0:
                self._set_status("Select a pad first, then a sample")
                return
            self._assign_p6_pad(selected, tapped.data["path"])

    def _handle_event_sp404(self, event):
        """SP-404 librarian: tap pad + tap sample (or MOVE mode)."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Project prev/next arrows
            if hasattr(self, "_sp404_proj_rects"):
                for rect, delta in self._sp404_proj_rects:
                    if rect.collidepoint(mx, my):
                        if self._sp404_projects:
                            n = len(self._sp404_projects)
                            self._sp404_project_idx = (
                                self._sp404_project_idx + delta) % n
                            self._refresh_sp404_assignments()
                        return

            for rect, action_id, enabled in self._sp404_action_rects:
                if rect.collidepoint(mx, my):
                    if not enabled:
                        if action_id == "scan_bank":
                            self._set_status("SP-404 bank scan already running")
                        else:
                            self._set_status("Select a loaded pad first")
                        return
                    self._do_sp404_action(action_id)
                    return

        old_bank = self._sp404_grid.current_bank
        tapped_pad = self._sp404_grid.handle_event(event)
        if self._sp404_grid.current_bank != old_bank:
            if self._sp404_current_project_is_protocol():
                self._scan_sp404_current_bank()
            return
        if tapped_pad is not None:
            # MOVE mode: first tap = src, second = dest
            if self._sp404_move_mode:
                move_src = self._sp404_grid.move_src
                if move_src < 0:
                    self._sp404_grid.set_move_src(tapped_pad)
                    self._set_status("Now tap destination pad")
                else:
                    self._do_sp404_move(move_src, tapped_pad)
                    self._sp404_move_mode = False
                    self._sp404_grid.clear_move_src()
            return

        tapped = self._sp404_source_list.handle_event(event)
        if tapped and tapped.data:
            selected = self._sp404_grid.selected_pad
            if selected < 0:
                self._set_status("Select a pad first, then a sample")
                return
            self._assign_sp404_pad(selected, tapped.data["path"])

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

    # ── P-6 librarian actions ──────────────────────────────────────

    def _assign_p6_pad(self, global_idx: int, src_wav: str):
        lib = self.p6_lib
        if lib is None:
            self._set_status("P-6 librarian unavailable")
            return
        if not lib.is_mounted():
            self._set_status(lib.diagnostic())
            return
        bank_idx = global_idx // 6
        pad_idx = global_idx % 6
        self._set_status(
            f"Converting {os.path.basename(src_wav)[:24]} → P-6 WAV...")

        def _worker():
            dest = lib.write_pad(bank_idx, pad_idx, src_wav)
            if dest:
                name = os.path.basename(src_wav)
                bank_letter = "ABCDEFGH"[bank_idx]
                self._set_status(
                    f"Loaded {name[:24]} → {bank_letter}-{pad_idx + 1}")
                self._refresh_p6_assignments()
            else:
                err = lib.last_error or "Load failed — check the P-6 mount"
                self._set_status(err[:80])

        threading.Thread(target=_worker, daemon=True).start()

    def _do_p6_action(self, action_id: str):
        lib = self.p6_lib
        if lib is None:
            return

        if action_id == "debug":
            self._debug_panel_visible = True
            return

        if action_id == "clear_pad":
            selected = self._p6_grid.selected_pad
            if selected < 0:
                self._set_status("Select a pad first")
                return
            bank_idx = selected // 6
            pad_idx = selected % 6
            if lib.clear_pad(bank_idx, pad_idx):
                self._set_status(f"Cleared {'ABCDEFGH'[bank_idx]}-{pad_idx + 1}")
                self._refresh_p6_assignments()

        elif action_id == "clear_bank":
            bank_idx = self._p6_grid.current_bank
            self._confirm(
                f"Clear entire bank {'ABCDEFGH'[bank_idx]}?",
                lambda: self._do_clear_bank_p6(bank_idx),
            )

        elif action_id == "clear_all":
            self._confirm(
                "Clear ALL 48 pads on P-6?",
                self._do_clear_all_p6,
            )

        elif action_id == "backup":
            if not lib.is_mounted():
                self._set_status(lib.diagnostic())
                return
            if lib.busy:
                self._set_status("Busy with another operation")
                return
            self._set_status("Starting P-6 backup...")

            def _done(ok, msg):
                self._set_status(f"P-6 backup: {msg[:40]}")

            ts = time.strftime("%Y-%m-%d_%H-%M")
            lib.backup(f"P-6 {ts}", description="From Compa librarian",
                       on_complete=_done)

        elif action_id == "restore":
            if not lib.is_mounted():
                self._set_status(lib.diagnostic())
                return
            images = lib.list_images()
            if not images:
                self._set_status("No P-6 backups found")
                return
            self._confirm(
                f"Restore most recent backup? ({images[0]['name']})",
                lambda: self._do_restore_p6(images[0]["path"]),
            )

    def _do_clear_bank_p6(self, bank_idx: int):
        lib = self.p6_lib
        if lib is None:
            return
        if lib.clear_bank(bank_idx):
            self._set_status(f"Cleared bank {'ABCDEFGH'[bank_idx]}")
            self._refresh_p6_assignments()

    def _do_clear_all_p6(self):
        lib = self.p6_lib
        if lib is None:
            return
        if lib.clear_all():
            self._set_status("Cleared all P-6 pads")
            self._refresh_p6_assignments()
        else:
            err = lib.last_error or "Clear all failed"
            self._set_status(err[:80])

    def _do_restore_p6(self, image_path: str):
        lib = self.p6_lib
        if lib is None or lib.busy:
            return
        self._set_status("Restoring P-6...")

        def _done(ok, msg):
            self._set_status(f"P-6 restore: {msg[:40]}")
            if ok:
                self._refresh_p6_assignments()

        lib.restore(image_path, on_complete=_done)

    # ── SP-404 librarian actions ───────────────────────────────────

    def _assign_sp404_pad(self, global_idx: int, src_wav: str):
        lib = self.sp404_lib
        if lib is None:
            self._set_status("SP-404 librarian unavailable")
            return
        if not lib.is_mounted():
            self._set_status(lib.diagnostic())
            return
        # Make sure we have an up-to-date project list
        if not self._sp404_projects:
            self._refresh_sp404_projects()
        proj = self._current_sp404_project()
        if not proj:
            self._set_status("No SP-404 project — creating PROJECT_01...")
            self._refresh_sp404_projects()
            proj = self._current_sp404_project()
            if not proj:
                self._set_status("Couldn't create project dir")
                return
        bank_idx = global_idx // 16
        pad_idx = global_idx % 16
        self._set_status(
            f"Converting {os.path.basename(src_wav)[:24]} → SMP...")

        def _worker():
            dest = lib.write_pad(proj, bank_idx, pad_idx, src_wav)
            if dest:
                bank_letter = chr(ord("A") + bank_idx)
                self._set_status(
                    f"Loaded → {bank_letter}{pad_idx + 1:02d}")
                self._refresh_sp404_assignments()
            else:
                err = lib.last_error or "SMP write failed"
                self._set_status(err[:80])

        threading.Thread(target=_worker, daemon=True).start()

    def _do_sp404_action(self, action_id: str):
        lib = self.sp404_lib
        if lib is None:
            return

        if action_id == "debug":
            self._debug_panel_visible = True
            return

        if action_id == "scan_bank":
            self._scan_sp404_current_bank()
            return

        # Auto-rescan projects so we don't fail on first use
        if not self._sp404_projects and lib.is_mounted():
            self._refresh_sp404_projects()
        proj = self._current_sp404_project()
        if not proj and action_id not in ("backup", "restore", "debug"):
            self._set_status(lib.diagnostic())
            return

        if action_id == "clear_pad":
            selected = self._sp404_grid.selected_pad
            if selected < 0:
                self._set_status("Select a pad first")
                return
            b = selected // 16
            p = selected % 16
            if lib.clear_pad(proj, b, p):
                bank_letter = chr(ord("A") + b)
                self._set_status(f"Cleared {bank_letter}{p + 1:02d}")
                self._refresh_sp404_assignments()

        elif action_id == "move":
            self._sp404_move_mode = not self._sp404_move_mode
            if self._sp404_move_mode:
                self._sp404_grid.set_move_src(-1)
                self._set_status("MOVE: tap source pad, then destination")
            else:
                self._sp404_grid.clear_move_src()
                self._set_status("MOVE cancelled")

        elif action_id == "clear_bank":
            bank_idx = self._sp404_grid.current_bank
            bank_letter = chr(ord("A") + bank_idx)
            self._confirm(
                f"Clear entire bank {bank_letter}?",
                lambda: self._do_clear_bank_sp404(proj, bank_idx),
            )

        elif action_id == "backup":
            if not lib.is_mounted():
                self._set_status(lib.diagnostic())
                return
            if lib.busy:
                self._set_status("Busy with another operation")
                return
            self._set_status("Starting SP-404 backup...")

            def _done(ok, msg):
                self._set_status(f"SP-404 backup: {msg[:36]}")

            ts = time.strftime("%Y-%m-%d_%H-%M")
            lib.backup(f"SP-404 {ts}",
                       description="From Compa librarian",
                       on_complete=_done)

        elif action_id == "restore":
            if not lib.is_mounted():
                self._set_status(lib.diagnostic())
                return
            images = lib.list_images()
            if not images:
                self._set_status("No SP-404 backups found")
                return
            self._confirm(
                f"Restore most recent backup? ({images[0]['name']})",
                lambda: self._do_restore_sp404(images[0]["path"]),
            )

    def _do_sp404_move(self, src_global: int, dst_global: int):
        lib = self.sp404_lib
        if lib is None:
            return
        proj = self._current_sp404_project()
        if not proj:
            return
        src_b, src_p = src_global // 16, src_global % 16
        dst_b, dst_p = dst_global // 16, dst_global % 16
        if lib.move_pad(proj, src_b, src_p, dst_b, dst_p):
            self._set_status(
                f"Moved {chr(65 + src_b)}{src_p + 1:02d} → "
                f"{chr(65 + dst_b)}{dst_p + 1:02d}")
            self._refresh_sp404_assignments()
        else:
            self._set_status("Move failed")

    def _do_clear_bank_sp404(self, proj: str, bank_idx: int):
        lib = self.sp404_lib
        if lib is None:
            return
        if lib.clear_bank(proj, bank_idx):
            self._set_status(f"Cleared bank {chr(65 + bank_idx)}")
            self._refresh_sp404_assignments()

    def _do_restore_sp404(self, image_path: str):
        lib = self.sp404_lib
        if lib is None or lib.busy:
            return
        self._set_status("Restoring SP-404...")

        def _done(ok, msg):
            self._set_status(f"SP-404 restore: {msg[:36]}")
            if ok:
                self._refresh_sp404_assignments()

        lib.restore(image_path, on_complete=_done)

    # ── Confirm modal helper ───────────────────────────────────────

    def _confirm(self, message: str, action):
        self._confirm_modal.show(
            title="Confirm", message=message,
        )
        self._confirm_action = action

    # ── Update tick ────────────────────────────────────────────────

    def update(self):
        storage = getattr(self.app, "akai_storage", None)
        if storage:
            storage.scan_and_mount()

        if self._device_type == "akai":
            if self._mode == "push":
                self._push_list.update()
            elif self._mode == "pull":
                self._pull_browser.update()
            else:
                self._kit_list.update()
        elif self._device_type == "p6":
            self._p6_source_list.update()
            # Poll for mount/unmount state changes
            if self.p6_lib:
                now_mounted = self.p6_lib.is_mounted()
                if now_mounted != getattr(self, "_p6_was_mounted", None):
                    self._p6_was_mounted = now_mounted
                    if now_mounted:
                        self._refresh_p6_assignments()
        elif self._device_type == "sp404":
            self._sp404_source_list.update()
            if self.sp404_lib:
                now_mounted = self.sp404_lib.is_mounted()
                normal_mode = (
                    not now_mounted
                    and hasattr(self.sp404_lib, "normal_mode_available")
                    and self.sp404_lib.normal_mode_available()
                )
                state = (now_mounted, normal_mode)
                if state != getattr(self, "_sp404_was_state", None):
                    self._sp404_was_state = state
                    if now_mounted:
                        self._refresh_sp404_projects()
                        self._refresh_sp404_assignments()
                    elif normal_mode:
                        self._refresh_sp404_projects()
                        self._refresh_sp404_assignments()

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # Device-type tab bar (always visible)
        self._draw_device_tabs(surface, f_small)

        # Dispatch to the active device view
        if self._device_type == "akai":
            self._draw_akai(surface, f_med, f_small, f_tiny)
        elif self._device_type == "p6":
            self._draw_p6(surface, f_med, f_small, f_tiny)
        elif self._device_type == "sp404":
            self._draw_sp404(surface, f_med, f_small, f_tiny)

        # Confirm modal overlay on top of everything
        if self._confirm_modal.visible:
            self._confirm_modal.draw(surface)

    def _draw_device_tabs(self, surface: pygame.Surface, f_small):
        """Top tab bar: AKAI · P-6 · SP-404."""
        W = theme.SCREEN_WIDTH
        y = self.DEVICE_TAB_Y
        h = self.DEVICE_TAB_H

        storage = getattr(self.app, "akai_storage", None)
        akai_ok = storage and storage.is_connected
        p6_ok = self.p6_lib is not None and self.p6_lib.is_mounted()
        sp_ok = (
            self.sp404_lib is not None
            and (self.sp404_lib.is_mounted()
                 or (hasattr(self.sp404_lib, "normal_mode_available")
                     and self.sp404_lib.normal_mode_available()))
        )

        tabs = [
            ("AKAI",   "akai",  akai_ok),
            ("P-6",    "p6",    p6_ok),
            ("SP-404", "sp404", sp_ok),
        ]
        num = len(tabs)
        gap = 4
        tab_w = (W - 20 - gap * (num - 1)) // num

        self._device_tab_rects = []
        for i, (label, dtype, mount_ok) in enumerate(tabs):
            x = 10 + i * (tab_w + gap)
            rect = pygame.Rect(x, y, tab_w, h)
            self._device_tab_rects.append((rect, dtype))

            is_active = (dtype == self._device_type)

            if is_active:
                bg = theme.ACCENT
                tc = theme.BG
            elif mount_ok:
                bg = theme.BG_LIGHTER
                tc = theme.TEXT
            else:
                bg = theme.BG_PANEL
                tc = theme.TEXT_DIM

            pygame.draw.rect(surface, bg, rect, border_radius=6)
            pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=6)

            lbl = f_small.render(label, True, tc)
            surface.blit(lbl, lbl.get_rect(center=(rect.centerx, rect.centery - 2)))

            # Mount state dot
            dot_color = theme.GREEN if mount_ok else theme.TEXT_DIM
            pygame.draw.circle(surface, dot_color,
                               (rect.right - 12, rect.centery), 3)

    def _draw_akai(self, surface: pygame.Surface, f_med, f_small, f_tiny):
        storage = getattr(self.app, "akai_storage", None)
        connected = storage and storage.is_connected

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
            if not self._embedded:
                theme.draw_screen_header(surface, "TRANSFER", header_info)

            # Drive info label + drive selector buttons on DRIVE_ROW
            info = f"{active_d.label}: {active_d.free_gb}GB free" if active_d else ""
            label_surf = f_small.render(info, True, theme.TEXT_DIM)
            surface.blit(label_surf, (12, self.DRIVE_ROW_Y + 4))

            self._drive_btn_rects = []
            dx = theme.SCREEN_WIDTH - 16
            for i in range(len(drives) - 1, -1, -1):
                d = drives[i]
                is_active = (i == self._active_drive)
                label = f"{d.label} ({d.free_gb}G)"
                w = max(100, len(label) * 7 + 20)
                rect = pygame.Rect(dx - w, self.DRIVE_ROW_Y, w, self.DRIVE_ROW_H)
                bg = theme.ACCENT if is_active else theme.BUTTON_BG
                tc = theme.BG if is_active else theme.TEXT_DIM
                pygame.draw.rect(surface, bg, rect, border_radius=4)
                surf = f_tiny.render(label, True, tc)
                surface.blit(surf, surf.get_rect(center=rect.center))
                self._drive_btn_rects.append((rect, i))
                dx = rect.x - 6
        else:
            # "No device" hint in the drive row
            hint = f_small.render(
                "No MPC/Force mounted — put device in Computer Mode",
                True, theme.TEXT_DIM)
            surface.blit(hint, (12, self.DRIVE_ROW_Y + 4))
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
            y = self.CONTENT_Y + 12
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

    # ── P-6 librarian view ─────────────────────────────────────────

    def _draw_p6(self, surface: pygame.Surface, f_med, f_small, f_tiny):
        lib = self.p6_lib
        mounted = lib is not None and lib.is_mounted()

        # Mount status row (replaces AKAI's drive row)
        if mounted:
            # Re-read live assignments each draw so the view is fresh
            self._p6_cached_assignments = lib.read_assignments()
            self._p6_grid.set_pads(self._p6_cached_assignments)
        title, subtitle = self._p6_storage_summary()
        info_surf = f_small.render(title[:110], True, theme.TEXT_DIM)
        surface.blit(info_surf, (12, self.DRIVE_ROW_Y + 2))
        sub_surf = f_tiny.render(subtitle[:118], True, theme.TEXT_DIM)
        surface.blit(sub_surf, (12, self.DRIVE_ROW_Y + 18))

        # Librarian grid and source list draw in their own laid-out rects
        self._relayout_librarian()
        self._p6_grid.draw(surface)
        if not mounted:
            self._draw_storage_offline_overlay(
                surface, self._p6_grid.rect, "P-6 storage not mounted",
                "DEBUG shows the detected USB disk and mount error.", f_small, f_tiny)
        legend = "LIVE = already on device · PEND = pending import"
        legend_surf = f_tiny.render(legend, True, theme.TEXT_DIM)
        surface.blit(legend_surf, (self._p6_grid.rect.x, self._p6_grid.rect.bottom + 6))

        # Source list header
        src_rect = self._p6_source_list.rect
        detail_rect = pygame.Rect(src_rect.x, self.DRIVE_ROW_Y + 32, src_rect.width, 132)
        self._draw_selected_pad_card(surface, self._selected_pad_detail("p6", mounted),
                                     detail_rect, f_small, f_tiny)
        label = f"LOCAL AUDIO · {len(self._p6_source_list.items)}"
        if not self._p6_source_list.items:
            label += " · recordings/ samples/ Music/"
        label_surf = f_tiny.render(label, True, theme.ACCENT)
        surface.blit(label_surf, (src_rect.x, src_rect.y - 14))
        self._p6_source_list.draw(surface)

        # Action bar at bottom
        self._draw_librarian_actions(
            surface, f_small, f_tiny,
            self._p6_action_specs(mounted),
            self._p6_action_rects,
            lib,
        )

        # Status line
        self._draw_status_line(surface, f_small)

        # Debug panel overlay
        if self._debug_panel_visible:
            self._draw_debug_panel(surface, f_small, f_tiny, lib)

    # ── SP-404 librarian view ──────────────────────────────────────

    def _draw_sp404(self, surface: pygame.Surface, f_med, f_small, f_tiny):
        lib = self.sp404_lib
        mounted = lib is not None and lib.is_mounted()
        normal_mode = self._sp404_normal_mode()

        # Mount + project status row — auto-refresh while drawing
        if mounted:
            if not self._sp404_projects:
                self._refresh_sp404_projects()
            if self._sp404_projects:
                idx = max(0, min(self._sp404_project_idx,
                                 len(self._sp404_projects) - 1))
                proj = self._sp404_projects[idx]
                # Refresh the pad grid too
                pads = lib.read_project_pads(proj["path"])
                self._sp404_grid.set_pads(pads)
        title, subtitle = self._sp404_storage_summary()
        info_surf = f_small.render(title[:110], True, theme.TEXT_DIM)
        surface.blit(info_surf, (12, self.DRIVE_ROW_Y + 2))
        sub_surf = f_tiny.render(subtitle[:118], True, theme.TEXT_DIM)
        surface.blit(sub_surf, (12, self.DRIVE_ROW_Y + 18))

        # Project prev/next arrows (top-right)
        self._sp404_proj_rects = []
        if self._sp404_projects and len(self._sp404_projects) > 1:
            arrow_y = self.DRIVE_ROW_Y
            prev_rect = pygame.Rect(
                theme.SCREEN_WIDTH - 80, arrow_y, 28, self.DRIVE_ROW_H)
            next_rect = pygame.Rect(
                theme.SCREEN_WIDTH - 48, arrow_y, 32, self.DRIVE_ROW_H)
            pygame.draw.rect(surface, theme.BUTTON_BG, prev_rect, border_radius=4)
            pygame.draw.rect(surface, theme.BUTTON_BG, next_rect, border_radius=4)
            lp = f_small.render("<", True, theme.TEXT)
            ln = f_small.render(">", True, theme.TEXT)
            surface.blit(lp, lp.get_rect(center=prev_rect.center))
            surface.blit(ln, ln.get_rect(center=next_rect.center))
            self._sp404_proj_rects.append((prev_rect, -1))
            self._sp404_proj_rects.append((next_rect, 1))

        # Grid + source list
        self._relayout_librarian()
        self._sp404_grid.draw(surface)
        if not mounted and not normal_mode:
            self._draw_storage_offline_overlay(
                surface, self._sp404_grid.rect, "SP-404 storage not mounted",
                "Normal mode supports read-only SCAN BANK.", f_small, f_tiny)
        legend = "SCAN BANK = read SP pads without storage" if normal_mode and not mounted else "LOAD = present in project"
        legend_surf = f_tiny.render(legend, True, theme.TEXT_DIM)
        surface.blit(legend_surf, (self._sp404_grid.rect.x, self._sp404_grid.rect.bottom + 6))
        src_rect = self._sp404_source_list.rect
        detail_rect = pygame.Rect(src_rect.x, self.DRIVE_ROW_Y + 32, src_rect.width, 132)
        self._draw_selected_pad_card(surface, self._selected_pad_detail("sp404", mounted or normal_mode),
                                     detail_rect, f_small, f_tiny)
        label = f"LOCAL AUDIO · {len(self._sp404_source_list.items)}"
        if not self._sp404_source_list.items:
            label += " · recordings/ samples/ Music/"
        label_surf = f_tiny.render(label, True, theme.ACCENT)
        surface.blit(label_surf, (src_rect.x, src_rect.y - 14))
        self._sp404_source_list.draw(surface)

        # Action bar
        self._draw_librarian_actions(
            surface, f_small, f_tiny,
            self._sp404_action_specs(mounted, normal_mode),
            self._sp404_action_rects,
            lib,
        )
        self._draw_status_line(surface, f_small)

        # Debug panel overlay
        if self._debug_panel_visible:
            self._draw_debug_panel(surface, f_small, f_tiny, lib)

    def _draw_storage_offline_overlay(self, surface: pygame.Surface,
                                      rect: pygame.Rect, title: str,
                                      subtitle: str, f_small, f_tiny):
        """Dim the pad grid when device storage is not actually mounted."""
        panel = rect.inflate(-24, -24)
        overlay = pygame.Surface((panel.width, panel.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        surface.blit(overlay, panel.topleft)
        pygame.draw.rect(surface, theme.RED, panel, 2, border_radius=8)

        title_surf = f_small.render(title[:42], True, theme.TEXT_BRIGHT)
        sub_surf = f_tiny.render(subtitle[:64], True, theme.TEXT_DIM)
        surface.blit(title_surf, title_surf.get_rect(
            center=(panel.centerx, panel.centery - 10)))
        surface.blit(sub_surf, sub_surf.get_rect(
            center=(panel.centerx, panel.centery + 12)))

    def _draw_librarian_actions(self, surface, f_small, f_tiny,
                                  buttons: list,
                                  out_rects: list, lib):
        """Draw an action button row at the bottom of the content pane."""
        out_rects.clear()
        W = theme.SCREEN_WIDTH
        H = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT
        y = H - self.ACTION_H - 4
        gap = 6
        n = len(buttons)
        btn_w = (W - 20 - gap * (n - 1)) // n
        btn_h = self.ACTION_H - 8

        busy = lib is not None and lib.busy
        for i, spec in enumerate(buttons):
            action_id = spec["id"]
            label = spec["label"]
            color = spec["color"]
            enabled = spec.get("enabled", True)
            x = 10 + i * (btn_w + gap)
            rect = pygame.Rect(x, y, btn_w, btn_h)
            out_rects.append((rect, action_id, enabled and not busy))

            # Dim disabled buttons while busy
            active = enabled and not busy
            draw_color = color if active else theme.BG_PANEL
            text_color = theme.TEXT_BRIGHT if active else theme.TEXT_DIM

            pygame.draw.rect(surface, draw_color, rect, border_radius=6)
            pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=6)
            lbl = f_small.render(label, True, text_color)
            surface.blit(lbl, lbl.get_rect(center=rect.center))

        # Progress bar overlay if backup/restore is running
        if busy:
            bar_y = y - 10
            bar_rect = pygame.Rect(10, bar_y, W - 20, 6)
            pygame.draw.rect(surface, theme.BG_PANEL, bar_rect, border_radius=3)
            fill_w = int(bar_rect.width * max(0.0, min(1.0, lib.progress)))
            if fill_w > 0:
                fill = pygame.Rect(bar_rect.x, bar_rect.y, fill_w, bar_rect.height)
                pygame.draw.rect(surface, theme.ACCENT, fill, border_radius=3)

    def _draw_status_line(self, surface: pygame.Surface, f_small):
        """Draw the current status line (shared by P-6 and SP-404 views)."""
        if not self._status:
            return
        age = time.monotonic() - self._status_time
        if age >= 8.0:
            return
        color = theme.GREEN if ("Loaded" in self._status
                                or "Cleared" in self._status
                                or "Moved" in self._status
                                or "complete" in self._status.lower()) \
            else theme.ACCENT
        surf = f_small.render(self._status[:80], True, color)
        H = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT
        y = H - self.ACTION_H - 28
        surface.blit(surf, (12, y))

    def _handle_debug_action(self, action: str, payload):
        """Handle a tap on a USE/MOUNT button inside the debug panel."""
        lib = self.p6_lib if self._device_type == "p6" else self.sp404_lib
        if lib is None:
            return

        if action == "clear_manual":
            lib.set_manual_mount("")
            self._set_status("Cleared manual mount override")
            return

        if action == "use_mounted":
            mount_point = payload
            lib.set_manual_mount(mount_point)
            self._set_status(f"Using {mount_point}")
            if self._device_type == "p6":
                self._refresh_p6_assignments()
            else:
                self._refresh_sp404_projects()
                self._refresh_sp404_assignments()
            self._debug_panel_visible = False
            return

        if action == "mount_unmounted":
            from engine.device_mount import active_mount_partition
            part = payload
            mp = active_mount_partition(
                part, mount_name=part.label or self._device_type, force=True)
            if mp:
                lib.set_manual_mount(mp)
                self._set_status(f"Mounted {part.device} at {mp}")
                if self._device_type == "p6":
                    self._refresh_p6_assignments()
                else:
                    self._refresh_sp404_projects()
                    self._refresh_sp404_assignments()
                self._debug_panel_visible = False
            else:
                self._set_status(f"Mount failed for {part.device}")
            return

    def _draw_debug_panel(self, surface: pygame.Surface,
                           f_small, f_tiny, lib):
        """Render the debug panel overlay with live mount diagnostic info.

        Has two sub-tabs:
          DRIVES — mounted + unmounted partitions with USE / MOUNT buttons
          RAW    — raw lsblk / lsusb / /dev output for troubleshooting
        """
        W = theme.SCREEN_WIDTH
        H = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT

        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        surface.blit(overlay, (0, 0))

        panel = pygame.Rect(20, 20, W - 40, H - 40)
        pygame.draw.rect(surface, theme.BG_PANEL, panel, border_radius=10)
        pygame.draw.rect(surface, theme.ACCENT, panel, 2, border_radius=10)

        title_h = 36
        title_bar = pygame.Rect(panel.x, panel.y, panel.width, title_h)
        pygame.draw.rect(surface, theme.BG_LIGHTER, title_bar,
                         border_top_left_radius=10,
                         border_top_right_radius=10)
        pygame.draw.line(surface, theme.BORDER,
                         (panel.x, panel.y + title_h),
                         (panel.right, panel.y + title_h))

        title = "P-6 STORAGE DIAGNOSTIC" if self._device_type == "p6" \
            else "SP-404 STORAGE DIAGNOSTIC"
        f_title = theme.font("large")
        title_surf = f_title.render(title, True, theme.ACCENT)
        surface.blit(title_surf, (panel.x + 14, panel.y + 8))

        close_size = 26
        self._debug_close_rect = pygame.Rect(
            panel.right - close_size - 10, panel.y + 5,
            close_size, close_size,
        )
        pygame.draw.rect(surface, theme.BG, self._debug_close_rect,
                         border_radius=4)
        pygame.draw.rect(surface, theme.BORDER, self._debug_close_rect,
                         1, border_radius=4)
        x_surf = f_small.render("X", True, theme.TEXT)
        surface.blit(x_surf, x_surf.get_rect(center=self._debug_close_rect.center))

        # Sub-tab bar inside the debug panel
        tab_bar_y = panel.y + title_h + 6
        tab_h = 26
        sub_tabs = [("DRIVES", "drives"), ("RAW", "raw")]
        tab_w = (panel.width - 30 - 6 * (len(sub_tabs) - 1)) // len(sub_tabs)
        self._debug_subtab_rects = []
        for i, (label, key) in enumerate(sub_tabs):
            rect = pygame.Rect(
                panel.x + 15 + i * (tab_w + 6),
                tab_bar_y, tab_w, tab_h,
            )
            active = (getattr(self, "_debug_subtab", "drives") == key)
            bg = theme.ACCENT if active else theme.BG_LIGHTER
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=5)
            lbl = f_small.render(label, True, tc)
            surface.blit(lbl, lbl.get_rect(center=rect.center))
            self._debug_subtab_rects.append((rect, key))

        # Content area under the tab bar
        content_top = tab_bar_y + tab_h + 8
        content_rect = pygame.Rect(
            panel.x + 10, content_top,
            panel.width - 20, panel.bottom - content_top - 28,
        )

        # Fetch state once per draw
        from engine.device_mount import diagnostic_info
        info = diagnostic_info()

        self._debug_use_rects: list[tuple[pygame.Rect, str, object]] = []
        subtab = getattr(self, "_debug_subtab", "drives")
        if subtab == "raw":
            self._draw_debug_raw(surface, f_small, f_tiny, info, content_rect)
        else:
            self._draw_debug_drives(surface, f_small, f_tiny, info,
                                     content_rect, lib)

        # Footer hint
        hint = f_tiny.render(
            "Tap X to close. Tap RAW for lsblk/lsusb output to share.",
            True, theme.TEXT_DIM)
        surface.blit(hint, (panel.x + 14, panel.bottom - 20))

    def _draw_debug_drives(self, surface, f_small, f_tiny, info,
                            content_rect, lib):
        content_x = content_rect.x + 6
        y = content_rect.y

        # Current active mount
        if lib is not None and lib.is_mounted():
            surf = f_small.render(f"ACTIVE: {lib.mount_path}",
                                   True, theme.GREEN)
            surface.blit(surf, (content_x, y))
            y += 22

            if lib._manual_mount:
                clear_rect = pygame.Rect(content_x, y, 140, 24)
                pygame.draw.rect(surface, theme.ACCENT_DIM, clear_rect,
                                 border_radius=4)
                lbl = f_tiny.render("Clear manual", True, theme.TEXT_BRIGHT)
                surface.blit(lbl, lbl.get_rect(center=clear_rect.center))
                self._debug_use_rects.append((clear_rect, "clear_manual", None))
                y += 30
            else:
                y += 6

        if not info["lsblk_available"]:
            surf = f_small.render("ERROR: lsblk not available",
                                   True, theme.RED)
            surface.blit(surf, (content_x, y))
            y += 22
            return

        mounted = info["mounted"]
        unmounted = info["unmounted"]

        # Combined "all partitions" view — catches raw disks too
        all_parts = info.get("all_partitions", [])

        if not mounted and not unmounted and not all_parts:
            surf = f_small.render(
                "No partitions found via lsblk.",
                True, theme.YELLOW)
            surface.blit(surf, (content_x, y))
            y += 22
            surf = f_tiny.render(
                "Tap RAW to see lsusb / /dev / lsblk output.",
                True, theme.TEXT_DIM)
            surface.blit(surf, (content_x, y))
            y += 18
            return

        header = f_small.render(
            f"MOUNTED DRIVES ({len(mounted)})", True, theme.ACCENT)
        surface.blit(header, (content_x, y))
        y += 22
        if not mounted:
            surface.blit(f_tiny.render("  (none)", True, theme.TEXT_DIM),
                         (content_x, y))
            y += 20
        for m in mounted:
            if y > content_rect.bottom - 50:
                break
            label = m.label or "(no label)"
            text = f"  {m.device} → {m.mount_point}  [{label}]  {m.size_gb:.0f}G"
            surface.blit(f_small.render(text[:100], True, theme.TEXT),
                         (content_x, y))
            use_rect = pygame.Rect(
                content_rect.right - 80, y - 2, 70, 22)
            pygame.draw.rect(surface, theme.ACCENT, use_rect, border_radius=4)
            ulbl = f_tiny.render("USE", True, theme.BG)
            surface.blit(ulbl, ulbl.get_rect(center=use_rect.center))
            self._debug_use_rects.append((use_rect, "use_mounted", m.mount_point))
            y += 22
            try:
                entries = sorted(os.listdir(m.mount_point))[:8]
                if entries:
                    sub = f_tiny.render(
                        f"    {', '.join(entries)[:95]}",
                        True, theme.TEXT_DIM)
                    surface.blit(sub, (content_x, y))
                    y += 18
            except Exception:
                pass

        y += 6

        header = f_small.render(
            f"UNMOUNTED PARTITIONS / DISKS ({len(unmounted)})",
            True, theme.ACCENT)
        surface.blit(header, (content_x, y))
        y += 22
        if not unmounted:
            surface.blit(f_tiny.render("  (none)", True, theme.TEXT_DIM),
                         (content_x, y))
            y += 20
        for p in unmounted:
            if y > content_rect.bottom - 26:
                more = f_tiny.render("  ...more truncated",
                                      True, theme.TEXT_DIM)
                surface.blit(more, (content_x, y))
                break
            label = p.label or "(no label)"
            fs = p.fs_type or "?"
            hardware = " ".join(x for x in (getattr(p, "vendor", ""),
                                            getattr(p, "model", "")) if x).strip()
            suffix = f"  {hardware}" if hardware else ""
            text = f"  {p.device}  [{label}]  {p.size}  {fs}{suffix}"
            surface.blit(f_small.render(text[:100], True, theme.TEXT),
                         (content_x, y))
            mount_rect = pygame.Rect(
                content_rect.right - 80, y - 2, 70, 22)
            pygame.draw.rect(surface, theme.BLUE, mount_rect, border_radius=4)
            mlbl = f_tiny.render("MOUNT", True, theme.BG)
            surface.blit(mlbl, mlbl.get_rect(center=mount_rect.center))
            self._debug_use_rects.append((mount_rect, "mount_unmounted", p))
            y += 22

    def _draw_debug_raw(self, surface, f_small, f_tiny, info, content_rect):
        """Scrollable raw output panel — shows lsblk, lsusb, /dev/sd*."""
        lines: list[str] = []

        lines.append("── lsblk ─────────────────────────────")
        for line in info.get("lsblk_raw", "").splitlines()[:30]:
            lines.append(line)
        lines.append("")

        lines.append("── /dev (sd*, mmcblk1*) ──────────────")
        for entry in info.get("dev_sd_list", [])[:20]:
            lines.append(entry)
        if not info.get("dev_sd_list"):
            lines.append("(no sd or mmcblk1 entries)")
        lines.append("")

        lines.append("── lsusb ────────────────────────────")
        for line in info.get("lsusb_raw", "").splitlines()[:15]:
            # Highlight Roland entries
            lines.append(line)

        content_x = content_rect.x + 6
        y = content_rect.y
        line_h = 16
        for line in lines:
            if y + line_h > content_rect.bottom:
                break
            if line.startswith("──"):
                color = theme.ACCENT
            elif "Roland" in line or "0582" in line:
                color = theme.GREEN
            else:
                color = theme.TEXT_DIM if line == "" or line.startswith("(") \
                    else theme.TEXT
            # Monospaced-ish — use small font, long lines truncated
            truncated = line[:120]
            surface.blit(f_tiny.render(truncated, True, color),
                         (content_x, y))
            y += line_h
