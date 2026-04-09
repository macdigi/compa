"""Compa App — main entry point.

Turns the Pi + ATOM SQ + touchscreen into a control surface,
sample manager, and recorder for the Roland AIRA Compact P-6.
"""

import os
import signal
import sys
import pygame

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ui import theme
from ui.components.button import Button
from ui.screens.p6_session_screen import P6SessionScreen
from ui.screens.p6_control_screen import P6ControlScreen
from ui.screens.p6_pattern_screen import P6PatternScreen
from ui.screens.p6_record_screen import P6RecordScreen
from ui.screens.p6_sample_screen import P6SampleScreen
from ui.screens.p6_help_screen import P6HelpScreen
from ui.screens.p6_radio_screen import P6RadioScreen
from engine.atom_sq import AtomSQ, find_atom_sq_ports
from engine.p6_midi import P6Midi, find_p6_ports
from engine.midi_router import MidiRouter, Layer
from engine.p6_recorder import P6Recorder
from ui.splash import run_splash

# Custom pygame events (thread-safe MIDI → UI bridge)
P6_TRANSPORT_EVENT = pygame.USEREVENT + 1


def load_config() -> dict:
    config = {
        "P6_SAMPLE_RATE": 48000,
        "P6_RECORDING_DIR": os.path.join(PROJECT_ROOT, "recordings"),
        "P6_SESSIONS_DIR": os.path.join(PROJECT_ROOT, "sessions"),
        "P6_MIDI_PORT_HINT": "P-6",
        "AUDIO_DEVICE_HINT": "iD4",
        "LOCAL_SAMPLE_CACHE": os.path.join(PROJECT_ROOT, "samples"),
        "P6_AUTO_RECORD": "1",
    }
    config_path = os.path.join(PROJECT_ROOT, "setup", "config.env")
    if os.path.exists(config_path):
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    config[key.strip()] = val.strip()
    config["P6_SAMPLE_RATE"] = int(config["P6_SAMPLE_RATE"])
    return config


