"""Push 2 hardware constants — every magic number in one place.

References for the values here:
- Ableton/push-interface MIDI & Display Interface Manual (rev 1.1, Jan 2017)
- Live 12 manual chapter "Using Push 2"
- Memory: reference_push2_model.md

We do NOT import a third-party Push 2 library. This file is the
ground truth, reused throughout engine/push2/.
"""
from __future__ import annotations

# ── USB identification ──────────────────────────────────────────────
USB_VENDOR_ID = 0x2982
USB_PRODUCT_ID = 0x1967
USB_DISPLAY_ENDPOINT_OUT = 0x01

# ── Display geometry ────────────────────────────────────────────────
DISPLAY_WIDTH = 960
DISPLAY_HEIGHT = 160
DISPLAY_LINE_PIXEL_BYTES = DISPLAY_WIDTH * 2  # 1920
DISPLAY_LINE_FILLER_BYTES = 128
DISPLAY_LINE_BYTES = DISPLAY_LINE_PIXEL_BYTES + DISPLAY_LINE_FILLER_BYTES  # 2048
DISPLAY_FRAME_BYTES = DISPLAY_LINE_BYTES * DISPLAY_HEIGHT  # 327,680
DISPLAY_HEADER = bytes([0xFF, 0xCC, 0xAA, 0x88, 0, 0, 0, 0,
                        0, 0, 0, 0, 0, 0, 0, 0])
DISPLAY_XOR_PATTERN = bytes([0xE7, 0xF3, 0xE7, 0xFF])  # 32-bit, repeating
DISPLAY_TARGET_FPS = 60

# ── MIDI port name fragments — used to find the two ports ──────────
MIDI_LIVE_PORT_FRAGMENT = "Live Port"
MIDI_USER_PORT_FRAGMENT = "User Port"
MIDI_PORT_GENERIC_FRAGMENT = "Ableton Push 2"

# ── Pads ────────────────────────────────────────────────────────────
PAD_NOTE_LOW = 36   # bottom-left
PAD_NOTE_HIGH = 99  # top-right
PAD_GRID_ROWS = 8
PAD_GRID_COLS = 8

def pad_note(col: int, row: int) -> int:
    """Map (col, row) where row 0 is bottom and col 0 is left → MIDI note.

    Bottom-left pad = note 36; top-right = note 99.
    """
    if not (0 <= col < PAD_GRID_COLS and 0 <= row < PAD_GRID_ROWS):
        raise ValueError(f"pad ({col},{row}) out of range")
    return PAD_NOTE_LOW + row * PAD_GRID_COLS + col

def pad_coords(note: int) -> tuple[int, int]:
    """Inverse of pad_note. Returns (col, row)."""
    if not (PAD_NOTE_LOW <= note <= PAD_NOTE_HIGH):
        raise ValueError(f"note {note} not a pad")
    idx = note - PAD_NOTE_LOW
    return idx % PAD_GRID_COLS, idx // PAD_GRID_COLS

# ── Encoders ────────────────────────────────────────────────────────
ENCODER_TEMPO_CC = 14
ENCODER_SWING_CC = 15
ENCODER_TRACK_CCS = (71, 72, 73, 74, 75, 76, 77, 78)
ENCODER_MASTER_CC = 79

# Touch notes (Note On/Off when capacitive surface touched)
ENCODER_TOUCH_TRACK_NOTES = (0, 1, 2, 3, 4, 5, 6, 7)
ENCODER_TOUCH_MASTER_NOTE = 8
ENCODER_TOUCH_SWING_NOTE = 9
ENCODER_TOUCH_TEMPO_NOTE = 10  # may not actually fire on hardware

ENCODER_NAMES = {
    ENCODER_TEMPO_CC: "tempo",
    ENCODER_SWING_CC: "swing",
    ENCODER_MASTER_CC: "master",
    **{cc: f"track{i+1}" for i, cc in enumerate(ENCODER_TRACK_CCS)},
}

# ── Touch strip ─────────────────────────────────────────────────────
TOUCH_STRIP_TOUCH_NOTE = 12  # Note On 12 = strip touched
TOUCH_STRIP_NUM_LEDS = 31

