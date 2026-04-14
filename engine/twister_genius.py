"""Twister Genius — Midi Fighter Twister deep integration for SP-404 FX.

Each of the 16 Twister knobs is assigned an SP-404 effect.
Press a knob → load that effect on the active bus + turn FX on.
Turn while pressed → adjust Ctrl 1 of that effect.
Release → effect stays active.

LED colors on each knob reflect which effect is assigned.
Auto-detects Twister and maps everything on startup.

Twister MIDI protocol (default factory config):
  Channel 0: Encoder turns (CC 0-15, value 0-127)
  Channel 1: Encoder presses (CC 0-15, 127=press, 0=release)
  Channel 1 (output): LED color (CC 0-15, value = color wheel)
  Channel 2 (output): LED animation (CC 0-15)
"""

import logging
import threading
import time
from dataclasses import dataclass, field

try:
    import rtmidi
except ImportError:
    rtmidi = None

from engine.sp404_effects import BUS12_FX, BUS34_FX, INPUT_FX, TAB_FX_LIST

log = logging.getLogger(__name__)

# ── Twister LED Color Wheel (0-127) ─────────────────────────────────
# Maps to the Twister's internal color wheel:
#   0=off, 1-17=red→orange, 18-34=orange→yellow, 35-51=yellow→green,
#   52-68=green→cyan, 69-85=cyan→blue, 86-102=blue→purple,
#   103-119=purple→pink, 120-126=pink→red, 127=white

COLOR_RED = 70
COLOR_ORANGE = 60
COLOR_YELLOW = 63
COLOR_GREEN = 50
COLOR_CYAN = 57
COLOR_BLUE = 73
COLOR_PURPLE = 90
COLOR_PINK = 110
COLOR_WHITE = 127
COLOR_OFF = 0

# Animation types (sent on channel 2)
ANIM_NONE = 0       # Solid color, follows knob position
ANIM_STROBE = 48    # Fast strobe
ANIM_PULSE = 16     # Slow pulse
ANIM_RAINBOW = 127  # Rainbow cycle

# ── SP-404 CC numbers for Ctrl 1-6 ──────────────────────────────────
CC_CTRL1 = 16
CC_CTRL2 = 17
CC_CTRL3 = 18
CC_CTRL4 = 80
CC_CTRL5 = 81
CC_CTRL6 = 82

# ── Per-Effect Smart Parameter Map ──────────────────────────────────
# Each effect defines:
#   "turn_cc": which CC the knob controls when turned (main parameter)
#   "setup": list of (cc, value) pairs sent on load to prime the effect
#
# If an effect isn't listed, defaults to CC16 (Ctrl 1) with no setup.

