"""Mixer screen — 16 channel strips with faders, pan, mute/solo, meters."""

import pygame
from .. import theme
from ..components.button import Button


class MixerScreen:
    """Mixer view: 16 vertical channel strips for current bank + master fader."""

    # Channel strip layout constants
    NUM_CHANNELS = 16
    STRIP_GAP = 2
    MASTER_WIDTH = 52

    def __init__(self, app):
        self.app = app

        # Working area
        self.area_y = theme.HEADER_HEIGHT
        self.area_h = theme.SCREEN_HEIGHT - theme.HEADER_HEIGHT - theme.NAV_HEIGHT

        # Strip sizing — leave room for master on right
        usable_w = theme.SCREEN_WIDTH - self.MASTER_WIDTH - 8
        self.strip_w = (usable_w - self.STRIP_GAP * (self.NUM_CHANNELS - 1)) // self.NUM_CHANNELS
        self.master_x = theme.SCREEN_WIDTH - self.MASTER_WIDTH - 4

        # Bank buttons in header
        self.bank_buttons: list[Button] = []
        bank_names = ["A", "B", "C", "D"]
        btn_w = 40
        btn_x = theme.SCREEN_WIDTH - len(bank_names) * (btn_w + 4) - 8
        for i, name in enumerate(bank_names):
            btn = Button(
                pygame.Rect(btn_x + i * (btn_w + 4), 2, btn_w, 30),
                name,
                color=theme.BUTTON_BG,
                active_color=theme.ACCENT,
                font_name="medium",
            )
            if name == "A":
                btn.active = True
            self.bank_buttons.append(btn)

        # Per-channel state
        self.volumes = [0.8] * self.NUM_CHANNELS
        self.pans = [0.0] * self.NUM_CHANNELS       # -1.0 to 1.0
        self.mutes = [False] * self.NUM_CHANNELS
        self.solos = [False] * self.NUM_CHANNELS
        self.fx_sends = [0.0] * self.NUM_CHANNELS   # 0.0 to 1.0
        self.levels = [0.0] * self.NUM_CHANNELS      # real-time meter level
        self.master_volume = 0.85
        self.master_level = 0.0

        # Drag state
        self._dragging_fader = -1   # -1 = none, 0-15 = channel, 16 = master
        self._dragging_fx = -1
        self._drag_start_y = 0
        self._drag_start_val = 0.0

    def on_enter(self):
        """Sync mixer state from pads."""
        self._sync_from_pads()

    def on_exit(self):
        pass

    def _sync_from_pads(self):
        """Pull volume/pan from current pad bank."""
        pads = self.app.pad_bank.current_pads
        for i, pad in enumerate(pads[:self.NUM_CHANNELS]):
            self.volumes[i] = pad.volume
            self.pans[i] = pad.pan

    def _sync_to_pads(self):
        """Push volume/pan back to pad bank."""
        pads = self.app.pad_bank.current_pads
        for i, pad in enumerate(pads[:self.NUM_CHANNELS]):
            pad.volume = self.volumes[i]
            pad.pan = self.pans[i]

    def _strip_rect(self, index: int) -> pygame.Rect:
        """Get the rect for a channel strip."""
        x = index * (self.strip_w + self.STRIP_GAP)
        return pygame.Rect(x, self.area_y, self.strip_w, self.area_h)

    def update(self):
        """Update per-frame state."""
        # Sync bank button active states
        for i, btn in enumerate(self.bank_buttons):
            btn.active = (["A", "B", "C", "D"][i] == self.app.pad_bank.current_bank)

        # Update levels from audio engine if available
        if hasattr(self.app, 'audio_engine') and hasattr(self.app.audio_engine, 'get_levels'):
            levels = self.app.audio_engine.get_levels()
            if levels is not None:
                for i in range(min(len(levels), self.NUM_CHANNELS)):
                    self.levels[i] = levels[i]
                self.master_level = max(self.levels) if self.levels else 0.0
        else:
            # Decay meters
            for i in range(self.NUM_CHANNELS):
                self.levels[i] = max(0.0, self.levels[i] - 0.03)
            self.master_level = max(0.0, self.master_level - 0.02)

    def draw(self, surface: pygame.Surface):
        """Draw the mixer screen."""
        # Header
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, theme.HEADER_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)

        f = theme.font("large")
        title = f.render("MIXER", True, theme.TEXT_BRIGHT)
        surface.blit(title, (12, 6))

        # Bank buttons
        f_sm = theme.font("small")
        bank_label = f_sm.render("Bank:", True, theme.TEXT_DIM)
        surface.blit(bank_label, (self.bank_buttons[0].rect.x - 50, 10))
        for btn in self.bank_buttons:
            btn.draw(surface)

        pygame.draw.line(surface, theme.BORDER,
                        (0, theme.HEADER_HEIGHT),
                        (theme.SCREEN_WIDTH, theme.HEADER_HEIGHT))

        # Draw channel strips
        for i in range(self.NUM_CHANNELS):
            self._draw_strip(surface, i)

        # Draw master strip
        self._draw_master(surface)

    def _draw_strip(self, surface: pygame.Surface, index: int):
        """Draw a single channel strip."""
        sr = self._strip_rect(index)
        f_sm = theme.font("small")

        # Strip background
        pygame.draw.rect(surface, theme.BG_PANEL, sr)

        x = sr.x
        y = sr.y + 2
        w = sr.width
        cx = x + w // 2

        # Pad number label
        pad_num = str(index + 1)
        is_playing = self.levels[index] > 0.05
        color = theme.ACCENT if is_playing else theme.TEXT_DIM
        num_surf = f_sm.render(pad_num, True, color)
        num_rect = num_surf.get_rect(centerx=cx, top=y)
        surface.blit(num_surf, num_rect)
        y += 16

        # Level meter (left side of strip)
        meter_x = x + 2
        meter_w = 6
        meter_h = sr.height - 130
        meter_y = y
        self._draw_meter(surface, meter_x, meter_y, meter_w, meter_h, self.levels[index])
        y_fader = y

        # Volume fader (right portion)
        fader_x = x + 12
        fader_w = w - 16
        fader_h = meter_h
        # Fader track
        track_x = fader_x + fader_w // 2 - 1
        pygame.draw.rect(surface, theme.KNOB_TRACK,
                        (track_x, y_fader, 3, fader_h))
        # Fader thumb
        thumb_h = 12
        thumb_y = y_fader + int((1.0 - self.volumes[index]) * (fader_h - thumb_h))
        thumb_rect = pygame.Rect(fader_x, thumb_y, fader_w, thumb_h)
        thumb_color = theme.ACCENT if self._dragging_fader == index else theme.TEXT
        pygame.draw.rect(surface, thumb_color, thumb_rect, border_radius=3)
        # Volume %
        vol_text = f"{int(self.volumes[index] * 100)}"
        vol_surf = f_sm.render(vol_text, True, theme.TEXT_DIM)
        vol_rect = vol_surf.get_rect(centerx=cx, top=y_fader + fader_h + 2)
        surface.blit(vol_surf, vol_rect)
        y = y_fader + fader_h + 16

        # Pan indicator
        pan_w = w - 8
        pan_x = x + 4
        pan_y = y
        pygame.draw.rect(surface, theme.KNOB_TRACK, (pan_x, pan_y, pan_w, 6))
        pan_pos = pan_x + int((self.pans[index] + 1.0) * 0.5 * pan_w)
        pan_pos = max(pan_x + 2, min(pan_x + pan_w - 3, pan_pos))
        pygame.draw.rect(surface, theme.ACCENT, (pan_pos - 2, pan_y - 1, 5, 8))
        y += 14

        # Mute button
        mute_y = y
        mute_rect = pygame.Rect(x + 1, mute_y, w // 2 - 2, 18)
        mute_color = theme.RED if self.mutes[index] else theme.BUTTON_BG
        pygame.draw.rect(surface, mute_color, mute_rect, border_radius=2)
        m_surf = f_sm.render("M", True, theme.TEXT)
        m_rect = m_surf.get_rect(center=mute_rect.center)
        surface.blit(m_surf, m_rect)

        # Solo button
        solo_rect = pygame.Rect(x + w // 2 + 1, mute_y, w // 2 - 2, 18)
        solo_color = theme.YELLOW if self.solos[index] else theme.BUTTON_BG
        pygame.draw.rect(surface, solo_color, solo_rect, border_radius=2)
        s_surf = f_sm.render("S", True, theme.BG if self.solos[index] else theme.TEXT)
        s_rect = s_surf.get_rect(center=solo_rect.center)
        surface.blit(s_surf, s_rect)
        y += 22

        # FX send knob (small horizontal bar)
        fx_y = y
        fx_w = w - 8
        fx_x = x + 4
        fx_label = f_sm.render("FX", True, theme.TEXT_DIM)
        fx_label_rect = fx_label.get_rect(centerx=cx, top=fx_y)
        surface.blit(fx_label, fx_label_rect)
        fx_bar_y = fx_y + 14
        pygame.draw.rect(surface, theme.KNOB_TRACK, (fx_x, fx_bar_y, fx_w, 5))
        fill_w = int(self.fx_sends[index] * fx_w)
        if fill_w > 0:
            pygame.draw.rect(surface, theme.GREEN, (fx_x, fx_bar_y, fill_w, 5))

    def _draw_master(self, surface: pygame.Surface):
        """Draw the master fader strip on the far right."""
        mx = self.master_x
        mw = self.MASTER_WIDTH
        my = self.area_y
        mh = self.area_h
        f_sm = theme.font("small")
        f_med = theme.font("medium")

        # Background
        master_rect = pygame.Rect(mx, my, mw, mh)
        pygame.draw.rect(surface, theme.BG_LIGHTER, master_rect)
        pygame.draw.line(surface, theme.BORDER, (mx, my), (mx, my + mh))

        # Label
        label = f_med.render("MST", True, theme.TEXT_BRIGHT)
        label_rect = label.get_rect(centerx=mx + mw // 2, top=my + 4)
        surface.blit(label, label_rect)

        # Fader
        fader_y = my + 24
        fader_h = mh - 80
        track_x = mx + mw // 2 - 1
        pygame.draw.rect(surface, theme.KNOB_TRACK, (track_x, fader_y, 3, fader_h))

        thumb_h = 14
        thumb_y = fader_y + int((1.0 - self.master_volume) * (fader_h - thumb_h))
        thumb_rect = pygame.Rect(mx + 6, thumb_y, mw - 12, thumb_h)
        thumb_color = theme.ACCENT if self._dragging_fader == 16 else theme.TEXT
        pygame.draw.rect(surface, thumb_color, thumb_rect, border_radius=3)

        # Volume text
        vol_text = f"{int(self.master_volume * 100)}%"
        vol_surf = f_sm.render(vol_text, True, theme.TEXT_DIM)
        vol_rect = vol_surf.get_rect(centerx=mx + mw // 2, top=fader_y + fader_h + 4)
        surface.blit(vol_surf, vol_rect)

        # Master meter
        meter_y = fader_y + fader_h + 22
        meter_h = mh - (meter_y - my) - 4
        if meter_h > 10:
            self._draw_meter(surface, mx + 8, meter_y, mw - 16, meter_h, self.master_level)

    def _draw_meter(self, surface: pygame.Surface, x: int, y: int,
                    w: int, h: int, level: float):
        """Draw a vertical level meter with green/yellow/red gradient."""
        # Background
        pygame.draw.rect(surface, (15, 15, 20), (x, y, w, h))

        if level <= 0.0:
            return

        fill_h = int(level * h)
        fill_h = min(fill_h, h)

        # Draw from bottom up, coloring by height
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

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events."""
        # Bank buttons
        for i, btn in enumerate(self.bank_buttons):
            if btn.handle_event(event):
                bank = ["A", "B", "C", "D"][i]
                self.app.pad_bank.select_bank(bank)
                self._sync_from_pads()
                return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            return self._handle_press(event.pos)

        elif event.type == pygame.MOUSEMOTION:
            if self._dragging_fader >= 0:
                self._handle_fader_drag(event.pos)
                return True

        elif event.type == pygame.MOUSEBUTTONUP:
            if self._dragging_fader >= 0:
                self._dragging_fader = -1
                self._sync_to_pads()
                return True

        return False

    def _handle_press(self, pos: tuple) -> bool:
        """Handle a touch/click press."""
        mx, my = pos

        # Check master fader
        master_rect = pygame.Rect(self.master_x, self.area_y + 24,
                                  self.MASTER_WIDTH, self.area_h - 80)
        if master_rect.collidepoint(pos):
            self._dragging_fader = 16
            return True

        # Check channel strips
        for i in range(self.NUM_CHANNELS):
            sr = self._strip_rect(i)
            if not sr.collidepoint(pos):
                continue

            # Mute button region
            mute_y = sr.bottom - 45
            mute_rect = pygame.Rect(sr.x + 1, mute_y, sr.width // 2 - 2, 18)
            if mute_rect.collidepoint(pos):
                self.mutes[i] = not self.mutes[i]
                return True

            # Solo button region
            solo_rect = pygame.Rect(sr.x + sr.width // 2 + 1, mute_y,
                                    sr.width // 2 - 2, 18)
            if solo_rect.collidepoint(pos):
                self.solos[i] = not self.solos[i]
                return True

            # Fader region (upper area of strip)
            fader_top = sr.y + 18
            fader_bottom = sr.bottom - 64
            if fader_top <= my <= fader_bottom:
                self._dragging_fader = i
                # Set volume directly from tap position
                ratio = 1.0 - (my - fader_top) / max(1, fader_bottom - fader_top)
                self.volumes[i] = max(0.0, min(1.0, ratio))
                self._sync_to_pads()
                return True

            return True

        return False

    def _handle_fader_drag(self, pos: tuple):
        """Handle fader drag motion."""
        idx = self._dragging_fader
        if idx < 0:
            return

        if idx == 16:
            # Master fader
            fader_top = self.area_y + 24
            fader_h = self.area_h - 80
        else:
            sr = self._strip_rect(idx)
            fader_top = sr.y + 18
            fader_h = sr.height - 82

        ratio = 1.0 - (pos[1] - fader_top) / max(1, fader_h)
        ratio = max(0.0, min(1.0, ratio))

        if idx == 16:
            self.master_volume = ratio
        else:
            self.volumes[idx] = ratio

    def on_pad_trigger(self, pad_index: int, velocity: float):
        """Visual feedback for triggered pads."""
        if 0 <= pad_index < self.NUM_CHANNELS:
            self.levels[pad_index] = velocity

    def on_pad_release(self, pad_index: int):
        pass
