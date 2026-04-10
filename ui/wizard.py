"""First-time setup wizard — runs on initial boot before splash."""

import builtins
import os
import subprocess
import pygame


def _flip(screen):
    pygame.display.flip()
    fb_blit = getattr(builtins, '_compa_fb_blit', None)
    if fb_blit:
        fb_blit(screen)
from . import theme

# Flag file path — wizard runs if this doesn't exist
SETUP_FLAG = "/home/pi/compa/.setup_complete"

# ASCII art logo (same as splash)
COMPA_LOGO = [
    "  ___ ___  __  __ ___  _   ",
    " / __/ _ \\|  \\/  | _ \\/ \\  ",
    "| (_| (_) | |\\/| |  _/ _ \\ ",
    " \\___\\___/|_|  |_|_|/_/ \\_\\",
]


def run_wizard(screen: pygame.Surface, clock: pygame.time.Clock, app):
    """Run the multi-step setup wizard. Modifies app settings in place."""
    sw, sh = screen.get_size()
    fps = 30

    f_hero = theme.font("hero")
    f_title = theme.font("title")
    f_large = theme.font("large")
    f_med = theme.font("medium")
    f_small = theme.font("small")
    f_mono = theme.font("mono_med")

    def _wait_for_tap():
        """Block until the user taps/clicks anywhere. Returns False if quit."""
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return False
                if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN,
                                  pygame.KEYDOWN):
                    return True
            clock.tick(fps)

    def _wait_for_choice(rects: list) -> int:
        """Block until user taps one of the given rects. Returns index, or -1 on quit."""
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return -1
                if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                    if event.type == pygame.FINGERDOWN:
                        mx = int(event.x * sw)
                        my = int(event.y * sh)
                    else:
                        mx, my = event.pos
                    for i, rect in enumerate(rects):
                        if rect.collidepoint(mx, my):
                            return i
                if event.type == pygame.KEYDOWN:
                    # Number keys 1-9 for quick selection
                    if pygame.K_1 <= event.key <= pygame.K_9:
                        idx = event.key - pygame.K_1
                        if idx < len(rects):
                            return idx
                    # Enter/space selects first option
                    if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        return 0
            clock.tick(fps)

    def _draw_footer(text: str):
        """Draw footer hint text."""
        surf = f_small.render(text, True, theme.TEXT_DIM)
        screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh - 40))

    def _draw_step_indicator(step: int, total: int):
        """Draw step dots at bottom."""
        dot_spacing = 16
        total_w = total * dot_spacing
        start_x = sw // 2 - total_w // 2
        for i in range(total):
            cx = start_x + i * dot_spacing + 6
            cy = sh - 60
            if i == step:
                pygame.draw.circle(screen, theme.ACCENT, (cx, cy), 5)
            elif i < step:
                pygame.draw.circle(screen, theme.GREEN, (cx, cy), 4)
            else:
                pygame.draw.circle(screen, theme.TEXT_DIM, (cx, cy), 3, 1)

    total_steps = 6

    # ── Step 1: Welcome ─────────────────────────────────────────────
    screen.fill(theme.BG)

    # Draw logo
    logo_y = sh // 2 - 80
    for i, line in enumerate(COMPA_LOGO):
        surf = f_mono.render(line, True, theme.ACCENT)
        x = sw // 2 - surf.get_width() // 2
        screen.blit(surf, (x, logo_y + i * 22))

    # Welcome text
    text_y = logo_y + len(COMPA_LOGO) * 22 + 30
    surf = f_large.render("Welcome to Compa", True, theme.TEXT_BRIGHT)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, text_y))

    surf = f_med.render("P-6 Companion", True, theme.TEXT_DIM)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, text_y + 34))

    _draw_step_indicator(0, total_steps)
    _draw_footer("Tap anywhere to continue")
    _flip(screen)

    if not _wait_for_tap():
        return

    # ── Step 2: Display Detected ────────────────────────────────────
    screen.fill(theme.BG)

    surf = f_title.render("Display Detected", True, theme.ACCENT)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 60))

    res_text = f"{sw} x {sh}"
    surf = f_hero.render(res_text, True, theme.TEXT_BRIGHT)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 10))

    surf = f_small.render("pixels", True, theme.TEXT_DIM)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 + 40))

    _draw_step_indicator(1, total_steps)
    _draw_footer("Tap anywhere to continue")
    _flip(screen)

    if not _wait_for_tap():
        return

    # ── Step 3: Input Mode ──────────────────────────────────────────
    screen.fill(theme.BG)

    surf = f_title.render("Input Mode", True, theme.ACCENT)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 80))

    surf = f_small.render("How will you control Compa?", True, theme.TEXT_DIM)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 45))

    btn_w, btn_h = 180, 44
    gap = 20
    touch_rect = pygame.Rect(sw // 2 - btn_w - gap // 2, sh // 2, btn_w, btn_h)
    mouse_rect = pygame.Rect(sw // 2 + gap // 2, sh // 2, btn_w, btn_h)

    theme.draw_button(screen, touch_rect, "TOUCHSCREEN", f_med, active=True)
    theme.draw_button(screen, mouse_rect, "MOUSE", f_med, active=False)

    _draw_step_indicator(2, total_steps)
    _draw_footer("Select your input method")
    _flip(screen)

    choice = _wait_for_choice([touch_rect, mouse_rect])
    if choice == -1:
        return
    use_mouse = (choice == 1)

    # Apply mouse mode
    from ui.p6_app import save_config_key
    app.mouse_mode = use_mouse
    pygame.mouse.set_visible(use_mouse)
    save_config_key("MOUSE_MODE", "1" if use_mouse else "0")

    # ── Step 4: Touch Calibration (only if touchscreen) ─────────────
    if not use_mouse:
        screen.fill(theme.BG)

        surf = f_title.render("Touch Calibration", True, theme.ACCENT)
        screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 80))

        surf = f_small.render("Calibrate your touchscreen for accurate input",
                              True, theme.TEXT_DIM)
        screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 45))

        cal_rect = pygame.Rect(sw // 2 - btn_w - gap // 2, sh // 2, btn_w, btn_h)
        skip_rect = pygame.Rect(sw // 2 + gap // 2, sh // 2, btn_w, btn_h)

        theme.draw_button(screen, cal_rect, "CALIBRATE NOW", f_med, active=True)
        theme.draw_button(screen, skip_rect, "SKIP", f_med, active=False)

        _draw_step_indicator(3, total_steps)
        _draw_footer("Calibration improves touch accuracy")
        _flip(screen)

        choice = _wait_for_choice([cal_rect, skip_rect])
        if choice == -1:
            return
        if choice == 0:
            # Run ts_calibrate
            env = os.environ.copy()
            env["TSLIB_TSDEVICE"] = env.get("TSLIB_TSDEVICE", "/dev/input/touchscreen")
            env["TSLIB_FBDEVICE"] = env.get("TSLIB_FBDEVICE", "/dev/fb0")
            env["TSLIB_CALIBFILE"] = "/etc/pointercal"
            try:
                proc = subprocess.Popen(["sudo", "ts_calibrate"], env=env)
                proc.wait(timeout=60)
            except Exception as e:
                print(f"Calibration: {e}", flush=True)

            # Brief pause after calibration
            screen.fill(theme.BG)
            surf = f_med.render("Calibration complete", True, theme.GREEN)
            screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2))
            _flip(screen)
            pygame.time.wait(1500)
    else:
        # Mouse mode — show a brief confirmation
        screen.fill(theme.BG)
        surf = f_title.render("Mouse Mode", True, theme.ACCENT)
        screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 40))
        surf = f_med.render("Mouse cursor enabled", True, theme.GREEN)
        screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 + 10))
        _draw_step_indicator(3, total_steps)
        _draw_footer("Tap anywhere to continue")
        _flip(screen)
        if not _wait_for_tap():
            return

    # ── Step 5: P-6 Connection ──────────────────────────────────────
    screen.fill(theme.BG)

    surf = f_title.render("P-6 Connection", True, theme.ACCENT)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 60))

    if app.p6 and app.p6.connected:
        surf = f_large.render("P-6 Connected!", True, theme.GREEN)
        screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2))
    else:
        surf = f_large.render("P-6 not found", True, theme.YELLOW)
        screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 5))
        surf = f_small.render("Connect via USB and restart, or continue without",
                              True, theme.TEXT_DIM)
        screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 + 30))

    _draw_step_indicator(4, total_steps)
    _draw_footer("Tap anywhere to continue")
    _flip(screen)

    if not _wait_for_tap():
        return

    # ── Step 6: Ready! ──────────────────────────────────────────────
    screen.fill(theme.BG)

    surf = f_hero.render("Ready!", True, theme.ACCENT)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 40))

    surf = f_med.render("Setup complete", True, theme.GREEN)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 + 20))

    _draw_step_indicator(5, total_steps)
    _draw_footer("Tap anywhere to start")
    _flip(screen)

    if not _wait_for_tap():
        return

    # Create the flag file so wizard doesn't run again
    try:
        flag_dir = os.path.dirname(SETUP_FLAG)
        os.makedirs(flag_dir, exist_ok=True)
        with open(SETUP_FLAG, "w") as f:
            f.write("setup_complete\n")
        print(f"Setup wizard complete, flag written: {SETUP_FLAG}", flush=True)
    except Exception as e:
        print(f"Could not write setup flag: {e}", flush=True)
