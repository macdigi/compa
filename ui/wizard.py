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

    total_steps = 7

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

    surf = f_med.render("SP-404 MK2 + P-6 Companion", True, theme.TEXT_DIM)
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
    # Picker auto-detects whichever input the user is actually
    # using — moving the mouse picks MOUSE, tapping the screen
    # picks TOUCHSCREEN, M/T keys pick explicitly. This handles the
    # awkward case where you've set up a Pi with a mouse but no
    # touchscreen yet (or vice versa) and would otherwise be unable
    # to pick the option that matches your actual hardware.
    btn_w, btn_h = 180, 44
    gap = 20

    def _draw_input_mode_picker(highlight: str):
        """highlight ∈ {'touch', 'mouse', 'none'} — bumps that
        button's active styling. Reused by the auto-detect loop
        below to nudge the picker when the user moves the mouse
        or taps the screen."""
        screen.fill(theme.BG)
        s = f_title.render("Input Mode", True, theme.ACCENT)
        screen.blit(s, (sw // 2 - s.get_width() // 2, sh // 2 - 110))
        s = f_small.render(
            "How will you control Compa?", True, theme.TEXT_DIM)
        screen.blit(s, (sw // 2 - s.get_width() // 2, sh // 2 - 80))

        tr = pygame.Rect(
            sw // 2 - btn_w - gap // 2, sh // 2 - 30, btn_w, btn_h)
        mr = pygame.Rect(
            sw // 2 + gap // 2, sh // 2 - 30, btn_w, btn_h)
        theme.draw_button(
            screen, tr, "TOUCHSCREEN  (T)", f_med,
            active=(highlight == "touch"))
        theme.draw_button(
            screen, mr, "MOUSE  (M)", f_med,
            active=(highlight == "mouse"))

        # Auto-detect hint below the buttons.
        s = f_small.render(
            "Touch the screen, move the mouse, or press T / M",
            True, theme.TEXT_DIM)
        screen.blit(
            s, (sw // 2 - s.get_width() // 2, sh // 2 + 30))

        _draw_step_indicator(2, total_steps)
        _draw_footer("First input wins — pick by using it")
        _flip(screen)
        return tr, mr

    touch_rect, mouse_rect = _draw_input_mode_picker("none")

    # Auto-detect loop. Watch for any of:
    #   - mouse motion (more than ~5 pixels of travel from origin) → mouse
    #   - mouse click on either button → that button's mode
    #   - finger touch anywhere → touch
    #   - M / T keys → mouse / touch
    use_mouse = None
    mouse_origin: tuple[int, int] | None = None
    while use_mouse is None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type == pygame.FINGERDOWN:
                # Finger event → definitely a touchscreen.
                use_mouse = False
                _draw_input_mode_picker("touch")
                pygame.time.wait(180)
                break
            if event.type == pygame.MOUSEMOTION:
                if mouse_origin is None:
                    mouse_origin = event.pos
                    continue
                dx = abs(event.pos[0] - mouse_origin[0])
                dy = abs(event.pos[1] - mouse_origin[1])
                if dx + dy > 8:
                    use_mouse = True
                    _draw_input_mode_picker("mouse")
                    pygame.time.wait(180)
                    break
            if event.type == pygame.MOUSEBUTTONDOWN:
                # Click is treated as a deliberate selection: which
                # rect did they hit?
                if touch_rect.collidepoint(event.pos):
                    use_mouse = False
                    _draw_input_mode_picker("touch")
                    pygame.time.wait(180)
                    break
                if mouse_rect.collidepoint(event.pos):
                    use_mouse = True
                    _draw_input_mode_picker("mouse")
                    pygame.time.wait(180)
                    break
                # Click outside both buttons but with a real cursor
                # — treat as MOUSE selection.
                use_mouse = True
                _draw_input_mode_picker("mouse")
                pygame.time.wait(180)
                break
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_m:
                    use_mouse = True
                    _draw_input_mode_picker("mouse")
                    pygame.time.wait(180)
                    break
                if event.key == pygame.K_t:
                    use_mouse = False
                    _draw_input_mode_picker("touch")
                    pygame.time.wait(180)
                    break
                if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    # Default = touchscreen (matches Compa OS image
                    # primary use-case).
                    use_mouse = False
                    _draw_input_mode_picker("touch")
                    pygame.time.wait(180)
                    break
        clock.tick(fps)

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
            # In-app calibration: 4 corners + center, capture taps,
            # compute affine matrix, save to ~/.config/compa/touch_calibration.json.
            # (Replaces the old ts_calibrate subprocess which only handles
            # resistive ADS7846-class panels — it hangs on modern HID
            # touchscreens, taking over the framebuffer and freezing the wizard.)
            from engine.touch_calibration import compute_matrix, TouchCalibration

            INSET = 60
            R_OUT = 28
            R_IN = 6
            cal_targets = [
                (INSET, INSET),
                (sw - INSET, INSET),
                (sw - INSET, sh - INSET),
                (INSET, sh - INSET),
                (sw // 2, sh // 2),
            ]
            cal_raw = []
            cal_aborted = False
            for idx, (tx, ty) in enumerate(cal_targets):
                # Draw target + progress
                screen.fill(theme.BG)
                title_s = f_title.render("Touch Calibration",
                                         True, theme.ACCENT)
                screen.blit(title_s, (sw // 2 - title_s.get_width() // 2,
                                      sh // 2 - 110))
                prog_s = f_small.render(f"{idx} / {len(cal_targets)}",
                                        True, theme.TEXT)
                screen.blit(prog_s, (sw // 2 - prog_s.get_width() // 2,
                                     sh // 2 - 75))
                hint_s = f_small.render("Tap each target as it appears",
                                        True, theme.TEXT_DIM)
                screen.blit(hint_s, (sw // 2 - hint_s.get_width() // 2,
                                     sh // 2 - 55))
                pygame.draw.circle(screen, theme.ACCENT_BRIGHT,
                                   (tx, ty), R_OUT, 3)
                pygame.draw.line(screen, theme.ACCENT_BRIGHT,
                                 (tx - R_OUT, ty), (tx + R_OUT, ty), 1)
                pygame.draw.line(screen, theme.ACCENT_BRIGHT,
                                 (tx, ty - R_OUT), (tx, ty + R_OUT), 1)
                pygame.draw.circle(screen, theme.TEXT_BRIGHT,
                                   (tx, ty), R_IN)
                _flip(screen)

                # Wait for tap (raw — no calibration applied yet)
                tap = None
                while tap is None:
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            cal_aborted = True
                            break
                        if (event.type == pygame.KEYDOWN
                                and event.key == pygame.K_ESCAPE):
                            cal_aborted = True
                            break
                        if (event.type == pygame.MOUSEBUTTONDOWN
                                and event.button == 1):
                            tap = event.pos
                            break
                        if event.type == pygame.FINGERDOWN:
                            tap = (int(event.x * sw), int(event.y * sh))
                            break
                    if cal_aborted:
                        break
                    clock.tick(fps)
                if cal_aborted:
                    break
                cal_raw.append(tap)

            # Compute + save matrix
            screen.fill(theme.BG)
            if not cal_aborted and len(cal_raw) == len(cal_targets):
                try:
                    matrix = compute_matrix(cal_raw, cal_targets)
                    cal = TouchCalibration()
                    cal.save(matrix)
                    if hasattr(app, "touch_calibration"):
                        app.touch_calibration.load()
                    surf = f_med.render("Calibration saved",
                                        True, theme.GREEN)
                except Exception as e:
                    print(f"Calibration: {e}", flush=True)
                    surf = f_med.render("Calibration failed — skipped",
                                        True, theme.YELLOW)
            else:
                surf = f_med.render("Calibration cancelled",
                                    True, theme.TEXT_DIM)
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

    # ── Step 5: WiFi Setup ──────────────────────────────────────────
    # If the Pi is already online (Ethernet seeded by user, or a
    # previously-saved WiFi profile auto-connected on boot), skip
    # straight through with a "you're connected" confirmation.
    # Otherwise scan, list, let the user pick + enter password via
    # the OnScreenKeyboard, attempt connection.
    _step_wifi(
        screen, clock, app, fps, sw, sh,
        f_hero, f_title, f_large, f_med, f_small,
        _draw_step_indicator, _draw_footer, _flip,
        total_steps, _wait_for_tap,
    )

    # ── Step 6: P-6 Connection ──────────────────────────────────────
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

    _draw_step_indicator(5, total_steps)
    _draw_footer("Tap anywhere to continue")
    _flip(screen)

    if not _wait_for_tap():
        return

    # ── Step 7: Ready! ──────────────────────────────────────────────
    screen.fill(theme.BG)

    surf = f_hero.render("Ready!", True, theme.ACCENT)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 - 40))

    surf = f_med.render("Setup complete", True, theme.GREEN)
    screen.blit(surf, (sw // 2 - surf.get_width() // 2, sh // 2 + 20))

    _draw_step_indicator(6, total_steps)
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


# ── WiFi step helpers ───────────────────────────────────────────────

def _wifi_status() -> dict:
    """Return current network connection status. Empty dict on error.

    Keys:
      connected (bool):  True if any network interface is connected
      ssid (str|None):   SSID we're connected to, if WiFi
      ip (str|None):     v4 address, if available
    """
    result: dict = {"connected": False, "ssid": None, "ip": None}
    try:
        # nmcli reports connectivity in a single line (full / portal /
        # limited / none). "full" is the only state we accept as
        # "skip the wifi step entirely."
        out = subprocess.run(
            ["nmcli", "-t", "networking", "connectivity"],
            capture_output=True, text=True, timeout=4,
        )
        if (out.returncode == 0
                and out.stdout.strip() == "full"):
            result["connected"] = True
        # Pull the active SSID + IP for any wifi connection.
        out = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,STATE,DEVICE,CONNECTION",
             "device", "status"],
            capture_output=True, text=True, timeout=4,
        )
        if out.returncode == 0:
            for line in out.stdout.split("\n"):
                parts = line.split(":")
                if len(parts) < 4:
                    continue
                if parts[0] == "wifi" and parts[1] == "connected":
                    result["ssid"] = parts[3] or None
                    break
        # Best-effort IP (just for display).
        out = subprocess.run(
            ["hostname", "-I"], capture_output=True,
            text=True, timeout=3,
        )
        if out.returncode == 0:
            ips = out.stdout.strip().split()
            if ips:
                result["ip"] = ips[0]
    except Exception:
        pass
    return result


def _wifi_scan() -> list[dict]:
    """Trigger a rescan and return the visible networks, brightest
    first, deduped by SSID. Empty list on error / no WiFi adapter."""
    try:
        # Force a rescan; ignore errors (the device might be busy).
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SECURITY,SIGNAL",
             "device", "wifi", "list"],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0:
            return []
    except Exception:
        return []
    nets: list[dict] = []
    seen: set[str] = set()
    for line in out.stdout.split("\n"):
        # nmcli's -t output is colon-separated; SSIDs containing
        # colons are escaped with backslash. Cheap unescape:
        line = line.replace("\\:", "\x00")
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid = parts[0].replace("\x00", ":")
        sec = parts[1]
        try:
            signal = int(parts[2])
        except (ValueError, IndexError):
            signal = 0
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        nets.append({
            "ssid": ssid,
            "secured": bool(sec.strip()),
            "signal": signal,
        })
    nets.sort(key=lambda n: -n["signal"])
    return nets[:10]


def _wifi_connect(ssid: str, password: str) -> tuple[bool, str]:
    """Attempt to connect. Returns (success, message)."""
    try:
        cmd = ["sudo", "-n", "nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd.extend(["password", password])
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=45,
        )
        if out.returncode == 0:
            return True, out.stdout.strip() or "Connected"
        # nmcli writes the user-friendly error to stderr.
        msg = (out.stderr or out.stdout or "Unknown error").strip()
        return False, msg[:120]
    except subprocess.TimeoutExpired:
        return False, "Connection timed out"
    except Exception as e:
        return False, f"{e}"[:120]


def _step_wifi(
    screen, clock, app, fps, sw, sh,
    f_hero, f_title, f_large, f_med, f_small,
    draw_step_indicator, draw_footer, flip,
    total_steps, wait_for_tap,
) -> None:
    """Step 5 of the wizard: WiFi setup.

    If we're already online, show "Connected" and continue. Otherwise
    scan, list available networks, prompt for password via the
    OnScreenKeyboard, attempt connection, surface the result.
    Always allows Skip — Ethernet users or "no network needed" users
    aren't blocked.
    """
    step_idx = 4

    # ── Already connected? Just confirm and move on. ────────────────
    status = _wifi_status()
    if status.get("connected"):
        screen.fill(theme.BG)
        s = f_title.render("Network", True, theme.ACCENT)
        screen.blit(s, (sw // 2 - s.get_width() // 2, sh // 2 - 90))
        line = (f"Connected — {status['ssid']}"
                if status.get("ssid") else "Online via Ethernet")
        s = f_large.render(line, True, theme.GREEN)
        screen.blit(s, (sw // 2 - s.get_width() // 2, sh // 2 - 30))
        if status.get("ip"):
            s = f_small.render(
                f"IP {status['ip']}", True, theme.TEXT_DIM)
            screen.blit(
                s, (sw // 2 - s.get_width() // 2, sh // 2 + 10))
        draw_step_indicator(step_idx, total_steps)
        draw_footer("Tap anywhere to continue")
        flip(screen)
        wait_for_tap()
        return

    # ── Scan + pick loop ────────────────────────────────────────────
    while True:
        # Scanning indicator
        screen.fill(theme.BG)
        s = f_title.render("WiFi Setup", True, theme.ACCENT)
        screen.blit(s, (sw // 2 - s.get_width() // 2, 24))
        s = f_med.render("Scanning for networks…", True, theme.TEXT_DIM)
        screen.blit(s, (sw // 2 - s.get_width() // 2, sh // 2 - 12))
        draw_step_indicator(step_idx, total_steps)
        flip(screen)
        pygame.event.pump()  # keep input responsive

        nets = _wifi_scan()

        # If no WiFi adapter / no networks, present skip-only.
        if not nets:
            screen.fill(theme.BG)
            s = f_title.render("WiFi Setup", True, theme.ACCENT)
            screen.blit(s, (sw // 2 - s.get_width() // 2, 24))
            s = f_large.render(
                "No WiFi networks found", True, theme.YELLOW)
            screen.blit(
                s, (sw // 2 - s.get_width() // 2, sh // 2 - 40))
            s = f_small.render(
                "If you're on Ethernet, you can skip this step.",
                True, theme.TEXT_DIM)
            screen.blit(
                s, (sw // 2 - s.get_width() // 2, sh // 2 - 6))

            btn_w, btn_h, gap = 180, 44, 20
            rescan_rect = pygame.Rect(
                sw // 2 - btn_w - gap // 2, sh // 2 + 30,
                btn_w, btn_h)
            skip_rect = pygame.Rect(
                sw // 2 + gap // 2, sh // 2 + 30, btn_w, btn_h)
            theme.draw_button(
                screen, rescan_rect, "RESCAN", f_med, active=False)
            theme.draw_button(
                screen, skip_rect, "SKIP", f_med, active=True)
            draw_step_indicator(step_idx, total_steps)
            draw_footer("Tap RESCAN to retry, SKIP to continue")
            flip(screen)

            choice = _wait_for_choice_local(
                clock, fps, [rescan_rect, skip_rect])
            if choice in (-1, 1):
                return  # skip
            continue  # rescan

        # ── Network list ────────────────────────────────────────────
        screen.fill(theme.BG)
        s = f_title.render("WiFi Setup", True, theme.ACCENT)
        screen.blit(s, (sw // 2 - s.get_width() // 2, 24))
        s = f_small.render(
            "Tap a network to connect", True, theme.TEXT_DIM)
        screen.blit(s, (sw // 2 - s.get_width() // 2, 60))

        list_top = 84
        row_h = 36
        list_w = sw - 80
        list_x = 40
        net_rects: list[pygame.Rect] = []
        for i, net in enumerate(nets):
            r = pygame.Rect(
                list_x, list_top + i * (row_h + 4), list_w, row_h)
            net_rects.append(r)
            pygame.draw.rect(
                screen, theme.BG_PANEL, r, border_radius=6)
            pygame.draw.rect(
                screen, theme.BORDER, r, 1, border_radius=6)
            # SSID
            ss = f_med.render(net["ssid"][:32], True, theme.TEXT)
            screen.blit(
                ss, (r.x + 12, r.y + (r.height - ss.get_height()) // 2))
            # Signal strength bars (right side)
            bar_x = r.right - 80
            bar_y = r.y + r.height // 2
            bar_count = 4
            level = max(1, min(bar_count,
                               int(net["signal"] / 25) + 1))
            for j in range(bar_count):
                bw = 4
                bh = 4 + j * 3
                bar = pygame.Rect(
                    bar_x + j * 7, bar_y - bh // 2 + 4, bw, bh)
                color = (theme.ACCENT if j < level else theme.BG_LIGHTER)
                pygame.draw.rect(screen, color, bar, border_radius=1)
            # Lock icon for secured networks
            if net["secured"]:
                lock_x = r.right - 24
                ls = f_small.render("🔒", True, theme.TEXT_DIM)
                # Some pygame builds don't render emoji — fall back
                # to a simple text marker.
                if ls.get_width() < 6:
                    ls = f_small.render("PWD", True, theme.TEXT_DIM)
                screen.blit(
                    ls,
                    (lock_x - ls.get_width(),
                     r.y + (r.height - ls.get_height()) // 2))

        # Action buttons at bottom
        btn_w, btn_h, gap = 180, 44, 20
        actions_y = sh - btn_h - 60
        rescan_rect = pygame.Rect(
            sw // 2 - btn_w - gap // 2, actions_y, btn_w, btn_h)
        skip_rect = pygame.Rect(
            sw // 2 + gap // 2, actions_y, btn_w, btn_h)
        theme.draw_button(
            screen, rescan_rect, "RESCAN", f_med, active=False)
        theme.draw_button(
            screen, skip_rect, "SKIP — USE ETHERNET", f_med,
            active=False)

        draw_step_indicator(step_idx, total_steps)
        draw_footer("Networks shown strongest first")
        flip(screen)

        # ── Wait for selection ─────────────────────────────────────
        choice = _wait_for_choice_local(
            clock, fps,
            net_rects + [rescan_rect, skip_rect])
        if choice == -1 or choice == len(net_rects) + 1:
            return  # skip / quit
        if choice == len(net_rects):
            continue  # rescan
        net = nets[choice]

        # ── Password (if secured) ──────────────────────────────────
        password = ""
        if net["secured"]:
            entered = _get_keyboard_input(
                app, screen, clock, fps,
                title=f"Password for {net['ssid']}",
                password=True,
            )
            if entered is None:
                continue  # cancel — back to network list
            password = entered

        # ── Connecting screen ──────────────────────────────────────
        screen.fill(theme.BG)
        s = f_title.render(
            "Connecting…", True, theme.ACCENT)
        screen.blit(s, (sw // 2 - s.get_width() // 2, sh // 2 - 60))
        s = f_med.render(net["ssid"], True, theme.TEXT_BRIGHT)
        screen.blit(s, (sw // 2 - s.get_width() // 2, sh // 2 - 20))
        s = f_small.render(
            "This can take up to 30 seconds.",
            True, theme.TEXT_DIM)
        screen.blit(s, (sw // 2 - s.get_width() // 2, sh // 2 + 14))
        draw_step_indicator(step_idx, total_steps)
        flip(screen)
        pygame.event.pump()

        ok, msg = _wifi_connect(net["ssid"], password)

        # ── Result ─────────────────────────────────────────────────
        screen.fill(theme.BG)
        if ok:
            s = f_title.render(
                "Connected ✓", True, theme.GREEN)
            screen.blit(
                s, (sw // 2 - s.get_width() // 2, sh // 2 - 60))
            s = f_large.render(
                net["ssid"], True, theme.TEXT_BRIGHT)
            screen.blit(
                s, (sw // 2 - s.get_width() // 2, sh // 2 - 16))
            # Pull IP for display.
            new_status = _wifi_status()
            if new_status.get("ip"):
                s = f_small.render(
                    f"IP {new_status['ip']}",
                    True, theme.TEXT_DIM)
                screen.blit(
                    s, (sw // 2 - s.get_width() // 2, sh // 2 + 16))
            draw_step_indicator(step_idx, total_steps)
            draw_footer("Tap anywhere to continue")
            flip(screen)
            wait_for_tap()
            return
        else:
            s = f_title.render(
                "Connection failed", True, theme.YELLOW)
            screen.blit(
                s, (sw // 2 - s.get_width() // 2, sh // 2 - 80))
            # Wrap the error message.
            err_y = sh // 2 - 30
            err = msg if msg else "Unknown error"
            for line in _wrap_text(err, f_small, sw - 60):
                ls = f_small.render(line, True, theme.TEXT_DIM)
                screen.blit(
                    ls, (sw // 2 - ls.get_width() // 2, err_y))
                err_y += f_small.get_linesize()

            retry_rect = pygame.Rect(
                sw // 2 - btn_w - gap // 2, sh - btn_h - 60,
                btn_w, btn_h)
            skip_rect = pygame.Rect(
                sw // 2 + gap // 2, sh - btn_h - 60, btn_w, btn_h)
            theme.draw_button(
                screen, retry_rect, "TRY AGAIN", f_med, active=True)
            theme.draw_button(
                screen, skip_rect, "SKIP", f_med, active=False)
            draw_step_indicator(step_idx, total_steps)
            draw_footer("Bad password? Try Again. Or Skip for now.")
            flip(screen)
            choice = _wait_for_choice_local(
                clock, fps, [retry_rect, skip_rect])
            if choice in (-1, 1):
                return
            continue  # back to network list


def _get_keyboard_input(app, screen, clock, fps,
                          title: str, password: bool = False) -> str | None:
    """Use the OnScreenKeyboard to get a string from the user.
    Returns the entered text, or None if the user cancelled."""
    result = {"text": None, "submitted": False}

    def on_submit(text):
        result["text"] = text
        result["submitted"] = True

    def on_cancel():
        result["text"] = None
        result["submitted"] = False

    kb = getattr(app, "keyboard", None)
    if kb is None:
        return None
    kb.show(
        title=title,
        default="",
        password=password,
        on_submit=on_submit,
        on_cancel=on_cancel,
    )

    while kb.visible:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                kb.visible = False
                return None
            kb.handle_event(event)
        screen.fill(theme.BG)
        kb.draw(screen)
        _flip(screen)
        clock.tick(fps)

    return result["text"] if result["submitted"] else None


def _wait_for_choice_local(clock, fps, rects: list) -> int:
    """Standalone version of run_wizard's _wait_for_choice — used by
    the WiFi step's helpers, which are module-level. Returns the
    rect index that was tapped, or -1 on quit. Number keys 1..9 also
    select the corresponding rect."""
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return -1
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                if event.type == pygame.FINGERDOWN:
                    sw, sh = pygame.display.get_surface().get_size()
                    mx = int(event.x * sw)
                    my = int(event.y * sh)
                else:
                    if event.button != 1:
                        continue
                    mx, my = event.pos
                for i, r in enumerate(rects):
                    if r.collidepoint(mx, my):
                        return i
            if event.type == pygame.KEYDOWN:
                if pygame.K_1 <= event.key <= pygame.K_9:
                    idx = event.key - pygame.K_1
                    if idx < len(rects):
                        return idx
                if event.key == pygame.K_ESCAPE:
                    return -1
        clock.tick(fps)


def _wrap_text(text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = ""
    for w in words:
        test = (line + " " + w).strip()
        if font.size(test)[0] > max_w:
            if line:
                lines.append(line)
            line = w
        else:
            line = test
    if line:
        lines.append(line)
    return lines
