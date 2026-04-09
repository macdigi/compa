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
from engine.atom_sq import AtomSQ, find_atom_sq_ports
from engine.p6_midi import P6Midi, find_p6_ports
from engine.midi_router import MidiRouter, Layer
from engine.p6_recorder import P6Recorder

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
        if "SDL_VIDEODRIVER" not in os.environ:
            os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
        os.environ.setdefault("SDL_FBDEV", "/dev/fb0")
        pygame.init()
        theme.init_fonts()

        self.screen = pygame.display.set_mode(
            (theme.SCREEN_WIDTH, theme.SCREEN_HEIGHT),
            pygame.FULLSCREEN,
        )
        pygame.display.set_caption("Compa")
        pygame.mouse.set_visible(False)  # Hide cursor for touchscreen

        # Store actual display dimensions for touch mapping
        info = pygame.display.Info()
        self._display_w = info.current_w
        self._display_h = info.current_h

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
            "help":    P6HelpScreen(self),
        }
        self.current_screen_name = "session"

        # ── Nav bar ──────────────────────────────────────────────────
        nav_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT
        nav_labels = [
            ("SESSION", "session"),
            ("CONTROL", "control"),
            ("PATTERN", "pattern"),
            ("RECORD",  "record"),
            ("SAMPLE",  "sample"),
        ]
        self.nav_buttons: list[tuple[Button, str]] = []
        btn_w = 140
        btn_gap = 6
        total_w = len(nav_labels) * btn_w + (len(nav_labels) - 1) * btn_gap
        start_x = (theme.SCREEN_WIDTH - total_w) // 2
        for i, (label, screen_name) in enumerate(nav_labels):
            btn = Button(
                pygame.Rect(start_x + i * (btn_w + btn_gap), nav_y + 6, btn_w, 32),
                label,
                font_name="small",
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

            # Convert touch events to mouse events for all screens
            # Use actual display dimensions for accurate mapping
            if event.type == pygame.FINGERDOWN:
                mx = int(event.x * self._display_w)
                my = int(event.y * self._display_h)
                event = pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                           pos=(mx, my), button=1)
            elif event.type == pygame.FINGERUP:
                mx = int(event.x * self._display_w)
                my = int(event.y * self._display_h)
                event = pygame.event.Event(pygame.MOUSEBUTTONUP,
                                           pos=(mx, my), button=1)
            elif event.type == pygame.FINGERMOTION:
                mx = int(event.x * self._display_w)
                my = int(event.y * self._display_h)
                event = pygame.event.Event(pygame.MOUSEMOTION,
                                           pos=(mx, my), rel=(int(event.dx * self._display_w),
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
                    pygame.K_F5: "sample", pygame.K_F6: "help",
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
        if hasattr(self.current_screen, "update"):
            self.current_screen.update()
        for btn, screen_name in self.nav_buttons:
            btn.active = (screen_name == self.current_screen_name)

    def _draw(self):
        self.screen.fill(theme.BG)
        self.current_screen.draw(self.screen)
        self._draw_nav()
        pygame.display.flip()

    def _draw_nav(self):
        pygame.draw.rect(self.screen, theme.NAV_BG, self._nav_rect)
        pygame.draw.line(
            self.screen, theme.BORDER,
            (0, self._nav_rect.y),
            (theme.SCREEN_WIDTH, self._nav_rect.y),
        )

        # Nav buttons (row 1)
        for btn, _ in self.nav_buttons:
            btn.draw(self.screen)

        f = theme.font("small")

        # Help button (left of nav buttons)
        help_rect = pygame.Rect(4, self._nav_rect.y + 4, 28, 28)
        help_bg = theme.ACCENT if self.current_screen_name == "help" else theme.BUTTON_BG
        help_tc = theme.BG if self.current_screen_name == "help" else theme.TEXT_DIM
        pygame.draw.rect(self.screen, help_bg, help_rect, border_radius=4)
        surf = f.render("?", True, help_tc)
        self.screen.blit(surf, surf.get_rect(center=help_rect.center))

        # Status bar (row 2, below nav buttons)
        status_y = self._nav_rect.y + 36
        x = 40

        # Transport state
        if self.p6 and self.p6.state.playing:
            t_surf = f.render("PLAY", True, theme.GREEN)
        elif self.recorder.is_recording:
            t_surf = f.render("REC", True, theme.RED)
        else:
            t_surf = f.render("STOP", True, theme.TEXT_DIM)
        self.screen.blit(t_surf, (x, status_y))
        x += t_surf.get_width() + 12

        # BPM from P-6
        if self.p6:
            bpm = self.p6.state.bpm
            bpm_surf = f.render(f"{bpm:.0f}bpm", True, theme.TEXT_DIM)
            self.screen.blit(bpm_surf, (x, status_y))
            x += bpm_surf.get_width() + 12

        # Auto-record indicator
        if self.auto_record:
            surf = f.render("AUTO", True, theme.RED)
            self.screen.blit(surf, (x, status_y))
            x += surf.get_width() + 12

        # Layer indicator
        if self.router:
            surf = f.render(self.router.layer.value.upper(), True, theme.ACCENT)
            self.screen.blit(surf, (x, status_y))
            x += surf.get_width() + 12

        # Right side: connection status
        rx = theme.SCREEN_WIDTH - 8
        if self.p6 and self.p6.connected:
            surf = f.render("P-6", True, theme.GREEN)
        else:
            surf = f.render("NO P-6", True, theme.RED)
        rx -= surf.get_width()
        self.screen.blit(surf, (rx, status_y))

        if self.atom_sq and self.atom_sq.connected:
            surf = f.render("ATOM", True, theme.GREEN)
            rx -= surf.get_width() + 8
            self.screen.blit(surf, (rx, status_y))

    def _shutdown(self):
        print("Shutting down Compa...")
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

    app.run()


if __name__ == "__main__":
    main()
