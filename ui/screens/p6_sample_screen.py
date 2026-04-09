"""P-6 Sample Screen — file browser + visual waveform slicer.

Two modes:
  BROWSE: File browser for navigating local sample library + recordings
  SLICER: Visual waveform with slice markers, preview, export, P-6 transfer
"""

import os
import pygame
import numpy as np
from .. import theme
from engine.sample_slicer import SampleSlicer, P6_MOUNT_PATH


class P6SampleScreen:
    """Dual-mode sample browser and waveform slicer."""

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
        self._p6_mounted = False

        # Waveform zoom
        self._zoom = 1.0
        self._zoom_offset = 0.0

        # Start/End trim marker dragging
        self._dragging_marker = None  # "start", "end", or None
        self._place_mode = "slice"    # "slice" (add slice markers) or "trim" (set S/E)

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
            browse_btn = pygame.Rect(16, 6, 100, 30)
            slicer_btn = pygame.Rect(126, 6, 100, 30)
            if browse_btn.collidepoint(mx, my):
                self._mode = "browse"
                return
            if slicer_btn.collidepoint(mx, my):
                self._mode = "slicer"
                return

            if self._mode == "browse":
                self._handle_browse(mx, my)
            else:
                self._handle_slicer(mx, my)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            mx_pos = event.pos[0] if hasattr(event, 'pos') else 0
            my_pos = event.pos[1] if hasattr(event, 'pos') else 0
            if self._mode == "browse":
                max_s = max(0, len(self._file_list) - 14)
                if event.button == 4:
                    self._file_scroll = max(0, self._file_scroll - 1)
                else:
                    self._file_scroll = min(max_s, self._file_scroll + 1)
            else:
                # Scroll on waveform = zoom, scroll on list = scroll list
                wave_rect = pygame.Rect(16, 44, 730, 190)
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
        zoom_x = theme.SCREEN_WIDTH - 80
        return pygame.Rect(16, 44, zoom_x - 20, 190)

    def _handle_slicer(self, mx, my):
        if not self._slicer.loaded:
            return

        # Zoom buttons FIRST
        zoom_x = theme.SCREEN_WIDTH - 80
        zoom_in_rect = pygame.Rect(zoom_x, 44, 60, 36)
        zoom_out_rect = pygame.Rect(zoom_x, 86, 60, 36)
        zoom_fit_rect = pygame.Rect(zoom_x, 128, 60, 36)
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

        # S/E mode toggle button
        se_btn = pygame.Rect(zoom_x, 176, 60, 28)
        if se_btn.collidepoint(mx, my):
            self._place_mode = "trim" if self._place_mode == "slice" else "slice"
            return

        # Waveform click
        wave_rect = self._get_wave_rect()
        if wave_rect.collidepoint(mx, my):
            frame = self._pixel_to_frame(mx, wave_rect)

            if self._place_mode == "trim":
                # In trim mode: left half = set start, right half = set end
                mid = (self._slicer.start_frame + self._slicer.end_frame) // 2
                if frame < mid:
                    self._slicer.set_start(frame, snap_zero=True)
                    self._dragging_marker = "start"
                else:
                    self._slicer.set_end(frame, snap_zero=True)
                    self._dragging_marker = "end"
            else:
                # Slice mode: add/remove slice markers
                tolerance = int(15 / wave_rect.width / self._zoom * self._slicer.total_frames)
                if not self._slicer.remove_nearest_marker(frame, tolerance):
                    self._slicer.add_marker(frame, snap_zero=True)
            return

        list_y = 250
        slices = self._slicer.get_slices()
        for i in range(min(4, len(slices) - self._slice_scroll)):
            row_rect = pygame.Rect(16, list_y + i * 28, 500, 26)
            if row_rect.collidepoint(mx, my):
                self._slicer.preview_slice(self._slice_scroll + i)
                return

        btn_y = 365
        for j, n in enumerate([2, 4, 8, 16]):
            rect = pygame.Rect(16 + j * 90, btn_y, 80, 34)
            if rect.collidepoint(mx, my):
                self._slicer.auto_slice(n)
                return

        clear_rect = pygame.Rect(16 + 4 * 90, btn_y, 80, 34)
        if clear_rect.collidepoint(mx, my):
            self._slicer.clear_markers()
            return

        export_rect = pygame.Rect(theme.SCREEN_WIDTH - 250, btn_y, 110, 34)
        if export_rect.collidepoint(mx, my):
            if self._slicer.export_slices(normalize=True):
                self._export_flash = 30
            return

        transfer_rect = pygame.Rect(theme.SCREEN_WIDTH - 130, btn_y, 110, 34)
        if transfer_rect.collidepoint(mx, my):
            if self._slicer.transfer_to_p6() > 0:
                self._transfer_flash = 30
            return

        # Edit toolbar row — positions must match _draw_slicer exactly
        edit_y = 340
        edit_actions = [
            (pygame.Rect(60, edit_y, 70, 28), "TRIM"),
            (pygame.Rect(136, edit_y, 70, 28), "NORM"),
            (pygame.Rect(212, edit_y, 70, 28), "MONO"),
            (pygame.Rect(288, edit_y, 70, 28), "UNDO"),
            (pygame.Rect(380, edit_y, 60, 28), "22k"),
            (pygame.Rect(446, edit_y, 60, 28), "14k"),
            (pygame.Rect(512, edit_y, 60, 28), "11k"),
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

        # (Zoom buttons handled at top of _handle_slicer)

    def _zoom_in(self, mouse_x: int = 400):
        """Zoom into the waveform, centered on mouse position."""
        if self._zoom >= 32.0:
            return
        wave_rect = pygame.Rect(16, 44, 730, 190)
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
        if self._export_flash > 0:
            self._export_flash -= 1
        if self._transfer_flash > 0:
            self._transfer_flash -= 1

    def draw(self, surface: pygame.Surface):
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")

        # Mode toggles
        for rect, label, active in [
            (pygame.Rect(16, 6, 100, 30), "BROWSE", self._mode == "browse"),
            (pygame.Rect(126, 6, 100, 30), "SLICER", self._mode == "slicer"),
        ]:
            bg = theme.ACCENT if active else theme.BUTTON_BG
            tc = theme.BG if active else theme.TEXT
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # P-6 mount
        self._p6_mounted = os.path.isdir(P6_MOUNT_PATH)
        mt = "P-6 USB: READY" if self._p6_mounted else "P-6 USB: NOT MOUNTED"
        mc = theme.GREEN if self._p6_mounted else theme.TEXT_DIM
        surf = f_small.render(mt, True, mc)
        surface.blit(surf, (theme.SCREEN_WIDTH - surf.get_width() - 16, 14))

        if self._mode == "browse":
            self._draw_browse(surface, f_med, f_small)
        else:
            self._draw_slicer(surface, f_large, f_med, f_small)

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

            icon = "/" if item["is_dir"] else " "
            color = theme.ACCENT if item["is_dir"] else theme.TEXT
            surf = f_small.render(f"{icon} {item['name']}", True, color)
            surface.blit(surf, (20, list_y + i * item_h + 3))

        if not self._file_list:
            surf = f_small.render("No files found", True, theme.TEXT_DIM)
            surface.blit(surf, (16, list_y))

        surf = f_small.render("Tap WAV to load into slicer", True, theme.TEXT_DIM)
        surface.blit(surf, (16, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 18))

    def _draw_slicer(self, surface, f_large, f_med, f_small):
        if not self._slicer.loaded:
            surf = f_med.render("No file loaded -- tap BROWSE and select a WAV", True, theme.TEXT_DIM)
            surface.blit(surf, (16, 100))
            return

        info = f"{self._slicer.filename}  |  {self._slicer.duration_secs:.1f}s  |  {self._slicer.sample_rate}Hz"
        surf = f_small.render(info, True, theme.TEXT)
        surface.blit(surf, (240, 14))

        # Waveform (leave room for zoom buttons on right)
        zoom_x = theme.SCREEN_WIDTH - 80
        wave_rect = pygame.Rect(16, 44, zoom_x - 20, 190)
        pygame.draw.rect(surface, theme.WAVEFORM_BG, wave_rect, border_radius=4)

        waveform = self._slicer.waveform
        if waveform is not None:
            max_val = max(float(np.max(waveform)), 0.001)
            total_frames = self._slicer.total_frames

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
                # Map pixel to waveform index
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

            # ── Start/End trim markers (always visible) ────────────
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

            # S marker (green line + label)
            if wave_rect.x <= s_px <= wave_rect.right:
                pygame.draw.line(surface, theme.GREEN,
                                (s_px, wave_rect.y), (s_px, wave_rect.bottom), 2)
                s_label = f_small.render("S", True, theme.BG)
                s_bg = pygame.Rect(s_px - 1, wave_rect.bottom - 18, 16, 16)
                pygame.draw.rect(surface, theme.GREEN, s_bg, border_radius=2)
                surface.blit(s_label, (s_px + 2, wave_rect.bottom - 17))

            # E marker (yellow line + label)
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

        # ── Zoom buttons + mode toggle (right of waveform) ──────
        zoom_x = theme.SCREEN_WIDTH - 80
        zoom_in_rect = pygame.Rect(zoom_x, 44, 60, 32)
        zoom_out_rect = pygame.Rect(zoom_x, 80, 60, 32)
        zoom_fit_rect = pygame.Rect(zoom_x, 116, 60, 32)

        for rect, label in [(zoom_in_rect, "+"), (zoom_out_rect, "-"), (zoom_fit_rect, "FIT")]:
            pygame.draw.rect(surface, theme.BUTTON_BG, rect, border_radius=4)
            surf = f_med.render(label, True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Zoom level
        if self._zoom > 1.0:
            surf = f_small.render(f"{self._zoom:.0f}x", True, theme.ACCENT)
            surface.blit(surf, (zoom_x + 8, 150))

        # Mode toggle: SLICE vs S/E
        se_btn = pygame.Rect(zoom_x, 176, 60, 28)
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

        # S/E time display
        s_time = self._slicer.start_frame / self._slicer.sample_rate
        e_time = self._slicer.end_frame / self._slicer.sample_rate
        sel_dur = e_time - s_time
        surf = f_small.render(f"S:{s_time:.2f}s  E:{e_time:.2f}s  ({sel_dur:.2f}s)",
                             True, theme.TEXT_DIM)
        surface.blit(surf, (zoom_x - 240, wave_rect.bottom + 2))

        # Scroll position indicator when zoomed
        if self._zoom > 1.0:
            bar_y = 210
            bar_w = 60
            bar_h = 20
            pygame.draw.rect(surface, (30, 30, 38),
                            (zoom_x, bar_y, bar_w, bar_h), border_radius=2)
            thumb_w = max(4, int(bar_w / self._zoom))
            thumb_x = zoom_x + int(self._zoom_offset * (bar_w - thumb_w))
            pygame.draw.rect(surface, theme.ACCENT,
                            (thumb_x, bar_y, thumb_w, bar_h), border_radius=2)

        # Slice list
        y = 240
        slices = self._slicer.get_slices()
        surf = f_small.render(f"{len(self._slicer.markers)} markers  |  {len(slices)} slices",
                             True, theme.TEXT_DIM)
        surface.blit(surf, (16, y))
        y += 18

        for i in range(min(4, max(0, len(slices) - self._slice_scroll))):
            real_idx = self._slice_scroll + i
            start, end, start_s, end_s = slices[real_idx]
            dur = end_s - start_s
            text = f"Slice {real_idx + 1:2d}:  {start_s:.2f}s - {end_s:.2f}s  ({dur:.2f}s)"
            surf = f_small.render(text, True, theme.TEXT)
            surface.blit(surf, (20, y + 4))
            y += 28

        if not slices and self._slicer.loaded:
            surf = f_small.render("Tap waveform to add slice markers", True, theme.TEXT_DIM)
            surface.blit(surf, (20, y + 4))

        # ── Edit toolbar ─────────────────────────────────────────────
        edit_y = 340
        surf = f_small.render("EDIT:", True, theme.TEXT_DIM)
        surface.blit(surf, (16, edit_y + 5))

        edit_btns = [
            (pygame.Rect(60, edit_y, 70, 28), "TRIM", theme.ACCENT),
            (pygame.Rect(136, edit_y, 70, 28), "NORM", theme.ACCENT),
            (pygame.Rect(212, edit_y, 70, 28), "MONO",
             theme.GREEN if self._slicer.channels == 1 else theme.ACCENT),
            (pygame.Rect(288, edit_y, 70, 28), "UNDO",
             theme.ACCENT if self._slicer.can_undo else theme.BUTTON_BG),
            (pygame.Rect(380, edit_y, 60, 28), "22k", theme.BUTTON_BG),
            (pygame.Rect(446, edit_y, 60, 28), "14k", theme.BUTTON_BG),
            (pygame.Rect(512, edit_y, 60, 28), "11k", theme.BUTTON_BG),
        ]
        for rect, label, bg in edit_btns:
            tc = theme.BG if bg == theme.GREEN else (theme.BG if bg == theme.ACCENT else theme.TEXT_DIM)
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            surf = f_small.render(label, True, tc)
            surface.blit(surf, surf.get_rect(center=rect.center))

        # Sample info line
        info = self._slicer.get_info()
        if info:
            ch_text = "mono" if info["channels"] == 1 else "stereo"
            info_text = (f'{info["sample_rate"]}Hz  {ch_text}  '
                        f'peak:{info["peak_db"]}dB  {info["duration"]:.1f}s')
            surf = f_small.render(info_text, True, theme.TEXT_DIM)
            surface.blit(surf, (590, edit_y + 6))

        # ── Slice action buttons ─────────────────────────────────────
        btn_y = 378
        # Auto-slice buttons
        for j, n in enumerate([2, 4, 8, 16]):
            rect = pygame.Rect(16 + j * 80, btn_y, 72, 32)
            pygame.draw.rect(surface, theme.BUTTON_BG, rect, border_radius=6)
            surf = f_med.render(f"/ {n}", True, theme.ACCENT)
            surface.blit(surf, surf.get_rect(center=rect.center))

        clear_rect = pygame.Rect(16 + 4 * 80, btn_y, 72, 32)
        pygame.draw.rect(surface, theme.BUTTON_BG, clear_rect, border_radius=6)
        surf = f_med.render("CLEAR", True, theme.RED)
        surface.blit(surf, surf.get_rect(center=clear_rect.center))

        # Export + transfer
        export_rect = pygame.Rect(theme.SCREEN_WIDTH - 250, btn_y, 110, 32)
        e_bg = theme.GREEN if self._export_flash > 0 else theme.ACCENT
        e_text = "EXPORTED!" if self._export_flash > 0 else "EXPORT"
        pygame.draw.rect(surface, e_bg, export_rect, border_radius=6)
        surf = f_med.render(e_text, True, theme.BG)
        surface.blit(surf, surf.get_rect(center=export_rect.center))

        transfer_rect = pygame.Rect(theme.SCREEN_WIDTH - 130, btn_y, 110, 32)
        if self._transfer_flash > 0:
            t_bg, t_text, t_tc = theme.GREEN, "DONE!", theme.BG
        elif self._p6_mounted:
            t_bg, t_text, t_tc = theme.ACCENT, "TO P-6", theme.BG
        else:
            t_bg, t_text, t_tc = theme.BUTTON_BG, "TO P-6", theme.TEXT_DIM
        pygame.draw.rect(surface, t_bg, transfer_rect, border_radius=6)
        surf = f_med.render(t_text, True, t_tc)
        surface.blit(surf, surf.get_rect(center=transfer_rect.center))

        # ── P-6 memory fit indicator ─────────────────────────────────
        if info:
            mem_y = btn_y + 38
            dur = info["duration"]
            from engine.sample_slicer import P6_SAMPLE_RATES
            fits = []
            for rate, max_s in sorted(P6_SAMPLE_RATES.items(), reverse=True):
                label = f"{rate//1000}k"
                if dur <= max_s:
                    fits.append((label, theme.GREEN))
                else:
                    fits.append((label, theme.RED))
            fit_text_parts = []
            fx = 16
            surf = f_small.render("P-6 fit:", True, theme.TEXT_DIM)
            surface.blit(surf, (fx, mem_y))
            fx += surf.get_width() + 6
            for label, color in fits:
                surf = f_small.render(label, True, color)
                surface.blit(surf, (fx, mem_y))
                fx += surf.get_width() + 10

        # Hint
        surf = f_small.render("Tap waveform: markers  |  TRIM: cut to selection  |  Tap slice: preview",
                             True, theme.TEXT_DIM)
        surface.blit(surf, (16, theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 18))
