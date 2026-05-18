"""Spectra Mapper — Midi Fighter Spectra integration for SP-404 pad/utility control.

16 RGB buttons mapped as:
  - Pad triggers (forward notes to SP-404 to play samples)
  - Bank switching (each bank = different LED color)
  - Assignable utility buttons (bus mute, etc.)

Works alongside TwisterGenius — Twister handles FX knobs,
Spectra handles pads and utilities.

Spectra MIDI protocol (factory default):
  - Buttons send Note On/Off on channel 3 (0-indexed: 2)
  - Notes 36-51 (bottom-left to top-right, 4x4 grid)
  - LED color: send Note On back to Spectra, velocity = color
  - Velocity color map: 0=off, 1-17=red, 18-35=orange, 36-53=yellow,
    54-71=green, 72-89=cyan, 90-107=blue, 108-125=purple, 126-127=white
"""

import logging
import threading
import time
from dataclasses import dataclass, field

try:
    import rtmidi
except ImportError:
    rtmidi = None

log = logging.getLogger(__name__)

# ── Spectra LED Colors (Note velocity values) ───────────────────────
# Spectra LED colors (Note velocity values)
# Confirmed: 44=yellow, 90=blue, 120=purple
S_OFF = 0
S_YELLOW = 44
S_ORANGE = 60
S_BLUE = 90
S_PURPLE = 110
S_RED = 120
S_WHITE = 127

# ── SP-404 Bank Colors ──────────────────────────────────────────────
BANK_COLORS = [
    S_BLUE,     # Bank A
    S_PURPLE,   # Bank B
    S_YELLOW,   # Bank C
    S_ORANGE,   # Bank D
    S_RED,      # Bank E
    S_WHITE,    # Bank F
    S_BLUE,     # Bank G
    S_PURPLE,   # Bank H
    S_YELLOW,   # Bank I
    S_ORANGE,   # Bank J
]

BANK_NAMES = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]

# ── Button Assignment Types ─────────────────────────────────────────

@dataclass
class ButtonAssign:
    """What a Spectra button does."""
    kind: str = "pad"       # "pad", "mute", "bank_up", "bank_down", "fx_page"
    pad_note: int = 36      # For "pad": MIDI note to send to SP-404
    bus: int = 0            # For "mute": which bus (0-3)
    label: str = ""


