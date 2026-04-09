"""Record screen — sample recording with waveform, meters, and controls."""

import time
import math
import pygame
from .. import theme
from ..components.button import Button


class RecordScreen:
    """Audio input recording with real-time waveform, level meters, and arm/record controls."""

    MAX_RECORD_SECONDS = 30.0

    def __init__(self, app):
        self.app = app

        self.area_y = theme.HEADER_HEIGHT
        self.area_h = theme.SCREEN_HEIGHT - theme.HEADER_HEIGHT - theme.NAV_HEIGHT

        # Recording state
        self.armed = False
        self.recording = False
        self.monitoring = False
        self.record_time = 0.0
        self.threshold = 0.05     # auto-start threshold (0.0–1.0)

        # Target pad
        self.target_bank = "A"
        self.target_pad = 0       # 0-indexed

        # Waveform buffer (ring buffer of recent samples for display)
        self.waveform_points = 700  # display width in samples
        self.waveform_data = [0.0] * self.waveform_points
        self.waveform_write_pos = 0

        # Input levels (stereo)
        self.level_l = 0.0
        self.level_r = 0.0

        # Blink timer for armed state
        self._blink_timer = 0.0
        self._blink_on = True

        # Recorded audio data reference
        self._recorded_data = None

        # --- Layout ---
        # Waveform display
        wf_margin = 12
        self.wf_rect = pygame.Rect(
            wf_margin, self.area_y + 8,
            theme.SCREEN_WIDTH - wf_margin * 2, 140,
        )

        # Input meters
        meter_y = self.wf_rect.bottom + 12
        self.meter_x = 12
        self.meter_w = 30
        self.meter_h = 100
        self.meter_gap = 8

        # Info area (next to meters)
        info_x = self.meter_x + self.meter_w * 2 + self.meter_gap + 24

        # Time display position
        self.time_y = meter_y + 8
        self.info_x = info_x

        # Target pad display position
        self.target_y = meter_y + 50

        # State indicator position (big dot)
        self.indicator_x = theme.SCREEN_WIDTH - 60
        self.indicator_y = meter_y + 30

        # Threshold slider
        self.threshold_x = info_x + 280
        self.threshold_y = meter_y + 4
        self.threshold_w = 160
        self.threshold_h = 24
        self._dragging_threshold = False

        # Bottom buttons
        btn_y = self.area_y + self.area_h - 52
        btn_h = 44
        btn_gap = 8
        btn_w = (theme.SCREEN_WIDTH - 24 - btn_gap * 4) // 5

        self.arm_btn = Button(
            pygame.Rect(12, btn_y, btn_w, btn_h), "ARM",
            color=theme.BUTTON_BG, active_color=theme.RED,
            font_name="medium", toggle=True,
        )
        self.record_btn = Button(
            pygame.Rect(12 + (btn_w + btn_gap), btn_y, btn_w, btn_h), "RECORD",
            color=theme.RED, font_name="medium",
        )
        self.monitor_btn = Button(
            pygame.Rect(12 + (btn_w + btn_gap) * 2, btn_y, btn_w, btn_h), "MONITOR",
            color=theme.BUTTON_BG, active_color=theme.GREEN,
            font_name="medium", toggle=True,
        )
        self.assign_btn = Button(
            pygame.Rect(12 + (btn_w + btn_gap) * 3, btn_y, btn_w, btn_h), "ASSIGN",
            color=theme.BUTTON_BG, active_color=theme.ACCENT,
            font_name="medium",
        )
        self.cancel_btn = Button(
            pygame.Rect(12 + (btn_w + btn_gap) * 4, btn_y, btn_w, btn_h), "CANCEL",
            color=theme.BUTTON_BG, font_name="medium",
        )

        # Pad target selector buttons
        self.pad_target_buttons: list[Button] = []
        pad_sel_y = meter_y + 76
        for i in range(16):
            bx = info_x + i * 28
            btn = Button(
                pygame.Rect(bx, pad_sel_y, 26, 22),
                str(i + 1),
                color=theme.BUTTON_BG,
                active_color=theme.ACCENT,
                font_name="small",
            )
            if i == 0:
                btn.active = True
            self.pad_target_buttons.append(btn)

    def on_enter(self):
        """Reset state on entering record screen."""
        self.armed = False
        self.recording = False
        self.record_time = 0.0
        self._recorded_data = None
        self.waveform_data = [0.0] * self.waveform_points
        self.waveform_write_pos = 0
        self.arm_btn.active = False
        self.record_btn.label = "RECORD"

    def on_exit(self):
        """Stop recording on leaving."""
        self.recording = False
        self.armed = False
        self.monitoring = False

    def update(self):
        """Per-frame update."""
        now = time.monotonic()

        # Blink timer for armed/recording indicator
        if now - self._blink_timer > 0.5:
            self._blink_timer = now
            self._blink_on = not self._blink_on

        # Update recording time
        if self.recording:
            # In real usage, record_time would come from audio engine
            self.record_time += 1.0 / 30.0  # approximate at 30fps
            if self.record_time >= self.MAX_RECORD_SECONDS:
                self._stop_recording()

        # Auto-start on threshold
        if self.armed and not self.recording:
            if max(self.level_l, self.level_r) >= self.threshold:
                self._start_recording()

        # Decay meters
        self.level_l = max(0.0, self.level_l - 0.04)
        self.level_r = max(0.0, self.level_r - 0.04)

        # Simulate some waveform activity when monitoring or recording
        if hasattr(self.app, 'audio_engine') and hasattr(self.app.audio_engine, 'get_input_level'):
            lvl = self.app.audio_engine.get_input_level()
            if lvl is not None:
                self.level_l, self.level_r = lvl

    def _start_recording(self):
        """Begin recording."""
        self.recording = True
        self.armed = False
        self.record_time = 0.0
        self.record_btn.label = "STOP"
        self.arm_btn.active = False

    def _stop_recording(self):
        """Stop recording."""
        self.recording = False
        self.record_btn.label = "RECORD"

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS.d"""
        mins = int(seconds) // 60
        secs = seconds - mins * 60
        return f"{mins:02d}:{secs:04.1f}"

    def draw(self, surface: pygame.Surface):
        """Draw the record screen."""
        # Header
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, theme.HEADER_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)
        f = theme.font("large")
        title = f.render("RECORD", True, theme.TEXT_BRIGHT)
        surface.blit(title, (12, 6))
        pygame.draw.line(surface, theme.BORDER,
                        (0, theme.HEADER_HEIGHT),
                        (theme.SCREEN_WIDTH, theme.HEADER_HEIGHT))

        # Waveform display
        self._draw_waveform(surface)

        # Input level meters
        self._draw_meters(surface)

        # Info section
        self._draw_info(surface)

        # Threshold slider
        self._draw_threshold(surface)

        # Recording state indicator
        self._draw_state_indicator(surface)

        # Pad target selector
        self._draw_pad_selector(surface)

        # Bottom buttons
        self.arm_btn.active = self.armed
        self.monitor_btn.active = self.monitoring
        self.arm_btn.draw(surface)
        self.record_btn.draw(surface)
        self.monitor_btn.draw(surface)
        self.assign_btn.draw(surface)
        self.cancel_btn.draw(surface)

    def _draw_waveform(self, surface: pygame.Surface):
        """Draw the real-time scrolling waveform."""
        r = self.wf_rect

        # Background
        pygame.draw.rect(surface, theme.WAVEFORM_BG, r, border_radius=4)
        pygame.draw.rect(surface, theme.BORDER, r, 1, border_radius=4)

        # Center line
        center_y = r.centery
        pygame.draw.line(surface, theme.BORDER,
                        (r.x + 2, center_y), (r.right - 2, center_y))

        # Waveform line
        if any(abs(v) > 0.001 for v in self.waveform_data):
            points = []
            step = max(1, self.waveform_points // r.width)
            for i in range(r.width):
                idx = (self.waveform_write_pos + i * step) % self.waveform_points
                val = self.waveform_data[idx]
                px = r.x + i
                py = center_y - int(val * (r.height // 2 - 4))
                py = max(r.y + 2, min(r.bottom - 2, py))
                points.append((px, py))

            if len(points) > 1:
                color = theme.RED if self.recording else theme.WAVEFORM_COLOR
                pygame.draw.lines(surface, color, False, points, 1)

        # Recording overlay text
        if self.recording:
            f_sm = theme.font("small")
            rec_label = f_sm.render("REC", True, theme.RED)
            surface.blit(rec_label, (r.x + 8, r.y + 6))
            # Time in waveform
            time_text = self._format_time(self.record_time)
            time_surf = f_sm.render(time_text, True, theme.RED)
            surface.blit(time_surf, (r.right - 80, r.y + 6))

    def _draw_meters(self, surface: pygame.Surface):
        """Draw stereo input level meters."""
        mx = self.meter_x
        mw = self.meter_w
        mh = self.meter_h
        my = self.wf_rect.bottom + 12
        f_sm = theme.font("small")

        # L channel
        self._draw_single_meter(surface, mx, my, mw // 2 - 1, mh, self.level_l)
        # R channel
        self._draw_single_meter(surface, mx + mw // 2 + 1, my, mw // 2 - 1, mh, self.level_r)

        # Labels
        l_label = f_sm.render("L", True, theme.TEXT_DIM)
        r_label = f_sm.render("R", True, theme.TEXT_DIM)
        surface.blit(l_label, (mx + 2, my + mh + 2))
        surface.blit(r_label, (mx + mw // 2 + 2, my + mh + 2))

    def _draw_single_meter(self, surface: pygame.Surface,
                           x: int, y: int, w: int, h: int, level: float):
        """Draw one vertical meter bar."""
        pygame.draw.rect(surface, (15, 15, 20), (x, y, w, h))

        if level <= 0:
            return

        fill_h = int(level * h)
        fill_h = min(fill_h, h)

        for py in range(fill_h):
            draw_y = y + h - 1 - py
            ratio = py / max(1, h)
            if ratio < 0.6:
                color = theme.GREEN
            elif ratio < 0.85:
                color = theme.YELLOW
            else:
                color = theme.RED
            pygame.draw.line(surface, color, (x, draw_y), (x + w - 1, draw_y))

    def _draw_info(self, surface: pygame.Surface):
        """Draw time and target pad info."""
        f_med = theme.font("medium")
        f_lg = theme.font("large")
        f_sm = theme.font("small")

        # Record time
        time_text = self._format_time(self.record_time)
        max_text = self._format_time(self.MAX_RECORD_SECONDS)
        time_display = f"{time_text} / {max_text}"
        time_surf = f_lg.render(time_display, True,
                               theme.RED if self.recording else theme.TEXT)
        surface.blit(time_surf, (self.info_x, self.time_y))

        # Target pad
        pad_num = self.target_pad + 1
        target_text = f"Recording to: {self.target_bank}-{pad_num:02d}"
        target_surf = f_med.render(target_text, True, theme.ACCENT)
        surface.blit(target_surf, (self.info_x, self.target_y))

    def _draw_threshold(self, surface: pygame.Surface):
        """Draw the auto-start threshold slider."""
        f_sm = theme.font("small")
        tx = self.threshold_x
        ty = self.threshold_y
        tw = self.threshold_w
        th = self.threshold_h

        # Label
        label = f_sm.render("THRESHOLD", True, theme.TEXT_DIM)
        surface.blit(label, (tx, ty - 2))
        ty += 16

        # Track
        pygame.draw.rect(surface, theme.KNOB_TRACK, (tx, ty, tw, th // 2),
                        border_radius=3)

        # Fill
        fill_w = int(self.threshold * tw)
        if fill_w > 0:
            pygame.draw.rect(surface, theme.ACCENT_DIM, (tx, ty, fill_w, th // 2),
                            border_radius=3)

        # Thumb
        thumb_x = tx + fill_w - 4
        thumb_x = max(tx, min(tx + tw - 8, thumb_x))
        pygame.draw.rect(surface, theme.ACCENT, (thumb_x, ty - 3, 8, th // 2 + 6),
                        border_radius=3)

        # Value
        val_text = f_sm.render(f"{int(self.threshold * 100)}%", True, theme.TEXT_DIM)
        surface.blit(val_text, (tx + tw + 8, ty - 2))

    def _draw_state_indicator(self, surface: pygame.Surface):
        """Draw the big recording/armed state indicator."""
        ix = self.indicator_x
        iy = self.indicator_y
        radius = 18

        if self.recording:
            # Solid red dot when recording
            pygame.draw.circle(surface, theme.RED, (ix, iy), radius)
            # Inner bright dot
            pygame.draw.circle(surface, (255, 100, 100), (ix, iy), radius - 6)
        elif self.armed:
            # Blinking red dot when armed
            if self._blink_on:
                pygame.draw.circle(surface, theme.RED, (ix, iy), radius)
            else:
                pygame.draw.circle(surface, theme.BUTTON_BG, (ix, iy), radius)
            pygame.draw.circle(surface, theme.BORDER, (ix, iy), radius, 2)
        else:
            # Dim circle when idle
            pygame.draw.circle(surface, theme.BUTTON_BG, (ix, iy), radius)
            pygame.draw.circle(surface, theme.BORDER, (ix, iy), radius, 2)

        # State label
        f_sm = theme.font("small")
        if self.recording:
            state_text = "REC"
            state_color = theme.RED
        elif self.armed:
            state_text = "ARMED"
            state_color = theme.RED if self._blink_on else theme.TEXT_DIM
        else:
            state_text = "IDLE"
            state_color = theme.TEXT_DIM
        state_surf = f_sm.render(state_text, True, state_color)
        state_rect = state_surf.get_rect(centerx=ix, top=iy + radius + 6)
        surface.blit(state_surf, state_rect)

    def _draw_pad_selector(self, surface: pygame.Surface):
        """Draw the target pad selector row."""
        f_sm = theme.font("small")
        pad_label = f_sm.render("Target pad:", True, theme.TEXT_DIM)
        by = self.pad_target_buttons[0].rect.y
        surface.blit(pad_label, (self.info_x, by - 14))

        for btn in self.pad_target_buttons:
            btn.draw(surface)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events."""
        # ARM button
        if self.arm_btn.handle_event(event):
            self.armed = self.arm_btn.active
            if self.armed:
                self.recording = False
                self.record_btn.label = "RECORD"
            return True

        # RECORD / STOP
        if self.record_btn.handle_event(event):
            if self.recording:
                self._stop_recording()
            else:
                self._start_recording()
            return True

        # MONITOR
        if self.monitor_btn.handle_event(event):
            self.monitoring = self.monitor_btn.active
            return True

        # ASSIGN — assign recorded audio to target pad
        if self.assign_btn.handle_event(event):
            if self._recorded_data is not None:
                # In real usage, this would assign the audio buffer to the pad
                pass
            return True

        # CANCEL — discard recording
        if self.cancel_btn.handle_event(event):
            self._stop_recording()
            self.armed = False
            self.arm_btn.active = False
            self.record_time = 0.0
            self._recorded_data = None
            self.waveform_data = [0.0] * self.waveform_points
            self.waveform_write_pos = 0
            return True

        # Pad target selector
        for i, btn in enumerate(self.pad_target_buttons):
            if btn.handle_event(event):
                self.target_pad = i
                for j, b in enumerate(self.pad_target_buttons):
                    b.active = (j == i)
                return True

        # Threshold slider drag
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            slider_rect = pygame.Rect(
                self.threshold_x, self.threshold_y + 14,
                self.threshold_w, self.threshold_h,
            )
            if slider_rect.collidepoint(event.pos):
                self._dragging_threshold = True
                self._update_threshold(event.pos[0])
                return True

        elif event.type == pygame.MOUSEMOTION and self._dragging_threshold:
            self._update_threshold(event.pos[0])
            return True

        elif event.type == pygame.MOUSEBUTTONUP:
            if self._dragging_threshold:
                self._dragging_threshold = False
                return True

        return False

    def _update_threshold(self, mouse_x: int):
        """Update threshold from mouse position."""
        ratio = (mouse_x - self.threshold_x) / max(1, self.threshold_w)
        self.threshold = max(0.0, min(1.0, ratio))

    def feed_audio(self, samples: list):
        """Feed audio samples for waveform display (called from audio engine)."""
        for s in samples:
            self.waveform_data[self.waveform_write_pos] = s
            self.waveform_write_pos = (self.waveform_write_pos + 1) % self.waveform_points