EFFECT_PARAMS = {
    # Performance FX
    "Downer":       {"turn_cc": CC_CTRL1},
    "Scatter":      {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL3, 80)]},  # Ctrl3=type
    "Ha-Dou":       {"turn_cc": CC_CTRL1},
    "Ko-Da-Ma":     {"turn_cc": CC_CTRL1},
    "Zan-Zou":      {"turn_cc": CC_CTRL1},
    "To-Gu-Ro":     {"turn_cc": CC_CTRL1},
    "SBF":          {"turn_cc": CC_CTRL1},
    "Stopper":      {"turn_cc": CC_CTRL1},
    "Back Spin":    {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL3, 100)]},  # Ctrl3=activate

    # Delays
    "Tape Echo":    {"turn_cc": CC_CTRL2, "setup": [(CC_CTRL1, 80)]},   # Turn=feedback, setup=level
    "TimeCtrlDly":  {"turn_cc": CC_CTRL1},
    "Cloud Delay":  {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL2, 90)]},   # Turn=time, setup=feedback
    "SX Delay":     {"turn_cc": CC_CTRL1},
    "Sync Delay":   {"turn_cc": CC_CTRL1},

    # Filters
    "Super Filter": {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL2, 80)]},   # Turn=cutoff, setup=reso
    "Isolator":     {"turn_cc": CC_CTRL1},  # Ctrl1=low band
    "Filter+Drive": {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL3, 64)]},   # Turn=cutoff, setup=drive
    "Wah":          {"turn_cc": CC_CTRL1},

    # Vinyl/Lo-fi
    "303 VinylSim": {"turn_cc": CC_CTRL1},
    "404 VinylSim": {"turn_cc": CC_CTRL1},
    "Cassette Sim": {"turn_cc": CC_CTRL1},
    "Lo-fi":        {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL2, 64)]},   # Turn=bit, setup=sample rate
    "WrmSaturator": {"turn_cc": CC_CTRL1},

    # Modulation
    "Reverb":       {"turn_cc": CC_CTRL2, "setup": [(CC_CTRL1, 90)]},   # Turn=time, setup=level
    "SX Reverb":    {"turn_cc": CC_CTRL2, "setup": [(CC_CTRL1, 90)]},
    "Chorus":       {"turn_cc": CC_CTRL1},
    "JUNO Chorus":  {"turn_cc": CC_CTRL1},
    "Flanger":      {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL2, 80)]},   # Turn=rate, setup=depth
    "Phaser":       {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL2, 80)]},
    "Tremolo/Pan":  {"turn_cc": CC_CTRL1},
    "Slicer":       {"turn_cc": CC_CTRL1},
    "Chromatic PS":  {"turn_cc": CC_CTRL1},

    # Distortion
    "Hyper-Reso":   {"turn_cc": CC_CTRL1},
    "Ring Mod":     {"turn_cc": CC_CTRL1},
    "Crusher":      {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL2, 64)]},   # Turn=crush, setup=mix
    "Overdrive":    {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL2, 80)]},   # Turn=drive, setup=tone
    "Distortion":   {"turn_cc": CC_CTRL1, "setup": [(CC_CTRL2, 80)]},
    "Resonator":    {"turn_cc": CC_CTRL1},

    # Utility
    "Equalizer":    {"turn_cc": CC_CTRL1},
    "Compressor":   {"turn_cc": CC_CTRL1},

    # Looper
    "DJFX Looper":  {"turn_cc": CC_CTRL3, "setup": [(CC_CTRL1, 90)]},  # Turn=length, setup=level

    # Input FX
    "Auto Pitch":   {"turn_cc": CC_CTRL1},
    "Vocoder":      {"turn_cc": CC_CTRL1},
    "Harmony":      {"turn_cc": CC_CTRL1},
    "Gt Amp Sim":   {"turn_cc": CC_CTRL1},
}


# ── Effect Presets ───────────────────────────────────────────────────

@dataclass
class EffectSlot:
    """One knob's effect assignment."""
    name: str           # Effect name (must match SP-404 effect list)
    color: int          # Twister LED color (0-127)
    active: bool = False  # Currently loaded on a bus?
    pressed: bool = False # Knob currently held down?


# ── Twister Knob Layout (4x4 grid) ──────────────────────────────────
#
# Physical layout:
#   Row 1:  [FX 0]  [FX 1]  [CTRL2/SHIFT]  [CTRL3]
#   Row 2:  [FX 2]  [FX 3]  [FX 4]         [FX 5]
#   Row 3:  [FX 6]  [FX 7]  [FX 8]         [FX 9]
#   Row 4:  [FX 10] [FX 11] [FX 12]        [FX 13]
#
# Knobs 2,3 = dynamic Ctrl 2/3 of active FX (color-follow)
# Knob 2 also acts as SHIFT (hold = Ctrl 4/5/6 layer)
# Knobs 0,1,4-15 = 14 FX slots

KNOB_CTRL2 = 2    # Top row, 3rd position: Ctrl 2 (or 5 shifted) + SHIFT
KNOB_CTRL3 = 3    # Top row, 4th position: Ctrl 3 (or 6 shifted)

# Which physical knobs are FX slots (in order)
# Slot 1 ↔ Slot 4 swapped: knob 2 (phys 1) and knob 7 (phys 6) traded places
FX_KNOB_INDICES = [0, 6, 4, 5, 1, 7, 8, 9, 10, 11, 12, 13, 14, 15]

