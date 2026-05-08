"""Sysex command builders for Push 2.

Each function returns a complete sysex message as bytes, ready to send
through the Live MIDI port. The constants live in constants.py; this
file only knows how to assemble bytes.
"""
from __future__ import annotations

from . import constants as C


def _frame(cmd: int, *args: int) -> bytes:
    """Wrap a command + args in the Push 2 sysex envelope."""
    payload = [
        C.SYSEX_START,
        *C.ABLETON_MFR_ID,
        C.SYSEX_DEVICE_ID,
        C.SYSEX_MODEL_ID,
        cmd,
        *args,
        C.SYSEX_END,
    ]
    if any(b > 0x7F and b not in (C.SYSEX_START, C.SYSEX_END) for b in payload):
        # Sysex bytes must be 0–127 except for the F0/F7 framers.
        raise ValueError(f"sysex byte out of range in {[hex(b) for b in payload]}")
    return bytes(payload)


# ── MIDI mode ──────────────────────────────────────────────────────
def set_midi_mode(mode: int) -> bytes:
    """0 = Live, 1 = User, 2 = Dual."""
    if mode not in (C.MIDI_MODE_LIVE, C.MIDI_MODE_USER, C.MIDI_MODE_DUAL):
        raise ValueError(f"invalid midi mode {mode}")
    return _frame(C.CMD_SET_MIDI_MODE, mode)


# ── Aftertouch mode ────────────────────────────────────────────────
def set_aftertouch_mode(mode: int) -> bytes:
    """0 = channel pressure, 1 = poly key pressure."""
    if mode not in (C.AFTERTOUCH_CHANNEL, C.AFTERTOUCH_POLY):
        raise ValueError(f"invalid aftertouch mode {mode}")
    return _frame(C.CMD_SET_AFTERTOUCH_MODE, mode)


def set_aftertouch_threshold(low: int, high: int) -> bytes:
    """Both args 0–4095 (12-bit)."""
    for v, n in ((low, "low"), (high, "high")):
        if not (0 <= v <= 4095):
            raise ValueError(f"{n} out of 0–4095: {v}")
    return _frame(
        C.CMD_SET_AFTERTOUCH_THRESHOLD,
        low & 0x7F, (low >> 7) & 0x1F,
        high & 0x7F, (high >> 7) & 0x1F,
    )


# ── Brightness ─────────────────────────────────────────────────────
def set_led_brightness(level: int) -> bytes:
    """0–127."""
    if not (0 <= level <= 127):
        raise ValueError(f"led brightness out of 0–127: {level}")
    return _frame(C.CMD_SET_LED_BRIGHTNESS, level)


def set_display_brightness(level: int) -> bytes:
    """0–255 (split into two 7-bit bytes by the protocol)."""
    if not (0 <= level <= 255):
        raise ValueError(f"display brightness out of 0–255: {level}")
    return _frame(C.CMD_SET_DISPLAY_BRIGHTNESS, level & 0x7F, (level >> 7) & 0x01)


# ── Palette ────────────────────────────────────────────────────────
def set_palette_entry(idx: int, r: int, g: int, b: int, w: int = 0) -> bytes:
    """Set palette entry idx (0–127) to (r,g,b,w) 8-bit each.

    Push 2's palette wire format splits each 8-bit channel into a 7+1
    pair (low 7 bits + high 1 bit). So one channel sends as two bytes.
    """
    if not (0 <= idx <= 127):
        raise ValueError(f"palette idx out of 0–127: {idx}")
    for v, n in ((r, "r"), (g, "g"), (b, "b"), (w, "w")):
        if not (0 <= v <= 255):
            raise ValueError(f"{n} out of 0–255: {v}")
    return _frame(
        C.CMD_SET_PALETTE_ENTRY,
        idx,
        r & 0x7F, (r >> 7) & 0x01,
        g & 0x7F, (g >> 7) & 0x01,
        b & 0x7F, (b >> 7) & 0x01,
        w & 0x7F, (w >> 7) & 0x01,
    )


def reapply_palette() -> bytes:
    return _frame(C.CMD_REAPPLY_PALETTE)


# ── Touch strip ────────────────────────────────────────────────────
def set_touch_strip_config(
    *,
    led_control_host: bool = False,
    host_sends_values: bool = True,
    pitch_bend: bool = True,
    bar_display: bool = True,
    bar_starts_bottom: bool = True,
    autoreturn_off: bool = False,
    autoreturn_to_bottom: bool = False,
) -> bytes:
    """Build the 7-bit flag word for sysex 0x17.

    Bit semantics per the Push 2 manual; defaults give Live's
    autoreturn-to-center pitch-bend strip.
    """
    flags = 0
    if led_control_host:        flags |= 0b0000001
    if host_sends_values:       flags |= 0b0000010
    if pitch_bend:              flags |= 0b0000100
    if bar_display:             flags |= 0b0001000
    if bar_starts_bottom:       flags |= 0b0010000
    if autoreturn_off:          flags |= 0b0100000
    if autoreturn_to_bottom:    flags |= 0b1000000
    return _frame(C.CMD_SET_TOUCH_STRIP_CONFIG, flags)


def set_touch_strip_leds(colors: list[int]) -> bytes:
    """Set all 31 strip LEDs. Each color is index 0–7.

    Per the manual, indices pack as 3-bit groups across 16 bytes total.
    """
    if len(colors) != C.TOUCH_STRIP_NUM_LEDS:
        raise ValueError(f"need {C.TOUCH_STRIP_NUM_LEDS} colors, got {len(colors)}")
    for v in colors:
        if not (0 <= v <= 7):
            raise ValueError(f"color out of 0–7: {v}")
    # Pack 31 × 3-bit = 93 bits → 14 bytes; padded to 16 in the frame.
    bits: list[int] = []
    for c in colors:
        bits.extend([(c >> 2) & 1, (c >> 1) & 1, c & 1])
    while len(bits) % 7:
        bits.append(0)
    bytes_out: list[int] = []
    for i in range(0, len(bits), 7):
        b = 0
        for j, bit in enumerate(bits[i:i + 7]):
            b |= bit << (6 - j)
        bytes_out.append(b)
    while len(bytes_out) < 16:
        bytes_out.append(0)
    return _frame(C.CMD_SET_TOUCH_STRIP_LEDS, *bytes_out[:16])


# ── Pad calibration / sensitivity ──────────────────────────────────
def set_pad_sensitivity(pad_idx: int, level: int) -> bytes:
    """Per-pad sensitivity 0 (regular) / 1 (reduced) / 2 (low).

    pad_idx is 0–63, sequential from bottom-left to top-right.
    """
    if not (0 <= pad_idx <= 63):
        raise ValueError(f"pad_idx out of 0–63: {pad_idx}")
    if level not in (0, 1, 2):
        raise ValueError(f"sensitivity level must be 0, 1, or 2: {level}")
    return _frame(C.CMD_SET_PAD_SENSITIVITY, pad_idx, level)