def save_config_key(key: str, value: str) -> None:
    """Update a single key in config.env, preserving other entries."""
    config_path = os.path.join(PROJECT_ROOT, "setup", "config.env")
    lines = []
    found = False
    if os.path.exists(config_path):
        with open(config_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        lines.append(f"{key}={value}\n")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        f.writelines(lines)


class P6App:
    """Compa application."""

    def __init__(self):
        self.config = load_config()

        # ── Pygame init ──────────────────────────────────────────────
        # Framebuffer mode for SPI LCDs (set by start.sh)
        self._fb_mode = os.environ.get("COMPA_FB_MODE") == "1"
        self._fb_file = None

        if "SDL_VIDEODRIVER" not in os.environ:
            os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
        os.environ.setdefault("SDL_FBDEV", "/dev/fb0")
        pygame.init()

        if self._fb_mode:
            # SPI LCD: use dummy driver, detect fb size, open fb for writing
            fb_dev = os.environ.get("COMPA_FB_DEV", "/dev/fb0")
            fb_num = fb_dev.replace("/dev/fb", "")
            fb_size = (480, 320)
            try:
                with open(f"/sys/class/graphics/fb{fb_num}/virtual_size") as f:
                    parts = f.read().strip().split(",")
                    fb_size = (int(parts[0]), int(parts[1]))
            except Exception:
                pass
            self.screen = pygame.display.set_mode(fb_size)
            self._display_w, self._display_h = fb_size
            try:
                import subprocess
                subprocess.run(["sudo", "chmod", "666", fb_dev], capture_output=True)
                self._fb_file = open(fb_dev, "wb")
            except Exception as e:
                print(f"Cannot open {fb_dev}: {e}", flush=True)
                self._fb_file = None
            print(f"FB mode: {fb_size[0]}x{fb_size[1]}", flush=True)
        else:
            # HDMI/KMSDRM: detect actual display
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            info = pygame.display.Info()
            self._display_w = info.current_w
            self._display_h = info.current_h

        # Update theme with actual dimensions
        theme.init_display(self._display_w, self._display_h)
        theme.init_fonts()

        pygame.display.set_caption("Compa")
        pygame.mouse.set_visible(False)

        print(f"Display: {self._display_w}x{self._display_h}", flush=True)

        # FB mode: start evdev touch reader thread
        self._evdev_thread = None
        if self._fb_mode:
            self._start_evdev_touch()

        # Touch-drag scroll tracking
        self._touch_start_y = 0
        self._touch_start_x = 0
        self._touch_is_scroll = False
        self._touch_scroll_threshold = 15  # pixels before drag becomes scroll

        self.clock = pygame.time.Clock()
        self.fps = 30
        self.running = True

        # ── Auto-record setting ─────────────────────────────────────
        self.auto_record = self.config.get("P6_AUTO_RECORD", "1") == "1"

        # ── MIDI devices (init before screens so state objects exist) ──
        self.atom_sq: AtomSQ | None = None
        self.p6: P6Midi | None = None
        self.router: MidiRouter | None = None

        # ── Recorder ─────────────────────────────────────────────────
        self.recorder = P6Recorder(
            recording_dir=self.config["P6_RECORDING_DIR"],
            device_hint=self.config["AUDIO_DEVICE_HINT"],
        )

        # ── Sessions directory ───────────────────────────────────────
        os.makedirs(self.config["P6_SESSIONS_DIR"], exist_ok=True)

        # ── Screens ─────────────────────────────────────────────────
        self.screens: dict[str, object] = {
            "session": P6SessionScreen(self),
            "control": P6ControlScreen(self),
            "pattern": P6PatternScreen(self),
            "record":  P6RecordScreen(self),
            "sample":  P6SampleScreen(self),
            "radio":   P6RadioScreen(self),
            "help":    P6HelpScreen(self),
        }
        self.current_screen_name = "session"

        # ── Nav bar (responsive) ─────────────────────────────────────
        nav_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT
        if theme.SCREEN_WIDTH >= 700:
            # Wide screen: full labels
            nav_labels = [
                ("SESSION", "session"), ("CONTROL", "control"),
                ("PATTERN", "pattern"), ("RECORD",  "record"),
                ("SAMPLE",  "sample"),  ("RADIO",   "radio"),
            ]
            font_name = "small"
        elif theme.SCREEN_WIDTH >= 400:
            # Medium screen: short labels
            nav_labels = [
                ("SES", "session"), ("CTL", "control"),
                ("PAT", "pattern"), ("REC", "record"),
                ("SMP", "sample"),  ("RAD", "radio"),
            ]
            font_name = "tiny"
        else:
            # Tiny screen: icons/minimal
            nav_labels = [
                ("S", "session"), ("C", "control"),
                ("P", "pattern"), ("R", "record"),
                ("F", "sample"),  ("~", "radio"),
            ]
            font_name = "tiny"

        self.nav_buttons: list[tuple[Button, str]] = []
        num_btns = len(nav_labels)
        btn_gap = 3
        avail_w = theme.SCREEN_WIDTH - 36  # room for ? button
        btn_w = (avail_w - (num_btns - 1) * btn_gap) // num_btns
        btn_h = min(28, theme.NAV_HEIGHT - 24)
        start_x = 32  # after ? button
        for i, (label, screen_name) in enumerate(nav_labels):
            btn = Button(
                pygame.Rect(start_x + i * (btn_w + btn_gap), nav_y + 4, btn_w, btn_h),
                label,
                font_name=font_name,
            )
            self.nav_buttons.append((btn, screen_name))

        self._nav_rect = pygame.Rect(0, nav_y, theme.SCREEN_WIDTH, theme.NAV_HEIGHT)

        # ── MIDI init (AFTER screens) ────────────────────────────────
        self._init_midi()

        # ── Status ───────────────────────────────────────────────────
        print(f"ATOM SQ: {'connected' if self.atom_sq else 'not found'}")
        print(f"P-6: {'connected' if self.p6 else 'not found'}")
        print(f"Recorder: {'ready' if self.recorder.available else 'no audio device'}")
        print(f"Auto-record: {'ON' if self.auto_record else 'OFF'}")

    def _init_midi(self):
        """Detect and connect ATOM SQ and P-6."""
        # ATOM SQ
        try:
            midi_in, midi_out = find_atom_sq_ports()
            if midi_in is not None:
                self.atom_sq = AtomSQ(midi_in, midi_out)
                print("ATOM SQ connected")
        except Exception as e:
            print(f"ATOM SQ init failed: {e}")

        # P-6
        try:
            midi_in, midi_out = find_p6_ports(self.config["P6_MIDI_PORT_HINT"])
            if midi_in is not None or midi_out is not None:
                self.p6 = P6Midi(midi_in, midi_out)
                # Wire P-6 transport for auto-record
                self.p6.on_transport = self._on_p6_transport
                print("P-6 MIDI connected")
        except Exception as e:
            print(f"P-6 MIDI init failed: {e}")

        # Router (needs both ATOM SQ and P-6)
        if self.atom_sq and self.p6:
            self.router = MidiRouter(self.atom_sq, self.p6)
            self.router.set_ui_button_callback(self._on_ui_button)
            self.router.on_transport = self._on_atomsq_transport
            print("MIDI router active: ATOM SQ → P-6")
        elif self.atom_sq:
            self.atom_sq.on_button = self._on_atomsq_button_fallback
            print("ATOM SQ connected but P-6 not found — navigation only")

    # ── Evdev touch input (for SPI LCD / FB mode) ─────────────────────

    def _start_evdev_touch(self):
        """Start a thread that reads evdev touch events and posts pygame events."""
        import threading
        self._evdev_thread = threading.Thread(target=self._evdev_loop, daemon=True)
        self._evdev_thread.start()

    def _evdev_loop(self):
        """Read ADS7846 touch events and convert to pygame mouse events."""
        try:
            import evdev
            import select
        except ImportError:
            print("evdev not available for touch input", flush=True)
            return

        # Find touch device
        dev = None
        for path in evdev.list_devices():
            d = evdev.InputDevice(path)
            if "touch" in d.name.lower() or "ads" in d.name.lower():
                dev = d
                break

        if not dev:
            print("No touch device found", flush=True)
            return

        print(f"Evdev touch: {dev.name} at {dev.path}", flush=True)

        # Load tslib calibration from /etc/pointercal if available
        # Format: A B C D E F S  (affine transform)
        # screen_x = (A * raw_x + B * raw_y + C) / S
        # screen_y = (D * raw_x + E * raw_y + F) / S
        cal = None
        try:
            with open("/etc/pointercal") as f:
                vals = list(map(int, f.read().strip().split()))
                if len(vals) >= 7:
                    cal = vals[:7]
                    print(f"Touch calibration loaded: {cal}", flush=True)
        except Exception:
            pass

        if not cal:
            print("No /etc/pointercal — using raw coordinates", flush=True)

        touch_x = touch_y = 0
        raw_x = raw_y = 0
        touching = False
        sw, sh = self._display_w, self._display_h

        while True:
            try:
                r, _, _ = select.select([dev], [], [], 0.05)
                if not r:
                    continue
                for event in dev.read():
                    if event.type == 3:  # EV_ABS
                        if event.code == 0:  # ABS_X
                            raw_x = event.value
                        elif event.code == 1:  # ABS_Y
                            raw_y = event.value

                        if cal:
                            A, B, C, D, E, F, S = cal
                            touch_x = (A * raw_x + B * raw_y + C) // S
                            touch_y = (D * raw_x + E * raw_y + F) // S
                        else:
                            touch_x = int(raw_x * sw / 4096)
                            touch_y = int(raw_y * sh / 4096)

                        touch_x = max(0, min(sw - 1, touch_x))
                        touch_y = max(0, min(sh - 1, touch_y))
                    elif event.type == 1 and event.code == 330:  # BTN_TOUCH
                        if event.value == 1 and not touching:
                            touching = True
                            pygame.event.post(pygame.event.Event(
                                pygame.MOUSEBUTTONDOWN, pos=(touch_x, touch_y), button=1))
                        elif event.value == 0 and touching:
                            touching = False
                            pygame.event.post(pygame.event.Event(
                                pygame.MOUSEBUTTONUP, pos=(touch_x, touch_y), button=1))
                    elif event.type == 0 and event.code == 0 and touching:  # SYN_REPORT
                        pygame.event.post(pygame.event.Event(
                            pygame.MOUSEMOTION, pos=(touch_x, touch_y),
                            rel=(0, 0), buttons=(1, 0, 0)))
            except Exception:
                pass

    # ── Transport callbacks ──────────────────────────────────────────

    def _on_p6_transport(self, action: str):
        """Handle transport from P-6 hardware (start/stop/continue).

        Called from MIDI poll thread — post a pygame event for thread safety.
        """
        pygame.event.post(pygame.event.Event(P6_TRANSPORT_EVENT, action=action))

    def _on_atomsq_transport(self, action: str):
        """Handle transport actions from the ATOM SQ via router."""
        if action == "record":
            if self.recorder.is_recording:
                self.recorder.stop_recording()
            else:
                meta = {}
                if self.p6:
                    meta["bpm_at_record"] = self.p6.state.bpm
                    meta["pattern_at_record"] = self.p6.state.active_pattern
                self.recorder.start_recording(metadata=meta)
                self.switch_screen("record")

    def _handle_p6_transport(self, action: str):
        """Process P-6 transport event in the main thread (auto-record + chain sync)."""
        if action in ("start", "continue"):
            if self.auto_record and not self.recorder.is_recording:
                if not self.recorder._monitoring:
                    self.recorder.start_monitoring()
                meta = {}
                if self.p6:
                    meta["bpm_at_record"] = self.p6.state.bpm
                    meta["pattern_at_record"] = self.p6.state.active_pattern
                self.recorder.start_recording(metadata=meta)
                self.switch_screen("record")
                print("Auto-record started", flush=True)
            # Chain sync: start chain on P-6 transport
            chain_player = self.screens["pattern"].chain_player
            if chain_player.sync_transport and chain_player.chain and chain_player.chain.steps:
                chain_player.start()
                if self.p6:
                    self.p6.on_clock_tick = chain_player.on_tick
                print("Chain sync started", flush=True)
        elif action == "stop":
            if self.auto_record and self.recorder.is_recording:
                self.recorder.stop_recording()
                print("Auto-record stopped", flush=True)
            chain_player = self.screens["pattern"].chain_player
            if chain_player.sync_transport and chain_player.playing:
                chain_player.stop()
                print("Chain sync stopped", flush=True)

    # ── UI button callbacks ──────────────────────────────────────────

    def _on_ui_button(self, name: str):
        """Handle navigation buttons from ATOM SQ via router."""
        screen_order = ["session", "control", "pattern", "record", "sample"]

        if name == "btn_a":
            idx = screen_order.index(self.current_screen_name) if self.current_screen_name in screen_order else 0
            self.switch_screen(screen_order[(idx - 1) % len(screen_order)])
        elif name == "btn_b":
            idx = screen_order.index(self.current_screen_name) if self.current_screen_name in screen_order else 0
            self.switch_screen(screen_order[(idx + 1) % len(screen_order)])
        elif name == "soft_1":
            self.switch_screen("session")
        elif name == "soft_2":
            self.switch_screen("control")
        elif name == "soft_3":
            self.switch_screen("pattern")
        elif name == "soft_4":
            self.switch_screen("record")
        elif name == "soft_5":
            self.switch_screen("sample")
        elif name in ("up", "down", "click", "select", "shift"):
            screen = self.current_screen
            if hasattr(screen, f"on_{name}"):
                getattr(screen, f"on_{name}")()

    def _on_atomsq_button_fallback(self, name: str, pressed: bool):
        """Fallback for ATOM SQ buttons when router is not active."""
        if pressed:
            self._on_ui_button(name)

    # ── Screen management ────────────────────────────────────────────

    @property
    def current_screen(self):
        return self.screens[self.current_screen_name]

    def switch_screen(self, name: str):
        if name in self.screens and name != self.current_screen_name:
            old_screen = self.screens.get(self.current_screen_name)
            if old_screen and hasattr(old_screen, "on_exit"):
                old_screen.on_exit()
            self.current_screen_name = name
            new_screen = self.screens[name]
            if hasattr(new_screen, "on_enter"):
                new_screen.on_enter()

    # ── Main loop ────────────────────────────────────────────────────

    def run(self):
        try:
            while self.running:
                self._handle_events()
                self._update()
                self._draw()
                self.clock.tick(self.fps)
        finally:
            self._shutdown()

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                continue

            # Convert touch events to mouse events with scroll detection
            if event.type == pygame.FINGERDOWN:
                mx = int(event.x * self._display_w)
                my = int(event.y * self._display_h)
                self._touch_start_x = mx
                self._touch_start_y = my
                self._touch_is_scroll = False
                event = pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                           pos=(mx, my), button=1)
            elif event.type == pygame.FINGERUP:
                mx = int(event.x * self._display_w)
                my = int(event.y * self._display_h)
                if self._touch_is_scroll:
                    # Was scrolling — don't send click
                    self._touch_is_scroll = False
                    event = pygame.event.Event(pygame.MOUSEBUTTONUP,
                                               pos=(mx, my), button=1)
                else:
                    event = pygame.event.Event(pygame.MOUSEBUTTONUP,
                                               pos=(mx, my), button=1)
            elif event.type == pygame.FINGERMOTION:
                mx = int(event.x * self._display_w)
                my = int(event.y * self._display_h)
                dy = my - self._touch_start_y

                if not self._touch_is_scroll and abs(dy) > self._touch_scroll_threshold:
                    self._touch_is_scroll = True

                if self._touch_is_scroll:
                    # Skip global scroll if a screen is handling its own drag
                    screen = self.current_screen
                    if hasattr(screen, '_dragging_scrollbar') and screen._dragging_scrollbar:
                        # Let the screen handle the drag directly as MOUSEMOTION
                        event = pygame.event.Event(pygame.MOUSEMOTION,
                                                   pos=(mx, my),
                                                   rel=(int(event.dx * self._display_w),
                                                        int(event.dy * self._display_h)),
                                                   buttons=(1, 0, 0))
                        self.current_screen.handle_event(event)
                        continue

                    # Generate scroll events based on drag direction
                    scroll_dy = int(event.dy * self._display_h)
                    if abs(scroll_dy) > 8:
                        # Scroll up (drag down) or scroll down (drag up)
                        btn = 4 if scroll_dy > 0 else 5
                        event = pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                                   pos=(mx, my), button=btn)
                        self._touch_start_y = my  # reset for next increment
                    else:
                        continue  # skip small motions
                else:
                    event = pygame.event.Event(pygame.MOUSEMOTION,
                                               pos=(mx, my),
                                               rel=(int(event.dx * self._display_w),
                                                    int(event.dy * self._display_h)),
                                               buttons=(1, 0, 0))

            # P-6 transport event (from MIDI thread via pygame event)
            if event.type == P6_TRANSPORT_EVENT:
                self._handle_p6_transport(event.action)
                continue

            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.running = False
                return

            # Check if current screen wants keyboard input (e.g., text area)
            screen_wants_kb = getattr(self.current_screen, "wants_keyboard", False)

            # Keyboard shortcuts (skip if screen wants keyboard)
            if event.type == pygame.KEYDOWN and not screen_wants_kb:
                NAV_KEYS = {
                    pygame.K_F1: "session", pygame.K_F2: "control",
                    pygame.K_F3: "pattern", pygame.K_F4: "record",
                    pygame.K_F5: "sample", pygame.K_F6: "radio",
                    pygame.K_F7: "help",
                }
                if event.key in NAV_KEYS:
                    self.switch_screen(NAV_KEYS[event.key])
                    continue
                elif event.key == pygame.K_SPACE:
                    if self.p6:
                        if self.p6.state.playing:
                            self.p6.send_stop()
                        else:
                            self.p6.send_start()
                    continue
                elif event.key == pygame.K_r:
                    if self.recorder.is_recording:
                        self.recorder.stop_recording()
                    else:
                        meta = {}
                        if self.p6:
                            meta["bpm_at_record"] = self.p6.state.bpm
                            meta["pattern_at_record"] = self.p6.state.active_pattern
                        self.recorder.start_recording(metadata=meta)
                    continue
                elif event.key == pygame.K_F12:
                    # Screenshot
                    path = "/tmp/compa_screen.png"
                    pygame.image.save(self.screen, path)
                    print(f"Screenshot saved: {path}", flush=True)
                    continue
                elif event.key == pygame.K_a:
                    # Toggle auto-record
                    self.auto_record = not self.auto_record
                    save_config_key("P6_AUTO_RECORD", "1" if self.auto_record else "0")
                    print(f"Auto-record: {'ON' if self.auto_record else 'OFF'}", flush=True)
                    continue

            # Help button
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                help_rect = pygame.Rect(4, self._nav_rect.y + 8, 28, 28)
                if help_rect.collidepoint(event.pos):
                    self.switch_screen("help")
                    continue

            # Nav bar
            nav_handled = False
            for btn, screen_name in self.nav_buttons:
                if btn.handle_event(event):
                    self.switch_screen(screen_name)
                    nav_handled = True
                    break
            if nav_handled:
                continue

            # Current screen
            self.current_screen.handle_event(event)

    def _update(self):
        # Check for remote screenshot request
        if os.path.exists("/tmp/compa_screenshot_request"):
            os.remove("/tmp/compa_screenshot_request")
            pygame.image.save(self.screen, "/tmp/compa_screen.png")
            print("Screenshot captured", flush=True)

        # Screenshot all screens at once
        if os.path.exists("/tmp/compa_screenshot_all"):
            os.remove("/tmp/compa_screenshot_all")
            original = self.current_screen_name
            for name in self.screens:
                self.switch_screen(name)
                self.screen.fill(theme.BG)
                self.current_screen.draw(self.screen)
                self._draw_nav()
                pygame.image.save(self.screen, f"/tmp/compa_{name}.png")
                print(f"Screenshot: {name}", flush=True)
            self.switch_screen(original)
            print("All screenshots captured", flush=True)

        if hasattr(self.current_screen, "update"):
            self.current_screen.update()
        for btn, screen_name in self.nav_buttons:
            btn.active = (screen_name == self.current_screen_name)

    def _draw(self):
        self.screen.fill(theme.BG)
        self.current_screen.draw(self.screen)
        self._draw_nav()
        pygame.display.flip()

        # SPI LCD: copy pygame surface to framebuffer as RGB565
        if self._fb_mode and self._fb_file:
            try:
                import numpy as np
                # Get pixels as 3D array (H, W, 3) uint8
                arr = pygame.surfarray.pixels3d(self.screen)
                # Transpose from (W,H,3) to (H,W,3)
                arr = np.transpose(arr, (1, 0, 2))
                r = arr[:, :, 0].astype(np.uint16)
                g = arr[:, :, 1].astype(np.uint16)
                b = arr[:, :, 2].astype(np.uint16)
                rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                self._fb_file.seek(0)
                self._fb_file.write(rgb565.astype(np.uint16).tobytes())
                self._fb_file.flush()
            except Exception:
                pass

    def _draw_nav(self):
        f = theme.font("small")
        f_tiny = theme.font("tiny")

        # Nav bar background with top accent line
        pygame.draw.rect(self.screen, theme.NAV_BG, self._nav_rect)
        pygame.draw.line(self.screen, theme.BORDER,
                        (0, self._nav_rect.y), (theme.SCREEN_WIDTH, self._nav_rect.y))
        # Subtle accent line at very top of nav
        pygame.draw.line(self.screen, theme.ACCENT_DIM,
                        (0, self._nav_rect.y), (theme.SCREEN_WIDTH, self._nav_rect.y))

        # ── Nav buttons (custom drawn for polish) ────────────────────
        for btn, screen_name in self.nav_buttons:
            rect = btn.rect
            active = (screen_name == self.current_screen_name)

            if active:
                # Active tab: accent background with glow
                glow = rect.inflate(2, 2)
                glow_surf = pygame.Surface((glow.width, glow.height), pygame.SRCALPHA)
                pygame.draw.rect(glow_surf, (*theme.ACCENT[:3], 25),
                                (0, 0, glow.width, glow.height), border_radius=6)
                self.screen.blit(glow_surf, glow.topleft)
                pygame.draw.rect(self.screen, theme.ACCENT, rect, border_radius=6)
                surf = f.render(btn.label, True, theme.BG)
            else:
                # Inactive: subtle background
                pygame.draw.rect(self.screen, theme.NAV_INACTIVE, rect, border_radius=6)
                pygame.draw.rect(self.screen, theme.BORDER, rect, 1, border_radius=6)
                surf = f.render(btn.label, True, theme.TEXT_DIM)

            self.screen.blit(surf, surf.get_rect(center=rect.center))

        # ── Help button ──────────────────────────────────────────────
        help_rect = pygame.Rect(4, self._nav_rect.y + 4, 26, 26)
        help_active = self.current_screen_name == "help"
        if help_active:
            pygame.draw.rect(self.screen, theme.ACCENT, help_rect, border_radius=13)
            surf = f.render("?", True, theme.BG)
        else:
            pygame.draw.rect(self.screen, theme.BORDER, help_rect, 1, border_radius=13)
            surf = f_tiny.render("?", True, theme.TEXT_DIM)
        self.screen.blit(surf, surf.get_rect(center=help_rect.center))

        # ── Status bar (bottom of nav) ───────────────────────────────
        status_y = self._nav_rect.y + 35
        x = 36

        # Transport dot + state
        if self.p6 and self.p6.state.playing:
            pygame.draw.circle(self.screen, theme.GREEN, (x, status_y + 7), 4)
            x += 12
            surf = f_tiny.render("PLAY", True, theme.GREEN)
        elif self.recorder.is_recording:
            pygame.draw.circle(self.screen, theme.RED, (x, status_y + 7), 4)
            x += 12
            surf = f_tiny.render("REC", True, theme.RED)
        else:
            pygame.draw.circle(self.screen, theme.TEXT_DIM, (x, status_y + 7), 3, 1)
            x += 12
            surf = f_tiny.render("STOP", True, theme.TEXT_DIM)
        self.screen.blit(surf, (x, status_y))
        x += surf.get_width() + 10

        # BPM
        if self.p6:
            bpm = self.p6.state.bpm
            surf = f_tiny.render(f"{bpm:.0f}", True, theme.TEXT)
            self.screen.blit(surf, (x, status_y))
            x += surf.get_width() + 2
            surf = f_tiny.render("bpm", True, theme.TEXT_DIM)
            self.screen.blit(surf, (x, status_y))
            x += surf.get_width() + 10

        # Auto-record badge
        if self.auto_record:
            badge = pygame.Rect(x, status_y - 1, 38, 16)
            pygame.draw.rect(self.screen, theme.RED, badge, border_radius=3)
            surf = f_tiny.render("AUTO", True, theme.TEXT_BRIGHT)
            self.screen.blit(surf, surf.get_rect(center=badge.center))
            x += 46

        # Right side: connection status with dots
        rx = theme.SCREEN_WIDTH - 10
        if self.p6 and self.p6.connected:
            surf = f_tiny.render("P-6", True, theme.GREEN)
            rx -= surf.get_width()
            self.screen.blit(surf, (rx, status_y))
            pygame.draw.circle(self.screen, theme.GREEN, (rx - 8, status_y + 7), 3)
            rx -= 16
        else:
            surf = f_tiny.render("P-6", True, theme.RED)
            rx -= surf.get_width()
            self.screen.blit(surf, (rx, status_y))
            pygame.draw.circle(self.screen, theme.RED, (rx - 8, status_y + 7), 3, 1)
            rx -= 16

    def _shutdown(self):
        print("Shutting down Compa...")
        if hasattr(self.screens.get("radio", None), '_radio'):
            self.screens["radio"]._radio.shutdown()
        self.recorder.shutdown()
        if self.router:
            pass  # Router doesn't need shutdown
        if self.atom_sq:
            self.atom_sq.shutdown()
        if self.p6:
            self.p6.shutdown()
        pygame.quit()


def main():
    app = P6App()

    def _sigterm(signum, frame):
        app.running = False
    signal.signal(signal.SIGTERM, _sigterm)

    # Animated splash screen
    run_splash(app.screen, app.clock)

    app.run()


if __name__ == "__main__":
    main()
