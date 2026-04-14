"""Audio Player Modal — transport controls with seek, speed, reverse.

A fullscreen overlay that appears when a file is being played.
Shows waveform preview, playhead, transport buttons, and speed controls.

Usage:
    player = AudioPlayer(app)
    player.show(filepath)  # starts playback + shows UI

    # In screen handle_event / update / draw:
    if player.visible:
        if player.handle_event(event):
            return  # consumed
        player.update()
        player.draw(surface)
"""

import os
import pygame
import numpy as np
from .. import theme

try:
    import soundfile as sf
except ImportError:
    sf = None


class AudioPlayer:
    """Fullscreen audio player modal."""

    def __init__(self, app):
        self.app = app
        self.visible = False
        self._filepath: str | None = None
        self._waveform: np.ndarray | None = None  # downsampled peaks
        self._duration: float = 0.0
        self._sample_rate: int = 44100
        self._dragging_seek = False

    def show(self, filepath: str):
        """Show the player and start playback."""
        if not os.path.isfile(filepath):
            return
        self._filepath = filepath
        self._load_waveform(filepath)
        self.visible = True
        # Reset transport state
        if hasattr(self.app, 'recorder'):
            self.app.recorder.set_playback_speed(1.0)
            self.app.recorder.set_playback_reverse(False)
            self.app.recorder.play(filepath)

    def hide(self):
        """Hide the player and stop playback."""
        self.visible = False
        if hasattr(self.app, 'recorder'):
            self.app.recorder.stop_playback()

    def _load_waveform(self, filepath: str):
        """Load and downsample the file for waveform preview."""
        if sf is None:
            self._waveform = None
            return
        try:
            data, rate = sf.read(filepath, dtype="float32")
            self._sample_rate = rate
            if data.ndim > 1:
                mono = data.mean(axis=1)
            else:
                mono = data
            self._duration = len(mono) / rate
            # Downsample to ~1000 peaks
            target_points = 1000
            step = max(1, len(mono) // target_points)
            peaks = np.zeros(target_points, dtype=np.float32)
            for i in range(target_points):
                start = i * step
                end = start + step
                if start < len(mono):
                    peaks[i] = float(np.max(np.abs(mono[start:end])))
            self._waveform = peaks
        except Exception as e:
            print(f"Waveform load error: {e}", flush=True)
            self._waveform = None

    def _layout(self):
        """Return rects for all UI elements."""
        pad = 20
        w = theme.SCREEN_WIDTH - pad * 2
        h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - pad * 2
        modal = pygame.Rect(pad, pad, w, h)

        # Close button (top-right)
        close = pygame.Rect(modal.right - 44, modal.y + 8, 36, 36)

        # Waveform (top half)
        wave = pygame.Rect(modal.x + 20, modal.y + 52, modal.width - 40, h // 3)

        # Seek bar (below waveform)
        seek = pygame.Rect(wave.x, wave.bottom + 10, wave.width, 14)

        # Time display below seek
        time_rect = pygame.Rect(seek.x, seek.bottom + 4, seek.width, 16)

        # Transport buttons (big, centered)
        btn_y = time_rect.bottom + 24
        btn_size = 56
        btn_gap = 14
        total_btn_w = 5 * btn_size + 4 * btn_gap  # rwnd | pause | play/stop | ff | rev
        btn_start_x = modal.centerx - total_btn_w // 2
        btns = {}
        labels = ["RWND", "PAUSE", "STOP", "FFWD", "REV"]
        for i, label in enumerate(labels):
            btns[label.lower()] = pygame.Rect(
                btn_start_x + i * (btn_size + btn_gap), btn_y, btn_size, btn_size
            )

        # Speed slider (below buttons)
        slider_y = btn_y + btn_size + 24
        slider = pygame.Rect(modal.x + 80, slider_y, modal.width - 160, 24)

        return {
            "modal": modal,
            "close": close,
            "wave": wave,
            "seek": seek,
            "time": time_rect,
            "buttons": btns,
            "slider": slider,
        }

    def handle_event(self, event) -> bool:
        """Returns True if event was consumed."""
        if not self.visible:
            return False

        lay = self._layout()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Click outside modal → close
            if not lay["modal"].collidepoint(mx, my):
                self.hide()
                return True

            # Close button
            if lay["close"].collidepoint(mx, my):
                self.hide()
                return True

            # Seek bar drag start
            if lay["seek"].collidepoint(mx, my):
                self._dragging_seek = True
                self._do_seek(mx, lay["seek"])
                return True

            # Transport buttons
            btns = lay["buttons"]
            if btns["pause"].collidepoint(mx, my):
                self.app.recorder.toggle_playback_pause()
                return True
            if btns["stop"].collidepoint(mx, my):
                self.hide()
                return True
            if btns["rwnd"].collidepoint(mx, my):
                self.app.recorder.seek_playback_relative(-5.0)
                return True
            if btns["ffwd"].collidepoint(mx, my):
                self.app.recorder.seek_playback_relative(5.0)
                return True
            if btns["rev"].collidepoint(mx, my):
                self.app.recorder.set_playback_reverse(
                    not self.app.recorder.playback_reverse)
                return True

            # Speed slider
            if lay["slider"].collidepoint(mx, my):
                self._set_speed_from_x(mx, lay["slider"])
                return True

            return True  # Consume all clicks when visible

        if event.type == pygame.MOUSEMOTION and self._dragging_seek:
            self._do_seek(event.pos[0], lay["seek"])
            return True

        if event.type == pygame.MOUSEBUTTONUP:
            self._dragging_seek = False

        # Swallow all events when visible
        return True

    def _do_seek(self, mx: int, seek_rect: pygame.Rect):
        frac = (mx - seek_rect.x) / seek_rect.width
        frac = max(0.0, min(1.0, frac))
        if hasattr(self.app, 'recorder'):
            total = getattr(self.app.recorder, '_playback_total_frames', 0)
            self.app.recorder.seek_playback(int(frac * total))

    def _set_speed_from_x(self, mx: int, slider_rect: pygame.Rect):
        frac = (mx - slider_rect.x) / slider_rect.width
        frac = max(0.0, min(1.0, frac))
        # 0.0 → 0.25x, 0.5 → 1.0x, 1.0 → 2.0x
        if frac < 0.5:
            speed = 0.25 + (frac * 2) * 0.75  # 0.25 .. 1.0
        else:
            speed = 1.0 + ((frac - 0.5) * 2) * 1.0  # 1.0 .. 2.0
        self.app.recorder.set_playback_speed(speed)

    def update(self):
        # Auto-close if playback finished
        if self.visible and hasattr(self.app, 'recorder'):
            if not self.app.recorder.is_playing_back:
                # Keep modal open even after end so user can re-seek
                pass

    def draw(self, surface: pygame.Surface):
        if not self.visible:
            return

        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        lay = self._layout()
        modal = lay["modal"]

        # Backdrop
        backdrop = pygame.Surface((theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT), pygame.SRCALPHA)
        backdrop.fill((0, 0, 0, 180))
        surface.blit(backdrop, (0, 0))

        # Modal
        pygame.draw.rect(surface, theme.BG_PANEL, modal, border_radius=12)
        pygame.draw.rect(surface, theme.ACCENT, modal, 2, border_radius=12)

        # Title
        name = os.path.basename(self._filepath) if self._filepath else ""
        if len(name) > 60:
            name = name[:57] + "..."
        surf = f_large.render(name, True, theme.ACCENT)
        surface.blit(surf, (modal.x + 20, modal.y + 14))

        # Close button (X)
        close = lay["close"]
        pygame.draw.rect(surface, theme.BUTTON_BG, close, border_radius=6)
        surf = f_med.render("X", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=close.center))

        # Waveform
        wave_rect = lay["wave"]
        pygame.draw.rect(surface, (8, 8, 14), wave_rect, border_radius=4)
        center_y = wave_rect.centery
        half_h = (wave_rect.height - 8) // 2

        if self._waveform is not None and len(self._waveform) > 0:
            progress = self.app.recorder.playback_progress if hasattr(self.app, 'recorder') else 0.0
            playhead_x = wave_rect.x + int(progress * wave_rect.width)
            step_x = wave_rect.width / len(self._waveform)
            for i, peak in enumerate(self._waveform):
                x = wave_rect.x + int(i * step_x)
                bh = int(min(1.0, peak * 3.0) * half_h)
                color = theme.ACCENT if x <= playhead_x else (60, 60, 80)
                pygame.draw.line(surface, color, (x, center_y - bh), (x, center_y + bh))
            # Playhead line
            pygame.draw.line(surface, theme.TEXT_BRIGHT,
                             (playhead_x, wave_rect.y + 2),
                             (playhead_x, wave_rect.bottom - 2), 2)

        # Seek bar
        seek = lay["seek"]
        pygame.draw.rect(surface, theme.BG_LIGHTER, seek, border_radius=4)
        if hasattr(self.app, 'recorder'):
            progress = self.app.recorder.playback_progress
            fill_w = int(seek.width * progress)
            if fill_w > 0:
                pygame.draw.rect(surface, theme.ACCENT, (seek.x, seek.y, fill_w, seek.height), border_radius=4)
            # Handle
            hx = seek.x + fill_w
            pygame.draw.circle(surface, theme.TEXT_BRIGHT, (hx, seek.centery), 9)

        # Time display
        if hasattr(self.app, 'recorder'):
            progress = self.app.recorder.playback_progress
            pos_s = progress * self._duration
            cur = f"{int(pos_s // 60)}:{int(pos_s % 60):02d}"
            total = f"{int(self._duration // 60)}:{int(self._duration % 60):02d}"
            time_text = f"{cur}  /  {total}"
            surf = f_small.render(time_text, True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=lay["time"].centerx, top=lay["time"].y))

        # Transport buttons
        btns = lay["buttons"]
        paused = self.app.recorder.playback_paused if hasattr(self.app, 'recorder') else False
        reverse = self.app.recorder.playback_reverse if hasattr(self.app, 'recorder') else False

        button_info = [
            ("rwnd", "<<", theme.BUTTON_BG, theme.TEXT),
            ("pause", ">" if paused else "||", theme.ACCENT if paused else theme.BUTTON_BG,
             theme.BG if paused else theme.TEXT),
            ("stop", "[]", theme.RED, theme.TEXT_BRIGHT),
            ("ffwd", ">>", theme.BUTTON_BG, theme.TEXT),
            ("rev", "REV", theme.ACCENT if reverse else theme.BUTTON_BG,
             theme.BG if reverse else theme.TEXT),
        ]
        for key, label, bg, fg in button_info:
            r = btns[key]
            pygame.draw.rect(surface, bg, r, border_radius=28)
            pygame.draw.rect(surface, theme.BORDER, r, 1, border_radius=28)
            surf = f_med.render(label, True, fg)
            surface.blit(surf, surf.get_rect(center=r.center))

        # Speed slider
        slider = lay["slider"]
        pygame.draw.rect(surface, theme.BG_LIGHTER, slider, border_radius=4)
        if hasattr(self.app, 'recorder'):
            speed = self.app.recorder.playback_speed
            # Map 0.25-1.0-2.0 → 0.0-0.5-1.0
            if speed <= 1.0:
                frac = (speed - 0.25) / 1.5
            else:
                frac = 0.5 + (speed - 1.0) / 2.0
            frac = max(0.0, min(1.0, frac))
            hx = slider.x + int(frac * slider.width)
            # Center line
            pygame.draw.line(surface, theme.BORDER,
                             (slider.centerx, slider.y - 4),
                             (slider.centerx, slider.bottom + 4))
            # Handle
            pygame.draw.circle(surface, theme.ACCENT, (hx, slider.centery), 11)
            # Label
            surf = f_tiny.render(f"SPEED  {speed:.2f}x", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=slider.centerx, top=slider.bottom + 6))