SLOTS_PER_PAGE = len(FX_KNOB_INDICES)  # 14


def _build_p6_pages() -> list[list[EffectSlot]]:
    """Build parameter pages for the P-6 (knobs = direct CC control)."""
    # P-6 params organized as pages matching the P-6's sections
    p6_params = [
        # Page 1: Granular engine (16 params — fills entire 4x4 Twister)
        ("Grain Size",  23, COLOR_YELLOW),   ("Grains",       21, COLOR_YELLOW),
        ("Head Pos",    19, COLOR_YELLOW),    ("Head Speed",   20, COLOR_YELLOW),
        ("Grain Shape", 15, COLOR_YELLOW),    ("Detune",       13, COLOR_YELLOW),
        ("Spread",      25, COLOR_YELLOW),    ("Jitter",       68, COLOR_YELLOW),
        ("Fine Tune",   18, COLOR_YELLOW),    ("Coarse Tune",  76, COLOR_YELLOW),
        ("Rev Prob",     3, COLOR_YELLOW),    ("Start Mode",   79, COLOR_YELLOW),
        ("Sample Sel",  88, COLOR_YELLOW),    ("Time KF",      16, COLOR_YELLOW),
        ("Cutoff KF",   26, COLOR_YELLOW),    ("Vel Sens",     78, COLOR_YELLOW),
        # Page 2: Filter + Envelope + Mixer + FX (16 params)
        ("Cutoff",      74, COLOR_YELLOW),    ("Resonance",    71, COLOR_YELLOW),
        ("Filter Type", 12, COLOR_YELLOW),    ("Env Depth",    24, COLOR_YELLOW),
        ("Attack",      73, COLOR_YELLOW),    ("Decay",        75, COLOR_YELLOW),
        ("Sustain",     30, COLOR_YELLOW),    ("Release",      72, COLOR_YELLOW),
        ("Level",        7, COLOR_YELLOW),    ("Pan",          10, COLOR_YELLOW),
        ("Auto Pan",     9, COLOR_YELLOW),    ("Send Delay",   85, COLOR_YELLOW),
        ("Delay Time",  90, COLOR_YELLOW),    ("Delay Level",  92, COLOR_YELLOW),
        ("Reverb Time", 89, COLOR_YELLOW),    ("Reverb Level", 91, COLOR_YELLOW),
    ]
    pages = []
    for page_start in range(0, len(p6_params), 16):  # 16 per page (full 4x4)
        chunk = p6_params[page_start:page_start + 16]
        page = []
        for name, cc, color in chunk:
            slot = EffectSlot(name, color)
            slot._p6_cc = cc  # Store the CC number for direct control
            page.append(slot)
        pages.append(page)
    return pages if pages else [[]]


def _build_pages_for_bus(bus_tab: str) -> list[list[EffectSlot]]:
    """Auto-generate FX pages from the full effects list for a bus.

    Skips (OFF), assigns evenly-spaced colors per page.
    Returns list of pages, each page is a list of 14 EffectSlots.
    """
    fx_list = TAB_FX_LIST.get(bus_tab, {})
    # Get all real effects (skip OFF)
    effects = [(idx, name) for idx, name in sorted(fx_list.items()) if name != "(OFF)"]

    pages = []
    for page_start in range(0, len(effects), SLOTS_PER_PAGE):
        page_effects = effects[page_start:page_start + SLOTS_PER_PAGE]
        page = []
        n = len(page_effects)
        for i, (fx_idx, fx_name) in enumerate(page_effects):
            # Evenly space colors across the wheel (1-125, avoid 0=off and 127=white)
            color = max(1, int(1 + (i * 124) / max(1, n - 1))) if n > 1 else 64
            page.append(EffectSlot(fx_name, color))
        pages.append(page)

    return pages if pages else [[]]  # At least one empty page