# ── Buttons (CC numbers) ───────────────────────────────────────────
# Mode buttons
BTN_SESSION = 51
BTN_NOTE = 50
BTN_MIX = 112
BTN_DEVICE = 110
BTN_BROWSE = 111
BTN_MASTER = 28
BTN_SETUP = 30
BTN_USER = 59
BTN_CLIP = 113
BTN_LAYOUT = 31
BTN_CONVERT = 35
BTN_ADD_DEVICE = 52
BTN_ADD_TRACK = 53

# Modifier buttons
BTN_SHIFT = 49
BTN_SELECT = 48
BTN_DELETE = 118
BTN_DUPLICATE = 88
BTN_QUANTIZE = 116
BTN_DOUBLE_LOOP = 117
BTN_FIXED_LENGTH = 90
BTN_MUTE = 60
BTN_SOLO = 61
BTN_STOP_CLIP = 29
BTN_REPEAT = 56
BTN_ACCENT = 57
BTN_NEW = 87

# Transport
BTN_PLAY = 85
BTN_RECORD = 86
BTN_TAP = 3
BTN_METRONOME = 9
BTN_AUTOMATE = 89
BTN_UNDO = 119

# Navigation
BTN_UP = 46
BTN_DOWN = 47
BTN_LEFT = 44
BTN_RIGHT = 45
BTN_PAGE_LEFT = 62
BTN_PAGE_RIGHT = 63
BTN_OCTAVE_UP = 55
BTN_OCTAVE_DOWN = 54
BTN_SCALE = 58

# Display button rows (left → right)
BTN_UPPER_DISPLAY_CCS = (102, 103, 104, 105, 106, 107, 108, 109)
BTN_LOWER_DISPLAY_CCS = (20, 21, 22, 23, 24, 25, 26, 27)

# Scene-launch column (top → bottom). Doubles as time-division pads
# (1/32t at top through 1/4 at bottom) in step-edit modes.
BTN_SCENE_LAUNCH_CCS = (43, 42, 41, 40, 39, 38, 37, 36)

# Time-division mapping when scene-launch buttons are repurposed.
# Top → bottom = 1/32t, 1/32, 1/16t, 1/16, 1/8t, 1/8, 1/4t, 1/4.
TIME_DIVISIONS = (
    "1/32t", "1/32", "1/16t", "1/16",
    "1/8t",  "1/8",  "1/4t",  "1/4",
)

# Reverse map: name → CC
BUTTON_NAMES: dict[int, str] = {
    BTN_SESSION: "session",
    BTN_NOTE: "note",
    BTN_MIX: "mix",
    BTN_DEVICE: "device",
    BTN_BROWSE: "browse",
    BTN_MASTER: "master",
    BTN_SETUP: "setup",
    BTN_USER: "user",
    BTN_CLIP: "clip",
    BTN_LAYOUT: "layout",
    BTN_CONVERT: "convert",
    BTN_ADD_DEVICE: "add_device",
    BTN_ADD_TRACK: "add_track",
    BTN_SHIFT: "shift",
    BTN_SELECT: "select",
    BTN_DELETE: "delete",
    BTN_DUPLICATE: "duplicate",
    BTN_QUANTIZE: "quantize",
    BTN_DOUBLE_LOOP: "double_loop",
    BTN_FIXED_LENGTH: "fixed_length",
    BTN_MUTE: "mute",
    BTN_SOLO: "solo",
    BTN_STOP_CLIP: "stop_clip",
    BTN_REPEAT: "repeat",
    BTN_ACCENT: "accent",
    BTN_NEW: "new",
    BTN_PLAY: "play",
    BTN_RECORD: "record",
    BTN_TAP: "tap",
    BTN_METRONOME: "metronome",
    BTN_AUTOMATE: "automate",
    BTN_UNDO: "undo",
    BTN_UP: "up",
    BTN_DOWN: "down",
    BTN_LEFT: "left",
    BTN_RIGHT: "right",
    BTN_PAGE_LEFT: "page_left",
    BTN_PAGE_RIGHT: "page_right",
    BTN_OCTAVE_UP: "octave_up",
    BTN_OCTAVE_DOWN: "octave_down",
    BTN_SCALE: "scale",
    **{cc: f"upper_display_{i+1}" for i, cc in enumerate(BTN_UPPER_DISPLAY_CCS)},
    **{cc: f"lower_display_{i+1}" for i, cc in enumerate(BTN_LOWER_DISPLAY_CCS)},
    **{cc: f"scene_{i+1}" for i, cc in enumerate(BTN_SCENE_LAUNCH_CCS)},
}

