"""Animated splash screen — COMPA logo then RARE DATA branding."""

import builtins
import os
import time
import pygame


def _flip(screen):
    """Flip display and blit to SPI LCD if in FB mode."""
    pygame.display.flip()
    fb_blit = getattr(builtins, '_compa_fb_blit', None)
    if fb_blit:
        fb_blit(screen)
from . import theme

# ASCII art logo
COMPA_LOGO = [
    "  ___ ___  __  __ ___  _   ",
    " / __/ _ \\|  \\/  | _ \\/ \\  ",
    "| (_| (_) | |\\/| |  _/ _ \\ ",
    " \\___\\___/|_|  |_|_|/_/ \\_\\",
]

TAGLINE = "P-6 Companion"


def run_splash(screen: pygame.Surface, clock: pygame.time.Clock):
    """Run the animated splash sequence. Returns when done."""
    sw, sh = screen.get_size()
    fps = 30

    # Load RARE DATA logo if available
    logo_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "docs", "raredata_logo.png")
    logo_img = None
    if os.path.exists(logo_path):
        try:
            logo_img = pygame.image.load(logo_path).convert_alpha()
            # Scale to fit nicely (max 250px tall, maintain aspect)
            lw, lh = logo_img.get_size()
            max_h = 220
            if lh > max_h:
                scale = max_h / lh
                logo_img = pygame.transform.smoothscale(
                    logo_img, (int(lw * scale), int(lh * scale)))
        except Exception:
            logo_img = None

    f_mono = theme.font("mono_med")
    f_hero = theme.font("hero")
    f_title = theme.font("title")
    f_med = theme.font("medium")
    f_small = theme.font("small")

    # ── Phase 1: COMPA logo typing animation (2 seconds) ────────────
    total_chars = sum(len(line) for line in COMPA_LOGO)
    type_frames = fps * 2  # 2 seconds
    chars_per_frame = max(1, total_chars / type_frames)

    for frame in range(type_frames + fps):  # +1 second hold
        for event in pygame.event.get():
            if event.type in (pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN,
                              pygame.FINGERDOWN):
                return  # Skip splash on any input

        screen.fill(theme.BG)

        # Calculate how many characters to show
        visible_chars = int(min(frame * chars_per_frame, total_chars))

        # Draw logo characters with typing effect
        chars_drawn = 0
        logo_y = sh // 2 - 60
        for i, line in enumerate(COMPA_LOGO):
            line_start = chars_drawn
            line_end = min(chars_drawn + len(line), visible_chars)
            visible = line[:max(0, line_end - line_start)]
            chars_drawn += len(line)

            if visible:
                # Orange text with slight glow
                surf = f_mono.render(visible, True, theme.ACCENT)
                x = sw // 2 - f_mono.size(COMPA_LOGO[0])[0] // 2
                screen.blit(surf, (x, logo_y + i * 22))

        # Blinking cursor at end of current line
        if visible_chars < total_chars and frame % 10 < 6:
            cursor_char = 0
            cursor_line = 0
            counted = 0
            for li, line in enumerate(COMPA_LOGO):
                if counted + len(line) > visible_chars:
                    cursor_line = li
                    cursor_char = visible_chars - counted
                    break
                counted += len(line)

            cursor_x = sw // 2 - f_mono.size(COMPA_LOGO[0])[0] // 2
            cursor_x += f_mono.size(COMPA_LOGO[cursor_line][:cursor_char])[0]
            cursor_y = logo_y + cursor_line * 22
            pygame.draw.rect(screen, theme.ACCENT,
                           (cursor_x, cursor_y, 10, 18))

        # Tagline fades in after logo is complete
        if frame > type_frames - 10:
            alpha = min(255, (frame - type_frames + 10) * 20)
            tag_surf = f_med.render(TAGLINE, True, theme.TEXT_DIM)
            tag_alpha = pygame.Surface(tag_surf.get_size(), pygame.SRCALPHA)
            tag_alpha.fill((255, 255, 255, alpha))
            tag_surf.set_alpha(alpha)
            tag_x = sw // 2 - tag_surf.get_width() // 2
            screen.blit(tag_surf, (tag_x, logo_y + len(COMPA_LOGO) * 22 + 16))

        # Version in corner
        v_surf = f_small.render("v1.0", True, (60, 60, 70))
        screen.blit(v_surf, (sw - v_surf.get_width() - 12, sh - 24))

        _flip(screen)
        clock.tick(fps)

    # ── Phase 2: Fade to RARE DATA logo (1.5 seconds) ───────────────
    fade_frames = fps  # 1 second fade
    hold_frames = fps * 2  # 2 second hold

    for frame in range(fade_frames + hold_frames):
        for event in pygame.event.get():
            if event.type in (pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN,
                              pygame.FINGERDOWN):
                return

        screen.fill(theme.BG)

        # Fade in
        alpha = min(255, int(frame / fade_frames * 255)) if frame < fade_frames else 255

        # "by" text
        by_surf = f_med.render("by", True, theme.TEXT_DIM)
        by_surf.set_alpha(alpha)
        screen.blit(by_surf, (sw // 2 - by_surf.get_width() // 2, sh // 2 - 160))

        # RARE DATA logo image
        if logo_img:
            logo_alpha = logo_img.copy()
            logo_alpha.set_alpha(alpha)
            lx = sw // 2 - logo_alpha.get_width() // 2
            ly = sh // 2 - logo_alpha.get_height() // 2
            screen.blit(logo_alpha, (lx, ly))
        else:
            # Fallback: text-only RARE DATA
            rd_surf = f_hero.render("RARE DATA", True, (235, 30, 50))
            rd_surf.set_alpha(alpha)
            screen.blit(rd_surf, (sw // 2 - rd_surf.get_width() // 2, sh // 2 - 20))

        # Subtle bottom text
        if frame > fade_frames // 2:
            a2 = min(255, (frame - fade_frames // 2) * 8)
            sub = f_small.render("raredata.net", True, theme.TEXT_DIM)
            sub.set_alpha(a2)
            screen.blit(sub, (sw // 2 - sub.get_width() // 2, sh // 2 + 140))

        _flip(screen)
        clock.tick(fps)

    # ── Phase 3: Quick fade out (0.5 seconds) ───────────────────────
    for frame in range(fps // 2):
        for event in pygame.event.get():
            if event.type in (pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN,
                              pygame.FINGERDOWN):
                return

        alpha = 255 - int(frame / (fps // 2) * 255)

        screen.fill(theme.BG)
        if logo_img:
            logo_alpha = logo_img.copy()
            logo_alpha.set_alpha(alpha)
            lx = sw // 2 - logo_alpha.get_width() // 2
            ly = sh // 2 - logo_alpha.get_height() // 2
            screen.blit(logo_alpha, (lx, ly))

        _flip(screen)
        clock.tick(fps)
