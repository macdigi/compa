"""Ableton Push 2 driver for Compa (Phase 1 — MIDI only).

Claims both Push 2 MIDI ports (Live + User) via rtmidi so the device
works regardless of whether the user has pressed the "User" button.
No USB bulk display yet — that comes in Phase 2. Modeled on
engine.atom_sq (port claim + raw MIDI poll thread + callbacks) and
coexists with Twister / Spectra / ATOM SQ.

Push 2 MIDI layout (either port, channel 0 = ch1 1-indexed):
  Pads:        notes 36-99, 8×8 grid. Bottom-left = 36, top-right = 99.
               Bottom 2 rows (notes 36-51) map to Compa pad.trigger.1-16.
               Upper rows unused in Phase 1 — reserved for future
               bank-explicit triggering (rows 3-4 = B, 5-6 = C, 7-8 = D).
  Colors:      note-on with velocity = color palette index (0 = off).
               Sent to BOTH ports so LEDs work in either mode.
  Play button: CC 85 ch0
  Record:      CC 86 ch0

Dual-port strategy: MIDI goes out from Push 2 to whichever port
corresponds to its current mode (Live button lit → Live Port,
User button lit → User Port). Rather than force the user to press
a button, we open MIDI In on BOTH ports and handle events the same
way. For color output we also send on both ports, which costs
nothing and guarantees LEDs respond in either mode.
"""

import logging
import threading
import time
from typing import Callable, Optional

try:
    import rtmidi
except ImportError:
    rtmidi = None

log = logging.getLogger(__name__)

# ── Push 2 MIDI map ──────────────────────────────────────────────────

PAD_NOTE_LO = 36
PAD_NOTE_HI = 99
PAD_CHANNEL = 0  # ch1 in 1-indexed

_BUTTON_CC = {
    # Transport
    85: "play",
    86: "record",
    3: "tap_tempo",
    9: "metronome",
    29: "stop_clip",
    # Modifier / mode
    49: "shift",
    48: "select",
    88: "duplicate",
    118: "delete",
    119: "undo",
    89: "automate",
    60: "mute",
    61: "solo",
    # Mode buttons (right column)
    30: "setup",
    31: "layout",
    59: "user",
    # Navigation (arrow row, right of encoders)
    62: "page_left",
    63: "page_right",
    54: "octave_down",
    55: "octave_up",
    # D-pad (upper-left of pad grid area)
    44: "nav_left",
    45: "nav_right",
    46: "nav_up",
    47: "nav_down",
    # 8 select buttons ABOVE the display
    102: "top_select_1",
    103: "top_select_2",
    104: "top_select_3",
    105: "top_select_4",
    106: "top_select_5",
    107: "top_select_6",
    108: "top_select_7",
    109: "top_select_8",
    # 8 select buttons BELOW the display (above the pads)
    20: "bot_select_1",
    21: "bot_select_2",
    22: "bot_select_3",
    23: "bot_select_4",
    24: "bot_select_5",
    25: "bot_select_6",
    26: "bot_select_7",
    27: "bot_select_8",
}

# Reverse map — name → CC number, for sending LED color commands to
# the same CC that fires on press.
_BUTTON_NAME_TO_CC = {name: cc for cc, name in _BUTTON_CC.items()}

# 8 main performance encoders (below the display). In the default
# relative-mode firmware, turning CW sends 1-63, turning CCW sends
# 127-65 as 2's-complement of -1..-63.
ENCODER_CC_LO = 71
ENCODER_CC_HI = 78

# ── Pad color palette (default Push 2 palette indices) ──────────────
# Verified-visible entries from the Push 2 default palette. 122 is the
# canonical "bright white" used as a safe universal Phase 1 color.
COLOR_OFF = 0
COLOR_WHITE = 122
COLOR_WHITE_BRIGHT = 127
COLOR_RED = 127
COLOR_GREEN = 126
COLOR_BLUE = 125
COLOR_YELLOW = 8
COLOR_ACCENT = 127   # High-contrast accent used for press-flash against white base

# Bank colors — 4 per pad page, cycling through distinct palette slots.
BANK_COLORS = [COLOR_RED, COLOR_BLUE, COLOR_GREEN, COLOR_YELLOW]

# Full 10-bank SP-404 MK2 palette (A–J). Page 0 = A-D, page 1 = E-H,
# page 2 = I-J. Picked for visual distinction across pages so the
# user sees which pad page they're on at a glance.
SP_BANK_COLORS = [
    127,  # A: red
    125,  # B: blue
    126,  # C: green
    8,    # D: yellow
    9,    # E: orange
    60,   # F: magenta/pink
    40,   # G: teal
    5,    # H: lime
    73,   # I: purple
    122,  # J: white
]


# ── Port discovery ───────────────────────────────────────────────────

