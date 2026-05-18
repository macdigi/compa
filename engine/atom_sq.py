"""
PreSonus ATOM SQ driver for pi-sampler (non-native mode).

Native mode does NOT work on Pi 3B ALSA — kills all MIDI input.
This driver uses raw MIDI polling for reliable pad/button input.

What works without native mode (discovered via raw MIDI dump):
- 32 pads: Notes 36-67 on ch10, velocity + aftertouch
- Buttons: CC 85, 86, 87, 89, 102, 104, 105 on ch1
- Soft buttons: CC 24-29 on ch3 (toggle 0/127)
- Extended buttons as notes on ch10: 78, 97, 98, 99
- Touchstrip: Channel aftertouch on ch8 (continuous position)
- Pad aftertouch: ch10 channel pressure + poly aftertouch

What does NOT work without native mode:
- Pad LEDs (no host control)
- OLED display (no SysEx)
- Rotary encoders (don't send in non-native)
"""

import logging
import threading
import time
from typing import Callable, List, Optional

try:
    import rtmidi
except ImportError:
    rtmidi = None

log = logging.getLogger(__name__)

# ── Pad mapping ──────────────────────────────────────────────────────────
_PAD_NOTE_LO = 36
_PAD_NOTE_HI = 67
_PAD_CHANNEL = 9  # ch10 zero-indexed

# ── Button CC mapping (ch1, non-native mode) ────────────────────────────
_BUTTON_CC_CH1 = {
    85: "btn_a",       # left-side button
    86: "btn_b",       # left-side button
    87: "up",          # nav up
    89: "down",        # nav down
    102: "shift",      # shift/modifier
    104: "select",     # select/enter
    105: "click",      # encoder click
}

# ── Soft button CC mapping (ch3, non-native mode, toggle 0/127) ─────────
_SOFT_BTN_CH3 = {
    24: "soft_1",
    25: "soft_2",
    26: "soft_3",
    27: "soft_4",
    28: "soft_5",
    29: "soft_6",
}

# ── Extended buttons as notes on ch10 (above pad range) ─────────────────
_BUTTON_NOTES_CH10 = {
    78: "bank",        # bank cycle button
    97: "play",        # transport play
    98: "stop",        # transport stop
    99: "record",      # transport record
}

# ── Touchstrip channel ──────────────────────────────────────────────────
_STRIP_CHANNEL = 7  # ch8 zero-indexed


