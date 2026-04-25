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
            pad_offset: int = 0) -> None:
        """Top 2 rows = pattern launch; bottom 6 rows = step sequencer.

        Top:
          row 7 (idx 56-63, very top) = patterns page*16+0..7
          row 6 (idx 48-55, just below) = patterns page*16+8..15
          - launch_bright on the active pattern, launch_dim on
            available patterns, off when beyond total_patterns

        Bottom (step seq for currently active pattern):
          row 5 = pad 1, row 4 = pad 2, ... row 0 = pad 6
          col N = step (step_offset + N)
          - programmed step → row's color
          - playhead column → bright white if on, dim white if off
          - off step + not playhead → off
        """
        current = (seq.current_step
                   if seq is not None and getattr(seq, "playing", False)
                   else -1)
        num_pads = getattr(seq, "num_pads", 0) if seq is not None else 0
        num_steps = getattr(seq, "num_steps", 0) if seq is not None else 0
        base_pat = pattern_launch_page * 16

        for idx in range(64):
            row = idx // 8
            col = idx % 8
            if row >= 6:
                top_down = 1 - (row - 6)              # row 7 → 0, row 6 → 1
                pat = base_pat + top_down * 8 + col + 1
                if pat > total_patterns:
                    color = COLOR_OFF
                elif pat == current_pattern:
                    color = launch_bright
                else:
                    color = launch_dim
                self.set_pad_color(idx, color)
                continue
            # Step area
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