def find_push2_ports() -> dict:
    """Find Push 2 MIDI ports. Returns a dict with keys:
      'user_in', 'user_out', 'live_in', 'live_out'
    Each value is an opened rtmidi instance or None.
    """
    result = {"user_in": None, "user_out": None,
              "live_in": None, "live_out": None}
    if rtmidi is None:
        log.warning("rtmidi not available — Push 2 disabled")
        return result

    def _open_in(match_kind: str):
        mi = rtmidi.MidiIn()
        for i, name in enumerate(mi.get_ports()):
            lower = name.lower()
            if "push 2" in lower and match_kind in lower:
                mi.open_port(i)
                mi.ignore_types(sysex=False, timing=True, active_sense=True)
                return mi
        mi.delete()
        return None

    def _open_out(match_kind: str):
        mo = rtmidi.MidiOut()
        for i, name in enumerate(mo.get_ports()):
            lower = name.lower()
            if "push 2" in lower and match_kind in lower:
                mo.open_port(i)
                return mo
        mo.delete()
        return None

    result["user_in"] = _open_in("user")
    result["user_out"] = _open_out("user")
    result["live_in"] = _open_in("live")
    result["live_out"] = _open_out("live")
    return result


# ── Driver ───────────────────────────────────────────────────────────

class Push2:
    """Ableton Push 2 driver — pads, buttons, pad RGB feedback.

    Accepts a dict from find_push2_ports() with up to 4 opened ports.
    Callbacks:
      on_pad(index: int, velocity: int)     — pad index 0-63, velocity 0-127
      on_button(name: str, value: int)      — "play" / "record" / ...
      on_encoder(index: int, delta: int)    — encoder 0-7, delta in ticks
                                              (positive = CW, negative = CCW)
    """

    def __init__(self, ports: dict) -> None:
        self.user_in = ports.get("user_in")
        self.user_out = ports.get("user_out")
        self.live_in = ports.get("live_in")
        self.live_out = ports.get("live_out")

        self.on_pad: Optional[Callable[[int, int], None]] = None
        self.on_button: Optional[Callable[[str, int], None]] = None
        self.on_encoder: Optional[Callable[[int, int], None]] = None

        # Per-pad base color. Press-flash sends a transient color without
        # touching this; note-off restores the base so kit colors persist.
        self._base_colors: list[int] = [COLOR_OFF] * 64

        self._stop = threading.Event()
        self._threads = []

        # Paint the 4-bank frame on init so the bottom-to-top bank
        # layout reads visually at a glance (rows 1-2 = A, 3-4 = B,
        # 5-6 = C, 7-8 = D). App-level code may overwrite per-device.
        self.clear_all_pads()
        self.light_bank_frame()

        # Spin up a poll thread per input port we managed to open.
        if self.user_in is not None:
            t = threading.Thread(target=self._poll, args=(self.user_in, "User"),
                                 daemon=True, name="Push2PollUser")
            t.start()
            self._threads.append(t)
        if self.live_in is not None:
            t = threading.Thread(target=self._poll, args=(self.live_in, "Live"),
                                 daemon=True, name="Push2PollLive")
            t.start()
            self._threads.append(t)

        log.info("Push 2 driver started — ports: user_in=%s user_out=%s "
                 "live_in=%s live_out=%s",
                 self.user_in is not None, self.user_out is not None,
                 self.live_in is not None, self.live_out is not None)

    # ── Output helpers ──────────────────────────────────────────────

    def _send_both(self, msg: list[int]) -> None:
        """Send a MIDI message to BOTH output ports (Live + User) so it
        reaches the Push 2 regardless of which mode it's in."""
        if self.user_out is not None:
            try:
                self.user_out.send_message(msg)
            except Exception as e:
                log.debug("user_out send failed: %s", e)
        if self.live_out is not None:
            try:
                self.live_out.send_message(msg)
            except Exception as e:
                log.debug("live_out send failed: %s", e)

    def _send_pad_raw(self, pad_idx: int, color: int) -> None:
        """Send a pad color WITHOUT updating the remembered base. Used
        for transient press-flashes so the underlying kit color stays."""
        if not (0 <= pad_idx < 64):
            return
        note = PAD_NOTE_LO + pad_idx
        color = max(0, min(127, color))
        self._send_both([0x90 | PAD_CHANNEL, note, color])

    def set_pad_color(self, pad_idx: int, color: int) -> None:
        """Set pad color and remember it as the base (the color a pad
        returns to after a press-flash)."""
        if not (0 <= pad_idx < 64):
            return
        color = max(0, min(127, color))
        self._base_colors[pad_idx] = color
        self._send_pad_raw(pad_idx, color)

    def restore_pad(self, pad_idx: int) -> None:
        """Re-send the remembered base color for a pad."""
        if 0 <= pad_idx < 64:
            self._send_pad_raw(pad_idx, self._base_colors[pad_idx])

    def clear_all_pads(self) -> None:
        for i in range(64):
            self.set_pad_color(i, COLOR_OFF)

    def light_all(self, color: int) -> None:
        for i in range(64):
            self.set_pad_color(i, color)

    def light_bank_frame(self) -> None:
        """Paint a 4-bank frame so the 4×16 layout is visible."""
        for pad in range(64):
            bank = pad // 16
            self.set_pad_color(pad, BANK_COLORS[bank])

    def light_bank_frame_for_page(self, pad_page: int, num_banks: int,
                                  colors: list[int] | None = None) -> None:
        """Paint the pad grid for the given pad page, respecting how
        many banks the focused device actually has.

        pad_page 0 = banks 0-3 (A-D), pad_page 1 = banks 4-7 (E-H),
        pad_page 2 = banks 8-9 (I-J). Each bank occupies 2 rows × 8
        cols = 16 pads. Pads whose effective bank is beyond num_banks
        are blanked."""
        palette = colors or SP_BANK_COLORS
        for pad in range(64):
            row_pair = pad // 16        # 0..3
            effective_bank = pad_page * 4 + row_pair
            if effective_bank < 0 or effective_bank >= num_banks:
                self.set_pad_color(pad, COLOR_OFF)
            else:
                idx = effective_bank if effective_bank < len(palette) else 0
                self.set_pad_color(pad, palette[idx])

    def set_pads_from_compa_banks(self, has_sample_flags: list[bool]) -> None:
        for i, loaded in enumerate(has_sample_flags[:64]):
            bank = i // 16
            color = BANK_COLORS[bank] if loaded else BANK_COLORS_DIM[bank]
            self.set_pad_color(i, color)

    def flash_pad(self, pad_idx: int, color: int = COLOR_ACCENT) -> None:
        """Transient flash that does not update the base color."""
        self._send_pad_raw(pad_idx, color)

    # ── Button LED control ───────────────────────────────────────────

    def set_button(self, name: str, color: int) -> None:
        """Light a named Push 2 button. `color` is a palette index 0-127
        (0 = off). Silently no-ops if the name isn't known."""
        cc = _BUTTON_NAME_TO_CC.get(name)
        if cc is None:
            return
        color = max(0, min(127, color))
        self._send_both([0xB0 | PAD_CHANNEL, cc, color])

    # ── Input polling ───────────────────────────────────────────────

    def _poll(self, midi_in, label: str) -> None:
        while not self._stop.is_set():
            msg = midi_in.get_message()
            if msg is None:
                time.sleep(0.001)
                continue
            data, _ts = msg
            self._handle(data, label)

    def _handle(self, data: list[int], source: str) -> None:
        if not data:
            return
        status = data[0] & 0xF0
        channel = data[0] & 0x0F

        # Pad note on/off — auto-flash on press, auto-restore on release.
        if len(data) >= 3 and channel == PAD_CHANNEL and status in (0x90, 0x80):
            note = data[1]
            velocity = data[2]
            if PAD_NOTE_LO <= note <= PAD_NOTE_HI:
                idx = note - PAD_NOTE_LO
                is_press = (status == 0x90 and velocity > 0)
                if is_press:
                    self._send_pad_raw(idx, COLOR_ACCENT)
                    if self.on_pad is not None:
                        try:
                            self.on_pad(idx, velocity)
                        except Exception as e:
                            log.warning("Push 2 pad callback failed: %s", e)
                else:
                    # Note-off OR note-on velocity 0 — restore base.
                    self.restore_pad(idx)
                return
            return

        if status == 0xB0 and len(data) >= 3:
            cc, value = data[1], data[2]
            # Encoders — relative mode, only on PAD_CHANNEL.
            if channel == PAD_CHANNEL and ENCODER_CC_LO <= cc <= ENCODER_CC_HI:
                if self.on_encoder is not None:
                    delta = value - 128 if value >= 64 else value
                    if delta != 0:
                        try:
                            self.on_encoder(cc - ENCODER_CC_LO, delta)
                        except Exception as e:
                            log.warning("Push 2 encoder callback failed: %s", e)
                return
            name = _BUTTON_CC.get(cc) if channel == PAD_CHANNEL else None
            if name and self.on_button is not None:
                try:
                    self.on_button(name, value)
                except Exception as e:
                    log.warning("Push 2 button callback failed: %s", e)
            return

    # ── Lifecycle ───────────────────────────────────────────────────

    def shutdown(self) -> None:
        self._stop.set()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=0.5)
        try:
            self.clear_all_pads()
        except Exception:
            pass
        for port in (self.user_in, self.user_out, self.live_in, self.live_out):
            if port is None:
                continue
            try:
                port.close_port()
            except Exception:
                pass
        log.info("Push 2 driver stopped")
