"""Pad edit screen — waveform, knobs, mode/choke controls."""

import os
import pygame
from .. import theme
from ..components.waveform import WaveformDisplay
from ..components.knob import Knob
from ..components.button import Button
from engine.pad_bank import PlayMode


class PadEditScreen:
    """Per-pad settings: waveform display, volume/pan/tune/attack/decay knobs."""

    def __init__(self, app):
        self.app = app
        self._last_pad_id = None

        # Waveform display
        wf_y = theme.HEADER_HEIGHT + 8
        self.waveform = WaveformDisplay(pygame.Rect(
            12, wf_y, theme.SCREEN_WIDTH - 24, 120
        ))

        # Knobs row
        knob_y = wf_y + 140
        knob_spacing = (theme.SCREEN_WIDTH - 40) // 5
        knob_start_x = 40

        self.vol_knob = Knob(
            (knob_start_x, knob_y), radius=26,
            min_val=0, max_val=100, value=80,
            label="VOL", format_func=lambda v: f"{int(v)}%", int_mode=True,
        )
        self.pan_knob = Knob(
            (knob_start_x + knob_spacing, knob_y), radius=26,
            min_val=-100, max_val=100, value=0,
            label="PAN",
            format_func=lambda v: "C" if abs(v) < 1 else f"L{int(abs(v))}" if v < 0 else f"R{int(v)}",
            int_mode=True,
        )
        self.tune_knob = Knob(
            (knob_start_x + knob_spacing * 2, knob_y), radius=26,
            min_val=-24, max_val=24, value=0,
            label="TUNE", format_func=lambda v: f"{int(v):+d}st", int_mode=True,
        )
        self.atk_knob = Knob(
            (knob_start_x + knob_spacing * 3, knob_y), radius=26,
            min_val=0, max_val=500, value=0,
            label="ATK", format_func=lambda v: f"{int(v)}ms", int_mode=True,
        )
        self.dec_knob = Knob(
            (knob_start_x + knob_spacing * 4, knob_y), radius=26,
            min_val=0, max_val=5000, value=0,
            label="DEC", format_func=lambda v: f"{int(v)}ms" if v > 0 else "OFF", int_mode=True,
        )
        self.knobs = [self.vol_knob, self.pan_knob, self.tune_knob, self.atk_knob, self.dec_knob]

        # Mode button
        mode_y = knob_y + 60
        self.mode_btn = Button(
            pygame.Rect(12, mode_y, 160, 34),
            "ONE-SHOT",
            color=theme.BUTTON_BG,
            active_color=theme.ACCENT,
        )

        # Choke group buttons
        self.choke_label_y = mode_y
        choke_x = 200
        self.choke_buttons: list[Button] = []
        choke_labels = ["None"] + [str(i) for i in range(1, 9)]
        for i, label in enumerate(choke_labels):
            btn = Button(
                pygame.Rect(choke_x + i * 50, mode_y, 44, 34),
                label,
                font_name="small",
            )
            self.choke_buttons.append(btn)

    def on_enter(self):
        """Sync knob values from current pad."""
        self._sync_from_pad()

    def _sync_from_pad(self):
        """Load current pad values into UI controls."""
        pad = self.app.pad_bank.selected
        self.vol_knob.value = pad.volume * 100
        self.pan_knob.value = pad.pan * 100
        self.tune_knob.value = pad.tune
        self.atk_knob.value = pad.attack
        self.dec_knob.value = pad.decay

        self.mode_btn.label = pad.mode.value.replace("_", "-").upper()
        self.mode_btn.active = (pad.mode == PlayMode.LOOP)

        # Update choke button states
        for i, btn in enumerate(self.choke_buttons):
            btn.active = (i == pad.choke_group)

        # Update waveform
        if pad.waveform_preview is not None:
            self.waveform.set_data(
                pad.waveform_preview,
                len(pad.audio_data) if pad.audio_data is not None else 0,
                pad.start,
                pad.end,
            )
        else:
            self.waveform.set_data(None, 0, 0, 0)

        self._last_pad_id = id(pad)

    def update(self):
        """Check if selected pad changed."""
        pad = self.app.pad_bank.selected
        if id(pad) != self._last_pad_id:
            self._sync_from_pad()

    def draw(self, surface: pygame.Surface):
        """Draw the pad edit screen."""
        pad = self.app.pad_bank.selected

        # Header
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, theme.HEADER_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)

        f = theme.font("large")
        pad_idx = self.app.pad_bank.selected_pad + 1
        bank = self.app.pad_bank.current_bank
        title = f"PAD {pad_idx}{bank}"
        if pad.sample_path:
            title += f": {os.path.basename(pad.sample_path)}"
        title_surf = f.render(title, True, theme.TEXT_BRIGHT)
        surface.blit(title_surf, (12, 6))

        # Waveform
        self.waveform.draw(surface)

        # Start/end frame labels
        f_sm = theme.font("small")
        start_text = f_sm.render(f"Start: {pad.start}", True, theme.TEXT_DIM)
        end_text = f_sm.render(f"End: {pad.end}", True, theme.TEXT_DIM)
        surface.blit(start_text, (12, self.waveform.rect.bottom + 4))
        surface.blit(end_text, (200, self.waveform.rect.bottom + 4))

        # Knobs
        for knob in self.knobs:
            knob.draw(surface)

        # Mode button
        self.mode_btn.draw(surface)

        # Choke group
        choke_label = f_sm.render("Choke:", True, theme.TEXT_DIM)
        surface.blit(choke_label, (self.choke_buttons[0].rect.x - 50, self.choke_label_y + 8))
        for btn in self.choke_buttons:
            btn.draw(surface)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events for pad editing."""
        pad = self.app.pad_bank.selected

        # Waveform drag
        changed, start, end = self.waveform.handle_event(event)
        if changed:
            pad.start = start
            pad.end = end
            return True

        # Knobs
        if self.vol_knob.handle_event(event):
            pad.volume = self.vol_knob.value / 100.0
            return True
        if self.pan_knob.handle_event(event):
            pad.pan = self.pan_knob.value / 100.0
            return True
        if self.tune_knob.handle_event(event):
            new_tune = int(self.tune_knob.value)
            if new_tune != pad.tune:
                pad.tune = new_tune
                # Reload sample with new pitch
                if pad.sample_path:
                    self.app.sample_loader.load_sample(pad.sample_path, pad)
            return True
        if self.atk_knob.handle_event(event):
            pad.attack = self.atk_knob.value
            return True
        if self.dec_knob.handle_event(event):
            pad.decay = self.dec_knob.value
            return True

        # Mode toggle
        if self.mode_btn.handle_event(event):
            if pad.mode == PlayMode.ONE_SHOT:
                pad.mode = PlayMode.LOOP
            else:
                pad.mode = PlayMode.ONE_SHOT
            self.mode_btn.label = pad.mode.value.replace("_", "-").upper()
            self.mode_btn.active = (pad.mode == PlayMode.LOOP)
            return True

        # Choke group
        for i, btn in enumerate(self.choke_buttons):
            if btn.handle_event(event):
                pad.choke_group = i
                for j, b in enumerate(self.choke_buttons):
                    b.active = (j == i)
                return True

        return False