class SpectraMapper:
    """Midi Fighter Spectra integration for SP-404."""

    # Spectra sends notes on this channel
    CH_IN = 2       # Channel 3 (0-indexed)
    NOTE_BASE = 36  # First button note

    def __init__(self):
        self._midi_in = None
        self._midi_out = None
        self._target_midi = None  # P6Midi for SP-404
        self._running = False
        self._thread = None
        self._connected = False

        # Current SP-404 bank (0-9 = A-J)
        self._bank = 0

        # Button assignments (16 buttons)
        # Default: 12 pads (top 3 rows) + 4 utilities (bottom row)
        self.buttons: list[ButtonAssign] = self._default_layout()

        # Bus mute state
        self._bus_muted: dict[int, bool] = {0: False, 1: False, 2: False, 3: False}

        # Software hold — suppress Note Off, track held notes
        self._hold_active = False
        self._held_notes: set[int] = set()  # notes currently sustained by hold

        # Callbacks
        self.on_state_changed = None  # () -> None
        self.on_pad_hit = None        # (note, velocity, bank_name) -> None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def bank(self) -> int:
        return self._bank

    @property
    def bank_name(self) -> str:
        return BANK_NAMES[self._bank] if self._bank < len(BANK_NAMES) else "?"

    @property
    def bank_color(self) -> int:
        return BANK_COLORS[self._bank] if self._bank < len(BANK_COLORS) else S_WHITE

    def _default_layout(self) -> list[ButtonAssign]:
        """Default: 14 pads + HOLD + compressor mute."""
        buttons = []
        # Pads 1-14 (buttons 0-13)
        for i in range(14):
            buttons.append(ButtonAssign("pad", pad_note=36 + i, label=f"Pad {i+1}"))
        # Button 14 (bottom row, 3rd from left): HOLD
        buttons.append(ButtonAssign("hold", label="HOLD"))
        # Button 15 (bottom-right): Compressor mute on Bus 1
        buttons.append(ButtonAssign("compressor_mute", bus=0, label="Comp B1"))
        return buttons

    # ── Detection & Connection ───────────────────────────────────────

    def detect(self) -> bool:
        if rtmidi is None:
            return False
        mi = None
        try:
            mi = rtmidi.MidiIn()
            for i in range(mi.get_port_count()):
                if "Midi Fighter Spectra" in mi.get_port_name(i):
                    return True
        except Exception:
            pass
        finally:
            if mi is not None:
                try:
                    mi.delete()
                except Exception:
                    pass
        return False

    def connect(self) -> bool:
        if rtmidi is None:
            return False

        mi = None
        mo = None
        in_port = out_port = None
        try:
            mi = rtmidi.MidiIn()
            mo = rtmidi.MidiOut()

            for i in range(mi.get_port_count()):
                if "Midi Fighter Spectra" in mi.get_port_name(i):
                    in_port = i
                    break
            for i in range(mo.get_port_count()):
                if "Midi Fighter Spectra" in mo.get_port_name(i):
                    out_port = i
                    break
        finally:
            if mi is not None:
                try:
                    mi.delete()
                except Exception:
                    pass
            if mo is not None:
                try:
                    mo.delete()
                except Exception:
                    pass

        if in_port is None:
            return False

        self._midi_in = rtmidi.MidiIn()
        self._midi_in.open_port(in_port)
        self._midi_in.ignore_types(sysex=True, timing=True, active_sense=True)

        if out_port is not None:
            self._midi_out = rtmidi.MidiOut()
            self._midi_out.open_port(out_port)

        self._connected = True
        log.info("Spectra Mapper connected")
        return True

    def set_target(self, target_midi):
        self._target_midi = target_midi

    # ── Start / Stop ─────────────────────────────────────────────────

    def start(self):
        if self._running or not self._midi_in:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._paint_all_leds()
        log.info("Spectra Mapper started — bank %s", self.bank_name)

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

    def _send_led(self, button: int, color: int):
        """Set button LED color. Button 0-15, color = velocity value."""
        if self._midi_out and 0 <= button <= 15:
            note = self.NOTE_BASE + button
            if color > 0:
                self._midi_out.send_message([0x90 | self.CH_IN, note, color])
            else:
                self._midi_out.send_message([0x80 | self.CH_IN, note, 0])

    def _paint_all_leds(self):
        """Paint all button LEDs based on their assignment and state."""
        bank_col = self.bank_color
        for i, btn in enumerate(self.buttons):
            if btn.kind == "pad":
                self._send_led(i, bank_col)
            elif btn.kind == "hold":
                self._send_led(i, S_PURPLE if self._hold_active else S_YELLOW)
            elif btn.kind == "compressor_mute":
                muted = self._bus_muted.get(btn.bus, False)
                self._send_led(i, S_RED if muted else S_WHITE)
            elif btn.kind == "mute":
                muted = self._bus_muted.get(btn.bus, False)
                self._send_led(i, S_RED if muted else S_WHITE)
            elif btn.kind == "bank_up":
                self._send_led(i, S_WHITE)
            elif btn.kind == "bank_down":
                self._send_led(i, S_WHITE)
            else:
                self._send_led(i, S_BLUE)

    # ── Bank Switching ───────────────────────────────────────────────

    def switch_bank(self, bank: int):
        """Switch Spectra bank indicator (0-9 = A-J).

        Cosmetic only — changes Spectra LED colors to indicate which bank
        you've selected on the SP-404. Does NOT send MIDI to the SP-404
        (bank switching isn't supported via MIDI on the SP-404 MK2).
        """
        self._bank = max(0, min(9, bank))
        self._paint_all_leds()
        log.info("Spectra bank → %s", self.bank_name)

        if self.on_state_changed:
            self.on_state_changed()

    def bank_up(self):
        self.switch_bank((self._bank + 1) % 10)

    def bank_down(self):
        self.switch_bank((self._bank - 1) % 10)

    # ── Bus Mute ─────────────────────────────────────────────────────

    def toggle_mute(self, bus: int):
        """Toggle mute on an SP-404 bus."""
        self._bus_muted[bus] = not self._bus_muted.get(bus, False)
        muted = self._bus_muted[bus]

        if self._target_midi:
            # Mute = set bus volume to 0, unmute = restore to 127
            # SP-404 doesn't have a dedicated mute CC, but we can use
            # the FX On/Off (CC19) as a mute toggle, or send volume CC7
            # Using CC19 (FX Off) effectively silences the bus
            self._target_midi.send_cc(19, 0 if muted else 127, channel=bus)

        self._paint_all_leds()
        log.info("Bus %d %s", bus + 1, "MUTED" if muted else "unmuted")

        if self.on_state_changed:
            self.on_state_changed()

    # ── Polling ──────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            msg = self._midi_in.get_message()
            if msg:
                data, _ = msg
                if len(data) >= 3:
                    status = data[0] & 0xF0
                    note = data[1]
                    vel = data[2]

                    if status == 0x90 and vel > 0:
                        # Note On — button press
                        btn_idx = note - self.NOTE_BASE
                        if 0 <= btn_idx < 16:
                            self._on_button_press(btn_idx, vel)
                    elif status == 0x80 or (status == 0x90 and vel == 0):
                        # Note Off — button release
                        btn_idx = note - self.NOTE_BASE
                        if 0 <= btn_idx < 16:
                            self._on_button_release(btn_idx)
            else:
                time.sleep(0.001)

    def _on_button_press(self, btn_idx: int, velocity: int):
        """Handle Spectra button press."""
        btn = self.buttons[btn_idx]

        if btn.kind == "pad":
            pad_note = btn.pad_note
            if self._target_midi:
                self._target_midi.send_note_on(pad_note, velocity, channel=9)
            if self._hold_active:
                self._held_notes.add(pad_note)
            self._send_led(btn_idx, S_WHITE)
            if self.on_pad_hit:
                self.on_pad_hit(pad_note, velocity, self.bank_name)

        elif btn.kind == "hold":
            # Toggle software hold — suppresses Note Off on pad release
            self._hold_active = not self._hold_active
            if not self._hold_active and self._held_notes:
                # Releasing hold: send Note Off for held notes
                if self._target_midi:
                    for note in self._held_notes:
                        self._target_midi.send_note_off(note, channel=9)
                self._held_notes.clear()
            # Purple when active (distinct from blue pads), yellow when inactive
            self._send_led(btn_idx, S_PURPLE if self._hold_active else S_YELLOW)
            log.info("HOLD %s", "ON" if self._hold_active else "OFF")
            if self.on_state_changed:
                self.on_state_changed()

        elif btn.kind == "compressor_mute":
            self._activate_compressor(btn.bus)
            self._bus_muted[btn.bus] = True
            self._send_led(btn_idx, S_RED)

        elif btn.kind == "mute":
            self.toggle_mute(btn.bus)

        elif btn.kind == "bank_up":
            self.bank_up()

        elif btn.kind == "bank_down":
            self.bank_down()

    def _on_button_release(self, btn_idx: int):
        """Handle Spectra button release."""
        btn = self.buttons[btn_idx]

        if btn.kind == "pad":
            if not self._hold_active:
                # Normal release — send Note Off
                if self._target_midi:
                    self._target_midi.send_note_off(btn.pad_note, channel=9)
            # else: hold is active, suppress Note Off (note stays playing)
            self._send_led(btn_idx, self.bank_color)

        elif btn.kind == "compressor_mute":
            # Momentary: deactivate compressor on release
            self._deactivate_compressor(btn.bus)
            self._bus_muted[btn.bus] = False
            self._send_led(btn_idx, S_ORANGE)

    # ── Compressor Mute ────────────────────────────────────────────────

    def _activate_compressor(self, bus: int):
        """Load compressor on a bus and turn FX on (momentary mute)."""
        if not self._target_midi:
            return
        from engine.sp404_effects import TAB_FX_LIST
        # Find compressor index on this bus
        bus_tabs = ["bus1_fx", "bus2_fx", "bus3_fx", "bus4_fx", "input_fx"]
        tab = bus_tabs[bus] if bus < len(bus_tabs) else "bus1_fx"
        fx_list = TAB_FX_LIST.get(tab, {})
        comp_idx = 0
        for idx, name in fx_list.items():
            if name == "Compressor":
                comp_idx = idx
                break
        if comp_idx == 0:
            return
        # Save current FX state so we can restore on release
        self._pre_comp_state = {}
        self._target_midi.send_cc(83, comp_idx, channel=bus)
        self._target_midi.send_cc(19, 127, channel=bus)
        log.info("Compressor ON on bus %d", bus + 1)

    def _deactivate_compressor(self, bus: int):
        """Turn off FX on the bus (restore from compressor mute)."""
        if not self._target_midi:
            return
        self._target_midi.send_cc(19, 0, channel=bus)
        log.info("Compressor OFF on bus %d", bus + 1)

    # ── Configuration ────────────────────────────────────────────────

    def assign_button(self, btn_idx: int, kind: str, **kwargs):
        """Reassign a button's function."""
        if 0 <= btn_idx < 16:
            self.buttons[btn_idx] = ButtonAssign(kind=kind, **kwargs)
            self._paint_all_leds()