def _find_fx_index(fx_name: str, bus_tab: str) -> int:
    """Find the CC#83 value for an effect name on a given bus.

    Returns 0 (OFF) if not found on this bus.
    """
    fx_list = TAB_FX_LIST.get(bus_tab, {})
    for idx, name in fx_list.items():
        if name == fx_name:
            return idx
    # Fuzzy match — try partial
    for idx, name in fx_list.items():
        if fx_name.lower() in name.lower():
            return idx
    return 0


class TwisterGenius:
    """Midi Fighter Twister deep integration for SP-404 FX control.

    Auto-detects Twister, maps 16 knobs to 16 effects, handles
    press-to-load and turn-to-control behavior.
    """

    # Twister MIDI channels (0-indexed)
    CH_TURN = 0   # Encoder rotation
    CH_PRESS = 1  # Encoder switch + LED color output
    CH_ANIM = 2   # LED animation output

    def __init__(self):
        self._midi_in = None
        self._midi_out = None
        self._target_midi = None  # P6Midi for SP-404
        self._running = False
        self._thread = None
        self._connected = False

        # Bus and page state
        self._active_bus = 0
        self._bus_tab_keys = ["bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"]

        # Auto-generate pages for the default bus
        self._pages: list[list[EffectSlot]] = _build_pages_for_bus(self.bus_tab)
        self._current_page = 0

        # Active slots = current page's effects
        self.slots: list[EffectSlot] = list(self._pages[0]) if self._pages else []

        # Map physical knob index ↔ slot index
        self._phys_to_slot = {FX_KNOB_INDICES[i]: i for i in range(len(FX_KNOB_INDICES))}
        self._slot_to_phys = {i: FX_KNOB_INDICES[i] for i in range(len(FX_KNOB_INDICES))}

        # Track which knob is pressed
        self._pressed_knobs: set[int] = set()

        # Last loaded effect per bus (stores slot index, not physical knob)
        self._bus_fx_state: dict[int, int] = {}  # bus_idx → slot index

        # SHIFT state: KNOB_CTRL2 held = shift active
        self._shift = False

        # Focus mode: when an FX knob is held, all others go dark
        self._focus_knob: int = -1  # physical knob index, -1 = no focus

        # Remember last parameter values per slot so re-activation restores them
        # Key: (page, slot_idx), Value: {cc: value}
        self._slot_values: dict[tuple[int, int], dict[int, int]] = {}

        # Mode: "momentary" (default) or "toggle"
        self.mode = "momentary"

        # Callbacks for UI updates
        self.on_state_changed = None   # () -> None (effect loaded/killed)
        self.on_param_changed = None   # (knob, value) -> None (param adjusted)
        self.on_cc_sent = None         # (channel, cc, value) -> None (update live state)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def active_bus(self) -> int:
        return self._active_bus

    @active_bus.setter
    def active_bus(self, bus: int):
        old = self._active_bus
        self._active_bus = max(0, min(4, bus))
        if self._active_bus != old:
            self._rebuild_pages()

    @property
    def bus_tab(self) -> str:
        return self._bus_tab_keys[self._active_bus]

    @property
    def current_page(self) -> int:
        return self._current_page

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def is_p6_mode(self) -> bool:
        """True if targeting a P-6 (direct CC mode instead of FX load mode)."""
        if self._target_midi and hasattr(self._target_midi, '_profile'):
            p = self._target_midi._profile
            if p and "P-6" in (p.short_name or ""):
                return True
        return False

    def _rebuild_pages(self):
        """Rebuild FX pages for the current device/bus."""
        if self.is_p6_mode:
            self._pages = _build_p6_pages()
        else:
            self._pages = _build_pages_for_bus(self.bus_tab)
        self._current_page = 0
        self._apply_page()

    def switch_page(self, page: int):
        """Switch to a specific FX page (0-indexed). Repaints LEDs."""
        if 0 <= page < len(self._pages):
            self._current_page = page
            self._apply_page()

    def next_page(self):
        """Cycle to the next FX page."""
        self.switch_page((self._current_page + 1) % len(self._pages))

    def _apply_page(self):
        """Load the current page's effects into the active slots and repaint."""
        page = self._pages[self._current_page] if self._current_page < len(self._pages) else []
        self.slots = list(page)
        # Clear active state for the new page
        for slot in self.slots:
            slot.active = False
        self._bus_fx_state.pop(self._active_bus, None)
        if self._connected:
            self._paint_all_leds()
        if self.on_state_changed:
            self.on_state_changed()

    # ── Detection & Connection ───────────────────────────────────────

    def detect(self) -> bool:
        """Scan for Midi Fighter Twister. Returns True if found."""
        if rtmidi is None:
            return False
        try:
            mi = rtmidi.MidiIn()
            for i in range(mi.get_port_count()):
                if "Midi Fighter Twister" in mi.get_port_name(i):
                    del mi
                    return True
            del mi
        except Exception:
            pass
        return False

    def connect(self) -> bool:
        """Connect to the Twister's MIDI ports."""
        if rtmidi is None:
            return False

        mi = rtmidi.MidiIn()
        mo = rtmidi.MidiOut()
        in_port = out_port = None

        for i in range(mi.get_port_count()):
            if "Midi Fighter Twister" in mi.get_port_name(i):
                in_port = i
                break
        for i in range(mo.get_port_count()):
            if "Midi Fighter Twister" in mo.get_port_name(i):
                out_port = i
                break

        del mi, mo

        if in_port is None:
            return False

        self._midi_in = rtmidi.MidiIn()
        self._midi_in.open_port(in_port)
        self._midi_in.ignore_types(sysex=True, timing=True, active_sense=True)

        if out_port is not None:
            self._midi_out = rtmidi.MidiOut()
            self._midi_out.open_port(out_port)

        self._connected = True
        log.info("Twister Genius connected")
        return True

    def set_target(self, target_midi):
        """Set the SP-404's P6Midi instance as the target."""
        self._target_midi = target_midi

    # ── Start / Stop ─────────────────────────────────────────────────

    def start(self):
        """Start the Twister polling thread and paint LEDs."""
        if self._running or not self._midi_in:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._paint_all_leds()
        log.info("Twister Genius started — 16 effects mapped")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._midi_in:
            self._midi_in.close_port()
            self._midi_in = None
        if self._midi_out:
            self._midi_out.close_port()
            self._midi_out = None
        self._connected = False

    # ── LED Control ──────────────────────────────────────────────────

    def _send_led(self, knob: int, color: int):
        """Set LED color for a knob (0-15). Sends on channel 1."""
        if self._midi_out and 0 <= knob <= 15:
            self._midi_out.send_message([0xB0 | self.CH_PRESS, knob, color])

    def _send_anim(self, knob: int, anim: int):
        """Set LED animation for a knob. Sends on channel 2."""
        if self._midi_out and 0 <= knob <= 15:
            self._midi_out.send_message([0xB0 | self.CH_ANIM, knob, anim])

    def _send_ring(self, knob: int, value: int):
        """Set ring indicator position (channel 0)."""
        if self._midi_out and 0 <= knob <= 15:
            self._midi_out.send_message([0xB0 | self.CH_TURN, knob, value])

    def _led_off(self, knob: int):
        """Turn a knob's LED completely off — blast all channels."""
        if self._midi_out and 0 <= knob <= 15:
            self._midi_out.send_message([0xB0 | self.CH_TURN, knob, 0])   # Ring off
            self._midi_out.send_message([0xB0 | self.CH_PRESS, knob, 0])  # Color off
            self._midi_out.send_message([0xB0 | self.CH_ANIM, knob, 0])   # Anim off

    def _led_on(self, knob: int, color: int, pulse: bool = False):
        """Set a knob's LED to a color, optionally pulsing."""
        if self._midi_out and 0 <= knob <= 15:
            self._midi_out.send_message([0xB0 | self.CH_PRESS, knob, color])
            anim = ANIM_PULSE if pulse else ANIM_NONE
            self._midi_out.send_message([0xB0 | self.CH_ANIM, knob, anim])

    def _paint_all_leds(self):
        """Paint all 16 knob LEDs with their assigned colors."""
        if self.is_p6_mode:
            # P-6 mode: ALL 16 knobs are params, paint them all
            for i in range(16):
                if i < len(self.slots):
                    self._led_on(i, self.slots[i].color)
                else:
                    self._led_off(i)
            return

        for i in range(16):
            if i == KNOB_CTRL2 or i == KNOB_CTRL3:
                color = self._get_active_fx_color()
                self._led_on(i, color if color else COLOR_OFF)
            else:
                slot_idx = self._phys_to_slot.get(i)
                if slot_idx is not None and slot_idx < len(self.slots):
                    slot = self.slots[slot_idx]
                    self._led_on(i, COLOR_WHITE if slot.active else slot.color)
                else:
                    self._led_off(i)

    def _get_active_fx_color(self) -> int:
        """Get the LED color of whatever effect is active on the current bus."""
        active_slot = self._bus_fx_state.get(self._active_bus)
        if active_slot is not None and active_slot < len(self.slots):
            return self.slots[active_slot].color
        return 0  # Off

    def _update_dynamic_leds(self):
        """Update Ctrl 2/3 knobs to match the current active effect color."""
        color = self._get_active_fx_color()
        self._led_on(KNOB_CTRL2, color if color else COLOR_OFF)
        self._led_on(KNOB_CTRL3, color if color else COLOR_OFF)

    def _enter_focus(self, phys_knob: int):
        """Focus mode: ALL knobs go dark except the active one + Ctrl 2/3.

        Active knob + Ctrl 2/3: all show the effect's color, pulsing together.
        Everything else: fully off.
        """
        self._focus_knob = phys_knob
        active_color = self._get_active_fx_color() or 1

        for i in range(16):
            if i == phys_knob or i == KNOB_CTRL2 or i == KNOB_CTRL3:
                self._led_on(i, active_color, pulse=True)
            else:
                self._led_off(i)

    def _exit_focus(self):
        """Restore all LED colors after focus mode ends."""
        self._focus_knob = -1
        self._paint_all_leds()

    def _flash_knob(self, knob: int):
        """Briefly flash a knob to indicate activation."""
        self._led_on(knob, COLOR_WHITE, pulse=True)

    # ── Effect Loading ───────────────────────────────────────────────

    def _load_effect(self, slot_idx: int, phys_knob: int):
        """Load the effect for slot_idx on the active bus.

        Also sends setup CCs from the effect's parameter map.
        """
        slot = self.slots[slot_idx]
        fx_idx = _find_fx_index(slot.name, self.bus_tab)

        if fx_idx == 0:
            log.warning("Effect '%s' not found on %s", slot.name, self.bus_tab)
            return

        ch = self._active_bus

        if self._target_midi:
            self._target_midi.send_cc(83, fx_idx, channel=ch)
            self._target_midi.send_cc(19, 127, channel=ch)

            # Restore saved values if we've used this slot before
            key = (self._current_page, slot_idx)
            saved = self._slot_values.get(key)
            if saved:
                for cc, val in saved.items():
                    self._target_midi.send_cc(cc, val, channel=ch)
                    if self.on_cc_sent:
                        self.on_cc_sent(ch, cc, val)
            else:
                # First time — send setup CCs from the effect parameter map
                params = EFFECT_PARAMS.get(slot.name, {})
                for setup_cc, setup_val in params.get("setup", []):
                    self._target_midi.send_cc(setup_cc, setup_val, channel=ch)
                    if self.on_cc_sent:
                        self.on_cc_sent(ch, setup_cc, setup_val)

        if self.on_cc_sent:
            self.on_cc_sent(ch, 83, fx_idx)
            self.on_cc_sent(ch, 19, 127)

        # Clear previous active on this bus
        prev = self._bus_fx_state.get(self._active_bus)
        if prev is not None and prev != slot_idx:
            self.slots[prev].active = False
            prev_phys = self._slot_to_phys.get(prev, -1)
            if prev_phys >= 0:
                self._led_on(prev_phys, self.slots[prev].color)

        slot.active = True
        self._bus_fx_state[self._active_bus] = slot_idx

        log.info("Loaded %s (CC83=%d) on bus %d", slot.name, fx_idx, ch + 1)

        if self.on_state_changed:
            self.on_state_changed()

    def _kill_effect(self, slot_idx: int):
        """Turn off the effect on the active bus and deactivate the slot."""
        ch = self._active_bus
        if self._target_midi:
            self._target_midi.send_cc(19, 0, channel=ch)
        if self.on_cc_sent:
            self.on_cc_sent(ch, 19, 0)

        slot = self.slots[slot_idx]
        slot.active = False
        self._bus_fx_state.pop(self._active_bus, None)

        phys = self._slot_to_phys.get(slot_idx, -1)
        if phys >= 0:
            self._led_on(phys, slot.color)
        self._update_dynamic_leds()

        if self.on_state_changed:
            self.on_state_changed()

    def _adjust_param(self, slot_idx: int, value: int):
        """Adjust the main parameter for this slot's assigned effect.

        Normal: sends the per-effect turn_cc (usually Ctrl 1).
        Shifted: sends Ctrl 4 instead (secondary layer).
        Stores the value so it can be restored on re-activation.
        """
        slot = self.slots[slot_idx]
        params = EFFECT_PARAMS.get(slot.name, {})

        if self._shift:
            turn_cc = CC_CTRL4
        else:
            turn_cc = params.get("turn_cc", CC_CTRL1)

        ch = self._active_bus
        if self._target_midi:
            self._target_midi.send_cc(turn_cc, value, channel=ch)

        # Remember this value for this slot
        key = (self._current_page, slot_idx)
        if key not in self._slot_values:
            self._slot_values[key] = {}
        self._slot_values[key][turn_cc] = value

        if self.on_cc_sent:
            self.on_cc_sent(ch, turn_cc, value)
        if self.on_param_changed:
            self.on_param_changed(slot_idx, value)

    # ── Polling ──────────────────────────────────────────────────────

    def _poll_loop(self):
        """Poll Twister for MIDI messages."""
        while self._running:
            msg = self._midi_in.get_message()
            if msg:
                data, _ = msg
                if len(data) >= 3:
                    status = data[0] & 0xF0
                    channel = data[0] & 0x0F
                    cc = data[1]
                    val = data[2]

                    if status == 0xB0 and 0 <= cc <= 15:
                        if channel == self.CH_TURN:
                            self._on_turn(cc, val)
                        elif channel == self.CH_PRESS:
                            self._on_press(cc, val)
            else:
                time.sleep(0.001)

    def _on_turn(self, phys_knob: int, value: int):
        """Handle encoder turn. P-6 mode = direct CC, SP-404 mode = FX param."""
        # P-6 direct CC mode: ALL 16 knobs map directly to params
        # P-6 receives CC on channel 15 (0-indexed: 14) = "Auto" channel
        if self.is_p6_mode:
            if phys_knob < len(self.slots):
                slot = self.slots[phys_knob]
                cc = getattr(slot, "_p6_cc", None)
                if cc is not None and self._target_midi:
                    self._target_midi.send_cc(cc, value, channel=14)
                    if self.on_cc_sent:
                        self.on_cc_sent(14, cc, value)
                    if self.on_param_changed:
                        self.on_param_changed(phys_knob, value)
            return

        ch = self._active_bus

        if phys_knob == KNOB_CTRL2:
            cc = CC_CTRL5 if self._shift else CC_CTRL2
            if self._target_midi:
                self._target_midi.send_cc(cc, value, channel=ch)
            active_slot = self._bus_fx_state.get(self._active_bus)
            if active_slot is not None:
                key = (self._current_page, active_slot)
                if key not in self._slot_values:
                    self._slot_values[key] = {}
                self._slot_values[key][cc] = value
            if self.on_cc_sent:
                self.on_cc_sent(ch, cc, value)
            if self.on_param_changed and active_slot is not None:
                self.on_param_changed(active_slot, value)
            return

        if phys_knob == KNOB_CTRL3:
            cc = CC_CTRL6 if self._shift else CC_CTRL3
            if self._target_midi:
                self._target_midi.send_cc(cc, value, channel=ch)
            active_slot = self._bus_fx_state.get(self._active_bus)
            if active_slot is not None:
                key = (self._current_page, active_slot)
                if key not in self._slot_values:
                    self._slot_values[key] = {}
                self._slot_values[key][cc] = value
            if self.on_cc_sent:
                self.on_cc_sent(ch, cc, value)
            if self.on_param_changed and active_slot is not None:
                self.on_param_changed(active_slot, value)
            return

        # FX knobs — map physical to slot
        slot_idx = self._phys_to_slot.get(phys_knob)
        if slot_idx is not None and slot_idx < len(self.slots):
            if phys_knob in self._pressed_knobs or self.slots[slot_idx].active:
                self._adjust_param(slot_idx, value)

    def _on_press(self, phys_knob: int, value: int):
        """Handle encoder press/release with shift, focus mode, momentary/toggle."""

        # P-6 mode: knob 4 (KNOB_CTRL3) press = cycle pages
        if self.is_p6_mode:
            if phys_knob == KNOB_CTRL3 and value >= 64:
                self.next_page()
            return

        # KNOB_CTRL2 = SHIFT key
        if phys_knob == KNOB_CTRL2:
            if value >= 64:
                self._shift = True
                self._pressed_knobs.add(phys_knob)
            else:
                self._shift = False
                self._pressed_knobs.discard(phys_knob)
            return

        # KNOB_CTRL3 = press cycles FX pages, turn for Ctrl 3/6
        if phys_knob == KNOB_CTRL3:
            if value >= 64:
                self._pressed_knobs.add(phys_knob)
                self.next_page()
            else:
                self._pressed_knobs.discard(phys_knob)
            return

        # FX knobs — map physical to slot
        slot_idx = self._phys_to_slot.get(phys_knob)
        if slot_idx is None or slot_idx >= len(self.slots):
            return

        if self.mode == "momentary":
            if value >= 64:
                self._pressed_knobs.add(phys_knob)
                self._load_effect(slot_idx, phys_knob)
                self._enter_focus(phys_knob)
            else:
                self._pressed_knobs.discard(phys_knob)
                if self.slots[slot_idx].active:
                    self._kill_effect(slot_idx)
                self._exit_focus()
        else:
            # Toggle mode
            if value >= 64:
                self._pressed_knobs.add(phys_knob)
                slot = self.slots[slot_idx]
                if slot.active and self._bus_fx_state.get(self._active_bus) == slot_idx:
                    self._kill_effect(slot_idx)
                    self._exit_focus()
                else:
                    self._load_effect(slot_idx, phys_knob)
                    self._enter_focus(phys_knob)
            else:
                self._pressed_knobs.discard(phys_knob)
                self._exit_focus()

    # ── Configuration ────────────────────────────────────────────────

    def assign_effect(self, slot_idx: int, effect_name: str, color: int = None):
        """Assign a different effect to a slot."""
        if 0 <= slot_idx < len(self.slots):
            slot = self.slots[slot_idx]
            slot.name = effect_name
            if color is not None:
                slot.color = color
            slot.active = False
            phys = self._slot_to_phys.get(slot_idx, -1)
            if phys >= 0:
                self._led_on(phys, slot.color)

    def get_slot_info(self, slot_idx: int) -> dict:
        """Get info about a slot's assignment for UI display."""
        slot = self.slots[slot_idx]
        fx_idx = _find_fx_index(slot.name, self.bus_tab)
        phys = self._slot_to_phys.get(slot_idx, -1)
        return {
            "slot": slot_idx,
            "phys_knob": phys,
            "effect": slot.name,
            "color": slot.color,
            "active": slot.active,
            "pressed": phys in self._pressed_knobs,
            "fx_index": fx_idx,
            "available": fx_idx > 0,
        }
