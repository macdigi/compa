"""P-6 Sample Screen — file browser, visual waveform slicer, format converter.

Three modes:
  BROWSE:  File browser for navigating local sample library + recordings
  SLICER:  Visual waveform with slice markers, preview, export, P-6 transfer
  CONVERT: Batch convert recordings to SP-404, P-6, or Akai MPC kit formats
"""

import os
import threading
import pygame
import numpy as np
from .. import theme
from engine.sample_slicer import SampleSlicer, P6_MOUNT_PATH
from engine.format_converter import convert_recordings_to_kit, list_supported_formats


class P6SampleScreen:
    """Three-mode sample screen: browser, waveform slicer, format converter."""

    def __init__(self, app):
        self.app = app
        self._mode = "browse"

        # File browser state
        sample_dir = app.config.get("LOCAL_SAMPLE_CACHE",
                                     os.path.join(os.path.dirname(os.path.dirname(
                                         os.path.abspath(__file__))), "samples"))
        self._root_dir = sample_dir
        self._current_dir = sample_dir
        os.makedirs(sample_dir, exist_ok=True)
        self._recordings_dir = app.config.get("P6_RECORDING_DIR", "recordings")

        # Touch-friendly folder browser for browse mode
        from ui.components.folder_browser import FolderBrowser
        browse_rect = pygame.Rect(16, 78,
                                   theme.SCREEN_WIDTH - 32,
                                   theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 82)
        self._browser = FolderBrowser(
            browse_rect, root_dir=sample_dir,
            file_filter=lambda f: any(f.lower().endswith(e)
                                      for e in (".wav", ".aif", ".aiff", ".mp3", ".flac")),
            item_height=44,
        )

        # Legacy state for backward compat (convert mode still uses these)
        self._file_list: list[dict] = []
        self._file_scroll = 0
        self._file_selected = -1
        self._refresh_files()

        # Slicer engine
        staging_dir = os.path.join(
            app.config.get("P6_SESSIONS_DIR", "sessions"), "slices")
        self._slicer = SampleSlicer(staging_dir)
        self._slice_scroll = 0
        self._export_flash = 0
        self._transfer_flash = 0
        self._last_exported_slices: list[str] = []  # For BUILD KIT workflow
        self._transient_sens = 0.3  # Transient detection sensitivity (0.0-1.0)
        self._slicer_status = ""
        self._slicer_status_time = 0.0
        self._p6_mounted = False

        # Waveform zoom
        self._zoom = 1.0
        self._zoom_offset = 0.0

        # Start/End trim marker dragging
        self._dragging_marker = None  # "start", "end", or None
        self._place_mode = "slice"    # "slice" (add slice markers) or "trim" (set S/E)

        # Convert mode state
        self._convert_recordings: list[dict] = []
        self._convert_selected: set[str] = set()  # set of paths
        self._convert_scroll = 0
        self._convert_target = "sp404"  # "sp404", "p6", or "mpc"
        self._convert_status = ""       # status message
        self._convert_busy = False
        self._convert_result_dir = ""
        self._convert_kit_name = ""

    def _refresh_files(self):
        self._file_list = []
        if not os.path.isdir(self._current_dir):
            return
        try:
            entries = sorted(os.listdir(self._current_dir))
        except Exception:
            return

        for name in entries:
            full = os.path.join(self._current_dir, name)
            if os.path.isdir(full) and not name.startswith("."):
                self._file_list.append({"name": name + "/", "path": full, "is_dir": True})

        audio_ext = {".wav", ".aif", ".aiff", ".flac", ".mp3"}
        for name in entries:
            full = os.path.join(self._current_dir, name)
            if os.path.isfile(full):
                if os.path.splitext(name)[1].lower() in audio_ext:
                    self._file_list.append({"name": name, "path": full, "is_dir": False})

        self._file_scroll = 0
        self._file_selected = -1

    def on_enter(self):
        self._p6_mounted = os.path.isdir(P6_MOUNT_PATH)
        self._refresh_files()

        # Check for cross-device workflow context
        ctx = getattr(self.app, "_screen_context", {})
        if ctx.get("recording_path"):
            path = ctx["recording_path"]
            if os.path.isfile(path) and self._slicer.load(path):
                self._mode = "slicer"
                self._slice_scroll = 0
                print(f"Auto-loaded recording: {os.path.basename(path)}", flush=True)
            self.app._screen_context = {}  # Consume context

        # Auto-detect connected device for convert target default
        if self._p6_mounted:
            self._convert_target = "p6"
        elif os.path.isdir("/media/pi/SP-404MKII") or os.path.isdir("/media/pi/SP404MKII"):
            self._convert_target = "sp404"

    def on_exit(self):
        self._slicer.stop_preview()

    def handle_event(self, event):
        # Drag S/E markers (no snap during drag for smoothness, snap on release)
        if event.type == pygame.MOUSEMOTION and self._dragging_marker and self._mode == "slicer":
            wave_rect = self._get_wave_rect()
            frame = self._pixel_to_frame(event.pos[0], wave_rect)
            if self._dragging_marker == "start":
                self._slicer.set_start(frame, snap_zero=False)
            elif self._dragging_marker == "end":
                self._slicer.set_end(frame, snap_zero=False)
            return

        if event.type == pygame.MOUSEBUTTONUP and self._dragging_marker:
            # Snap to zero crossing on release
            if self._dragging_marker == "start":
                self._slicer.set_start(self._slicer.start_frame, snap_zero=True)
            elif self._dragging_marker == "end":
                self._slicer.set_end(self._slicer.end_frame, snap_zero=True)
            self._dragging_marker = None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            browse_btn = pygame.Rect(theme.SCREEN_WIDTH - 350, 6, 100, 30)
            slicer_btn = pygame.Rect(theme.SCREEN_WIDTH - 240, 6, 100, 30)
            convert_btn = pygame.Rect(theme.SCREEN_WIDTH - 130, 6, 100, 30)
            if browse_btn.collidepoint(mx, my):
                self._mode = "browse"
                return
            if slicer_btn.collidepoint(mx, my):
                self._mode = "slicer"
                return
            if convert_btn.collidepoint(mx, my):
                self._mode = "convert"
                self._refresh_convert_recordings()
                return

            if self._mode == "browse":
                # Recordings shortcut button
                rec_rect = pygame.Rect(110, 42, 130, 28)
                if rec_rect.collidepoint(mx, my):
                    self._browser.navigate_to(self._recordings_dir)
                    return
                # FolderBrowser handles the rest
            elif self._mode == "slicer":
                self._handle_slicer(mx, my)
            elif self._mode == "convert":
                self._handle_convert(mx, my)

        # Browse mode: delegate ALL events to FolderBrowser
        if self._mode == "browse":
            result = self._browser.handle_event(event)
            if result and result.get("type") == "file":
                if self._slicer.load(result["path"]):
                    self._mode = "slicer"
                    self._slice_scroll = 0
            return

        # Slicer mode: delegate to slice TouchList for drag scroll
        if self._mode == "slicer" and hasattr(self, "_slice_touch_list"):
            tapped = self._slice_touch_list.handle_event(event)
            if tapped and tapped.data:
                idx = tapped.data.get("index", 0)
                self._slicer.preview_slice(idx)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            mx_pos = event.pos[0] if hasattr(event, 'pos') else 0
            my_pos = event.pos[1] if hasattr(event, 'pos') else 0
            if self._mode == "browse":
                pass  # Handled by FolderBrowser above
            elif self._mode == "convert":
                max_s = max(0, len(self._convert_recordings) - 9)
                if event.button == 4:
                    self._convert_scroll = max(0, self._convert_scroll - 1)
                else:
                    self._convert_scroll = min(max_s, self._convert_scroll + 1)
            else:
                # Scroll on waveform = zoom, scroll on list = scroll list
                wave_rect = pygame.Rect(16, 42, 700, 188)
                if wave_rect.collidepoint(mx_pos, my_pos):
                    if event.button == 4:
                        self._zoom_in(mx_pos)
                    else:
                        self._zoom_out()
                else:
                    slices = self._slicer.get_slices()
                    max_s = max(0, len(slices) - 4)
                    if event.button == 4:
                        self._slice_scroll = max(0, self._slice_scroll - 1)
                    else:
                        self._slice_scroll = min(max_s, self._slice_scroll + 1)

    def _handle_browse(self, mx, my):
        back_rect = pygame.Rect(16, 42, 80, 28)
        if back_rect.collidepoint(mx, my):
            parent = os.path.dirname(self._current_dir)
            if parent and len(parent) >= len(self._root_dir):
                self._current_dir = parent
                self._refresh_files()
            return

        rec_rect = pygame.Rect(110, 42, 130, 28)
        if rec_rect.collidepoint(mx, my):
            self._current_dir = self._recordings_dir
            self._refresh_files()
            return

        list_y = 78
        item_h = 24
        max_visible = (theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - list_y - 4) // item_h
        visible = self._file_list[self._file_scroll:self._file_scroll + max_visible]

        for i, item in enumerate(visible):
            item_rect = pygame.Rect(16, list_y + i * item_h, theme.SCREEN_WIDTH - 32, item_h - 2)
            if item_rect.collidepoint(mx, my):
                real_idx = self._file_scroll + i
                if item["is_dir"]:
                    self._current_dir = item["path"]
                    self._refresh_files()
                else:
                    self._file_selected = real_idx
                    if self._slicer.load(item["path"]):
                        self._mode = "slicer"
                        self._slice_scroll = 0
                return

    def _get_wave_rect(self):
        zx = theme.SCREEN_WIDTH - 66
        return pygame.Rect(16, 42, zx - 24, 256)

    def _handle_slicer(self, mx, my):
        if not self._slicer.loaded:
            return

        # Zoom buttons (right side)
        zx = theme.SCREEN_WIDTH - 66
        zoom_in_rect = pygame.Rect(zx, 42, 60, 36)
        zoom_out_rect = pygame.Rect(zx, 82, 60, 36)
        zoom_fit_rect = pygame.Rect(zx, 122, 60, 36)
        se_btn = pygame.Rect(zx, 162, 60, 36)
        if zoom_in_rect.collidepoint(mx, my):
            self._zoom_in(400)
            return
        if zoom_out_rect.collidepoint(mx, my):
            self._zoom_out()
            return
        if zoom_fit_rect.collidepoint(mx, my):
            self._zoom = 1.0
            self._zoom_offset = 0.0
            return
        if se_btn.collidepoint(mx, my):
            self._place_mode = "trim" if self._place_mode == "slice" else "slice"
            return

        # Waveform click (y=42-298, expanded)
        wave_rect = self._get_wave_rect()
        if wave_rect.collidepoint(mx, my):
            frame = self._pixel_to_frame(mx, wave_rect)
            if self._place_mode == "trim":
                mid = (self._slicer.start_frame + self._slicer.end_frame) // 2
                if frame < mid:
                    self._slicer.set_start(frame, snap_zero=True)
                    self._dragging_marker = "start"
                else:
                    self._slicer.set_end(frame, snap_zero=True)
                    self._dragging_marker = "end"
            else:
                tolerance = int(15 / wave_rect.width / self._zoom * self._slicer.total_frames)
                if not self._slicer.remove_nearest_marker(frame, tolerance):
                    self._slicer.add_marker(frame, snap_zero=True)
            return

        # ── Toolbar Row 1 (y=320): /2 /4 /8 /16 CLEAR TRANSIENT ─────
        r1y = 320
        bh = 24
        for j, n in enumerate([2, 4, 8, 16]):
            rect = pygame.Rect(16 + j * 56, r1y, 52, bh)
            if rect.collidepoint(mx, my):
                self._slicer.auto_slice(n)
                return

        clear_rect = pygame.Rect(16 + 4 * 56, r1y, 52, bh)
        if clear_rect.collidepoint(mx, my):
            self._slicer.clear_markers()
            return

        trans_rect = pygame.Rect(16 + 5 * 56, r1y, 80, bh)
        if trans_rect.collidepoint(mx, my):
            count = self._slicer.transient_slice(sensitivity=self._transient_sens)
            self._set_status(f"Transient: {count} slices")
            return

        sx = 16 + 5 * 56 + 84
        sens_down = pygame.Rect(sx, r1y, 24, bh)
        if sens_down.collidepoint(mx, my):
            self._transient_sens = max(0.05, self._transient_sens - 0.1)
            return
        sens_up = pygame.Rect(sx + 50, r1y, 24, bh)
        if sens_up.collidepoint(mx, my):
            self._transient_sens = min(1.0, self._transient_sens + 0.1)
            return

        # ── Toolbar Row 2 (y=348): edit + export ─────────────────────
        r2y = 348
        edit_rects = [
            (pygame.Rect(16, r2y, 56, bh), "trim"),
            (pygame.Rect(76, r2y, 56, bh), "norm"),
            (pygame.Rect(136, r2y, 56, bh), "mono"),
            (pygame.Rect(196, r2y, 56, bh), "undo"),
        ]
        for rect, action in edit_rects:
            if rect.collidepoint(mx, my):
                if action == "trim":
                    self._slicer.trim_to_selection()
                elif action == "norm":
                    self._slicer.normalize()
                elif action == "mono":
                    self._slicer.to_mono()
                elif action == "undo" and self._slicer.can_undo:
                    self._slicer.undo()
                return

        # Export
        rx = theme.SCREEN_WIDTH - 16
        dev_name = self.app.device_name
        tw = 100
        transfer_rect = pygame.Rect(rx - tw, r2y, tw, bh)
        if transfer_rect.collidepoint(mx, my):
            if self._slicer.transfer_to_p6() > 0:
                self._transfer_flash = 30
            return
        rx -= tw + 4

        ew = 80
        export_rect = pygame.Rect(rx - ew, r2y, ew, bh)
        if export_rect.collidepoint(mx, my):
            exported = self._slicer.export_slices(normalize=True)
            if exported:
                self._export_flash = 30
                self._last_exported_slices = exported
            return
        rx -= ew + 4

        # BUILD KIT — auto-exports if not already exported
        kw = 90
        kit_rect = pygame.Rect(rx - kw, r2y, kw, bh)
        if kit_rect.collidepoint(mx, my):
            # Auto-export if we have slices but haven't exported yet
            if not self._last_exported_slices:
                exported = self._slicer.export_slices(normalize=True)
                if exported:
                    self._last_exported_slices = exported
            if self._last_exported_slices:
                stem = os.path.splitext(os.path.basename(
                    self._slicer._filepath or "slices"))[0]
                self.app.switch_screen("kit", {
                    "slice_paths": self._last_exported_slices,
                    "kit_name": stem,
                })
            return

        # Edit toolbar row (y=318) — positions must match _draw_slicer exactly
        edit_y = 318
        edit_actions = [
            (pygame.Rect(60, edit_y, 65, 28), "TRIM"),
            (pygame.Rect(129, edit_y, 65, 28), "NORM"),
            (pygame.Rect(198, edit_y, 65, 28), "MONO"),
            (pygame.Rect(267, edit_y, 65, 28), "UNDO"),
            (pygame.Rect(354, edit_y, 60, 28), "22k"),
            (pygame.Rect(418, edit_y, 60, 28), "14k"),
            (pygame.Rect(482, edit_y, 60, 28), "11k"),
        ]
        for rect, label in edit_actions:
            if rect.collidepoint(mx, my):
                if label == "TRIM":
                    self._slicer.truncate()
                    self._zoom = 1.0
                    self._zoom_offset = 0.0
                elif label == "NORM":
                    self._slicer.normalize()
                elif label == "MONO":
                    self._slicer.stereo_to_mono("mix")
                elif label == "UNDO":
                    self._slicer.undo()
                    self._zoom = 1.0
                    self._zoom_offset = 0.0
                elif label == "22k":
                    self._slicer.downsample(22050)
                elif label == "14k":
                    self._slicer.downsample(14700)
                elif label == "11k":
                    self._slicer.downsample(11025)
                return

    def _set_status(self, text: str):
        import time
        self._slicer_status = text
        self._slicer_status_time = time.monotonic()

    def _zoom_in(self, mouse_x: int = 400):
        """Zoom into the waveform, centered on mouse position."""
        if self._zoom >= 32.0:
            return
        wave_rect = pygame.Rect(16, 42, 700, 188)
        # Center zoom on mouse position
        frac = (mouse_x - wave_rect.x) / wave_rect.width
        center = self._zoom_offset + frac / self._zoom
        self._zoom *= 2.0
        self._zoom_offset = max(0, min(center - 0.5 / self._zoom, 1.0 - 1.0 / self._zoom))

    def _zoom_out(self):
        """Zoom out the waveform."""
        if self._zoom <= 1.0:
            return
        center = self._zoom_offset + 0.5 / self._zoom
        self._zoom /= 2.0
        if self._zoom <= 1.0:
            self._zoom = 1.0
            self._zoom_offset = 0.0
        else:
            self._zoom_offset = max(0, min(center - 0.5 / self._zoom, 1.0 - 1.0 / self._zoom))

    def _pixel_to_frame(self, px: int, wave_rect: pygame.Rect) -> int:
        """Convert a pixel x position to a frame number, accounting for zoom."""
        frac = (px - wave_rect.x) / wave_rect.width
        sample_frac = self._zoom_offset + frac / self._zoom
        return int(sample_frac * self._slicer.total_frames)

    def _frame_to_pixel(self, frame: int, wave_rect: pygame.Rect) -> int:
        """Convert a frame number to a pixel x position, accounting for zoom."""
        sample_frac = frame / self._slicer.total_frames
        view_frac = (sample_frac - self._zoom_offset) * self._zoom
        return int(wave_rect.x + view_frac * wave_rect.width)

    def update(self):
        if self._mode == "browse":
            self._browser.update()
        if self._mode == "slicer" and hasattr(self, "_slice_touch_list"):
            self._slice_touch_list.update()
        if self._export_flash > 0:
            self._export_flash -= 1
        if self._transfer_flash > 0:
            self._transfer_flash -= 1

    def draw(self, surface: pygame.Surface):
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")

        # Header
        theme.draw_screen_header(surface, "SAMPLES", self._mode.upper())

        # Mode toggles (overlay on header)
        for rect, label, active in [
            (pygame.Rect(theme.SCREEN_WIDTH - 350, 6, 100, 26), "BROWSE", self._mode == "browse"),
            (pygame.Rect(theme.SCREEN_WIDTH - 240, 6, 100, 26), "SLICER", self._mode == "slicer"),
            (pygame.Rect(theme.SCREEN_WIDTH - 130, 6, 100, 26), "CONVERT", self._mode == "convert"),
        ]:
            bg = theme.ACCENT if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Device mount status (device-agnostic)
        dev = self.app.device
        self._p6_mounted = os.path.isdir(P6_MOUNT_PATH)
        if dev and dev.mount_path:
            mounted = os.path.isdir(dev.mount_path)
            mt = f"{self.app.device_name} USB: READY" if mounted else f"{self.app.device_name} USB: --"
        else:
            mounted = self._p6_mounted
            mt = "USB: READY" if mounted else "USB: --"
        mc = theme.GREEN if mounted else theme.TEXT_DIM
        surf = f_small.render(mt, True, mc)
        surface.blit(surf, (theme.SCREEN_WIDTH - 480, 12))

        if self._mode == "browse":
            # Recordings shortcut button (above browser)
            rec_rect = pygame.Rect(110, 42, 130, 28)
            pygame.draw.rect(surface, theme.BUTTON_BG, rec_rect, border_radius=4)
            surf = f_small.render("RECORDINGS", True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=rec_rect.center))
            # Touch-friendly folder browser
            self._browser.draw(surface)
        elif self._mode == "slicer":
            self._draw_slicer(surface, f_large, f_med, f_small)
        elif self._mode == "convert":
            self._draw_convert(surface, f_large, f_med, f_small)

    def _draw_browse(self, surface, f_med, f_small):
        back_rect = pygame.Rect(16, 42, 80, 28)
        pygame.draw.rect(surface, theme.BUTTON_BG, back_rect, border_radius=4)
        surf = f_small.render("BACK", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=back_rect.center))

        rec_rect = pygame.Rect(110, 42, 130, 28)
        pygame.draw.rect(surface, theme.BUTTON_BG, rec_rect, border_radius=4)
        surf = f_small.render("RECORDINGS", True, theme.ACCENT)
        surface.blit(surf, surf.get_rect(center=rec_rect.center))

        rel = os.path.relpath(self._current_dir, self._root_dir)
        if rel == ".":
            rel = "/"
        surf = f_small.render(rel, True, theme.TEXT_DIM)
        surface.blit(surf, (260, 48))

        list_y = 78
        item_h = 24
        max_vis = (theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - list_y - 4) // item_h
        visible = self._file_list[self._file_scroll:self._file_scroll + max_vis]

        for i, item in enumerate(visible):
            real_idx = self._file_scroll + i
            row_rect = pygame.Rect(16, list_y + i * item_h, theme.SCREEN_WIDTH - 32, item_h - 2)
            if real_idx == self._file_selected:
                pygame.draw.rect(surface, theme.ACCENT_DIM, row_rect, border_radius=2)
            elif i % 2 == 1:
                pygame.draw.rect(surface, theme.BG_LIGHTER, row_rect, border_radius=2)

            icon = "/" if item["is_dir"] else " "
            color = theme.ACCENT if item["is_dir"] else theme.TEXT
            surf = f_small.render(f"{icon} {item['name']}", True, color)
            surface.blit(surf, (20, list_y + i * item_h + 3))

        if not self._file_list:
            empty_y = list_y + 40
            surf = f_med.render("No samples found", True, theme.TEXT_DIM)
            sr = surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=empty_y)
            surface.blit(surf, sr)
            hint = f_small.render("Drop WAV files in ~/compa/samples or tap RECORDINGS",
                                  True, theme.TEXT_DIM)
            hr = hint.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=empty_y + 28)
            surface.blit(hint, hr)

        surf = f_small.render("Tap WAV to load into slicer", True, theme.TEXT_DIM)
        surface.blit(surf, (16, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 18))

    def _draw_slicer(self, surface, f_large, f_med, f_small):
        f_tiny = theme.font("tiny")

        if not self._slicer.loaded:
            surf = f_med.render("No file loaded — tap BROWSE and select a WAV", True, theme.TEXT_DIM)
            surface.blit(surf, (16, 150))
            return

        # Filename info inline with header
        info = f"{self._slicer.filename}  |  {self._slicer.duration_secs:.1f}s  |  {self._slicer.sample_rate}Hz"
        surf = f_small.render(info, True, theme.TEXT)
        surface.blit(surf, (240, 14))

        # ── WAVEFORM (y=40-300, 260px tall, full width minus zoom panel) ──
        zx = theme.SCREEN_WIDTH - 66  # Zoom panel x
        wave_panel = pygame.Rect(12, 40, zx - 16, 260)
        theme.draw_panel(surface, wave_panel, border=True)
        wave_rect = pygame.Rect(16, 42, zx - 24, 256)
        pygame.draw.rect(surface, theme.WAVEFORM_BG, wave_rect, border_radius=4)

        waveform = self._slicer.waveform
        if waveform is not None:
            max_val = max(float(np.max(waveform)), 0.001)

            # Clip to visible zoom region
            clip = surface.get_clip()
            surface.set_clip(wave_rect)

            # Alternating slice backgrounds
            slices = self._slicer.get_slices()
            for idx, (start, end, _, _) in enumerate(slices):
                x1 = self._frame_to_pixel(start, wave_rect)
                x2 = self._frame_to_pixel(end, wave_rect)
                if idx % 2 == 1 and x2 > wave_rect.x and x1 < wave_rect.right:
                    x1c = max(wave_rect.x, x1)
                    x2c = min(wave_rect.right, x2)
                    if x2c > x1c:
                        tint = pygame.Surface((x2c - x1c, wave_rect.height), pygame.SRCALPHA)
                        tint.fill((255, 255, 255, 15))
                        surface.blit(tint, (x1c, wave_rect.y))

            # Waveform bars (zoom-aware)
            view_start = self._zoom_offset
            view_width = 1.0 / self._zoom
            w_len = len(waveform)
            for px_i in range(wave_rect.width):
                frac = px_i / wave_rect.width
                sample_frac = view_start + frac * view_width
                w_idx = int(sample_frac * w_len)
                if 0 <= w_idx < w_len:
                    val = waveform[w_idx] / max_val
                    bar_h = int(val * wave_rect.height * 0.9)
                    if bar_h > 0:
                        px = wave_rect.x + px_i
                        pygame.draw.line(surface, theme.WAVEFORM_COLOR,
                                        (px, wave_rect.bottom - bar_h), (px, wave_rect.bottom))

            # Slice markers (zoom-aware)
            for marker in self._slicer.markers:
                mpx = self._frame_to_pixel(marker, wave_rect)
                if wave_rect.x <= mpx <= wave_rect.right:
                    pygame.draw.line(surface, theme.WAVEFORM_MARKER,
                                    (mpx, wave_rect.y), (mpx, wave_rect.bottom), 2)
                    pygame.draw.polygon(surface, theme.WAVEFORM_MARKER, [
                        (mpx - 5, wave_rect.y), (mpx + 5, wave_rect.y), (mpx, wave_rect.y + 8)])

            # Start/End trim markers
            s_frame = self._slicer.start_frame
            e_frame = self._slicer.end_frame
            s_px = self._frame_to_pixel(s_frame, wave_rect)
            e_px = self._frame_to_pixel(e_frame, wave_rect)

            # Dim regions outside S/E
            if s_px > wave_rect.x:
                dim = pygame.Surface((min(s_px - wave_rect.x, wave_rect.width), wave_rect.height), pygame.SRCALPHA)
                dim.fill((0, 0, 0, 100))
                surface.blit(dim, (wave_rect.x, wave_rect.y))
            if e_px < wave_rect.right:
                dim_w = wave_rect.right - max(e_px, wave_rect.x)
                if dim_w > 0:
                    dim = pygame.Surface((dim_w, wave_rect.height), pygame.SRCALPHA)
                    dim.fill((0, 0, 0, 100))
                    surface.blit(dim, (max(e_px, wave_rect.x), wave_rect.y))

            # S marker (green)
            if wave_rect.x <= s_px <= wave_rect.right:
                pygame.draw.line(surface, theme.GREEN,
                                (s_px, wave_rect.y), (s_px, wave_rect.bottom), 2)
                s_label = f_small.render("S", True, theme.BG)
                s_bg = pygame.Rect(s_px - 1, wave_rect.bottom - 18, 16, 16)
                pygame.draw.rect(surface, theme.GREEN, s_bg, border_radius=2)
                surface.blit(s_label, (s_px + 2, wave_rect.bottom - 17))

            # E marker (yellow)
            if wave_rect.x <= e_px <= wave_rect.right:
                pygame.draw.line(surface, theme.YELLOW,
                                (e_px, wave_rect.y), (e_px, wave_rect.bottom), 2)
                e_label = f_small.render("E", True, theme.BG)
                e_bg = pygame.Rect(e_px - 15, wave_rect.bottom - 18, 16, 16)
                pygame.draw.rect(surface, theme.YELLOW, e_bg, border_radius=2)
                surface.blit(e_label, (e_px - 13, wave_rect.bottom - 17))

            surface.set_clip(clip)

        # Center line
        pygame.draw.line(surface, theme.BORDER,
                        (wave_rect.x, wave_rect.centery),
                        (wave_rect.right, wave_rect.centery), 1)

        # ── Zoom buttons (stacked on right) ──────────────────────────
        zoom_in_rect = pygame.Rect(zx, 42, 60, 36)
        zoom_out_rect = pygame.Rect(zx, 82, 60, 36)
        zoom_fit_rect = pygame.Rect(zx, 122, 60, 36)

        for rect, label in [(zoom_in_rect, "+"), (zoom_out_rect, "-"), (zoom_fit_rect, "FIT")]:
            pygame.draw.rect(surface, theme.BUTTON_BG, rect, border_radius=4)
            surf = f_med.render(label, True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Mode toggle: SLICE vs S/E
        se_btn = pygame.Rect(zx, 162, 60, 36)
        if self._place_mode == "trim":
            se_bg = theme.GREEN
            se_label = "S / E"
        else:
            se_bg = theme.BUTTON_BG
            se_label = "SLICE"
        pygame.draw.rect(surface, se_bg, se_btn, border_radius=4)
        se_tc = theme.BG if self._place_mode == "trim" else theme.TEXT_DIM
        surf = f_small.render(se_label, True, se_tc)
        surface.blit(surf, surf.get_rect(center=se_btn.center))

        # Zoom level indicator
        if self._zoom > 1.0:
            surf = f_small.render(f"{self._zoom:.0f}x", True, theme.ACCENT)
            surface.blit(surf, (zx + 12, 202))
            bar_w = 56
            bar_h = 8
            pygame.draw.rect(surface, (30, 30, 38),
                            (zx + 2, 218, bar_w, bar_h), border_radius=2)
            thumb_w = max(4, int(bar_w / self._zoom))
            thumb_x = zx + 2 + int(self._zoom_offset * (bar_w - thumb_w))
            pygame.draw.rect(surface, theme.ACCENT,
                            (thumb_x, 218, thumb_w, bar_h), border_radius=2)

        # ── Info bar (y=302): S/E times + marker/slice count + sample info ──
        s_time = self._slicer.start_frame / self._slicer.sample_rate
        e_time = self._slicer.end_frame / self._slicer.sample_rate
        sel_dur = e_time - s_time
        slices = self._slicer.get_slices()
        sample_info = self._slicer.get_info()

        # Info bar
        info_y = 304
        info_text = (f"S:{s_time:.2f}s  E:{e_time:.2f}s  ({sel_dur:.2f}s)"
                     f"   {len(self._slicer.markers)} markers  {len(slices)} slices")
        surf = f_small.render(info_text, True, theme.TEXT_DIM)
        surface.blit(surf, (16, info_y))

        # Sample info (right side of info bar)
        if sample_info:
            ch_text = "mono" if sample_info["channels"] == 1 else "stereo"
            si_text = f'{sample_info["sample_rate"]}Hz {ch_text} peak:{sample_info["peak_db"]}dB'
            surf = f_tiny.render(si_text, True, theme.TEXT_DIM)
            surface.blit(surf, (theme.SCREEN_WIDTH - surf.get_width() - 16, info_y + 2))

        # ── TOOLBAR ROW 1 (y=320): slice division + transient ────────
        r1y = 320
        bh = 24
        # /2 /4 /8 /16 CLEAR
        for j, n in enumerate([2, 4, 8, 16]):
            rect = pygame.Rect(16 + j * 56, r1y, 52, bh)
            pygame.draw.rect(surface, theme.BUTTON_BG, rect, border_radius=4)
            surf = f_small.render(f"/{n}", True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=rect.center))

        clear_rect = pygame.Rect(16 + 4 * 56, r1y, 52, bh)
        pygame.draw.rect(surface, theme.BUTTON_BG, clear_rect, border_radius=4)
        surf = f_small.render("CLR", True, theme.RED)
        surface.blit(surf, surf.get_rect(center=clear_rect.center))

        # TRANSIENT + sensitivity
        trans_rect = pygame.Rect(16 + 5 * 56, r1y, 80, bh)
        pygame.draw.rect(surface, theme.YELLOW, trans_rect, border_radius=4)
        surf = f_tiny.render("TRANSIENT", True, theme.BG)
        surface.blit(surf, surf.get_rect(center=trans_rect.center))

        sx = 16 + 5 * 56 + 84
        sens_down = pygame.Rect(sx, r1y, 24, bh)
        pygame.draw.rect(surface, theme.BUTTON_BG, sens_down, border_radius=3)
        surf = f_small.render("-", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=sens_down.center))
        surf = f_tiny.render(f"{self._transient_sens:.1f}", True, theme.ACCENT)
        surface.blit(surf, (sx + 28, r1y + 5))
        sens_up = pygame.Rect(sx + 50, r1y, 24, bh)
        pygame.draw.rect(surface, theme.BUTTON_BG, sens_up, border_radius=3)
        surf = f_small.render("+", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=sens_up.center))

        # Status text
        import time
        if self._slicer_status and time.monotonic() - self._slicer_status_time < 5.0:
            surf = f_tiny.render(self._slicer_status, True, theme.GREEN)
            surface.blit(surf, (sx + 80, r1y + 5))

        # ── TOOLBAR ROW 2 (y=348): edit + export ─────────────────────
        r2y = 348
        edit_btns = [
            (pygame.Rect(16, r2y, 56, bh), "TRIM", theme.ACCENT),
            (pygame.Rect(76, r2y, 56, bh), "NORM", theme.ACCENT),
            (pygame.Rect(136, r2y, 56, bh), "MONO",
             theme.GREEN if self._slicer.channels == 1 else theme.ACCENT),
            (pygame.Rect(196, r2y, 56, bh), "UNDO",
             theme.ACCENT if self._slicer.can_undo else theme.BUTTON_BG),
        ]
        for rect, label, bg in edit_btns:
            tc = theme.BG if bg in (theme.GREEN, theme.ACCENT) else theme.TEXT_DIM
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_tiny.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Right side: BUILD KIT, EXPORT, TRANSFER
        rx = theme.SCREEN_WIDTH - 16
        dev_name = self.app.device_name

        # Transfer button (device-agnostic)
        tw = 100
        transfer_rect = pygame.Rect(rx - tw, r2y, tw, bh)
        if self._transfer_flash > 0:
            t_bg, t_text, t_tc = theme.GREEN, "DONE!", theme.BG
        elif self._p6_mounted:
            t_bg, t_text, t_tc = theme.ACCENT, f"TO {dev_name}", theme.BG
        else:
            t_bg, t_text, t_tc = theme.BUTTON_BG, f"TO {dev_name}", theme.TEXT_DIM
        pygame.draw.rect(surface, t_bg, transfer_rect, border_radius=4)
        surf = f_tiny.render(t_text, True, t_tc)
        surface.blit(surf, surf.get_rect(center=transfer_rect.center))
        rx -= tw + 4

        # Export
        ew = 80
        export_rect = pygame.Rect(rx - ew, r2y, ew, bh)
        e_bg = theme.GREEN if self._export_flash > 0 else theme.ACCENT
        e_text = "EXPORTED!" if self._export_flash > 0 else "EXPORT"
        pygame.draw.rect(surface, e_bg, export_rect, border_radius=4)
        surf = f_tiny.render(e_text, True, theme.BG)
        surface.blit(surf, surf.get_rect(center=export_rect.center))
        rx -= ew + 4

        # BUILD KIT (always visible when slices exist)
        slices = self._slicer.get_slices()
        if len(slices) > 1:  # More than 1 slice means markers exist
            kw = 90
            kit_rect = pygame.Rect(rx - kw, r2y, kw, bh)
            bg = theme.GREEN if self._last_exported_slices else theme.BLUE
            pygame.draw.rect(surface, bg, kit_rect, border_radius=4)
            label = "BUILD KIT" if not self._last_exported_slices else "KIT >"
            surf = f_tiny.render(label, True, theme.BG)
            surface.blit(surf, surf.get_rect(center=kit_rect.center))

        # ── SLICE LIST ────────────────────────────────────────────────
        list_y = 390  # Below toolbar (348+24=372) + label (14px) + gap
        list_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - list_y - 2

        # Update slice list items
        if not hasattr(self, "_slice_touch_list"):
            from ui.components.touch_list import TouchList
            self._slice_touch_list = TouchList(
                pygame.Rect(16, list_y, theme.SCREEN_WIDTH - 32, list_h),
                item_height=40,
            )

        self._slice_touch_list.set_rect(pygame.Rect(16, list_y, theme.SCREEN_WIDTH - 32, list_h))

        # Rebuild slice items
        from ui.components.touch_list import TouchListItem
        slice_items = []
        for i, (start, end, start_s, end_s) in enumerate(slices):
            dur = end_s - start_s
            slice_items.append(TouchListItem(
                text=f"Slice {i + 1:2d}",
                subtext=f"{start_s:.2f}s — {end_s:.2f}s  ({dur:.2f}s)",
                icon=str(i + 1),
                icon_color=theme.ACCENT,
                data={"index": i, "start": start, "end": end},
            ))
        if len(slice_items) != len(self._slice_touch_list.items):
            saved = self._slice_touch_list.scroll_offset
            self._slice_touch_list.set_items(slice_items)
            self._slice_touch_list.scroll_offset = saved

        # Slice list label
        surf = f_tiny.render(f"SLICES ({len(slices)})  tap to preview", True, theme.TEXT_DIM)
        surface.blit(surf, (16, 375))

        self._slice_touch_list.draw(surface)

        if not slices and self._slicer.loaded:
            surf = f_small.render("Tap waveform to add slice markers", True, theme.TEXT_DIM)
            cy = list_y + list_h // 2
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, centery=cy))

    # ── CONVERT mode ──────────────────────────────────────────────────

    def _refresh_convert_recordings(self):
        """Load recording list from recorder."""
        try:
            self._convert_recordings = self.app.recorder.list_recordings()
        except Exception:
            self._convert_recordings = []
        self._convert_scroll = 0

    def _generate_kit_name(self) -> str:
        """Auto-generate kit name from date + count."""
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        return f"kit_{stamp}"

    def _draw_convert(self, surface, f_large, f_med, f_small):
        """Draw the CONVERT mode UI."""

        # ── Title label (y=42) ───────────────────────────────────────
        surf = f_med.render("Select recordings to convert", True, theme.TEXT)
        surface.blit(surf, (16, 42))

        # ── Recording list (y=60-300, rows 26px, max 9 visible) ─────
        list_y = 60
        item_h = 26
        max_visible = 9
        visible = self._convert_recordings[self._convert_scroll:
                                           self._convert_scroll + max_visible]

        for i, rec in enumerate(visible):
            row_rect = pygame.Rect(16, list_y + i * item_h,
                                   theme.SCREEN_WIDTH - 32, item_h - 2)
            path = rec.get("path", "")
            selected = path in self._convert_selected

            # Background
            if selected:
                pygame.draw.rect(surface, theme.ACCENT_DIM, row_rect, border_radius=2)
            elif i % 2 == 1:
                pygame.draw.rect(surface, theme.BG_LIGHTER, row_rect, border_radius=2)

            # Checkbox indicator
            cb_rect = pygame.Rect(row_rect.x + 4, row_rect.y + 4, 16, 16)
            if selected:
                pygame.draw.rect(surface, theme.ACCENT, cb_rect, border_radius=3)
                surf = f_small.render("x", True, theme.BG)
                surface.blit(surf, surf.get_rect(center=cb_rect.center))
            else:
                pygame.draw.rect(surface, theme.BORDER, cb_rect, 1, border_radius=3)

            # Filename
            fname = rec.get("filename", "???")
            surf = f_small.render(fname, True, theme.TEXT)
            surface.blit(surf, (row_rect.x + 26, row_rect.y + 5))

            # Duration + size on right
            dur = rec.get("duration", 0)
            size = rec.get("size_mb", 0)
            info = f"{dur:.1f}s  {size:.1f}MB"
            surf = f_small.render(info, True, theme.TEXT_DIM)
            surface.blit(surf, (row_rect.right - surf.get_width() - 8, row_rect.y + 5))

        if not self._convert_recordings:
            surf = f_med.render("No recordings found", True, theme.TEXT_DIM)
            sr = surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=list_y + 40)
            surface.blit(surf, sr)
            hint = f_small.render("Record some audio first, then come back here",
                                  True, theme.TEXT_DIM)
            hr = hint.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=list_y + 68)
            surface.blit(hint, hr)

        # Scroll indicator
        total = len(self._convert_recordings)
        if total > max_visible:
            shown_end = min(self._convert_scroll + max_visible, total)
            surf = f_small.render(
                f"{self._convert_scroll + 1}-{shown_end}/{total}",
                True, theme.TEXT_DIM)
            surface.blit(surf, (theme.SCREEN_WIDTH - surf.get_width() - 20, 42))

        # ── Selection summary (y=302) ────────────────────────────────
        sel_count = len(self._convert_selected)
        total_dur = sum(r.get("duration", 0)
                        for r in self._convert_recordings
                        if r.get("path", "") in self._convert_selected)
        summary = f"Selected: {sel_count} files  |  Total: {total_dur:.1f}s"
        surf = f_small.render(summary, True,
                              theme.ACCENT if sel_count > 0 else theme.TEXT_DIM)
        surface.blit(surf, (16, 302))

        # Select all / deselect all buttons
        sel_all_rect = pygame.Rect(400, 298, 80, 24)
        desel_rect = pygame.Rect(486, 298, 80, 24)
        pygame.draw.rect(surface, theme.BUTTON_BG, sel_all_rect, border_radius=4)
        surf = f_small.render("ALL", True, theme.ACCENT)
        surface.blit(surf, surf.get_rect(center=sel_all_rect.center))
        pygame.draw.rect(surface, theme.BUTTON_BG, desel_rect, border_radius=4)
        surf = f_small.render("NONE", True, theme.TEXT_DIM)
        surface.blit(surf, surf.get_rect(center=desel_rect.center))

        # ── Target format buttons (y=330) ────────────────────────────
        surf = f_small.render("Target:", True, theme.TEXT_DIM)
        surface.blit(surf, (16, 336))
        targets = [
            (pygame.Rect(80, 330, 100, 28), "sp404", "SP-404"),
            (pygame.Rect(186, 330, 80, 28), "p6", "P-6"),
            (pygame.Rect(272, 330, 110, 28), "mpc", "AKAI MPC"),
        ]
        for rect, tid, label in targets:
            active = self._convert_target == tid
            bg = theme.ACCENT if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # ── Kit name (y=366) ─────────────────────────────────────────
        kit_label = self._convert_kit_name or self._generate_kit_name()
        surf = f_small.render("Kit:", True, theme.TEXT_DIM)
        surface.blit(surf, (16, 372))
        name_rect = pygame.Rect(50, 366, 240, 26)
        pygame.draw.rect(surface, theme.BG_LIGHTER, name_rect, border_radius=4)
        pygame.draw.rect(surface, theme.BORDER, name_rect, 1, border_radius=4)
        surf = f_small.render(kit_label, True, theme.TEXT)
        surface.blit(surf, (56, 372))

        # ── CONVERT button (y=400) ───────────────────────────────────
        convert_rect = pygame.Rect(16, 398, 130, 32)
        if self._convert_busy:
            c_bg, c_text = theme.BUTTON_BG, "CONVERTING..."
            c_tc = theme.YELLOW
        elif sel_count > 0:
            c_bg, c_text = theme.ACCENT, "CONVERT"
            c_tc = theme.BG
        else:
            c_bg, c_text = theme.BUTTON_BG, "CONVERT"
            c_tc = theme.TEXT_DIM
        pygame.draw.rect(surface, c_bg, convert_rect, border_radius=6)
        surf = f_med.render(c_text, True, c_tc)
        surface.blit(surf, surf.get_rect(center=convert_rect.center))

        # ── Status (y=436) ───────────────────────────────────────────
        if self._convert_status:
            is_err = "error" in self._convert_status.lower()
            is_done = "done" in self._convert_status.lower()
            sc = theme.RED if is_err else (theme.GREEN if is_done else theme.YELLOW)
            surf = f_small.render(self._convert_status, True, sc)
            surface.blit(surf, (16, 436))

        # ── TRANSFER TO DEVICE button (y=398, right side) ────────────
        transfer_rect = pygame.Rect(theme.SCREEN_WIDTH - 200, 398, 180, 32)
        has_result = bool(self._convert_result_dir
                          and os.path.isdir(self._convert_result_dir))
        if has_result:
            t_bg = theme.ACCENT
            t_tc = theme.BG
        else:
            t_bg = theme.BUTTON_BG
            t_tc = theme.TEXT_DIM
        pygame.draw.rect(surface, t_bg, transfer_rect, border_radius=6)
        surf = f_small.render("TRANSFER TO DEVICE", True, t_tc)
        surface.blit(surf, surf.get_rect(center=transfer_rect.center))

        # ── KIT BUILDER button (y=366, right of kit name) ────────────
        kit_builder_rect = pygame.Rect(310, 366, 120, 26)
        pygame.draw.rect(surface, theme.ACCENT, kit_builder_rect, border_radius=4)
        surf = f_small.render("KIT BUILDER", True, theme.BG)
        surface.blit(surf, surf.get_rect(center=kit_builder_rect.center))

        # ── Hint (y=456) ────────────────────────────────────────────
        surf = f_small.render(
            "Tap rows to select  |  Pick target format  |  CONVERT  |  TRANSFER",
            True, theme.TEXT_DIM)
        surface.blit(surf, (16, 456))

    def _handle_convert(self, mx, my):
        """Handle taps in CONVERT mode."""

        # ── Recording list taps (y=60-294, rows 26px, max 9) ─────────
        list_y = 60
        item_h = 26
        max_visible = 9
        visible = self._convert_recordings[self._convert_scroll:
                                           self._convert_scroll + max_visible]

        for i, rec in enumerate(visible):
            row_rect = pygame.Rect(16, list_y + i * item_h,
                                   theme.SCREEN_WIDTH - 32, item_h - 2)
            if row_rect.collidepoint(mx, my):
                path = rec.get("path", "")
                if path in self._convert_selected:
                    self._convert_selected.discard(path)
                else:
                    self._convert_selected.add(path)
                return

        # ── Select all / deselect all ────────────────────────────────
        sel_all_rect = pygame.Rect(400, 298, 80, 24)
        desel_rect = pygame.Rect(486, 298, 80, 24)
        if sel_all_rect.collidepoint(mx, my):
            for rec in self._convert_recordings:
                self._convert_selected.add(rec.get("path", ""))
            return
        if desel_rect.collidepoint(mx, my):
            self._convert_selected.clear()
            return

        # ── Target format buttons (y=330) ────────────────────────────
        targets = [
            (pygame.Rect(80, 330, 100, 28), "sp404"),
            (pygame.Rect(186, 330, 80, 28), "p6"),
            (pygame.Rect(272, 330, 110, 28), "mpc"),
        ]
        for rect, tid in targets:
            if rect.collidepoint(mx, my):
                self._convert_target = tid
                return

        # ── KIT BUILDER button (y=366, right of kit name) ────────────
        kit_builder_rect = pygame.Rect(310, 366, 120, 26)
        if kit_builder_rect.collidepoint(mx, my):
            self.app.switch_screen("kit")
            return

        # ── CONVERT button (y=398) ───────────────────────────────────
        convert_rect = pygame.Rect(16, 398, 130, 32)
        if convert_rect.collidepoint(mx, my):
            if self._convert_busy or not self._convert_selected:
                return
            self._run_conversion()
            return

        # ── TRANSFER TO DEVICE (y=398 right) ─────────────────────────
        transfer_rect = pygame.Rect(theme.SCREEN_WIDTH - 200, 398, 180, 32)
        if transfer_rect.collidepoint(mx, my):
            if self._convert_result_dir and os.path.isdir(self._convert_result_dir):
                self._transfer_converted()
            return

    def _run_conversion(self):
        """Start format conversion in a background thread."""
        kit_name = self._convert_kit_name or self._generate_kit_name()
        self._convert_kit_name = kit_name
        sessions_dir = self.app.config.get("P6_SESSIONS_DIR", "sessions")
        output_dir = os.path.join(sessions_dir, "converted", kit_name)
        os.makedirs(output_dir, exist_ok=True)

        recordings = sorted(self._convert_selected)
        target = self._convert_target
        self._convert_busy = True
        self._convert_status = f"Converting {len(recordings)} files to {target}..."

        def worker():
            try:
                result = convert_recordings_to_kit(
                    recordings, kit_name, output_dir, target)
                if result:
                    self._convert_result_dir = output_dir
                    self._convert_status = (
                        f"Done! {len(recordings)} files converted to {output_dir}")
                else:
                    self._convert_status = "Error: conversion returned no output"
            except Exception as e:
                self._convert_status = f"Error: {e}"
            finally:
                self._convert_busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _transfer_converted(self):
        """Copy converted output to connected device USB mount."""
        import shutil

        # Determine device mount path
        if self._convert_target == "p6" and os.path.isdir(P6_MOUNT_PATH):
            dest = os.path.join(P6_MOUNT_PATH, "SAMPLE")
        elif self._convert_target == "sp404":
            for mount in ["/media/pi/SP-404MKII", "/media/pi/SP404MKII"]:
                if os.path.isdir(mount):
                    dest = os.path.join(mount, "IMPORT")
                    break
            else:
                self._convert_status = "Error: SP-404 not connected"
                return
        elif self._convert_target == "mpc":
            # MPC/Force: look for common mount points
            for mount in ["/media/pi/FORCE", "/media/pi/MPC"]:
                if os.path.isdir(mount):
                    dest = mount
                    break
            else:
                self._convert_status = "Error: MPC/Force not connected"
                return
        else:
            self._convert_status = "Error: device not connected"
            return

        os.makedirs(dest, exist_ok=True)
        src = self._convert_result_dir
        count = 0
        try:
            for fname in os.listdir(src):
                s = os.path.join(src, fname)
                d = os.path.join(dest, fname)
                if os.path.isfile(s):
                    shutil.copy2(s, d)
                    count += 1
            self._convert_status = f"Transferred {count} files to {dest}"
        except Exception as e:
            self._convert_status = f"Transfer error: {e}"
