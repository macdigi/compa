"""Compa App — main entry point.

Turns the Pi + ATOM SQ + touchscreen into a control surface,
sample manager, and recorder for the Roland AIRA Compact P-6.
"""

import os
import signal
import sys
import time
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
from ui.screens.p6_settings_screen import P6SettingsScreen
from ui.screens.kit_builder_screen import KitBuilderScreen
from ui.screens.transfer_screen import TransferScreen
from ui.screens.device_workspace import DeviceWorkspaceScreen
from engine.atom_sq import AtomSQ, find_atom_sq_ports
from engine.p6_midi import P6Midi, find_p6_ports
from engine.midi_router import MidiRouter, Layer
from engine.p6_recorder import P6Recorder
from engine.device_profiles import DeviceManager
from engine.audio_router import AudioRoute, find_device_index
from engine.midi_lfo import MidiLFO
from engine.midi_mapper import MidiMapper
from engine.twister_genius import TwisterGenius
from engine.spectra_mapper import SpectraMapper
from engine.usb_storage import AkaiStorageManager
from ui.splash import run_splash
from ui.wizard import run_wizard

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


def get_device_config(config: dict, key: str, device_short_name: str = "") -> str:
    """Get a config value with device-specific override support.

    Checks ``{DEVICE}_{KEY}`` first (e.g. ``SP404_REC_THRESHOLD``),
    then falls back to the global ``{KEY}``.
    """
    if device_short_name:
        # Normalize: "SP-404" → "SP404", "P-6" → "P6"
        prefix = device_short_name.replace("-", "").replace(" ", "")
        device_key = f"{prefix}_{key}"
        if device_key in config:
            return config[device_key]
    return config.get(key, "")


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

        # Store fb blit function globally so splash/wizard can use it
        import builtins
        if self._fb_mode and self._fb_file:
            def _fb_blit(surface):
                try:
                    import numpy as np
                    arr = pygame.surfarray.pixels3d(surface)
                    arr = np.transpose(arr, (1, 0, 2))
                    r = arr[:, :, 0].astype(np.uint16)
                    g = arr[:, :, 1].astype(np.uint16)
                    b = arr[:, :, 2].astype(np.uint16)
                    rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                    self._fb_file.seek(0)
                    self._fb_file.write(rgb565.tobytes())
                    self._fb_file.flush()
                except Exception:
                    pass
            builtins._compa_fb_blit = _fb_blit
        else:
            builtins._compa_fb_blit = None

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

        # ── Device detection (multi-device) ──────────────────────────
        self.device_manager = DeviceManager()
        detected = self.device_manager.detect()
        if detected:
            connected = self.device_manager.connected
            if len(connected) > 1:
                names = ", ".join(connected.keys())
                print(f"Multi-device hub: {names} (focus: {self.device_manager.focus_key})", flush=True)
            else:
                print(f"Device detected: {detected.name}", flush=True)
        else:
            print("No USB device detected — running in offline mode", flush=True)

        # Load saved device color preferences
        for short_name in self.device_manager.connected:
            saved_color = self.config.get(f"COLOR_{short_name}")
            if saved_color:
                theme.set_device_color(short_name, saved_color)

        # Apply hardware theme for focused device
        if self.device_name != "---":
            theme.apply_theme_for_device(self.device_name)

        # ── Mouse mode (show cursor, skip touch-to-mouse conversion) ──
        self.mouse_mode = self.config.get("MOUSE_MODE", "0") == "1"
        if self.mouse_mode:
            pygame.mouse.set_visible(True)

        # ── MIDI devices (init before screens so state objects exist) ──
        self.atom_sq: AtomSQ | None = None
        self._midi_connections: dict[str, P6Midi] = {}  # short_name -> P6Midi
        self.router: MidiRouter | None = None

        # ── Audio routing (device-to-device bridge) ──────────────────
        self.audio_route: AudioRoute | None = None

        # ── Monitor output (which device your headphones are on) ─────
        self.monitor_output: str = ""  # Device short_name for headphone out
        self._monitor_route: AudioRoute | None = None

        # ── LFO automation engine ────────────────────────────────────
        self.lfo = MidiLFO()

        # ── MIDI controller mapper ───────────────────────────────────
        self.midi_mapper = MidiMapper()

        # ── Twister Genius (auto-detect + connect) ───────────────────
        self.twister = TwisterGenius()

        # ── Spectra Mapper (auto-detect + connect) ───────────────────
        self.spectra = SpectraMapper()

        # ── Live CC state (for workspace parameter tracking + HUD) ───
        # Per-bus dict of {cc: value} updated by incoming SP-404 MIDI
        self.live_cc: dict[int, dict[int, int]] = {i: {} for i in range(16)}
        # HUD notification queue: [(text, color, timestamp), ...]
        self._hud_messages: list[tuple[str, tuple, float]] = []

        # ── Akai USB storage (Computer Mode file transfer) ───────────
        self.akai_storage = AkaiStorageManager()

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
            "settings": P6SettingsScreen(self),
            "kit": KitBuilderScreen(self),
            "transfer": TransferScreen(self),
            "device_workspace": DeviceWorkspaceScreen(self),
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
                ("XFER",    "transfer"),
            ]
            font_name = "small"
        elif theme.SCREEN_WIDTH >= 400:
            # Medium screen: short labels
            nav_labels = [
                ("SES", "session"), ("CTL", "control"),
                ("PAT", "pattern"), ("REC", "record"),
                ("SMP", "sample"),  ("RAD", "radio"),
                ("XFR", "transfer"),
            ]
            font_name = "tiny"
        else:
            # Tiny screen: icons/minimal
            nav_labels = [
                ("S", "session"), ("C", "control"),
                ("P", "pattern"), ("R", "record"),
                ("F", "sample"),  ("~", "radio"),
                ("X", "transfer"),
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
        for sn, conn in self._midi_connections.items():
            focused = " [FOCUS]" if sn == self.device_manager.focus_key else ""
            print(f"  {sn}: MIDI {'connected' if conn.connected else 'failed'}{focused}")
        if not self._midi_connections:
            print("  No MIDI devices found")
        print(f"Recorder: {'ready' if self.recorder.available else 'no audio device'}")
        print(f"Auto-record: {'ON' if self.auto_record else 'OFF'}")

    def _init_midi(self):
        """Detect and connect ATOM SQ and ALL target devices."""
        # ATOM SQ
        try:
            midi_in, midi_out = find_atom_sq_ports()
            if midi_in is not None:
                self.atom_sq = AtomSQ(midi_in, midi_out)
                print("ATOM SQ connected")
        except Exception as e:
            print(f"ATOM SQ init failed: {e}")

        # Connect MIDI for ALL detected devices
        for short_name, profile in self.device_manager.connected.items():
            hint = profile.midi_hint
            if not hint:
                continue  # No MIDI for this device (e.g. generic USB audio)
            try:
                midi_in, midi_out = find_p6_ports(hint)
                if midi_in is not None or midi_out is not None:
                    conn = P6Midi(midi_in, midi_out, profile=profile)
                    conn.on_transport = self._on_p6_transport
                    self._midi_connections[short_name] = conn
                    print(f"{profile.name} MIDI connected")
            except Exception as e:
                print(f"{profile.name} MIDI init failed: {e}")

        # Router (needs both ATOM SQ and focused device)
        if self.atom_sq and self.p6:
            self.router = MidiRouter(self.atom_sq, self.p6)
            self.router.set_ui_button_callback(self._on_ui_button)
            self.router.on_transport = self._on_atomsq_transport
            print(f"MIDI router active: ATOM SQ → {self.device_name}")
        elif self.atom_sq:
            self.atom_sq.on_button = self._on_atomsq_button_fallback
            print("ATOM SQ connected but no device found — navigation only")

        # Wire SP-404 CC feedback for live parameter tracking
        sp404_midi = self._midi_connections.get("SP-404MKII")
        if sp404_midi:
            orig_cc = sp404_midi.on_cc
            def _on_sp404_cc(channel, cc, value, _orig=orig_cc):
                # Store in live state (channel = bus index)
                if 0 <= channel <= 15:
                    self.live_cc[channel][cc] = value
                if _orig:
                    _orig(channel, cc, value)
            sp404_midi.on_cc = _on_sp404_cc

        # Twister Genius — auto-detect and connect
        if self.twister.detect():
            if self.twister.connect():
                if sp404_midi:
                    self.twister.set_target(sp404_midi)
                self.twister.on_state_changed = self._on_twister_state
                self.twister.on_param_changed = self._on_twister_param
                self.twister.on_cc_sent = self._on_twister_cc_sent
                self.twister.start()
                print(f"Twister Genius: 16 effects mapped to SP-404")
            else:
                print("Twister Genius: detected but connect failed")
        else:
            print("Twister Genius: not detected")

        # Spectra Mapper — auto-detect and connect
        if self.spectra.detect():
            if self.spectra.connect():
                if sp404_midi:
                    self.spectra.set_target(sp404_midi)
                self.spectra.on_state_changed = self._on_spectra_state
                self.spectra.on_pad_hit = self._on_spectra_pad
                self.spectra.start()
                print(f"Spectra Mapper: 16 buttons mapped (bank {self.spectra.bank_name})")
            else:
                print("Spectra Mapper: detected but connect failed")
        else:
            print("Spectra Mapper: not detected")

    # ── Evdev touch input (for SPI LCD / FB mode) ─────────────────────

    def _start_evdev_touch(self):
        """Start threads for touch, mouse, and keyboard in FB mode."""
        import threading
        self._evdev_thread = threading.Thread(target=self._evdev_loop, daemon=True)
        self._evdev_thread.start()
        self._mice_thread = threading.Thread(target=self._mice_loop, daemon=True)
        self._mice_thread.start()
        self._start_keyboard_reader()

    def _start_keyboard_reader(self):
        """Start keyboard evdev reader for FB mode."""
        import threading
        t = threading.Thread(target=self._keyboard_loop, daemon=True)
        t.start()

    def _keyboard_loop(self):
        """Read keyboard via evdev in FB mode."""
        try:
            import evdev
            import select as sel
        except ImportError:
            return

        # Find keyboard
        kbd = None
        for path in evdev.list_devices():
            d = evdev.InputDevice(path)
            if "keyboard" in d.name.lower() or "Keyboard" in d.name:
                kbd = d
                print(f"Evdev keyboard: {d.name} at {d.path}", flush=True)
                break

        if not kbd:
            print("No keyboard found", flush=True)
            return

        # Evdev keycode → pygame keycode mapping (common keys)
        KEYMAP = {
            1: pygame.K_ESCAPE, 28: pygame.K_RETURN, 14: pygame.K_BACKSPACE,
            15: pygame.K_TAB, 57: pygame.K_SPACE, 111: pygame.K_DELETE,
            103: pygame.K_UP, 108: pygame.K_DOWN, 105: pygame.K_LEFT, 106: pygame.K_RIGHT,
            59: pygame.K_F1, 60: pygame.K_F2, 61: pygame.K_F3, 62: pygame.K_F4,
            63: pygame.K_F5, 64: pygame.K_F6, 65: pygame.K_F7, 66: pygame.K_F8,
            67: pygame.K_F9, 68: pygame.K_F10, 87: pygame.K_F11, 88: pygame.K_F12,
            # Letters
            30: pygame.K_a, 48: pygame.K_b, 46: pygame.K_c, 32: pygame.K_d,
            18: pygame.K_e, 33: pygame.K_f, 34: pygame.K_g, 35: pygame.K_h,
            23: pygame.K_i, 36: pygame.K_j, 37: pygame.K_k, 38: pygame.K_l,
            50: pygame.K_m, 49: pygame.K_n, 24: pygame.K_o, 25: pygame.K_p,
            16: pygame.K_q, 19: pygame.K_r, 31: pygame.K_s, 20: pygame.K_t,
            22: pygame.K_u, 47: pygame.K_v, 17: pygame.K_w, 45: pygame.K_x,
            21: pygame.K_y, 44: pygame.K_z,
            # Numbers
            2: pygame.K_1, 3: pygame.K_2, 4: pygame.K_3, 5: pygame.K_4,
            6: pygame.K_5, 7: pygame.K_6, 8: pygame.K_7, 9: pygame.K_8,
            10: pygame.K_9, 11: pygame.K_0,
            # Punctuation
            12: pygame.K_MINUS, 13: pygame.K_EQUALS, 26: pygame.K_LEFTBRACKET,
            27: pygame.K_RIGHTBRACKET, 39: pygame.K_SEMICOLON, 40: pygame.K_QUOTE,
            41: pygame.K_BACKQUOTE, 43: pygame.K_BACKSLASH, 51: pygame.K_COMMA,
            52: pygame.K_PERIOD, 53: pygame.K_SLASH,
        }

        # Evdev keycode → unicode character (lowercase)
        CHARMAP = {
            30: 'a', 48: 'b', 46: 'c', 32: 'd', 18: 'e', 33: 'f', 34: 'g',
            35: 'h', 23: 'i', 36: 'j', 37: 'k', 38: 'l', 50: 'm', 49: 'n',
            24: 'o', 25: 'p', 16: 'q', 19: 'r', 31: 's', 20: 't', 22: 'u',
            47: 'v', 17: 'w', 45: 'x', 21: 'y', 44: 'z',
            2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7',
            9: '8', 10: '9', 11: '0', 57: ' ', 12: '-', 13: '=',
            51: ',', 52: '.', 53: '/', 39: ';', 40: "'", 26: '[', 27: ']',
            43: '\\', 41: '`',
        }
        SHIFT_CHARMAP = {
            2: '!', 3: '@', 4: '#', 5: '$', 6: '%', 7: '^', 8: '&',
            9: '*', 10: '(', 11: ')', 12: '_', 13: '+',
            51: '<', 52: '>', 53: '?', 39: ':', 40: '"', 26: '{', 27: '}',
            43: '|', 41: '~',
        }

        shift = False

        while True:
            try:
                r, _, _ = sel.select([kbd], [], [], 0.05)
                if not r:
                    continue
                for event in kbd.read():
                    if event.type != 1:  # EV_KEY only
                        continue
                    # Track shift
                    if event.code in (42, 54):  # LEFT/RIGHT SHIFT
                        shift = (event.value != 0)
                        continue

                    pg_key = KEYMAP.get(event.code)
                    if not pg_key:
                        continue

                    if event.value == 1:  # Key down
                        if shift and event.code in SHIFT_CHARMAP:
                            uni = SHIFT_CHARMAP[event.code]
                        elif shift and event.code in CHARMAP and CHARMAP[event.code].isalpha():
                            uni = CHARMAP[event.code].upper()
                        else:
                            uni = CHARMAP.get(event.code, '')

                        pygame.event.post(pygame.event.Event(
                            pygame.KEYDOWN, key=pg_key, unicode=uni, mod=0))
                    elif event.value == 0:  # Key up
                        pygame.event.post(pygame.event.Event(
                            pygame.KEYUP, key=pg_key, mod=0))
            except Exception:
                pass

    def _mice_loop(self):
        """Read USB mouse via /dev/input/mice (3-byte PS/2 protocol)."""
        import struct, select, builtins
        sw, sh = self._display_w, self._display_h
        mx, my = sw // 2, sh // 2
        btn_down = False

        try:
            fd = os.open("/dev/input/mice", os.O_RDONLY | os.O_NONBLOCK)
        except Exception as e:
            print(f"Cannot open /dev/input/mice: {e}", flush=True)
            return

        print("Mouse reader started (/dev/input/mice)", flush=True)

        while True:
            try:
                r, _, _ = select.select([fd], [], [], 0.02)
                if not r:
                    continue
                data = os.read(fd, 3)
                if len(data) != 3:
                    continue

                btn, dx, dy = struct.unpack("bbb", data)
                left = bool(btn & 1)

                mx = max(0, min(sw - 1, mx + dx))
                my = max(0, min(sh - 1, my - dy))  # Y is inverted in PS/2

                builtins._compa_mouse_pos = (mx, my)

                # Post motion
                pygame.event.post(pygame.event.Event(
                    pygame.MOUSEMOTION, pos=(mx, my),
                    rel=(dx, -dy), buttons=(1 if left else 0, 0, 0)))

                # Button state changes
                if left and not btn_down:
                    btn_down = True
                    pygame.event.post(pygame.event.Event(
                        pygame.MOUSEBUTTONDOWN, pos=(mx, my), button=1))
                elif not left and btn_down:
                    btn_down = False
                    pygame.event.post(pygame.event.Event(
                        pygame.MOUSEBUTTONUP, pos=(mx, my), button=1))

                # Scroll wheel (bit 3 = wheel data present in 4th byte on some mice)
                # Basic 3-byte protocol doesn't have scroll, but btn byte bits 4-7 may indicate
            except Exception:
                pass

    def _evdev_loop(self):
        """Read touch AND mouse events via evdev (for FB/dummy SDL mode)."""
        try:
            import evdev
            import select
        except ImportError:
            print("evdev not available for input", flush=True)
            return

        # Find all input devices
        devices = []
        touch_dev = None
        mouse_dev = None
        for path in evdev.list_devices():
            d = evdev.InputDevice(path)
            caps = d.capabilities()
            if "touch" in d.name.lower() or "ads" in d.name.lower():
                touch_dev = d
                devices.append(d)
                print(f"Evdev touch: {d.name} at {d.path}", flush=True)
            elif 2 in caps and 1 in caps:  # EV_REL + EV_KEY = real mouse
                # Check it has BTN_LEFT (code 272)
                key_caps = caps.get(1, [])
                key_codes = []
                for item in key_caps:
                    if isinstance(item, (list, tuple)):
                        key_codes.append(item[0])
                    else:
                        key_codes.append(item)
                if 272 in key_codes:  # BTN_LEFT
                    mouse_dev = d
                    if d not in devices:
                        devices.append(d)
                    print(f"Evdev mouse: {d.name} at {d.path}", flush=True)

        if not devices:
            print("No input devices found", flush=True)
            return

        dev = touch_dev  # for backward compat below

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
        mouse_x = mouse_y = 0
        raw_x = raw_y = 0
        touching = False
        mouse_btn = False
        sw, sh = self._display_w, self._display_h
        mouse_x, mouse_y = sw // 2, sh // 2  # Start cursor in center

        while True:
            try:
                r, _, _ = select.select(devices, [], [], 0.05)
                if not r:
                    continue
                for ready_dev in r:
                    for event in ready_dev.read():
                        # ── Touch events (ABS) ────────────────────
                        if ready_dev == touch_dev and not self.mouse_mode:
                            if event.type == 3:  # EV_ABS
                                if event.code == 0:
                                    raw_x = event.value
                                elif event.code == 1:
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
                            elif event.type == 1 and event.code == 330:
                                if event.value == 1 and not touching:
                                    touching = True
                                    pygame.event.post(pygame.event.Event(
                                        pygame.MOUSEBUTTONDOWN,
                                        pos=(touch_x, touch_y), button=1))
                                elif event.value == 0 and touching:
                                    touching = False
                                    pygame.event.post(pygame.event.Event(
                                        pygame.MOUSEBUTTONUP,
                                        pos=(touch_x, touch_y), button=1))
                            elif event.type == 0 and event.code == 0 and touching:
                                pygame.event.post(pygame.event.Event(
                                    pygame.MOUSEMOTION,
                                    pos=(touch_x, touch_y),
                                    rel=(0, 0), buttons=(1, 0, 0)))

                        # ── Mouse events (REL) ────────────────────
                        elif ready_dev == mouse_dev:
                            if event.type == 2:  # EV_REL
                                if event.code == 0:  # REL_X
                                    mouse_x = max(0, min(sw - 1, mouse_x + event.value))
                                elif event.code == 1:  # REL_Y
                                    mouse_y = max(0, min(sh - 1, mouse_y + event.value))
                                elif event.code == 8:  # REL_WHEEL
                                    btn = 4 if event.value > 0 else 5
                                    pygame.event.post(pygame.event.Event(
                                        pygame.MOUSEBUTTONDOWN,
                                        pos=(mouse_x, mouse_y), button=btn))
                                pygame.event.post(pygame.event.Event(
                                    pygame.MOUSEMOTION,
                                    pos=(mouse_x, mouse_y),
                                    rel=(0, 0),
                                    buttons=(1 if mouse_btn else 0, 0, 0)))
                                builtins._compa_mouse_pos = (mouse_x, mouse_y)
                            elif event.type == 1:  # EV_KEY (buttons)
                                if event.code == 272:  # BTN_LEFT
                                    if event.value == 1:
                                        mouse_btn = True
                                        pygame.event.post(pygame.event.Event(
                                            pygame.MOUSEBUTTONDOWN,
                                            pos=(mouse_x, mouse_y), button=1))
                                    elif event.value == 0:
                                        mouse_btn = False
                                        pygame.event.post(pygame.event.Event(
                                            pygame.MOUSEBUTTONUP,
                                            pos=(mouse_x, mouse_y), button=1))
                                elif event.code == 273:  # BTN_RIGHT
                                    if event.value == 1:
                                        pygame.event.post(pygame.event.Event(
                                            pygame.MOUSEBUTTONDOWN,
                                            pos=(mouse_x, mouse_y), button=3))
            except Exception:
                pass

    # ── HUD + Twister callbacks ────────────────────────────────────

    def push_hud(self, text: str, color: tuple = None):
        """Push a notification to the HUD overlay."""
        import time
        if color is None:
            color = theme.ACCENT
        self._hud_messages.append((text, color, time.monotonic()))
        # Keep max 3
        if len(self._hud_messages) > 3:
            self._hud_messages.pop(0)

    def _on_twister_state(self):
        """Called when Twister loads/kills an effect."""
        tw = self.twister
        bus = tw.active_bus + 1
        active_knob = tw._bus_fx_state.get(tw.active_bus)
        if active_knob is not None:
            slot = tw.slots[active_knob]
            self.push_hud(f"Bus {bus}: {slot.name}", self._twister_slot_color(slot))
        else:
            self.push_hud(f"Bus {bus}: FX OFF", theme.TEXT_DIM)

    def _on_twister_cc_sent(self, channel: int, cc: int, value: int):
        """Track CCs we send to the SP-404 (it doesn't echo CC83/CC19 back)."""
        if 0 <= channel <= 15:
            self.live_cc[channel][cc] = value

    def _on_twister_param(self, knob: int, value: int):
        """Called when Twister adjusts a parameter. Throttled to avoid spam."""
        import time
        now = time.monotonic()
        if now - getattr(self, "_last_param_hud", 0) < 0.08:
            return  # Throttle: max ~12 HUD updates/sec
        self._last_param_hud = now

        if knob >= len(self.twister.slots):
            return
        slot = self.twister.slots[knob]
        bus = self.twister.active_bus
        live = self.live_cc.get(bus, {})
        cc_names = {16: "Ctrl1", 17: "Ctrl2", 18: "Ctrl3",
                    80: "Ctrl4", 81: "Ctrl5", 82: "Ctrl6"}
        label = slot.name
        for cc_num, name in cc_names.items():
            if live.get(cc_num) == value:
                label = f"{slot.name} {name}"
                break
        # Replace last param message instead of stacking
        msgs = self._hud_messages
        if msgs and msgs[-1][0].startswith(slot.name):
            msgs[-1] = (f"{label}: {value}", self._twister_slot_color(slot), now)
        else:
            self.push_hud(f"{label}: {value}", self._twister_slot_color(slot))

    def _on_spectra_state(self):
        """Called when Spectra changes bank, mute, or hold state."""
        sp = self.spectra
        if sp._hold_active:
            self.push_hud("HOLD ON", theme.YELLOW)
        else:
            self.push_hud(f"Bank {sp.bank_name}", theme.ACCENT)

    def _on_spectra_pad(self, note: int, velocity: int, bank_name: str):
        """Called when Spectra triggers a pad."""
        pad_num = note - 35  # notes 36-51 → pads 1-16
        self.push_hud(f"{bank_name}:{pad_num}", theme.TEXT_BRIGHT)

    @staticmethod
    def _twister_slot_color(slot):
        """Map Twister color wheel value to RGB for HUD display."""
        from engine.twister_genius import (COLOR_RED, COLOR_ORANGE, COLOR_YELLOW,
            COLOR_GREEN, COLOR_CYAN, COLOR_BLUE, COLOR_PURPLE, COLOR_PINK)
        mapping = {
            COLOR_RED: (220, 50, 50), COLOR_ORANGE: (235, 140, 30),
            COLOR_YELLOW: (230, 210, 40), COLOR_GREEN: (50, 195, 70),
            COLOR_CYAN: (50, 200, 200), COLOR_BLUE: (70, 140, 230),
            COLOR_PURPLE: (160, 80, 200), COLOR_PINK: (220, 80, 160),
        }
        return mapping.get(slot.color, theme.ACCENT)

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

    @property
    def p6(self) -> "P6Midi | None":
        """MIDI connection to the focused device.

        This property replaces the old ``self.p6`` attribute.  All 49+
        references across screens continue to work because they read
        ``self.app.p6`` which now resolves to the focused device.
        """
        key = self.device_manager.focus_key
        return self._midi_connections.get(key) if key else None

    @property
    def device(self):
        """Active (focused) DeviceProfile (or None)."""
        return self.device_manager.active

    @property
    def device_name(self) -> str:
        """Short display name for the focused device."""
        d = self.device_manager.active
        return d.short_name if d else "---"

    def switch_focus(self, short_name: str) -> bool:
        """Switch which device the UI controls.

        Updates DeviceManager focus, rebuilds MIDI router, switches
        audio monitoring, and retargets Twister to the new device.
        """
        if not self.device_manager.set_focus(short_name):
            return False

        print(f"Focus → {short_name}", flush=True)

        # Auto-apply hardware theme
        theme.apply_theme_for_device(short_name)

        # Switch audio monitoring to the focused device
        dev = self.device_manager.active
        if dev and dev.audio_hint:
            self.recorder.switch_device(dev.audio_hint)
            # Clear playback device cache so next play() retargets to new focus
            if hasattr(self.recorder, 'clear_playback_cache'):
                self.recorder.clear_playback_cache()
            # Retarget radio output to follow focus
            radio_screen = self.screens.get("radio")
            if radio_screen and hasattr(radio_screen, '_radio'):
                if hasattr(radio_screen._radio, 'retarget'):
                    radio_screen._radio.retarget(dev.audio_hint)
            if not self.recorder._monitoring:
                self.recorder.start_monitoring()

        # Retarget Twister to the focused device's MIDI
        focused_midi = self._midi_connections.get(short_name)
        if self.twister.connected and focused_midi:
            self.twister.set_target(focused_midi)
            self.twister._rebuild_pages()
            print(f"Twister → {short_name}", flush=True)

        # Rewire MIDI router to focused device
        if self.atom_sq and self.p6:
            self.router = MidiRouter(self.atom_sq, self.p6)
            self.router.set_ui_button_callback(self._on_ui_button)
            self.router.on_transport = self._on_atomsq_transport

        # Notify current screen
        screen = self.current_screen
        if hasattr(screen, "on_focus_changed"):
            screen.on_focus_changed()

        return True

    def cycle_focus(self):
        """Cycle to the next connected device (for nav bar tap)."""
        new_key = self.device_manager.cycle_focus()
        if new_key:
            print(f"Focus → {new_key}", flush=True)
            if self.atom_sq and self.p6:
                self.router = MidiRouter(self.atom_sq, self.p6)
                self.router.set_ui_button_callback(self._on_ui_button)
                self.router.on_transport = self._on_atomsq_transport
            screen = self.current_screen
            if hasattr(screen, "on_focus_changed"):
                screen.on_focus_changed()

    # ── Audio routing ────────────────────────────────────────────────

    def start_audio_route(self, source_key: str, dest_key: str) -> bool:
        """Start audio routing from one device to another.

        Args:
            source_key: short_name of source device (e.g. "SP-404")
            dest_key: short_name of destination device (e.g. "P-6")

        Returns True if route started successfully.
        """
        if self.audio_route and self.audio_route.is_active:
            self.audio_route.stop()

        connected = self.device_manager.connected
        src_profile = connected.get(source_key)
        dst_profile = connected.get(dest_key)
        if not src_profile or not dst_profile:
            return False

        src_idx = find_device_index(src_profile.audio_hint)
        dst_idx = find_device_index(dst_profile.audio_hint)
        if src_idx is None or dst_idx is None:
            return False

        src_rate = src_profile.supported_sample_rates[0] if src_profile.supported_sample_rates else 44100
        dst_rate = dst_profile.supported_sample_rates[0] if dst_profile.supported_sample_rates else 44100

        self.audio_route = AudioRoute(src_idx, src_rate, dst_idx, dst_rate)
        return self.audio_route.start()

    def stop_audio_route(self):
        """Stop any active audio route."""
        if self.audio_route:
            self.audio_route.stop()
            self.audio_route = None

    # ── Monitor output routing ───────────────────────────────────

    def set_monitor_output(self, device_short_name: str):
        """Set which device your headphones are on.

        When you monitor a different device, its audio auto-routes
        through to this output device.
        """
        self.monitor_output = device_short_name
        print(f"Monitor output → {device_short_name}", flush=True)

    def route_monitor(self, source_key: str):
        """Auto-route monitored audio to the headphone output device.

        Uses the recorder's built-in monitor output — no second input
        stream needed. The recorder's audio callback forwards data
        directly to the output device.
        """
        # Stop existing monitor output
        self.recorder.stop_monitor_output()

        if not self.monitor_output:
            return  # No monitor output set

        if source_key == self.monitor_output:
            return  # Already hearing it directly through headphones

        connected = self.device_manager.connected
        dst_profile = connected.get(self.monitor_output)
        if not dst_profile:
            return

        dst_idx = find_device_index(dst_profile.audio_hint)
        if dst_idx is None:
            return

        # Use the device's actual default sample rate (not profile's first entry)
        try:
            import sounddevice as sd
            dev_info = sd.query_devices(dst_idx)
            dst_rate = int(dev_info.get("default_samplerate", 48000))
        except Exception:
            dst_rate = dst_profile.supported_sample_rates[-1] if dst_profile.supported_sample_rates else 48000

        if self.recorder.start_monitor_output(dst_idx, dst_rate):
            print(f"Monitor route: {source_key} → {self.monitor_output}", flush=True)
        else:
            print(f"Monitor route failed", flush=True)

    # ── MIDI clock relay ─────────────────────────────────────────────

    def start_clock_relay(self, source_key: str, dest_key: str) -> bool:
        """Relay MIDI clock from one device to another.

        The source device's clock ticks (0xF8) and transport messages
        (start/stop/continue) are forwarded to the destination device.
        """
        src = self._midi_connections.get(source_key)
        dst = self._midi_connections.get(dest_key)
        if not src or not dst:
            return False

        # Wire the source's clock tick to forward to destination
        def _relay_tick():
            if dst._out:
                dst._out.send_message([0xF8])

        def _relay_transport(state):
            if state == "start" and dst._out:
                dst._out.send_message([0xFA])
            elif state == "stop" and dst._out:
                dst._out.send_message([0xFC])
            elif state == "continue" and dst._out:
                dst._out.send_message([0xFB])

        # Store original callbacks
        self._clock_relay_source = source_key
        self._clock_relay_dest = dest_key
        self._clock_relay_orig_tick = src.on_clock_tick
        self._clock_relay_orig_transport = src.on_transport

        # Chain callbacks: relay + original
        orig_tick = src.on_clock_tick
        orig_transport = src.on_transport

        def _combined_tick():
            _relay_tick()
            if orig_tick:
                orig_tick()

        def _combined_transport(state):
            _relay_transport(state)
            if orig_transport:
                orig_transport(state)

        src.on_clock_tick = _combined_tick
        src.on_transport = _combined_transport
        self._clock_relay_active = True
        print(f"Clock relay: {source_key} → {dest_key}", flush=True)
        return True

    def stop_clock_relay(self):
        """Stop the MIDI clock relay and restore original callbacks."""
        if not getattr(self, "_clock_relay_active", False):
            return
        src = self._midi_connections.get(getattr(self, "_clock_relay_source", ""))
        if src:
            src.on_clock_tick = getattr(self, "_clock_relay_orig_tick", None)
            src.on_transport = getattr(self, "_clock_relay_orig_transport", None)
        self._clock_relay_active = False
        print("Clock relay stopped", flush=True)

    @property
    def clock_relay_active(self) -> bool:
        return getattr(self, "_clock_relay_active", False)

    # ── Hot-plug detection ───────────────────────────────────────────

    def _check_hotplug(self):
        """Periodic USB rescan to detect newly connected/disconnected devices."""
        from engine.device_detect import scan_usb_devices

        try:
            usb_devices = scan_usb_devices()
        except Exception:
            return

        current_keys = set(self.device_manager.connected.keys())
        found_keys = set()

        # Check which registered profiles match current USB bus
        for profile in self.device_manager.profiles.values():
            for dev in usb_devices:
                if (dev["vendor"] == profile.usb_vendor
                        and dev["product"] in profile.usb_products):
                    found_keys.add(profile.short_name)
                    break

        # Detect new devices
        new_devices = found_keys - current_keys
        for short_name in new_devices:
            profile = self.device_manager.get_profile(short_name)
            if not profile:
                continue
            self.device_manager._connected[short_name] = profile
            if self.device_manager._focus_key is None:
                self.device_manager.set_focus(short_name)
            # Try to open MIDI
            if profile.midi_hint:
                try:
                    midi_in, midi_out = find_p6_ports(profile.midi_hint)
                    if midi_in is not None or midi_out is not None:
                        conn = P6Midi(midi_in, midi_out, profile=profile)
                        conn.on_transport = self._on_p6_transport
                        self._midi_connections[short_name] = conn
                        print(f"Hot-plug: {profile.name} connected", flush=True)
                except Exception as e:
                    print(f"Hot-plug MIDI failed for {short_name}: {e}", flush=True)

        # Detect removed devices
        removed_devices = current_keys - found_keys
        for short_name in removed_devices:
            conn = self._midi_connections.pop(short_name, None)
            if conn:
                try:
                    conn.shutdown()
                except Exception:
                    pass
            self.device_manager._connected.pop(short_name, None)
            print(f"Hot-plug: {short_name} disconnected", flush=True)

            # If focused device was removed, switch to another
            if short_name == self.device_manager.focus_key:
                remaining = list(self.device_manager._connected.keys())
                if remaining:
                    self.device_manager.set_focus(remaining[0])
                    screen = self.current_screen
                    if hasattr(screen, "on_focus_changed"):
                        screen.on_focus_changed()
                    print(f"Focus auto-switched to {remaining[0]}", flush=True)
                else:
                    self.device_manager._focus_key = None
                    self.device_manager._active_device = None

    def switch_screen(self, name: str, context: dict | None = None):
        """Switch to a screen, optionally passing context data.

        Context is stored in self._screen_context and consumed by
        the target screen's on_enter(). Screens that don't use context
        ignore it — fully backward compatible.
        """
        if name in self.screens and name != self.current_screen_name:
            old_screen = self.screens.get(self.current_screen_name)
            if old_screen and hasattr(old_screen, "on_exit"):
                old_screen.on_exit()
            self._screen_context = context or {}
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
            # Skip in FB mode (evdev handles touch) and when mouse mode is on
            if not self.mouse_mode and not self._fb_mode:
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
            elif event.type in (pygame.FINGERDOWN, pygame.FINGERUP, pygame.FINGERMOTION):
                continue  # mouse_mode on: ignore touch events entirely

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
                    pygame.K_F7: "help", pygame.K_F8: "settings",
                    pygame.K_F9: "kit",
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
                elif event.key == pygame.K_m:
                    # Toggle mouse mode
                    self.mouse_mode = not self.mouse_mode
                    pygame.mouse.set_visible(self.mouse_mode)
                    save_config_key("MOUSE_MODE", "1" if self.mouse_mode else "0")
                    print(f"Mouse mode: {'ON' if self.mouse_mode else 'OFF'}", flush=True)
                    continue

            # Settings button (replaces help button)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                settings_rect = pygame.Rect(4, self._nav_rect.y + 4, 26, 26)
                if settings_rect.collidepoint(event.pos):
                    self.switch_screen("settings")
                    continue

                # Device tap-to-switch (multi-device nav bar indicator)
                if hasattr(self, "_device_tap_rects"):
                    for tap_rect, short_name in self._device_tap_rects:
                        if tap_rect.collidepoint(event.pos):
                            self.switch_focus(short_name)
                            break

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
        # Hot-plug: periodic USB rescan (every 5 seconds)
        now = time.monotonic()
        if now - getattr(self, "_last_usb_scan", 0) > 5.0:
            self._last_usb_scan = now
            self._check_hotplug()
            # Also scan for Akai USB storage (Computer Mode)
            self.akai_storage.scan_and_mount()

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

        # Draw software cursor in FB + mouse mode
        if self._fb_mode and self.mouse_mode:
            # Get cursor position from evdev thread
            if self._evdev_thread:
                # Draw a small orange crosshair cursor
                mx, my = pygame.mouse.get_pos()
                # Mouse pos won't work in dummy mode, use a shared var
                pass
            # Draw cursor at last known mouse position
            import builtins
            mp = getattr(builtins, '_compa_mouse_pos', None)
            if mp:
                cx, cy = mp
                pygame.draw.line(self.screen, theme.ACCENT, (cx - 8, cy), (cx + 8, cy), 2)
                pygame.draw.line(self.screen, theme.ACCENT, (cx, cy - 8), (cx, cy + 8), 2)
                pygame.draw.circle(self.screen, theme.ACCENT, (cx, cy), 3)

        # SPI LCD: copy pygame surface to framebuffer
        import builtins
        fb_blit = getattr(builtins, '_compa_fb_blit', None)
        if fb_blit:
            fb_blit(self.screen)

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

        # ── Settings button (replaces help) ─────────────────────────
        settings_rect = pygame.Rect(4, self._nav_rect.y + 4, 26, 26)
        settings_active = self.current_screen_name == "settings"
        if settings_active:
            pygame.draw.rect(self.screen, theme.ACCENT, settings_rect, border_radius=13)
            surf = f_tiny.render("SET", True, theme.BG)
        else:
            pygame.draw.rect(self.screen, theme.BORDER, settings_rect, 1, border_radius=13)
            surf = f_tiny.render("SET", True, theme.TEXT_DIM)
        self.screen.blit(surf, surf.get_rect(center=settings_rect.center))

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

        # Mouse mode badge
        if self.mouse_mode:
            badge = pygame.Rect(x, status_y - 1, 48, 16)
            pygame.draw.rect(self.screen, theme.BLUE, badge, border_radius=3)
            surf = f_tiny.render("MOUSE", True, theme.TEXT_BRIGHT)
            self.screen.blit(surf, surf.get_rect(center=badge.center))
            x += 56

        # Right side: multi-device connection status
        # Show ALL connected devices; focused one is bright, others dim.
        # Store rects for tap-to-switch handling.
        rx = theme.SCREEN_WIDTH - 10
        self._device_tap_rects: list[tuple[pygame.Rect, str]] = []
        connected = self.device_manager.connected
        focus_key = self.device_manager.focus_key

        for short_name in reversed(list(connected.keys())):
            midi_conn = self._midi_connections.get(short_name)
            is_focused = (short_name == focus_key)
            is_connected = midi_conn and midi_conn.connected

            if is_focused and is_connected:
                color = theme.GREEN
            elif is_focused:
                color = theme.RED
            elif is_connected:
                color = theme.TEXT_DIM
            else:
                color = (80, 40, 40)

            surf = f_tiny.render(short_name, True, color)
            rx -= surf.get_width()
            self.screen.blit(surf, (rx, status_y))

            # Connection dot
            dot_x = rx - 8
            if is_connected:
                pygame.draw.circle(self.screen, color, (dot_x, status_y + 7), 3)
            else:
                pygame.draw.circle(self.screen, color, (dot_x, status_y + 7), 3, 1)

            # Focus bracket
            if is_focused and len(connected) > 1:
                bracket_rect = pygame.Rect(rx - 13, status_y - 2, surf.get_width() + 16, 16)
                pygame.draw.rect(self.screen, color, bracket_rect, 1, border_radius=3)

            # Store tap rect for event handling
            tap_rect = pygame.Rect(dot_x - 4, status_y - 4, surf.get_width() + 20, 20)
            self._device_tap_rects.append((tap_rect, short_name))

            rx = dot_x - 14  # gap between devices

    def _shutdown(self):
        print("Shutting down Compa...")
        self.recorder.stop_monitor_output()
        if self.audio_route and self.audio_route.is_active:
            self.audio_route.stop()
        # Don't unmount Akai storage on shutdown — leave drives mounted
        # so they're available immediately on next boot
        if hasattr(self.screens.get("radio", None), '_radio'):
            self.screens["radio"]._radio.shutdown()
        self.recorder.shutdown()
        if self.router:
            pass  # Router doesn't need shutdown
        if self.atom_sq:
            self.atom_sq.shutdown()
        # Shut down ALL MIDI connections
        for sn, conn in self._midi_connections.items():
            try:
                conn.shutdown()
            except Exception:
                pass
        self._midi_connections.clear()
        pygame.quit()


SETUP_FLAG = "/home/pi/compa/.setup_complete"


def main():
    app = P6App()

    def _sigterm(signum, frame):
        app.running = False
    signal.signal(signal.SIGTERM, _sigterm)

    # First-time setup wizard (only if setup hasn't been completed)
    if not os.path.exists(SETUP_FLAG):
        run_wizard(app.screen, app.clock, app)

    # Animated splash screen (skip if splash disabled in config)
    if app.config.get("SKIP_SPLASH", "0") != "1":
        run_splash(app.screen, app.clock)

    app.run()


if __name__ == "__main__":
    main()
