"""Compa UI theme — Claude Code inspired dark aesthetic.

Deep blacks, warm orange accents, clean typography, subtle depth.
"""

import pygame

# Screen dimensions (defaults — updated by init_display() at startup)
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600

# ── Color Palette ────────────────────────────────────────────────────
# Background layers (darkest → lightest for depth)
BG = (10, 10, 14)              # Deep black base
BG_PANEL = (28, 28, 38)        # Raised panel — VISIBLE contrast
BG_LIGHTER = (38, 38, 50)      # Cards, elevated surfaces
BG_INPUT = (22, 22, 32)        # Input fields, text areas

# Borders (visible separators)
BORDER = (55, 55, 68)          # Default border — brighter
BORDER_LIGHT = (70, 70, 85)    # Emphasized border
BORDER_FOCUS = (255, 130, 40)  # Focused element border

# Text hierarchy
TEXT = (210, 210, 218)          # Primary text
TEXT_DIM = (120, 120, 135)      # Secondary/muted text
TEXT_BRIGHT = (248, 248, 252)   # Emphasized text

# Accent — warm orange (Claude Code inspired)
ACCENT = (235, 120, 30)        # Primary accent
ACCENT_DIM = (160, 80, 18)     # Dimmed accent
ACCENT_BRIGHT = (255, 155, 50) # Bright accent hover
ACCENT_GLOW = (235, 120, 30, 40)  # Glow effect (with alpha)

# Status colors
GREEN = (50, 195, 70)
RED = (210, 55, 55)
YELLOW = (210, 195, 40)
BLUE = (70, 140, 230)

# Component colors
PAD_OFF = (32, 32, 42)
PAD_ACTIVE = (235, 120, 30)
PAD_PLAYING = (255, 175, 70)
PAD_SELECTED = (60, 60, 180)

KNOB_BG = (30, 30, 40)
KNOB_TRACK = (50, 50, 62)
KNOB_FILL = (235, 120, 30)

WAVEFORM_BG = (10, 10, 14)
WAVEFORM_COLOR = (80, 180, 240)
WAVEFORM_MARKER = (235, 65, 65)

BUTTON_BG = (45, 45, 58)
BUTTON_HOVER = (58, 58, 72)
BUTTON_ACTIVE = (60, 60, 170)
BUTTON_TEXT = (210, 210, 218)

MODAL_BG = (28, 28, 38)
MODAL_OVERLAY = (0, 0, 0, 180)

NAV_BG = (14, 14, 20)
NAV_ACTIVE = (235, 120, 30)
NAV_INACTIVE = (42, 42, 55)

SCROLLBAR = (35, 35, 45)
SCROLLBAR_THUMB = (80, 80, 95)

# ── Font Sizes ───────────────────────────────────────────────────────
FONT_TINY = 12
FONT_SMALL = 14
FONT_MEDIUM = 17
FONT_LARGE = 22
FONT_TITLE = 26
FONT_HERO = 34  # For big BPM displays etc.

# ── Layout Constants ─────────────────────────────────────────────────
NAV_HEIGHT = 52
HEADER_HEIGHT = 36
PAD_SPACING = 6
PAD_GRID_COLS = 4
PAD_GRID_ROWS = 4
SIDE_PANEL_WIDTH = 220
KNOB_SIZE = 56
BUTTON_HEIGHT = 36
BUTTON_RADIUS = 8
SCROLL_SPEED = 30
PANEL_RADIUS = 6
CARD_PADDING = 12

# ── Cached Fonts ─────────────────────────────────────────────────────
_fonts = {}


def init_display(width: int = 0, height: int = 0):
    """Set actual screen dimensions. Call after pygame display is created."""
    global SCREEN_WIDTH, SCREEN_HEIGHT
    if width > 0 and height > 0:
        SCREEN_WIDTH = width
        SCREEN_HEIGHT = height


def init_fonts():
    """Initialize fonts after pygame.init(). Scales font sizes for small screens."""
    global _fonts
    pygame.font.init()
    # Scale fonts if screen is smaller than default
    scale = min(SCREEN_WIDTH / 800, SCREEN_HEIGHT / 600)
    if scale < 0.85:
        # Small screen — shrink fonts
        sizes = {
            "tiny": max(9, int(FONT_TINY * scale)),
            "small": max(10, int(FONT_SMALL * scale)),
            "medium": max(12, int(FONT_MEDIUM * scale)),
            "large": max(16, int(FONT_LARGE * scale)),
            "title": max(18, int(FONT_TITLE * scale)),
            "hero": max(22, int(FONT_HERO * scale)),
            "mono": max(10, int(FONT_SMALL * scale)),
            "mono_med": max(12, int(FONT_MEDIUM * scale)),
        }
    else:
        sizes = {
            "tiny": FONT_TINY, "small": FONT_SMALL, "medium": FONT_MEDIUM,
            "large": FONT_LARGE, "title": FONT_TITLE, "hero": FONT_HERO,
            "mono": FONT_SMALL, "mono_med": FONT_MEDIUM,
        }
    _fonts = {
        "tiny": pygame.font.SysFont("dejavusans", sizes["tiny"]),
        "small": pygame.font.SysFont("dejavusans", sizes["small"]),
        "medium": pygame.font.SysFont("dejavusans", sizes["medium"]),
        "large": pygame.font.SysFont("dejavusans", sizes["large"]),
        "title": pygame.font.SysFont("dejavusans", sizes["title"]),
        "hero": pygame.font.SysFont("dejavusansmono", sizes["hero"]),
        "mono": pygame.font.SysFont("dejavusansmono", sizes["mono"]),
        "mono_med": pygame.font.SysFont("dejavusansmono", sizes["mono_med"]),
    }