NAMES_TO_BUTTONS: dict[str, int] = {v: k for k, v in BUTTON_NAMES.items()}

# Button classes — RGB vs white-only (drives the LED-write protocol)
RGB_BUTTONS: frozenset[int] = frozenset({
    BTN_PLAY, BTN_RECORD, BTN_AUTOMATE, BTN_MUTE, BTN_SOLO, BTN_STOP_CLIP,
    *BTN_UPPER_DISPLAY_CCS, *BTN_LOWER_DISPLAY_CCS, *BTN_SCENE_LAUNCH_CCS,
})

# ── Sysex ───────────────────────────────────────────────────────────
SYSEX_START = 0xF0
SYSEX_END = 0xF7
ABLETON_MFR_ID = (0x00, 0x21, 0x1D)
SYSEX_DEVICE_ID = 0x01
SYSEX_MODEL_ID = 0x01

# Command IDs
CMD_SET_PALETTE_ENTRY = 0x03
CMD_REAPPLY_PALETTE = 0x05
CMD_SET_LED_BRIGHTNESS = 0x06
CMD_GET_LED_BRIGHTNESS = 0x07
CMD_SET_DISPLAY_BRIGHTNESS = 0x08
CMD_GET_DISPLAY_BRIGHTNESS = 0x09
CMD_SET_MIDI_MODE = 0x0A
CMD_SET_TOUCH_STRIP_CONFIG = 0x17
CMD_SET_TOUCH_STRIP_LEDS = 0x19
CMD_SET_AFTERTOUCH_THRESHOLD = 0x1B
CMD_GET_PAD_CALIBRATION = 0x1D
CMD_SET_AFTERTOUCH_MODE = 0x1E
CMD_SET_VELOCITY_CURVE = 0x20
CMD_GET_VELOCITY_CURVE = 0x21
CMD_OVERRIDE_PAD_CALIBRATION = 0x22
CMD_SET_PAD_SENSITIVITY = 0x28
CMD_SET_PEDAL_CONFIG_1 = 0x30
CMD_SET_PEDAL_CONFIG_2 = 0x31
CMD_SET_PEDAL_CONFIG_3 = 0x32

# MIDI mode args
MIDI_MODE_LIVE = 0
MIDI_MODE_USER = 1
MIDI_MODE_DUAL = 2

# Aftertouch mode args
AFTERTOUCH_CHANNEL = 0
AFTERTOUCH_POLY = 1

# ── LED animation channels ─────────────────────────────────────────
# Channel 0 = static; 1–5 = transition (24th, 16th, 8th, 1/4, 1/2);
# 6–10 = pulse (same intervals); 11–15 = blink.
ANIM_STATIC = 0
ANIM_TRANSITION_24TH = 1
ANIM_TRANSITION_16TH = 2
ANIM_TRANSITION_8TH = 3
ANIM_TRANSITION_QUARTER = 4
ANIM_TRANSITION_HALF = 5
ANIM_PULSE_24TH = 6
ANIM_PULSE_16TH = 7
ANIM_PULSE_8TH = 8
ANIM_PULSE_QUARTER = 9
ANIM_PULSE_HALF = 10
ANIM_BLINK_24TH = 11
ANIM_BLINK_16TH = 12
ANIM_BLINK_8TH = 13
ANIM_BLINK_QUARTER = 14
ANIM_BLINK_HALF = 15

# ── Default palette indices (RGB pads) ─────────────────────────────
# Indices we name explicitly. The full palette is built in palette.py.
COLOR_BLACK = 0
COLOR_WHITE = 122
COLOR_LIGHT_GRAY = 123
COLOR_DARK_GRAY = 124
COLOR_BLUE = 125
COLOR_GREEN = 126
COLOR_RED = 127

# Curated track colors — what the 8 default tracks get coloured as.
# These map to palette indices populated in palette.py.
TRACK_COLOR_INDICES = (5, 11, 21, 28, 56, 75, 96, 110)
