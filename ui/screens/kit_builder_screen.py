"""Kit Builder Screen -- visual 4x4 pad grid for creating Akai MPC .Drum.xpm programs.

Layout (800x600, nav=52px, content=548px):
  y=0-38:    Header with kit name + RENAME button
  y=42-350:  Split view: LEFT pad grid (4x4), RIGHT sample browser
  y=354-390: Bank selector (A-H) + pad count
  y=394-430: Action buttons: CLEAR PAD, CLEAR ALL, EXPORT ADG, EXPORT XPM, EXPORT & UPLOAD
  y=434-450: Status line
"""

import os
import threading
import wave
import logging

import numpy as np
import pygame

from .. import theme
from ..components.modal import Modal
from engine.format_converter import generate_xpm, generate_adg, PadAssignment
from engine.drum_detector import scan_library, scan_summary
from engine.drum_mapper import auto_map
from engine.als_export import export_als

log = logging.getLogger(__name__)


def _wav_duration(path: str) -> float:
    """Get WAV duration in seconds. Returns 0.0 on error."""
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1] + "\u2026"


# Bank letters A-H
BANK_LETTERS = "ABCDEFGHIJ"  # Up to 10 banks for P-6 mode


class KitBuilderScreen:
    """Dedicated screen for building Akai MPC/Force .Drum.xpm drum kits."""

    def __init__(self, app):
        self.app = app

        # Kit state
        self._kit_name: str = "MyKit"
        self._pads: list[dict | None] = [None] * 128  # 8 banks x 16 pads
        self._current_bank: int = 0   # 0-7 (A-H)
        self._selected_pad: int = 0   # 0-127 absolute index

        # Sample browser (touch-friendly) — fits in shorter browser area
        from ui.components.touch_list import TouchList
        browser_rect = pygame.Rect(
            self._BROWSER_X, self._BROWSER_Y + self._BROWSER_HEADER_H,
            self._BROWSER_W, self._BROWSER_H - self._BROWSER_HEADER_H)
        # Recalculate to ensure it fits
        self._sample_touch_list = TouchList(browser_rect, item_height=36)

        # Legacy state
        self._sample_list: list[dict] = []
        self._sample_scroll: int = 0
        self._sample_source: str = "recordings"

        # Waveform preview cache
        self._wave_cache_path: str = ""
        self._wave_cache_data: np.ndarray | None = None
        self._WAVE_POINTS = 300  # Downsample to this many points

        # Kit mode: "mpc" (4x4, 8 banks, 128 pads) or "p6" (6 pads, ~10 banks, 64 slots)
        self._kit_mode = "mpc"

        # Export state
        self._last_export_name: str = ""
        self._status: str = ""
        self._status_timer: int = 0
        self._exporting: bool = False

        # Modal for rename
        self._modal = Modal("Rename Kit", "Enter kit name:",
                            buttons=["OK", "Cancel"], width=400, height=180)

        # Import mode state
        self._import_mode = False
        self._import_summary = ""
        from ui.components.folder_browser import FolderBrowser
        import_rect = pygame.Rect(16, 42, theme.SCREEN_WIDTH - 32,
                                   theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 90)
        sample_dir = app.config.get("LOCAL_SAMPLE_CACHE",
                                     os.path.join(os.path.dirname(os.path.dirname(
                                         os.path.dirname(os.path.abspath(__file__)))), "samples"))
        rec_dir = app.config.get("P6_RECORDING_DIR", "recordings")
        self._import_browser = FolderBrowser(
            import_rect, root_dir=sample_dir,
            file_filter=lambda f: f.lower().endswith((".wav", ".aif", ".aiff")),
            item_height=44,
        )
        self._import_root = sample_dir
        self._import_rec_dir = rec_dir

        # Audio preview (optional)
        self._preview_thread = None

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_enter(self):
        """Called when screen becomes active."""
        self._refresh_samples()

        # Check for cross-device workflow context (slices from slicer)
        ctx = getattr(self.app, "_screen_context", {})
        if ctx.get("slice_paths"):
            self._import_slice_batch(ctx["slice_paths"], ctx.get("kit_name", "Sliced Kit"))
            self.app._screen_context = {}  # Consume

    def on_exit(self):
        """Called when leaving screen."""
        pass

    # ── Sample list ─────────────────────────────────────────────────

    def _refresh_samples(self):
        """Build combined list of recordings + samples directory WAVs."""
        self._sample_list = []

        # Recordings from the recorder
        try:
            recs = self.app.recorder.list_recordings()
            for r in recs:
                self._sample_list.append({
                    "filename": r.get("filename", "???"),
                    "path": r.get("path", ""),
                    "duration": r.get("duration", 0.0),
                    "source": "rec",
                })
        except Exception:
            pass

        # WAV files from samples/ directory
        sample_dir = self.app.config.get(
            "LOCAL_SAMPLE_CACHE",
            os.path.join(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))), "samples"))
        if os.path.isdir(sample_dir):
            try:
                for fname in sorted(os.listdir(sample_dir)):
                    if fname.lower().endswith(".wav"):
                        fpath = os.path.join(sample_dir, fname)
                        dur = _wav_duration(fpath)
                        self._sample_list.append({
                            "filename": fname,
                            "path": fpath,
                            "duration": dur,
                            "source": "lib",
                        })
            except Exception:
                pass

        self._sample_scroll = 0

        # Populate TouchList
        from ui.components.touch_list import TouchListItem
        items = []
        for s in self._sample_list:
            dur = s.get("duration", 0)
            icon = "R" if s["source"] == "rec" else "L"
            icon_color = theme.ACCENT if s["source"] == "rec" else theme.BLUE
            items.append(TouchListItem(
                text=s["filename"][:28],
                subtext=f"{dur:.1f}s" if dur else "",
                icon=icon,
                icon_color=icon_color,
                data=s,
            ))
        self._sample_touch_list.set_items(items)

    # ── Computed helpers ────────────────────────────────────────────

    @property
    def _pads_per_bank(self) -> int:
        return 6 if self._kit_mode == "p6" else 16

    @property
    def _num_banks(self) -> int:
        return 10 if self._kit_mode == "p6" else 8

    @property
    def _total_pads(self) -> int:
        return self._pads_per_bank * self._num_banks

    @property
    def _grid_cols(self) -> int:
        return 3 if self._kit_mode == "p6" else 4

    @property
    def _grid_rows(self) -> int:
        return 2 if self._kit_mode == "p6" else 4

    def _bank_start(self) -> int:
        """First absolute pad index for the current bank."""
        return self._current_bank * self._pads_per_bank

    def _assigned_count(self) -> int:
        """How many pads have samples assigned."""
        return sum(1 for p in self._pads if p is not None)

    @property
    def wants_keyboard(self) -> bool:
        """Tell the app we want keyboard when modal is open."""
        return self._modal.visible

    # ── Layout constants (shared between draw and handle_event) ─────

    # Pad grid (MPC mode: 4 cols x 4 rows, P-6 mode: 3 cols x 2 rows)
    _GRID_X = 12
    _GRID_Y = 44
    _GRID_W = 430
    _GRID_H = 300
    _PAD_GAP = 6

    @property
    def _PAD_COLS(self):
        return 3 if self._kit_mode == "p6" else 4

    @property
    def _PAD_ROWS(self):
        return 2 if self._kit_mode == "p6" else 4

    # Sample browser: right panel
    _BROWSER_X = 452
    _BROWSER_Y = 44
    _BROWSER_W = 336
    _BROWSER_H = 250  # Shorter to make room for waveform
    _BROWSER_ITEM_H = 26
    _BROWSER_HEADER_H = 30

    # Bank selector row
    _BANK_Y = 354
    _BANK_H = 32

    # Action buttons row
    _ACTION_Y = 394
    _ACTION_H = 32

    # Status line
    _STATUS_Y = 436

    def _pad_rect(self, col: int, row: int) -> pygame.Rect:
        """Get the rect for a pad cell in the grid."""
        cell_w = (self._GRID_W - (self._PAD_COLS + 1) * self._PAD_GAP) // self._PAD_COLS
        cell_h = (self._GRID_H - (self._PAD_ROWS + 1) * self._PAD_GAP) // self._PAD_ROWS
        x = self._GRID_X + self._PAD_GAP + col * (cell_w + self._PAD_GAP)
        y = self._GRID_Y + self._PAD_GAP + row * (cell_h + self._PAD_GAP)
        return pygame.Rect(x, y, cell_w, cell_h)

    def _browser_list_y(self) -> int:
        """Top y of the scrollable sample list."""
        return self._BROWSER_Y + self._BROWSER_HEADER_H

    def _browser_max_visible(self) -> int:
        """How many sample rows fit in the browser panel."""
        available = self._BROWSER_H - self._BROWSER_HEADER_H
        return max(1, available // self._BROWSER_ITEM_H)

    # ── Button rects (used in both draw and handle_event) ───────────

    def _rename_btn_rect(self) -> pygame.Rect:
        return pygame.Rect(theme.SCREEN_WIDTH - 110, 6, 90, 26)

    def _refresh_btn_rect(self) -> pygame.Rect:
        return pygame.Rect(self._BROWSER_X + self._BROWSER_W - 80,
                           self._BROWSER_Y, 76, 26)

    def _bank_btn_rect(self, idx: int) -> pygame.Rect:
        btn_w = 38
        gap = 4
        start_x = 12
        return pygame.Rect(start_x + idx * (btn_w + gap), self._BANK_Y, btn_w, self._BANK_H)

    def _clear_pad_btn_rect(self) -> pygame.Rect:
        return pygame.Rect(12, self._ACTION_Y, 100, self._ACTION_H)

    def _clear_all_btn_rect(self) -> pygame.Rect:
        return pygame.Rect(118, self._ACTION_Y, 100, self._ACTION_H)

    def _import_btn_rect(self) -> pygame.Rect:
        return pygame.Rect(224, self._ACTION_Y, 100, self._ACTION_H)

    def _export_adg_btn_rect(self) -> pygame.Rect:
        return pygame.Rect(theme.SCREEN_WIDTH - 440, self._ACTION_Y, 124, self._ACTION_H)

    def _export_btn_rect(self) -> pygame.Rect:
        return pygame.Rect(theme.SCREEN_WIDTH - 310, self._ACTION_Y, 140, self._ACTION_H)

    def _upload_btn_rect(self) -> pygame.Rect:
        return pygame.Rect(theme.SCREEN_WIDTH - 164, self._ACTION_Y, 152, self._ACTION_H)

    # ── Event handling ──────────────────────────────────────────────

    def handle_event(self, event):
        # Modal takes priority
        if self._modal.visible:
            result = self._modal.handle_event(event)
            if result == "OK":
                new_name = self._modal.input_text.strip()
                if new_name:
                    self._kit_name = new_name
                self._modal.hide()
            elif result == "Cancel":
                self._modal.hide()
            return

        # Import mode — FolderBrowser takes over
        if self._import_mode:
            self._handle_import_event(event)
            return

        # TouchList handles drag scroll, wheel, and tap in browser panel
        tapped = self._sample_touch_list.handle_event(event)
        if tapped and tapped.data:
            # Find the index in _sample_list and assign
            for idx, s in enumerate(self._sample_list):
                if s["path"] == tapped.data.get("path"):
                    self._assign_sample(idx)
                    break

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            self._handle_click(mx, my)

        # Legacy scroll (only for non-browser areas now)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            mx = event.pos[0] if hasattr(event, 'pos') else 0
            if mx >= self._BROWSER_X:
                max_scroll = max(0, len(self._sample_list) - self._browser_max_visible())
                if event.button == 4:
                    self._sample_scroll = max(0, self._sample_scroll - 1)
                else:
                    self._sample_scroll = min(max_scroll, self._sample_scroll + 1)

    def _handle_click(self, mx: int, my: int):
        # ---- Header buttons ----
        rename_rect = self._rename_btn_rect()
        if rename_rect.collidepoint(mx, my):
            self._modal.show(title="Rename Kit", message="Enter kit name:",
                             input_mode=True, default_text=self._kit_name)
            return

        # Mode toggle (MPC ↔ P-6)
        mode_rect = pygame.Rect(theme.SCREEN_WIDTH - 200, 6, 80, 26)
        if mode_rect.collidepoint(mx, my):
            if self._kit_mode == "mpc":
                self._kit_mode = "p6"
                # Resize pads array for P-6 (60 pads: 6 x 10 banks)
                new_pads = [None] * 60
                for i in range(min(60, len(self._pads))):
                    new_pads[i] = self._pads[i]
                self._pads = new_pads
            else:
                self._kit_mode = "mpc"
                new_pads = [None] * 128
                for i in range(min(128, len(self._pads))):
                    new_pads[i] = self._pads[i]
                self._pads = new_pads
            self._current_bank = 0
            self._selected_pad = 0
            return

        # ---- Pad grid clicks ----
        if (self._GRID_X <= mx <= self._GRID_X + self._GRID_W and
                self._GRID_Y <= my <= self._GRID_Y + self._GRID_H):
            for row in range(self._PAD_ROWS):
                for col in range(self._PAD_COLS):
                    rect = self._pad_rect(col, row)
                    if rect.collidepoint(mx, my):
                        # MPC pad layout: bottom-left = pad 0 (row 3 col 0)
                        pad_in_bank = (self._PAD_ROWS - 1 - row) * self._PAD_COLS + col
                        abs_idx = self._bank_start() + pad_in_bank
                        self._selected_pad = abs_idx
                        # If pad has sample, preview it
                        if self._pads[abs_idx] is not None:
                            self._preview_pad(abs_idx)
                        return
            return

        # ---- Sample browser clicks ----
        if (self._BROWSER_X <= mx <= self._BROWSER_X + self._BROWSER_W and
                self._BROWSER_Y <= my <= self._BROWSER_Y + self._BROWSER_H):
            # Refresh button
            refresh_rect = self._refresh_btn_rect()
            if refresh_rect.collidepoint(mx, my):
                self._refresh_samples()
                return
            # TouchList handles tap + scroll below
            return

        # ---- Bank selector ----
        if self._BANK_Y <= my <= self._BANK_Y + self._BANK_H:
            for i in range(self._num_banks):
                rect = self._bank_btn_rect(i)
                if rect.collidepoint(mx, my):
                    self._current_bank = i
                    pad_in_old_bank = self._selected_pad % self._pads_per_bank
                    self._selected_pad = i * self._pads_per_bank + pad_in_old_bank
                    return
            return

        # ---- Action buttons ----
        if self._clear_pad_btn_rect().collidepoint(mx, my):
            self._pads[self._selected_pad] = None
            self._set_status("Pad cleared")
            return

        if self._clear_all_btn_rect().collidepoint(mx, my):
            self._pads = [None] * 128
            self._set_status("All pads cleared")
            return

        if self._import_btn_rect().collidepoint(mx, my):
            self._import_mode = True
            # Set correct rect before navigating
            browser_rect = pygame.Rect(
                16, 78, theme.SCREEN_WIDTH - 32,
                theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 130)
            self._import_browser.set_rect(browser_rect)
            self._import_browser.navigate_to(self._import_root)
            print(f"Import mode: browsing {self._import_root}", flush=True)
            return

        if self._export_adg_btn_rect().collidepoint(mx, my):
            self._export_adg()
            return

        # EXPORT ALS button (between ADG and XPM)
        als_rect = pygame.Rect(theme.SCREEN_WIDTH - 560, self._ACTION_Y, 110, self._ACTION_H)
        if als_rect.collidepoint(mx, my):
            self._export_als()
            return

        if self._export_btn_rect().collidepoint(mx, my):
            self._export_xpm()
            return

        if self._upload_btn_rect().collidepoint(mx, my):
            if self._last_export_name:
                # PUSH TO DEVICE — jump to transfer screen with kit pre-selected
                self.app.switch_screen("transfer", {
                    "mode": "kits",
                    "kit_name": self._last_export_name,
                })
            else:
                self._export_xpm(upload=True)
            return

    # ── Actions ─────────────────────────────────────────────────────

    def _assign_sample(self, list_idx: int):
        """Assign the selected sample to the currently selected pad."""
        sample = self._sample_list[list_idx]
        path = sample.get("path", "")
        if not path or not os.path.isfile(path):
            self._set_status("File not found")
            return

        self._pads[self._selected_pad] = {
            "path": path,
            "filename": sample.get("filename", os.path.basename(path)),
            "duration": sample.get("duration", _wav_duration(path)),
        }
        ppb = self._pads_per_bank
        bank_letter = BANK_LETTERS[self._selected_pad // ppb] if self._selected_pad // ppb < len(BANK_LETTERS) else "?"
        pad_num = (self._selected_pad % ppb) + 1
        self._set_status(
            f"Assigned to {bank_letter}{pad_num:02d}: {sample['filename']}")

        # Auto-advance to next pad
        next_pad = self._selected_pad + 1
        if next_pad < 128:
            self._selected_pad = next_pad
            # Switch bank if needed
            self._current_bank = next_pad // 16

    def _preview_pad(self, abs_idx: int):
        """Play a short preview of the pad's sample."""
        pad = self._pads[abs_idx]
        if pad is None:
            return
        path = pad.get("path", "")
        if not os.path.isfile(path):
            return

        def _play():
            try:
                import sounddevice as sd
                import soundfile as sf
                data, rate = sf.read(path, dtype="float32")
                # Limit preview to 3 seconds
                max_frames = rate * 3
                if len(data) > max_frames:
                    data = data[:max_frames]
                sd.play(data, rate)
            except Exception as e:
                log.debug("Preview failed: %s", e)

        self._preview_thread = threading.Thread(target=_play, daemon=True)
        self._preview_thread.start()

    def _export_xpm(self, upload: bool = False):
        """Export all assigned pads as a .Drum.xpm kit."""
        if self._exporting:
            return

        assigned = [(i, p) for i, p in enumerate(self._pads) if p is not None]
        if not assigned:
            self._set_status("No pads assigned -- nothing to export")
            return

        self._exporting = True
        self._set_status(f"Exporting {len(assigned)} pads...")

        def worker():
            try:
                sessions_dir = self.app.config.get("P6_SESSIONS_DIR", "sessions")
                output_dir = os.path.join(sessions_dir, "converted", self._kit_name)
                os.makedirs(output_dir, exist_ok=True)

                pad_assignments = []
                for idx, pad_info in assigned:
                    pad_assignments.append(PadAssignment(
                        pad_index=idx,
                        sample_path=pad_info["path"],
                        volume=1.0,
                        pan=0.5,
                        tune=0.0,
                    ))

                result = generate_xpm(self._kit_name, pad_assignments, output_dir)
                if result:
                    self._set_status(
                        f"Exported! {len(assigned)} pads -> "
                        f"{self._kit_name}.Drum.xpm")
                    self._last_export_name = self._kit_name
                    if upload:
                        self._set_status(
                            f"Exported! {len(assigned)} pads -> "
                            f"{self._kit_name}.Drum.xpm (upload not configured)")
                else:
                    self._set_status("Export failed -- check logs")
            except Exception as e:
                self._set_status(f"Export error: {e}")
            finally:
                self._exporting = False

        threading.Thread(target=worker, daemon=True).start()

    def _export_adg(self):
        """Export all assigned pads as an Ableton Live Drum Rack .adg preset."""
        if self._exporting:
            return

        assigned = [(i, p) for i, p in enumerate(self._pads) if p is not None]
        if not assigned:
            self._set_status("No pads assigned -- nothing to export")
            return

        self._exporting = True
        self._set_status(f"Exporting ADG with {len(assigned)} pads...")

        def worker():
            try:
                sessions_dir = self.app.config.get("P6_SESSIONS_DIR", "sessions")
                output_dir = os.path.join(sessions_dir, "converted", self._kit_name)
                os.makedirs(output_dir, exist_ok=True)

                pad_assignments = []
                for idx, pad_info in assigned:
                    pad_assignments.append(PadAssignment(
                        pad_index=idx,
                        sample_path=pad_info["path"],
                        volume=1.0,
                        pan=0.5,
                        tune=0.0,
                    ))

                result = generate_adg(self._kit_name, pad_assignments, output_dir)
                if result:
                    self._set_status(
                        f"Exported! {len(assigned)} pads -> "
                        f"{self._kit_name}.adg")
                    self._last_export_name = self._kit_name
                else:
                    self._set_status("ADG export failed -- check logs")
            except Exception as e:
                self._set_status(f"ADG export error: {e}")
            finally:
                self._exporting = False

        threading.Thread(target=worker, daemon=True).start()

    def _export_als(self):
        """Export as an Ableton Live Set with audio tracks per drum type."""
        if self._exporting:
            return
        assigned = [(i, p) for i, p in enumerate(self._pads) if p is not None]
        if not assigned:
            self._set_status("No pads assigned")
            return

        self._exporting = True
        self._set_status(f"Exporting ALS with {len(assigned)} samples...")

        def worker():
            try:
                sessions_dir = self.app.config.get("P6_SESSIONS_DIR", "sessions")
                output_dir = os.path.join(sessions_dir, "converted", self._kit_name)
                os.makedirs(output_dir, exist_ok=True)

                # Group pads by drum type for separate tracks
                tracks_by_type: dict[str, list[str]] = {}
                for idx, pad_info in assigned:
                    dtype = pad_info.get("drum_type", "samples")
                    if not dtype or dtype == "unknown":
                        dtype = "samples"
                    if dtype not in tracks_by_type:
                        tracks_by_type[dtype] = []
                    tracks_by_type[dtype].append(pad_info["path"])

                tracks = []
                for dtype, paths in tracks_by_type.items():
                    tracks.append({
                        "name": dtype.replace("_", " ").title(),
                        "samples": paths,
                    })

                # If no drum types, make one track with all samples
                if not tracks:
                    tracks = [{"name": self._kit_name,
                               "samples": [p["path"] for _, p in assigned]}]

                bpm = 120.0
                if self.app.p6:
                    bpm = self.app.p6.state.bpm

                als_path = os.path.join(output_dir, f"{self._kit_name}.als")
                result = export_als(self._kit_name, tracks, als_path, bpm=bpm)
                if result:
                    self._set_status(
                        f"Exported! {len(tracks)} tracks -> "
                        f"{self._kit_name}.als")
                    self._last_export_name = self._kit_name
                else:
                    self._set_status("ALS export failed")
            except Exception as e:
                self._set_status(f"ALS export error: {e}")
            finally:
                self._exporting = False

        threading.Thread(target=worker, daemon=True).start()

    def _set_status(self, msg: str):
        self._status = msg
        self._status_timer = 150  # ~5 seconds at 30fps

    # ── Update ──────────────────────────────────────────────────────

    def _handle_import_event(self, event):
        """Handle events in import browse mode."""
        # Ensure browser rect is correct before handling events
        browser_rect = pygame.Rect(
            16, 78, theme.SCREEN_WIDTH - 32,
            theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 130)
        self._import_browser.set_rect(browser_rect)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 44

            # CANCEL button
            cancel_rect = pygame.Rect(16, btn_y, 100, 36)
            if cancel_rect.collidepoint(mx, my):
                self._import_mode = False
                return

            # SCAN & IMPORT button
            scan_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, btn_y, 200, 36)
            if scan_rect.collidepoint(mx, my):
                self._do_import()
                return

        # Browser handles folder navigation, scrolling, etc.
        self._import_browser.handle_event(event)

    def _draw_import_mode(self, surface, f_med, f_small, f_tiny):
        """Draw the import folder browser overlay."""
        # Title
        surf = f_med.render("SELECT SAMPLE LIBRARY FOLDER", True, theme.ACCENT)
        surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=40))

        # Hint
        surf = f_tiny.render("Navigate to a folder with drum samples (Kicks/, Snares/, etc.)",
                            True, theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=60))

        # Browser (below hint)
        self._import_browser.set_rect(pygame.Rect(
            16, 78, theme.SCREEN_WIDTH - 32,
            theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 130))
        self._import_browser.draw(surface)

        # Bottom buttons
        btn_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 44

        cancel_rect = pygame.Rect(16, btn_y, 100, 36)
        pygame.draw.rect(surface, theme.BUTTON_BG, cancel_rect, border_radius=6)
        surf = f_small.render("CANCEL", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=cancel_rect.center))

        scan_rect = pygame.Rect(theme.SCREEN_WIDTH - 220, btn_y, 200, 36)
        pygame.draw.rect(surface, theme.GREEN, scan_rect, border_radius=6)
        surf = f_med.render("SCAN & IMPORT", True, theme.BG)
        surface.blit(surf, surf.get_rect(center=scan_rect.center))

        # Current path hint
        cur = self._import_browser.current_path
        if cur:
            surf = f_tiny.render(f"Will scan: {os.path.basename(cur)}/",
                                True, theme.TEXT_DIM)
            surface.blit(surf, (130, btn_y + 10))

    def _import_slice_batch(self, paths: list[str], kit_name: str):
        """Auto-assign a list of sample paths to consecutive pads."""
        self._pads = [None] * 128
        self._kit_name = kit_name
        self._current_bank = 0
        self._selected_pad = 0

        for i, path in enumerate(paths[:128]):
            if not os.path.isfile(path):
                continue
            dur = 0.0
            try:
                import soundfile as sf
                info = sf.info(path)
                dur = info.duration
            except Exception:
                pass
            self._pads[i] = {
                "path": path,
                "filename": os.path.basename(path),
                "duration": dur,
            }

        assigned = sum(1 for p in self._pads if p is not None)
        self._set_status(f"Imported {assigned} slices as '{kit_name}'")
        print(f"Slice batch import: {assigned} pads from {len(paths)} files", flush=True)

    def _load_waveform(self, path: str) -> np.ndarray | None:
        """Load and downsample a WAV for preview display."""
        if path == self._wave_cache_path and self._wave_cache_data is not None:
            return self._wave_cache_data
        try:
            import soundfile as sf
            data, rate = sf.read(path, dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)  # Mono mix
            # Downsample to WAVE_POINTS
            n = len(data)
            if n <= self._WAVE_POINTS:
                peaks = np.abs(data)
            else:
                chunk = n // self._WAVE_POINTS
                trimmed = data[:chunk * self._WAVE_POINTS]
                reshaped = trimmed.reshape(self._WAVE_POINTS, chunk)
                peaks = np.max(np.abs(reshaped), axis=1)
            self._wave_cache_path = path
            self._wave_cache_data = peaks
            return peaks
        except Exception:
            return None

    def _draw_waveform_preview(self, surface, f_tiny):
        """Draw waveform of the currently selected pad's sample."""
        pad_data = self._pads[self._selected_pad]
        wave_x = self._BROWSER_X
        wave_y = self._BROWSER_Y + self._BROWSER_H + 6
        wave_w = self._BROWSER_W
        wave_h = self._BANK_Y - wave_y - 4  # Fill space between browser and bank selector

        wave_rect = pygame.Rect(wave_x, wave_y, wave_w, wave_h)
        pygame.draw.rect(surface, theme.BG_PANEL, wave_rect, border_radius=4)
        pygame.draw.rect(surface, theme.BORDER, wave_rect, 1, border_radius=4)

        if not pad_data:
            surf = f_tiny.render("No sample on selected pad", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=wave_rect.center))
            return

        # Pad info label
        bank = self._selected_pad // 16
        pad_num = (self._selected_pad % 16) + 1
        label = f"{BANK_LETTERS[bank]}{pad_num:02d}: {pad_data['filename']}"
        drum_type = pad_data.get("drum_type", "")
        if drum_type:
            label += f" [{drum_type}]"
        surf = f_tiny.render(label, True, theme.ACCENT)
        surface.blit(surf, (wave_x + 4, wave_y + 2))

        # Load and draw waveform
        peaks = self._load_waveform(pad_data["path"])
        if peaks is None or len(peaks) == 0:
            return

        # Draw waveform bars
        draw_y = wave_y + 14
        draw_h = wave_h - 16
        max_val = max(float(np.max(peaks)), 0.001)
        bar_w = max(1, wave_w // len(peaks))

        points_top = []
        points_bot = []
        for i, peak in enumerate(peaks):
            x = wave_x + int(i * wave_w / len(peaks))
            h = int((peak / max_val) * draw_h * 0.5)
            cy = draw_y + draw_h // 2
            points_top.append((x, cy - h))
            points_bot.append((x, cy + h))

        if len(points_top) > 1:
            pygame.draw.lines(surface, theme.WAVEFORM_COLOR, False, points_top, 1)
            pygame.draw.lines(surface, theme.WAVEFORM_COLOR, False, points_bot, 1)

        # Center line
        cy = draw_y + draw_h // 2
        pygame.draw.line(surface, theme.BORDER, (wave_x + 2, cy), (wave_x + wave_w - 2, cy))

        # Duration
        dur = pad_data.get("duration", 0)
        if dur:
            dur_surf = f_tiny.render(f"{dur:.1f}s", True, theme.TEXT_DIM)
            surface.blit(dur_surf, (wave_x + wave_w - dur_surf.get_width() - 4, wave_y + 2))

    def _do_import(self):
        """Scan the current browser directory and auto-map to pads."""
        scan_dir = self._import_browser.current_path
        print(f"SCAN & IMPORT: scanning {scan_dir}", flush=True)
        if not scan_dir or not os.path.isdir(scan_dir):
            self._set_status("No folder selected")
            print("  No valid folder", flush=True)
            return

        # Scan and classify
        classified = scan_library(scan_dir)
        summary = scan_summary(classified)
        total = sum(len(v) for v in classified.values())

        if total == 0:
            self._set_status("No audio files found in this folder")
            return

        # Auto-map to pads
        mapped_pads = auto_map(classified)
        assigned = sum(1 for p in mapped_pads if p is not None)

        # Apply to Kit Builder
        self._pads = mapped_pads
        self._kit_name = os.path.basename(scan_dir)
        self._current_bank = 0
        self._selected_pad = 0
        self._import_mode = False
        self._import_summary = summary
        self._set_status(f"Imported {assigned} samples: {summary}")
        print(f"Smart import: {assigned} pads from {scan_dir}", flush=True)
        print(f"  {summary}", flush=True)

    def update(self):
        self._sample_touch_list.update()
        if self._import_mode:
            self._import_browser.update()
        if self._status_timer > 0:
            self._status_timer -= 1
            if self._status_timer == 0:
                self._status = ""

    # ── Drawing ─────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_title = theme.font("title")
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")
        f_mono = theme.font("mono")

        # ---- Header (y=0-38) ----
        mode_label = "P-6 MODE" if self._kit_mode == "p6" else "MPC MODE"
        theme.draw_screen_header(surface, "KIT BUILDER", f"{self._kit_name}  [{mode_label}]")

        # Import mode overlay
        if self._import_mode:
            self._draw_import_mode(surface, f_med, f_small, f_tiny)
            return

        # Rename button on header
        rename_rect = self._rename_btn_rect()
        theme.draw_button(surface, rename_rect, "RENAME", f_small)

        # Mode toggle (MPC ↔ P-6) — in header area
        mode_rect = pygame.Rect(theme.SCREEN_WIDTH - 200, 6, 80, 26)
        mode_bg = theme.YELLOW if self._kit_mode == "p6" else theme.ACCENT
        pygame.draw.rect(surface, mode_bg, mode_rect, border_radius=4)
        surf = f_tiny.render(mode_label, True, theme.BG)
        surface.blit(surf, surf.get_rect(center=mode_rect.center))

        # ---- Pad grid panel (left, y=42-344) ----
        grid_panel = pygame.Rect(
            self._GRID_X - 2, self._GRID_Y - 2,
            self._GRID_W + 4, self._GRID_H + 4)
        theme.draw_panel(surface, grid_panel, border=True)

        bank_start = self._bank_start()
        for row in range(self._PAD_ROWS):
            for col in range(self._PAD_COLS):
                rect = self._pad_rect(col, row)
                # Layout: bottom-left is pad 0
                pad_in_bank = (self._PAD_ROWS - 1 - row) * self._PAD_COLS + col
                abs_idx = bank_start + pad_in_bank
                if abs_idx >= len(self._pads):
                    continue
                pad_info = self._pads[abs_idx]
                is_selected = (abs_idx == self._selected_pad)

                # Pad background
                if pad_info is not None:
                    # Has sample: green tint
                    bg = (30, 60, 35)
                else:
                    bg = theme.PAD_OFF

                pygame.draw.rect(surface, bg, rect, border_radius=4)

                # Selected border
                if is_selected:
                    pygame.draw.rect(surface, theme.ACCENT, rect, 2, border_radius=4)
                else:
                    pygame.draw.rect(surface, theme.BORDER, rect, 1, border_radius=4)

                # Pad label: A01-A16 etc.
                bank_letter = BANK_LETTERS[self._current_bank] if self._current_bank < len(BANK_LETTERS) else "?"
                pad_label = f"{bank_letter}{pad_in_bank + 1:02d}"

                if pad_info is not None:
                    # Show pad number, sample name, duration
                    lbl = f_tiny.render(pad_label, True, theme.ACCENT)
                    surface.blit(lbl, (rect.x + 4, rect.y + 3))

                    # Truncated sample name
                    fname = pad_info.get("filename", "???")
                    name = os.path.splitext(fname)[0]
                    truncated = _truncate(name, 12)
                    name_surf = f_tiny.render(truncated, True, theme.TEXT)
                    surface.blit(name_surf, (rect.x + 4, rect.y + 18))

                    # Duration
                    dur = pad_info.get("duration", 0)
                    dur_str = f"{dur:.1f}s"
                    dur_surf = f_tiny.render(dur_str, True, theme.TEXT_DIM)
                    surface.blit(dur_surf, (rect.x + 4, rect.y + 33))
                else:
                    # Empty pad: dim centered label
                    lbl = f_small.render(pad_label, True, theme.TEXT_DIM)
                    lbl_rect = lbl.get_rect(center=rect.center)
                    surface.blit(lbl, lbl_rect)

        # ---- Sample browser panel (right, y=42-344) ----
        browser_panel = pygame.Rect(
            self._BROWSER_X - 2, self._BROWSER_Y - 2,
            self._BROWSER_W + 4, self._BROWSER_H + 4)
        theme.draw_panel(surface, browser_panel, border=True)

        # Browser header
        header_rect = pygame.Rect(
            self._BROWSER_X, self._BROWSER_Y,
            self._BROWSER_W, self._BROWSER_HEADER_H)
        pygame.draw.rect(surface, theme.BG_LIGHTER, header_rect)
        pygame.draw.line(surface, theme.BORDER,
                         (self._BROWSER_X, self._BROWSER_Y + self._BROWSER_HEADER_H),
                         (self._BROWSER_X + self._BROWSER_W,
                          self._BROWSER_Y + self._BROWSER_HEADER_H))

        browser_title = f"SAMPLES ({len(self._sample_list)})"
        surf = f_small.render(browser_title, True, theme.TEXT)
        surface.blit(surf, (self._BROWSER_X + 8, self._BROWSER_Y + 7))

        # Refresh button
        refresh_rect = self._refresh_btn_rect()
        theme.draw_button(surface, refresh_rect, "REFRESH", f_tiny)

        # Sample list (touch-friendly)
        self._sample_touch_list.draw(surface)

        # ---- Waveform preview of selected pad (below browser) ----
        self._draw_waveform_preview(surface, f_tiny)

        # ---- Bank selector (y=354-386) ----
        for i in range(self._num_banks):
            rect = self._bank_btn_rect(i)
            active = (i == self._current_bank)
            label = BANK_LETTERS[i] if i < len(BANK_LETTERS) else str(i)
            # Show dot if bank has any assigned pads
            ppb = self._pads_per_bank
            bank_has_pads = any(
                i * ppb + j < len(self._pads) and self._pads[i * ppb + j] is not None
                for j in range(ppb))
            if bank_has_pads and not active:
                theme.draw_button(surface, rect, label, f_small,
                                  active=False, color=theme.BG_LIGHTER)
                # Small green dot
                dot_x = rect.right - 8
                dot_y = rect.y + 6
                pygame.draw.circle(surface, theme.GREEN, (dot_x, dot_y), 3)
            else:
                theme.draw_button(surface, rect, label, f_small, active=active)

        # Pad count to the right of bank buttons
        count_x = 12 + 8 * (38 + 4) + 12
        count_text = f"{self._assigned_count()}/128 pads assigned"
        count_surf = f_small.render(count_text, True, theme.TEXT_DIM)
        surface.blit(count_surf, (count_x, self._BANK_Y + 8))

        # ---- Action buttons (y=394-426) ----
        # Clear pad
        clear_pad_rect = self._clear_pad_btn_rect()
        theme.draw_button(surface, clear_pad_rect, "CLEAR PAD", f_small)

        # Clear all
        clear_all_rect = self._clear_all_btn_rect()
        theme.draw_button(surface, clear_all_rect, "CLEAR ALL", f_small,
                          color=theme.RED)

        # IMPORT button
        import_rect = self._import_btn_rect()
        theme.draw_button(surface, import_rect, "IMPORT", f_small,
                          color=theme.BLUE)

        # Kit name display
        name_x = 336
        name_surf = f_med.render(self._kit_name, True, theme.ACCENT)
        surface.blit(name_surf, (name_x, self._ACTION_Y + 6))

        # Export buttons share has_pads state
        has_pads = self._assigned_count() > 0

        # Export ALS (Ableton Live Set)
        als_rect = pygame.Rect(theme.SCREEN_WIDTH - 560, self._ACTION_Y, 110, self._ACTION_H)
        if has_pads and not self._exporting:
            theme.draw_button(surface, als_rect, "EXPORT ALS", f_small,
                              active=True, color=theme.BLUE)
        else:
            theme.draw_button(surface, als_rect, "EXPORT ALS", f_small)

        # Export ADG (Ableton Drum Rack)
        export_adg_rect = self._export_adg_btn_rect()
        if self._exporting:
            theme.draw_button(surface, export_adg_rect, "EXPORTING...", f_small,
                              color=theme.YELLOW)
        elif has_pads:
            theme.draw_button(surface, export_adg_rect, "EXPORT ADG", f_small,
                              active=True)
        else:
            theme.draw_button(surface, export_adg_rect, "EXPORT ADG", f_small)

        # Export XPM
        export_rect = self._export_btn_rect()
        if self._exporting:
            theme.draw_button(surface, export_rect, "EXPORTING...", f_small,
                              color=theme.YELLOW)
        elif has_pads:
            theme.draw_button(surface, export_rect, "EXPORT XPM", f_small,
                              active=True)
        else:
            theme.draw_button(surface, export_rect, "EXPORT XPM", f_small)

        # Export & Upload
        upload_rect = self._upload_btn_rect()
        if self._last_export_name:
            pygame.draw.rect(surface, theme.GREEN, upload_rect, border_radius=6)
            surf = f_small.render("PUSH TO DEVICE →", True, theme.BG)
            surface.blit(surf, surf.get_rect(center=upload_rect.center))
        elif has_pads:
            theme.draw_button(surface, upload_rect, "EXPORT & UPLOAD", f_small,
                              active=True)
        else:
            theme.draw_button(surface, upload_rect, "EXPORT & UPLOAD", f_small)

        # ---- Status line (y=436) ----
        if self._status:
            is_err = "error" in self._status.lower() or "fail" in self._status.lower()
            is_ok = "exported" in self._status.lower()
            sc = theme.RED if is_err else (theme.GREEN if is_ok else theme.YELLOW)
            status_surf = f_small.render(self._status, True, sc)
            surface.blit(status_surf, (12, self._STATUS_Y))

        # Hint at bottom
        hint = "Tap pad to select | Tap sample to assign | Banks A-H"
        hint_surf = f_tiny.render(hint, True, theme.TEXT_DIM)
        surface.blit(hint_surf, (12, self._STATUS_Y + 16))

        # ---- Modal overlay (last, on top) ----
        self._modal.draw(surface)
