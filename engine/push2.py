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
    # Mode buttons (bottom row)
    50: "note",
    51: "session",
    58: "scale",
    # Right column above D-pad
    110: "device",
    111: "browse",
    112: "mix",
    113: "clip",
    # Left side, above the launch column
    52: "add_device",
    53: "add_track",
    28: "master",
    56: "repeat",
    57: "accent",
    # Edit / pattern column (left side)
    35: "convert",
    87: "new",
    90: "fixed_length",
    116: "quantize",
    117: "double_loop",
    # 8 launch buttons with time divisions (top→bottom: 1/4 → 1/32t)
    43: "launch_1",
    42: "launch_2",
    41: "launch_3",
    40: "launch_4",
    39: "launch_5",
    38: "launch_6",
    37: "launch_7",
    36: "launch_8",
}

# Special-purpose encoders (separate from the 8 main perf encoders).
# Same relative-encoding scheme: CW = 1..63, CCW = 127..65 as 2's-complement.
SPECIAL_ENCODER_CCS: dict[int, str] = {
    14: "tempo",     # left-side notched
    15: "swing",     # left-side smooth
    79: "master",    # top-right smooth
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

# Dim variants of each bank color, used for the inactive-bank slots
# in the bank-selector row and for "available pattern" cells in the
# bank-tinted pattern row. Indices match SP_BANK_COLORS.
SP_BANK_COLORS_DIM = [
    1,    # A: dim red
    45,   # B: dim blue
    19,   # C: dim green
    15,   # D: dim yellow
    11,   # E: dim orange
    55,   # F: dim magenta
    38,   # G: dim teal
    7,    # H: dim lime
    80,   # I: dim purple
    3,    # J: dim white
]


# ── Scale definitions ──────────────────────────────────────────────
# Each entry: list of pitch-class offsets from the root (0..11). The
# scale lookup is `(note - root_pc) % 12 in offsets`.
SCALES: list[tuple[str, tuple[int, ...]]] = [
    ("chromatic",  (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)),
    ("major",      (0, 2, 4, 5, 7, 9, 11)),
    ("minor",      (0, 2, 3, 5, 7, 8, 10)),
    ("min pent",   (0, 3, 5, 7, 10)),
    ("maj pent",   (0, 2, 4, 7, 9)),
    ("blues",      (0, 3, 5, 6, 7, 10)),
    ("dorian",     (0, 2, 3, 5, 7, 9, 10)),
    ("mixolydian", (0, 2, 4, 5, 7, 9, 10)),
    ("harm minor", (0, 2, 3, 5, 7, 8, 11)),
]
ROOT_NAMES = ["C", "C#", "D", "D#", "E", "F",
              "F#", "G", "G#", "A", "A#", "B"]


def in_scale(note: int, root_pc: int, offsets: tuple[int, ...]) -> bool:
    """True if `note` is a member of the scale rooted at `root_pc`."""
    return ((note - root_pc) % 12) in offsets


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
        # Special encoders: callback(name, delta_int).
        self.on_special_encoder: Optional[Callable[[str, int], None]] = None
        # Touch strip: callback(value_14bit) where 8192 = center.
        self.on_pitch_bend: Optional[Callable[[int], None]] = None

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
        """SP-style 2-row-per-bank layout (top-down: Bank A at the top,
        D at the bottom of each page). 4 banks per page.

        pad_page 0 = banks 0-3 (A-D), pad_page 1 = banks 4-7 (E-H),
        pad_page 2 = banks 8-9 (I-J). Each bank occupies 2 rows × 8
        cols = 16 pads. Pads whose effective bank is beyond num_banks
        are blanked."""
        palette = colors or SP_BANK_COLORS
        for pad in range(64):
            # Push 2 pad 0 is bottom-left; row 0 = bottom row. We want
            # Bank A on top, so the topmost row pair (rows 6-7) maps to
            # bank-on-page 0.
            row_pair_from_bottom = pad // 16   # 0 (bot pair) .. 3 (top pair)
            bank_in_page = 3 - row_pair_from_bottom
            effective_bank = pad_page * 4 + bank_in_page
            if effective_bank < 0 or effective_bank >= num_banks:
                self.set_pad_color(pad, COLOR_OFF)
            else:
                idx = effective_bank if effective_bank < len(palette) else 0
                self.set_pad_color(pad, palette[idx])

    def light_quad_bank_layout(self, pad_page: int, num_banks: int,
                               colors: list[int] | None = None) -> None:
        """4×4-quadrant layout. Each page shows up to 4 banks laid out
        like the SP itself: TL = first bank on page, TR = second,
        BL = third, BR = fourth. Within each quadrant, pad 1 is at the
        top-left and numbers fill row-by-row (matches SP pad numbering
        and `_compute_pad_note`'s pad_idx 0..15).

        Empty quadrants (no bank assigned at this page) are blanked."""
        palette = colors or SP_BANK_COLORS
        for idx in range(64):
            push2_row = idx // 8        # 0 = bottom row, 7 = top row
            push2_col = idx % 8
            top_half = push2_row >= 4
            right_half = push2_col >= 4
            # Quadrant index on this page: TL=0, TR=1, BL=2, BR=3.
            if top_half:
                bank_in_page = 0 if not right_half else 1
            else:
                bank_in_page = 2 if not right_half else 3
            effective_bank = pad_page * 4 + bank_in_page
            if effective_bank < 0 or effective_bank >= num_banks:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            cidx = effective_bank if effective_bank < len(palette) else 0
            self.set_pad_color(idx, palette[cidx])

    @staticmethod
    def quad_pad_to_bank_pad(idx: int) -> tuple[int, int]:
        """Inverse of light_quad_bank_layout — returns
        (bank_in_page, pad_in_bank_idx) for the given Push 2 pad idx.
        pad_in_bank_idx is 0..15 in SP top-left-first numbering, ready
        for `_compute_pad_note`."""
        push2_row = idx // 8        # 0 = bottom row
        push2_col = idx % 8
        top_half = push2_row >= 4
        right_half = push2_col >= 4
        if top_half:
            bank_in_page = 0 if not right_half else 1
        else:
            bank_in_page = 2 if not right_half else 3
        # Within a 4-row tall quadrant, top of quadrant is the row with
        # row_in_quad = 3 (push2_row 7 for top half, 3 for bottom half).
        row_in_quad = push2_row % 4
        col_in_quad = push2_col % 4
        # Pad 1 is top-left of quadrant; pad_in_bank goes left-to-right,
        # top-to-bottom (matches SP `_compute_pad_note` indexing).
        pad_in_bank = (3 - row_in_quad) * 4 + col_in_quad
        return (bank_in_page, pad_in_bank)

    @staticmethod
    def two_row_pad_to_bank_pad(idx: int) -> tuple[int, int]:
        """Inverse of light_bank_frame_for_page — returns
        (bank_in_page, pad_in_bank_idx) where pad_in_bank_idx is 0..15
        in SP top-left-first numbering.

        2x8 strip per bank: top row of the pair = SP rows 0-1 (pads
        1-8), bottom row of the pair = SP rows 2-3 (pads 9-16)."""
        push2_row = idx // 8        # 0 = bottom row
        push2_col = idx % 8
        row_pair_from_bottom = push2_row // 2     # 0..3
        bank_in_page = 3 - row_pair_from_bottom   # top pair = bank 0
        # Within a 2-row strip, the upper Push 2 row is the SP top half
        # (pads 1-8) and the lower Push 2 row is the SP bottom half
        # (pads 9-16). Push 2 col directly maps to "pad column" 0..7,
        # which we split into SP cols 0..3 for the left half + 0..3
        # for the right half.
        upper_of_pair = (push2_row % 2 == 1)
        sp_row_start = 0 if upper_of_pair else 2
        # Map 8 push2 cols → 2 SP rows × 4 cols. Cols 0-3 = SP row N,
        # cols 4-7 = SP row N+1.
        if push2_col < 4:
            sp_row = sp_row_start
            sp_col = push2_col
        else:
            sp_row = sp_row_start + 1
            sp_col = push2_col - 4
        pad_in_bank = sp_row * 4 + sp_col
        return (bank_in_page, pad_in_bank)

    # ── P-6 control layouts ────────────────────────────────────────

    def light_p6_row_layout(self, num_banks: int = 8,
                            colors: list[int] | None = None) -> None:
        """P-6 row-per-bank layout — all banks visible at once.

        P-6 has 6 pads × up to 8 banks. Each Push 2 row holds one bank:
        cols 0-5 = pads 1-6, cols 6-7 = blank. Bank 1 = top row, Bank
        N = bottom. Banks beyond num_banks are blanked."""
        palette = colors or SP_BANK_COLORS
        for idx in range(64):
            push2_row = idx // 8        # 0 = bottom row
            push2_col = idx % 8
            bank = 7 - push2_row        # row 7 = bank 0, row 0 = bank 7
            if bank >= num_banks or push2_col >= 6:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            cidx = bank if bank < len(palette) else 0
            self.set_pad_color(idx, palette[cidx])

    @staticmethod
    def p6_row_pad_to_bank_pad(idx: int) -> tuple[int, int]:
        """Inverse of light_p6_row_layout. Returns (bank, pad_in_bank)
        where pad_in_bank is 0..5 (P-6 pad 1..6 left to right). Returns
        (-1, -1) for blank cells (cols 6-7)."""
        push2_row = idx // 8
        push2_col = idx % 8
        if push2_col >= 6:
            return (-1, -1)
        bank = 7 - push2_row
        return (bank, push2_col)

    def light_p6_quad_layout(self, pad_page: int, num_banks: int = 8,
                             colors: list[int] | None = None) -> None:
        """P-6 4×4-quadrant layout. 4 banks per page, each bank's 6
        pads laid out in the top 2 rows × first 3 cols of its quadrant
        (mirrors the P-6 hardware's 2x3 pad layout). Page 0 = banks
        0-3, page 1 = banks 4-7."""
        palette = colors or SP_BANK_COLORS
        for idx in range(64):
            push2_row = idx // 8
            push2_col = idx % 8
            top_half = push2_row >= 4
            right_half = push2_col >= 4
            if top_half:
                bank_in_page = 0 if not right_half else 1
            else:
                bank_in_page = 2 if not right_half else 3
            effective_bank = pad_page * 4 + bank_in_page
            if effective_bank < 0 or effective_bank >= num_banks:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            row_in_quad = push2_row % 4
            col_in_quad = push2_col % 4
            # Top 2 rows of the 4-row quadrant + leftmost 3 cols hold the
            # 6 P-6 pads. Bottom 2 rows + rightmost col are blank.
            if row_in_quad < 2 or col_in_quad >= 3:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            cidx = effective_bank if effective_bank < len(palette) else 0
            self.set_pad_color(idx, palette[cidx])

    @staticmethod
    def p6_quad_pad_to_bank_pad(idx: int) -> tuple[int, int]:
        """Inverse of light_p6_quad_layout. Returns
        (bank_in_page, pad_in_bank) where pad_in_bank is 0..5 (P-6 pad
        1..6 in 2 rows × 3 cols, top row = pads 1-3). Returns (-1, -1)
        for cells outside the 2x3 active area."""
        push2_row = idx // 8
        push2_col = idx % 8
        top_half = push2_row >= 4
        right_half = push2_col >= 4
        if top_half:
            bank_in_page = 0 if not right_half else 1
        else:
            bank_in_page = 2 if not right_half else 3
        row_in_quad = push2_row % 4
        col_in_quad = push2_col % 4
        if row_in_quad < 2 or col_in_quad >= 3:
            return (-1, -1)
        # Pad 1 = top-left of quadrant's 2x3 area.
        # row_in_quad 3 = top of quadrant → P-6 row 0 (pads 1-3)
        # row_in_quad 2 = second row → P-6 row 1 (pads 4-6)
        p6_row = 3 - row_in_quad        # 0 or 1
        pad_in_bank = p6_row * 3 + col_in_quad
        return (bank_in_page, pad_in_bank)

    def set_pads_from_compa_banks(self, has_sample_flags: list[bool]) -> None:
        for i, loaded in enumerate(has_sample_flags[:64]):
            bank = i // 16
            color = BANK_COLORS[bank] if loaded else BANK_COLORS_DIM[bank]
            self.set_pad_color(i, color)

    def flash_pad(self, pad_idx: int, color: int = COLOR_ACCENT) -> None:
        """Transient flash that does not update the base color."""
        self._send_pad_raw(pad_idx, color)

    # ── Keys mode pad layout ───────────────────────────────────────

    def light_keys_layout(self, base_note: int = 36,
                          row_offset: int = 5,
                          min_note: int | None = None,
                          max_note: int | None = None,
                          scale: tuple[int, ...] | None = None,
                          root_pc: int = 0) -> None:
        """Light pads as a chromatic keyboard with rows offset by
        `row_offset` semitones.

        Color rules:
          - root note (every octave of root_pc): blue
          - in-scale notes:                       white
          - off-scale notes (scale != chromatic): very dim
          - out of [min_note, max_note] range:    off

        When `scale` is None, treat all 12 pitch classes as in-scale
        (chromatic). Scale-locked dispatch is handled in the app
        layer; the layout itself only colors."""
        lo = 0 if min_note is None else min_note
        hi = 127 if max_note is None else max_note
        all_pcs = scale is None or scale == SCALES[0][1]
        scale_set = set(scale) if scale else None
        for idx in range(64):
            row = idx // 8
            col = idx % 8
            note = base_note + row * row_offset + col
            if note > 127 or note < lo or note > hi:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            pc_off = (note - root_pc) % 12
            is_root = pc_off == 0
            in_sc = all_pcs or (scale_set is not None and pc_off in scale_set)
            if is_root:
                color = 125          # blue root
            elif in_sc:
                color = 122          # white in-scale
            else:
                color = 1            # very dim off-scale
            self.set_pad_color(idx, color)

    @staticmethod
    def keys_pad_to_note(pad_idx: int, base_note: int = 36,
                         row_offset: int = 5) -> int:
        """Inverse of light_keys_layout — returns MIDI note for pad."""
        row = pad_idx // 8
        col = pad_idx % 8
        return base_note + row * row_offset + col

    # ── In-key layout (every pad is a scale note) ────────────────────

    @staticmethod
    def in_key_pad_to_note(pad_idx: int,
                           scale_offsets: tuple[int, ...],
                           root_pc: int = 0,
                           base_note: int = 36,
                           row_offset_degrees: int = 3) -> int:
        """Map pad to MIDI note in in-key layout. Bottom-left pad
        plays the root in the octave containing `base_note`. Row
        offset of 3 scale degrees gives Ableton-Push-style chord
        shapes (root + 3rd + 5th vertically aligned)."""
        row = pad_idx // 8
        col = pad_idx % 8
        n = max(1, len(scale_offsets))
        degree = row * row_offset_degrees + col
        oct_shift = degree // n
        pc_in = scale_offsets[degree % n]
        base_oct_midi = (base_note // 12) * 12
        return base_oct_midi + root_pc + oct_shift * 12 + pc_in

    # ── DJ mode layout ──────────────────────────────────────────────

    # Order matches the columns of light_dj_layout:
    # col 0..4 = PLAY, CUE, SYNC, BEND+, BEND-.
    DJ_BUTTON_CCS = (20, 23, 22, 24, 25)
    DJ_BUTTON_COLORS = (126, 8, 50, 9, 9)  # green, yellow, blue, orange x2

    def light_dj_layout(self) -> None:
        """Bottom 2 rows are deck buttons (row 0 = Deck A, row 1 = Deck B);
        cols 0-4 = PLAY / CUE / SYNC / BEND+ / BEND-. Other pads off."""
        for idx in range(64):
            row = idx // 8
            col = idx % 8
            if row in (0, 1) and col < 5:
                color = self.DJ_BUTTON_COLORS[col]
            else:
                color = COLOR_OFF
            self.set_pad_color(idx, color)

    # ── Looper mode layout (visual only — Compa doesn't have CCs wired) ─

    LOOPER_BUTTON_COLORS = (127, 8, 122, 1, 3, 3)  # REC, OVERDUB, STOP, DELETE, UNDO, REDO

    def light_looper_layout(self) -> None:
        """3-cols x 2-rows looper button grid centered on the bottom
        of the pad surface. Mirrors Compa's on-screen looper layout."""
        for idx in range(64):
            row = idx // 8
            col = idx % 8
            # Place buttons in cols 1-3, rows 0-1: [REC OVERDUB STOP] /
            # [DELETE UNDO REDO]. cols 0 and 4-7 stay dark.
            if row in (0, 1) and 1 <= col <= 3:
                btn_idx = (1 - row) * 3 + (col - 1)  # 0..5: REC,OD,STOP,DEL,UNDO,REDO
                color = self.LOOPER_BUTTON_COLORS[btn_idx]
            else:
                color = COLOR_OFF
            self.set_pad_color(idx, color)

    # ── Pattern launch layout ───────────────────────────────────────

    def light_pattern_launch_layout(self, current_pattern: int,
                                     total_patterns: int,
                                     bright_color: int,
                                     dim_color: int) -> None:
        """Paint a pattern-launch grid that mirrors Compa's PATTERN tab.

        Pattern 1 = top-left. P-6 (64 patterns) fills the whole 8x8;
        SP-404 (16 patterns) fills the bottom-left 4x4 quadrant.
        Current pattern lights bright; others dim. Outside the active
        region stays off."""
        if total_patterns >= 64:
            n_cols, n_rows = 8, 8
        elif total_patterns >= 16:
            n_cols, n_rows = 4, 4
        else:
            n_cols, n_rows = 8, 1
        for idx in range(64):
            row = idx // 8
            col = idx % 8
            if col >= n_cols or row >= n_rows:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            inv_row = (n_rows - 1) - row
            pattern = inv_row * n_cols + col + 1
            if pattern > total_patterns:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            self.set_pad_color(idx,
                               bright_color if pattern == current_pattern
                               else dim_color)

    @staticmethod
    def pattern_launch_pad_to_pattern(pad_idx: int,
                                      total_patterns: int) -> int | None:
        """Inverse of light_pattern_launch_layout — pad idx → pattern N
        (1-indexed). Returns None if the pad is outside the active grid."""
        if total_patterns >= 64:
            n_cols, n_rows = 8, 8
        elif total_patterns >= 16:
            n_cols, n_rows = 4, 4
        else:
            return None
        row = pad_idx // 8
        col = pad_idx % 8
        if col >= n_cols or row >= n_rows:
            return None
        inv_row = (n_rows - 1) - row
        pattern = inv_row * n_cols + col + 1
        if pattern > total_patterns:
            return None
        return pattern

    # ── Combined Pattern mode layout (launch + step seq) ────────────

    def light_combined_pattern_layout(
            self, current_pattern: int, total_patterns: int,
            pattern_launch_page: int,
            seq, step_offset: int,
            launch_bright: int, launch_dim: int,
            pad_offset: int = 0,
            active_bank: int = 0,
            bank_offset: int = 0,
            bank_total: int = 8) -> None:
        """Row 7 = bank selector, row 6 = pattern launch (active-bank
        tinted), rows 0-5 = step seq.

        Row 7 (banks):
          col 0..7 = bank (offset + col), unique color per bank
          - active bank lit at full brightness
          - other available banks lit dim (per SP_BANK_COLORS_DIM)
          - off when beyond bank_total

        Row 6 (patterns, 8 per page):
          col N = pattern (page*8 + N)
          - active pattern lit at full bank-color brightness
          - other available patterns dim in the active bank's color
          - off when beyond total_patterns

        Bottom 6 rows (step seq):
          row 5 = pad (pad_offset + 0), row 0 = pad (pad_offset + 5)
          - programmed step → row's color
          - playhead column → bright white if on / dim white if off
          - off step + not playhead → off
        """
        current = (seq.current_step
                   if seq is not None and getattr(seq, "playing", False)
                   else -1)
        num_pads = getattr(seq, "num_pads", 0) if seq is not None else 0
        num_steps = getattr(seq, "num_steps", 0) if seq is not None else 0
        base_pat = pattern_launch_page * 8

        # Bank colors (full-brightness) and dim variants — reuse the
        # SP-bank palette so SP and P-6 share the same color identity
        # for banks A..H, with I/J extending for SP.
        bank_palette = SP_BANK_COLORS
        bank_dim_palette = SP_BANK_COLORS_DIM
        active_color = (bank_palette[active_bank]
                        if 0 <= active_bank < len(bank_palette) else 122)
        active_dim = (bank_dim_palette[active_bank]
                      if 0 <= active_bank < len(bank_dim_palette) else 3)

        # Device-themed pair: launch_bright is the device color
        # (yellow for P-6, orange for SP); launch_dim is its dim
        # variant. Used to color-tint the bank/pattern rows so the
        # whole pattern surface reads as that device.
        for idx in range(64):
            row = idx // 8
            col = idx % 8
            if row == 7:
                # Bank selector — dim white for inactive available
                # banks; active bank lights in the device theme color
                # so the row reads like a "selected tab".
                slot = bank_offset + col
                if slot >= bank_total:
                    self.set_pad_color(idx, COLOR_OFF)
                elif slot == active_bank:
                    self.set_pad_color(idx, launch_bright)
                else:
                    self.set_pad_color(idx, 3)   # dim white
                continue
            if row == 6:
                # Pattern launchers — dim device-tinted for inactive
                # available patterns; active pattern in full device
                # color. The dim tint differentiates this row from the
                # white-toned bank row above.
                pat = base_pat + col + 1
                if pat > total_patterns:
                    color = COLOR_OFF
                elif pat == current_pattern:
                    color = launch_bright
                else:
                    color = launch_dim
                self.set_pad_color(idx, color)
                continue
            pad = (5 - row) + pad_offset
            step = step_offset + col
            if seq is None or pad >= num_pads or step >= num_steps:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            try:
                is_on = bool(seq.grid[pad][step].active)
            except Exception:
                is_on = False
            is_current = step == current
            row_color = self.PATTERN_ROW_COLORS[pad % len(self.PATTERN_ROW_COLORS)]
            if is_on and is_current:
                color = 122
            elif is_on:
                color = row_color
            elif is_current:
                color = 3
            else:
                color = COLOR_OFF
            self.set_pad_color(idx, color)

    # ── Step-sequencer mode layout (kept for future re-expose) ──────

    # Color per row in the pattern grid so each pad is visually distinct.
    PATTERN_ROW_COLORS = [127, 125, 126, 8, 9, 60, 73, 122]

    def light_step_only_layout(
            self, seq, step_offset: int = 0,
            pad_offset: int = 0,
            num_pads_visible: int = 8) -> None:
        """Pure step-sequencer pad layout — all 8 rows × 8 cols dedicated
        to step cells. Bank selector + pattern launchers live on the
        Push 2 top/bot select button rows (above/below the display)
        instead of stealing pad rows.

        Pad mapping (top-down):
          row 7 = pad (pad_offset + 0)
          row 6 = pad (pad_offset + 1)
          ...
          row 0 = pad (pad_offset + 7)

        For devices with fewer pads than visible rows (P-6 = 6), rows
        beyond `num_pads_visible` are blanked. For devices with more
        pads than 8 (SP-404 = 16), `pad_offset` shifts the visible
        window — 2 pages of 8."""
        current = (seq.current_step
                   if seq is not None and getattr(seq, "playing", False)
                   else -1)
        num_pads = getattr(seq, "num_pads", 0) if seq is not None else 0
        num_steps = getattr(seq, "num_steps", 0) if seq is not None else 0
        for idx in range(64):
            row = idx // 8
            col = idx % 8
            # Top row of grid → pad (pad_offset + 0). Row 7 of Push 2
            # is the topmost row.
            local_idx = 7 - row
            if local_idx >= num_pads_visible:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            pad = pad_offset + local_idx
            step = step_offset + col
            if seq is None or pad >= num_pads or step >= num_steps:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            try:
                is_on = bool(seq.grid[pad][step].active)
            except Exception:
                is_on = False
            is_current = step == current
            row_color = self.PATTERN_ROW_COLORS[
                pad % len(self.PATTERN_ROW_COLORS)]
            if is_on and is_current:
                color = 122
            elif is_on:
                color = row_color
            elif is_current:
                color = 3
            else:
                color = COLOR_OFF
            self.set_pad_color(idx, color)

    def light_pattern_layout(self, seq, step_offset: int = 0) -> None:
        """Paint Push 2 as a step-sequencer grid.

        Layout:
          row N  → pad N of the sequencer (rows 0-7 → pads 0-7)
          col N  → step (step_offset + N), 8 visible at a time
          page_left/right cycles step_offset for 16-step patterns

        Lighting:
          step on  + current playhead → bright white (playing now)
          step on  + not current      → row's color (programmed)
          step off + current playhead → dim white (playhead chase)
          step off + not current      → off
        Pads outside the sequencer's pad/step bounds stay off."""
        if seq is None:
            for i in range(64):
                self.set_pad_color(i, COLOR_OFF)
            return
        current = seq.current_step if getattr(seq, "playing", False) else -1
        num_pads = getattr(seq, "num_pads", 8)
        num_steps = getattr(seq, "num_steps", 16)
        for idx in range(64):
            row = idx // 8
            col = idx % 8
            step = step_offset + col
            if row >= num_pads or step >= num_steps:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            try:
                is_on = bool(seq.grid[row][step].active)
            except Exception:
                is_on = False
            is_current = step == current
            if is_on and is_current:
                color = 122
            elif is_on:
                color = self.PATTERN_ROW_COLORS[row % len(self.PATTERN_ROW_COLORS)]
            elif is_current:
                color = 3
            else:
                color = COLOR_OFF
            self.set_pad_color(idx, color)

    def light_in_key_layout(self, scale_offsets: tuple[int, ...],
                            root_pc: int = 0, base_note: int = 36,
                            row_offset_degrees: int = 3,
                            min_note: int | None = None,
                            max_note: int | None = None) -> None:
        """Paint pads as in-key layout: every pad lights either blue
        (root, every octave) or white (in-scale). No off-scale pads
        because every pad IS a scale note."""
        lo = 0 if min_note is None else min_note
        hi = 127 if max_note is None else max_note
        for idx in range(64):
            note = Push2.in_key_pad_to_note(
                idx, scale_offsets, root_pc, base_note, row_offset_degrees)
            if note > 127 or note < lo or note > hi:
                self.set_pad_color(idx, COLOR_OFF)
                continue
            if (note - root_pc) % 12 == 0:
                self.set_pad_color(idx, 125)  # blue root
            else:
                self.set_pad_color(idx, 122)  # white in-scale

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
                # 5ms idle sleep — well under USB-MIDI latency
                # (~10ms typical) so input still feels instant, but
                # cuts the wake-up rate from 1000/s to 200/s. Two of
                # these threads run for the Push 2 (User + Live ports)
                # and the savings compound across every MIDI poll
                # loop in the codebase.
                time.sleep(0.005)
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
                    # Note-off OR note-on velocity 0 — restore base AND
                    # notify the app handler so modes that need release
                    # events (Keys, Sequence) can act on it.
                    self.restore_pad(idx)
                    if self.on_pad is not None:
                        try:
                            self.on_pad(idx, 0)
                        except Exception as e:
                            log.warning("Push 2 pad release callback failed: %s", e)
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
            # Special encoders (Tempo / Swing / Master).
            if channel == PAD_CHANNEL and cc in SPECIAL_ENCODER_CCS:
                if self.on_special_encoder is not None:
                    delta = value - 128 if value >= 64 else value
                    if delta != 0:
                        name = SPECIAL_ENCODER_CCS[cc]
                        try:
                            self.on_special_encoder(name, delta)
                        except Exception as e:
                            log.warning("Push 2 special encoder failed: %s", e)
                return
            name = _BUTTON_CC.get(cc) if channel == PAD_CHANNEL else None
            if name and self.on_button is not None:
                try:
                    self.on_button(name, value)
                except Exception as e:
                    log.warning("Push 2 button callback failed: %s", e)
            elif name is None and value > 0:
                print(f"[push2] unmapped CC ch{channel} cc={cc} "
                      f"val={value}", flush=True)
            return

        # Channel pressure (touchstrip) and other types — log when they
        # carry a meaningful value so we can map them.
        if status == 0xD0 and len(data) >= 2 and data[1] > 0:
            print(f"[push2] channel pressure ch{channel} val={data[1]}",
                  flush=True)
            return
        if status == 0xE0 and len(data) >= 3:
            # Pitch bend — Push 2's touch strip in default firmware mode.
            val = (data[2] << 7) | data[1]
            if self.on_pitch_bend is not None:
                try:
                    self.on_pitch_bend(val)
                except Exception as e:
                    log.warning("Push 2 pitch bend callback failed: %s", e)
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