def font(name: str = "medium") -> pygame.font.Font:
    """Get a cached font by name."""
    if not _fonts:
        init_fonts()
    return _fonts.get(name, _fonts["medium"])


def velocity_color(velocity: float) -> tuple:
    """Get pad color based on velocity (0.0-1.0)."""
    r = int(PAD_OFF[0] + (PAD_ACTIVE[0] - PAD_OFF[0]) * velocity)
    g = int(PAD_OFF[1] + (PAD_ACTIVE[1] - PAD_OFF[1]) * velocity)
    b = int(PAD_OFF[2] + (PAD_ACTIVE[2] - PAD_OFF[2]) * velocity)
    return (r, g, b)


# ── Drawing Helpers ──────────────────────────────────────────────────

def draw_panel(surface, rect, border=True):
    """Draw a raised panel with optional border."""
    pygame.draw.rect(surface, BG_PANEL, rect, border_radius=PANEL_RADIUS)
    if border:
        pygame.draw.rect(surface, BORDER, rect, 1, border_radius=PANEL_RADIUS)


def draw_card(surface, rect):
    """Draw an elevated card surface."""
    pygame.draw.rect(surface, BG_LIGHTER, rect, border_radius=PANEL_RADIUS)
    pygame.draw.rect(surface, BORDER, rect, 1, border_radius=PANEL_RADIUS)


def draw_button(surface, rect, label, f=None, active=False, color=None,
                text_color=None):
    """Draw a styled button with optional active state."""
    if f is None:
        f = font("small")
    if active:
        bg = color or ACCENT
        tc = text_color or BG
        # Subtle glow
        glow = rect.inflate(4, 4)
        glow_surf = pygame.Surface((glow.width, glow.height), pygame.SRCALPHA)
        pygame.draw.rect(glow_surf, (*ACCENT[:3], 30), (0, 0, glow.width, glow.height),
                        border_radius=BUTTON_RADIUS + 2)
        surface.blit(glow_surf, glow.topleft)
    else:
        bg = color or BUTTON_BG
        tc = text_color or TEXT
    pygame.draw.rect(surface, bg, rect, border_radius=BUTTON_RADIUS)
    # Top highlight for depth
    if not active:
        hl = pygame.Rect(rect.x + 1, rect.y + 1, rect.width - 2, 1)
        pygame.draw.rect(surface, (255, 255, 255, 8) if len(bg) == 3 else bg, hl)
    surf = f.render(label, True, tc)
    surface.blit(surf, surf.get_rect(center=rect.center))


def content_height() -> int:
    """Usable content height (screen minus nav bar)."""
    return SCREEN_HEIGHT - NAV_HEIGHT


def scale_x(x: int) -> int:
    """Scale an x position from 800-base to actual width."""
    return int(x * SCREEN_WIDTH / 800)


def scale_y(y: int) -> int:
    """Scale a y position from 600-base to actual height."""
    return int(y * SCREEN_HEIGHT / 600)


def draw_screen_header(surface, title, subtitle=""):
    """Draw consistent screen header with title bar."""
    f_title = font("title")
    f_small = font("small")
    # Dark header bar
    header_rect = pygame.Rect(0, 0, SCREEN_WIDTH, 38)
    pygame.draw.rect(surface, BG_PANEL, header_rect)
    pygame.draw.line(surface, BORDER, (0, 38), (SCREEN_WIDTH, 38))
    # Title in accent
    surf = f_title.render(title, True, ACCENT)
    surface.blit(surf, (14, 5))
    # Subtitle
    if subtitle:
        surf = f_small.render(subtitle, True, TEXT_DIM)
        surface.blit(surf, (14 + f_title.size(title)[0] + 12, 12))
    return 42  # y position after header


def draw_section_label(surface, x, y, text):
    """Draw a subtle section label."""
    f = font("tiny")
    surf = f.render(text, True, TEXT_DIM)
    surface.blit(surf, (x, y))
    line_x = x + surf.get_width() + 8
    if line_x < SCREEN_WIDTH - 20:
        pygame.draw.line(surface, BORDER, (line_x, y + 6), (SCREEN_WIDTH - 16, y + 6))
    return y + 16


def draw_meter(surface, x, y, w, h, level, label="", f=None):
    """Draw a horizontal level meter."""
    if f is None:
        f = font("small")
    if label:
        lbl = f.render(label, True, TEXT_DIM)
        surface.blit(lbl, (x, y))
        x += 20
        w -= 20
    pygame.draw.rect(surface, WAVEFORM_BG, (x, y, w, h), border_radius=3)
    fill_w = int(w * min(1.0, level))
    if fill_w > 0:
        color = RED if level > 0.9 else YELLOW if level > 0.7 else GREEN
        pygame.draw.rect(surface, color, (x, y, fill_w, h), border_radius=3)
        # Subtle highlight on top
        if fill_w > 4:
            hl_surf = pygame.Surface((fill_w, max(1, h // 3)), pygame.SRCALPHA)
            hl_surf.fill((255, 255, 255, 25))
            surface.blit(hl_surf, (x, y))