class AtomSQ:
    """ATOM SQ driver using raw MIDI polling (no native mode).

    Provides:
    - 32 velocity-sensitive pads (indices 0-31)
    - Button callbacks for all detected buttons
    - Transport controls (play/stop/record)
    - Bank button
    - Touchstrip position data
    - Aftertouch data
    - Polling thread for reliable input on Pi ALSA
    """

    def __init__(self, midi_in: "rtmidi.MidiIn", midi_out: "rtmidi.MidiOut") -> None:
        if rtmidi is None:
            raise RuntimeError("python-rtmidi not installed")

        self._in = midi_in
        self._out = midi_out
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_pad_hit: Optional[Callable[[int, float], None]] = None
        self.on_pad_release: Optional[Callable[[int], None]] = None
        self.on_pad_aftertouch: Optional[Callable[[int, int], None]] = None
        self.on_button: Optional[Callable[[str, bool], None]] = None
        self.on_touchstrip: Optional[Callable[[int], None]] = None

        # Filter timing/active_sense
        self._in.ignore_types(sysex=True, timing=True, active_sense=True)

        # Start polling
        self._start_polling()
        log.info("ATOM SQ polling driver started (non-native mode)")

    def _start_polling(self) -> None:
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        while self._running:
            try:
                msg = self._in.get_message()
                if msg:
                    data, _ = msg
                    if data:
                        self._dispatch(data)
                else:
                    time.sleep(0.001)
            except Exception:
                log.exception("ATOM SQ poll error")
                time.sleep(0.1)

    def _dispatch(self, msg: List[int]) -> None:
        if len(msg) < 2:
            return

        status = msg[0]
        channel = status & 0x0F
        msg_type = status & 0xF0

        # ── Pads + extended button notes on ch10 ─────────────────────
        if channel == _PAD_CHANNEL and msg_type in (0x90, 0x80) and len(msg) >= 3:
            note = msg[1]
            vel_raw = msg[2]

            # Standard pads (notes 36-67)
            if _PAD_NOTE_LO <= note <= _PAD_NOTE_HI:
                pad_index = note - _PAD_NOTE_LO
                if msg_type == 0x90 and vel_raw > 0:
                    velocity = vel_raw / 127.0
                    if self.on_pad_hit:
                        self.on_pad_hit(pad_index, velocity)
                else:
                    if self.on_pad_release:
                        self.on_pad_release(pad_index)
                return

            # Extended buttons sent as notes (78, 97, 98, 99)
            if note in _BUTTON_NOTES_CH10:
                name = _BUTTON_NOTES_CH10[note]
                pressed = (msg_type == 0x90 and vel_raw > 0)
                if self.on_button:
                    self.on_button(name, pressed)
                return

            return

        # ── Aftertouch on ch10 (channel pressure) ────────────────────
        if channel == _PAD_CHANNEL and msg_type == 0xD0:
            pressure = msg[1]
            if self.on_pad_aftertouch:
                self.on_pad_aftertouch(-1, pressure)
            return

        # ── Poly aftertouch on ch10 ──────────────────────────────────
        if channel == _PAD_CHANNEL and msg_type == 0xA0 and len(msg) >= 3:
            note = msg[1]
            pressure = msg[2]
            if _PAD_NOTE_LO <= note <= _PAD_NOTE_HI:
                pad_index = note - _PAD_NOTE_LO
                if self.on_pad_aftertouch:
                    self.on_pad_aftertouch(pad_index, pressure)
            return

        # ── Touchstrip: Channel aftertouch on ch8 ───────────────────
        if channel == _STRIP_CHANNEL and msg_type == 0xD0:
            position = msg[1]
            if self.on_touchstrip:
                self.on_touchstrip(position)
            return

        # ── Buttons: CC on ch1 ───────────────────────────────────────
        if channel == 0 and msg_type == 0xB0 and len(msg) >= 3:
            cc = msg[1]
            value = msg[2]
            if cc in _BUTTON_CC_CH1:
                name = _BUTTON_CC_CH1[cc]
                pressed = value >= 64
                if self.on_button:
                    self.on_button(name, pressed)
                return

        # ── Soft buttons: CC on ch3 ──────────────────────────────────
        if channel == 2 and msg_type == 0xB0 and len(msg) >= 3:
            cc = msg[1]
            value = msg[2]
            if cc in _SOFT_BTN_CH3:
                name = _SOFT_BTN_CH3[cc]
                pressed = value >= 64
                if self.on_button:
                    self.on_button(name, pressed)
                return

    @property
    def connected(self) -> bool:
        return self._running

    def shutdown(self) -> None:
        """Stop polling and close ports."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
        try:
            self._in.close_port()
        except Exception:
            pass
        try:
            self._out.close_port()
        except Exception:
            pass
        log.info("ATOM SQ shut down")

    def __enter__(self) -> "AtomSQ":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()


def find_atom_sq_ports(
    port_hint: str = "ATM SQ",
):
    """Find and open ATOM SQ MIDI ports. Returns (MidiIn, MidiOut) or (None, None)."""
    if rtmidi is None:
        return None, None

    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()

    in_port = None
    out_port = None

    for i in range(midi_in.get_port_count()):
        name = midi_in.get_port_name(i)
        if port_hint in name and "Control" not in name and "Through" not in name:
            in_port = i
            break

    for i in range(midi_out.get_port_count()):
        name = midi_out.get_port_name(i)
        if port_hint in name and "Control" not in name and "Through" not in name:
            out_port = i
            break

    if in_port is None:
        try:
            midi_in.delete()
        except Exception:
            pass
        try:
            midi_out.delete()
        except Exception:
            pass
        return None, None

    midi_in.open_port(in_port)
    if out_port is not None:
        midi_out.open_port(out_port)
    else:
        try:
            midi_out.delete()
        except Exception:
            pass
        midi_out = None

    log.info("ATOM SQ opened: in=%s", midi_in.get_port_name(in_port))
    return midi_in, midi_out
