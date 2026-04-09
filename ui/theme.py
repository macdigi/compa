"""Colors, fonts, and dimensions for touchscreen display."""

import pygame

# Screen dimensions (actual display is 800x600 via KMSDRM)
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600

# Colors — dark MPC-inspired theme
BG = (20, 20, 25)
BG_LIGHTER = (35, 35, 42)
BG_PANEL = (28, 28, 35)
BORDER = (60, 60, 70)
TEXT = (220, 220, 225)
TEXT_DIM = (140, 140, 150)
TEXT_BRIGHT = (255, 255, 255)
ACCENT = (255, 120, 30)       # Orange accent
ACCENT_DIM = (180, 85, 20)
ACCENT_BRIGHT = (255, 160, 60)
PAD_OFF = (50, 50, 60)
PAD_ACTIVE = (255, 120, 30)
PAD_PLAYING = (255, 180, 80)
PAD_SELECTED = (80, 80, 200)
GREEN = (60, 200, 80)
RED = (220, 60, 60)
YELLOW = (220, 200, 40)
KNOB_BG = (45, 45, 55)
KNOB_TRACK = (70, 70, 80)
KNOB_FILL = (255, 120, 30)
WAVEFORM_BG = (15, 15, 20)
WAVEFORM_COLOR = (100, 200, 255)
WAVEFORM_MARKER = (255, 80, 80)
SCROLLBAR = (80, 80, 90)
SCROLLBAR_THUMB = (140, 140, 150)
BUTTON_BG = (55, 55, 65)
BUTTON_ACTIVE = (80, 80, 200)
BUTTON_TEXT = (220, 220, 225)
MODAL_OVERLAY = (0, 0, 0, 180)
MODAL_BG = (40, 40, 50)
NAV_BG = (15, 15, 20)
NAV_ACTIVE = (255, 120, 30)

# Font sizes
FONT_SMALL = 14
FONT_MEDIUM = 18
FONT_LARGE = 24
FONT_TITLE = 28

# Layout
NAV_HEIGHT = 56
HEADER_HEIGHT = 36
PAD_SPACING = 6
PAD_GRID_COLS = 4
PAD_GRID_ROWS = 4
SIDE_PANEL_WIDTH = 220
KNOB_SIZE = 56
BUTTON_HEIGHT = 36
BUTTON_RADIUS = 6
SCROLL_SPEED = 30

# Cached fonts
_fonts = {}


def init_fonts():
    """Initialize fonts after pygame.init()."""
    global _fonts
    pygame.font.init()
    _fonts = {
        "small": pygame.font.SysFont("dejavusans", FONT_SMALL),
        "medium": pygame.font.SysFont("dejavusans", FONT_MEDIUM),
        "large": pygame.font.SysFont("dejavusans", FONT_LARGE),
        "title": pygame.font.SysFont("dejavusans", FONT_TITLE),
        "mono": pygame.font.SysFont("dejavusansmono", FONT_SMALL),
    }


def font(name: str = "medium") -> pygame.font.Font:
    """Get a cached font by name."""
    if not _fonts:
        init_fonts()
    return _fonts.get(name, _fonts["medium"])


def velocity_color(velocity: float) -> tuple:
    """Get pad color based on velocity (0.0–1.0)."""
    r = int(PAD_OFF[0] + (PAD_ACTIVE[0] - PAD_OFF[0]) * velocity)
    g = int(PAD_OFF[1] + (PAD_ACTIVE[1] - PAD_OFF[1]) * velocity)
    b = int(PAD_OFF[2] + (PAD_ACTIVE[2] - PAD_OFF[2]) * velocity)
    return (r, g, b)
