"""FX screen — per-pad and master effects chain with knobs and bypass."""

import pygame
from .. import theme
from ..components.knob import Knob
from ..components.button import Button


# Effect type definitions with parameter names and ranges
FX_TYPES = ["None", "Filter", "Reverb", "Delay", "Crush", "Drive"]

FX_PARAMS = {
    "None":   [("---", 0, 100), ("---", 0, 100), ("---", 0, 100)],
    "Filter": [("Freq", 20, 20000), ("Reso", 0, 100), ("Type", 0, 3)],
    "Reverb": [("Size", 0, 100), ("Damp", 0, 100), ("Mix", 0, 100)],
    "Delay":  [("Time", 10, 1000), ("Fdbk", 0, 100), ("Mix", 0, 100)],
    "Crush":  [("Bits", 1, 16), ("Rate", 1, 100), ("Mix", 0, 100)],
    "Drive":  [("Gain", 0, 100), ("Tone", 0, 100), ("Mix", 0, 100)],
}


class FxSlot:
    """State for one effect slot."""

    def __init__(self):
        self.fx_type = "None"
        self.params = [50.0, 50.0, 50.0]
        self.bypassed = False


class FxScreen:
    """Effects chain editor for individual pads and master bus."""

    NUM_SLOTS = 4

    def __init__(self, app):
        self.app = app

        self.area_y = theme.HEADER_HEIGHT
        self.area_h = theme.SCREEN_HEIGHT - theme.HEADER_HEIGHT - theme.NAV_HEIGHT

        # Target pad selector — 0-15 = pads, 16 = MASTER
        self.target = 16  # default to MASTER
        self.pad_selector_buttons: list[Button] = []

        # "MASTER" button
        master_btn = Button(
            pygame.Rect(12, self.area_y + 4, 64, 28),
            "MASTER",
            color=theme.BUTTON_BG,
            active_color=theme.ACCENT,
            font_name="small",
        )
        master_btn.active = True
        self.pad_selector_buttons.append(master_btn)

        # Pad 1-16 buttons
        for i in range(16):
            bx = 84 + i * 42
            btn = Button(
                pygame.Rect(bx, self.area_y + 4, 38, 28),
                str(i + 1),
                color=theme.BUTTON_BG,
                active_color=theme.ACCENT,
                font_name="small",
            )
            self.pad_selector_buttons.append(btn)

        # FX slots — middle section
        self.slots: list[FxSlot] = [FxSlot() for _ in range(self.NUM_SLOTS)]

        slot_y = self.area_y + 42
        slot_h = 180
        slot_w = (theme.SCREEN_WIDTH - 20) // self.NUM_SLOTS

        self.slot_rects: list[pygame.Rect] = []
        self.type_buttons: list[Button] = []
        self.bypass_buttons: list[Button] = []
        self.param_knobs: list[list[Knob]] = []

        for s in range(self.NUM_SLOTS):
            sx = 4 + s * slot_w
            self.slot_rects.append(pygame.Rect(sx, slot_y, slot_w - 4, slot_h))

            # Type selector button
            type_btn = Button(
                pygame.Rect(sx + 4, slot_y + 4, slot_w - 12, 28),
                "None",
                color=theme.BUTTON_BG,
                font_name="small",
            )
            self.type_buttons.append(type_btn)

            # Bypass button
            bypass_btn = Button(
                pygame.Rect(sx + slot_w - 40, slot_y + slot_h - 30, 32, 24),
                "BYP",
                color=theme.BUTTON_BG,
                active_color=theme.RED,
                font_name="small",
                toggle=True,
            )
            self.bypass_buttons.append(bypass_btn)

            # 3 parameter knobs
            knob_y = slot_y + 80
            knob_spacing = (slot_w - 12) // 3
            knobs = []
            for k in range(3):
                kx = sx + 4 + knob_spacing // 2 + k * knob_spacing
                knob = Knob(
                    (kx, knob_y), radius=20,
                    min_val=0, max_val=100, value=50,
                    label="---",
                    format_func=lambda v: f"{int(v)}",
                    int_mode=True,
                )
                knobs.append(knob)
            self.param_knobs.append(knobs)

        # Master effects section — bottom
        master_y = slot_y + slot_h + 12
        self.master_section_y = master_y

        # EQ knobs: Low, Mid, High
        eq_start_x = 60
        eq_spacing = 100
        self.eq_knobs = []
        for i, label in enumerate(["LOW", "MID", "HIGH"]):
            knob = Knob(
                (eq_start_x + i * eq_spacing, master_y + 46), radius=22,
                min_val=-12, max_val=12, value=0,
                label=label,
                format_func=lambda v: f"{v:+.0f}dB",
                int_mode=True,
            )
            self.eq_knobs.append(knob)

        # Compressor knobs: Threshold, Ratio
        comp_x = eq_start_x + 3 * eq_spacing + 40
        self.comp_thresh = Knob(
            (comp_x, master_y + 46), radius=22,
            min_val=-60, max_val=0, value=-20,
            label="THRESH",
            format_func=lambda v: f"{int(v)}dB",
            int_mode=True,
        )
        self.comp_ratio = Knob(
            (comp_x + 90, master_y + 46), radius=22,
            min_val=1, max_val=20, value=4,
            label="RATIO",
            format_func=lambda v: f"{v:.1f}:1",
            int_mode=False,
        )

        # Output level meter position
        self.output_meter_x = theme.SCREEN_WIDTH - 40
        self.output_meter_y = master_y + 10
        self.output_meter_h = 80
        self.output_level = 0.0

    def on_enter(self):
        """Sync state on screen entry."""
        self._update_slot_display()

    def on_exit(self):
        pass

    def _update_slot_display(self):
        """Update knob labels and ranges from current slot types."""
        for s in range(self.NUM_SLOTS):
            slot = self.slots[s]
            self.type_buttons[s].label = slot.fx_type
            self.bypass_buttons[s].active = slot.bypassed
            params = FX_PARAMS.get(slot.fx_type, FX_PARAMS["None"])
            for k in range(3):
                name, mn, mx = params[k]
                self.param_knobs[s][k].label = name
                self.param_knobs[s][k].min_val = mn
                self.param_knobs[s][k].max_val = mx
                self.param_knobs[s][k].value = slot.params[k]

    def _cycle_fx_type(self, slot_index: int):
        """Cycle the effect type for a slot."""
        slot = self.slots[slot_index]
        idx = FX_TYPES.index(slot.fx_type) if slot.fx_type in FX_TYPES else 0
        idx = (idx + 1) % len(FX_TYPES)
        slot.fx_type = FX_TYPES[idx]
        # Reset params to defaults
        params = FX_PARAMS[slot.fx_type]
        for k in range(3):
            _, mn, mx = params[k]
            slot.params[k] = (mn + mx) / 2.0
        self._update_slot_display()

    def update(self):
        """Per-frame update."""
        # Decay output meter
        self.output_level = max(0.0, self.output_level - 0.02)

    def draw(self, surface: pygame.Surface):
        """Draw the FX screen."""
        # Header
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, theme.HEADER_HEIGHT)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)
        f = theme.font("large")
        title = f.render("EFFECTS", True, theme.TEXT_BRIGHT)
        surface.blit(title, (12, 6))
        pygame.draw.line(surface, theme.BORDER,
                        (0, theme.HEADER_HEIGHT),
                        (theme.SCREEN_WIDTH, theme.HEADER_HEIGHT))

        # Pad selector row
        for btn in self.pad_selector_buttons:
            btn.draw(surface)

        # FX slots
        f_sm = theme.font("small")
        for s in range(self.NUM_SLOTS):
            sr = self.slot_rects[s]
            slot = self.slots[s]

            # Slot background
            pygame.draw.rect(surface, theme.BG_PANEL, sr, border_radius=4)
            pygame.draw.rect(surface, theme.BORDER, sr, 1, border_radius=4)

            # Slot number
            slot_label = f_sm.render(f"SLOT {s + 1}", True, theme.TEXT_DIM)
            surface.blit(slot_label, (sr.x + 6, sr.y + 36))

            # Active indicator (green dot when not None and not bypassed)
            if slot.fx_type != "None" and not slot.bypassed:
                pygame.draw.circle(surface, theme.GREEN,
                                  (sr.right - 12, sr.y + 16), 5)
            elif slot.fx_type != "None" and slot.bypassed:
                pygame.draw.circle(surface, theme.RED,
                                  (sr.right - 12, sr.y + 16), 5)

            # Type button
            self.type_buttons[s].draw(surface)

            # Bypass button
            self.bypass_buttons[s].draw(surface)

            # Param knobs
            for knob in self.param_knobs[s]:
                knob.draw(surface)

        # Master effects section
        self._draw_master_section(surface)

    def _draw_master_section(self, surface: pygame.Surface):
        """Draw the master EQ, compressor, and output meter."""
        my = self.master_section_y
        f_sm = theme.font("small")
        f_med = theme.font("medium")

        # Section background
        section_rect = pygame.Rect(0, my, theme.SCREEN_WIDTH, self.area_y + self.area_h - my)
        pygame.draw.rect(surface, theme.BG_PANEL, section_rect)
        pygame.draw.line(surface, theme.BORDER, (0, my), (theme.SCREEN_WIDTH, my))

        # Section label
        label = f_med.render("MASTER", True, theme.TEXT_BRIGHT)
        surface.blit(label, (12, my + 6))

        # EQ label
        eq_label = f_sm.render("EQ", True, theme.TEXT_DIM)
        surface.blit(eq_label, (12, my + 30))

        # EQ knobs
        for knob in self.eq_knobs:
            knob.draw(surface)

        # Compressor label
        comp_x = self.comp_thresh.center[0] - 40
        comp_label = f_sm.render("COMP", True, theme.TEXT_DIM)
        surface.blit(comp_label, (comp_x, my + 6))

        self.comp_thresh.draw(surface)
        self.comp_ratio.draw(surface)

        # Output level meter
        ox = self.output_meter_x
        oy = self.output_meter_y
        oh = self.output_meter_h
        ow = 20
        pygame.draw.rect(surface, (15, 15, 20), (ox, oy, ow, oh))
        if self.output_level > 0:
            fill_h = int(self.output_level * oh)
            for py in range(fill_h):
                draw_y = oy + oh - 1 - py
                ratio = py / max(1, oh)
                if ratio < 0.6:
                    color = theme.GREEN
                elif ratio < 0.85:
                    color = theme.YELLOW
                else:
                    color = theme.RED
                pygame.draw.line(surface, color, (ox, draw_y), (ox + ow - 1, draw_y))

        out_label = f_sm.render("OUT", True, theme.TEXT_DIM)
        out_rect = out_label.get_rect(centerx=ox + ow // 2, top=oy + oh + 4)
        surface.blit(out_label, out_rect)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle events."""
        # Pad selector
        for i, btn in enumerate(self.pad_selector_buttons):
            if btn.handle_event(event):
                self.target = 16 if i == 0 else i - 1
                for j, b in enumerate(self.pad_selector_buttons):
                    b.active = (j == i)
                return True

        # FX type buttons (cycle on click)
        for s, btn in enumerate(self.type_buttons):
            if btn.handle_event(event):
                self._cycle_fx_type(s)
                return True

        # Bypass buttons
        for s, btn in enumerate(self.bypass_buttons):
            if btn.handle_event(event):
                self.slots[s].bypassed = btn.active
                return True

        # Param knobs
        for s in range(self.NUM_SLOTS):
            for k, knob in enumerate(self.param_knobs[s]):
                if knob.handle_event(event):
                    self.slots[s].params[k] = knob.value
                    return True

        # EQ knobs
        for knob in self.eq_knobs:
            if knob.handle_event(event):
                return True

        # Compressor knobs
        if self.comp_thresh.handle_event(event):
            return True
        if self.comp_ratio.handle_event(event):
            return True

        return False
