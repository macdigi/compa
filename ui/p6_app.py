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
from ui.screens.file_browser_screen import FileBrowserScreen
from ui.screens.device_workspace import DeviceWorkspaceScreen
from ui.screens.io_settings_screen import IOSettingsScreen
from ui.screens.controller_screen import ControllerScreen
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
from engine.push2 import Push2, find_push2_ports
from engine.controller_actions import dispatch as _dispatch_action
from engine.master_clock import MasterClock
try:
    from engine.push2_display import Push2Display
    from ui.push2_render import Push2Renderer
except Exception as _push2_disp_err:
    Push2Display = None
    Push2Renderer = None
from engine.compa_link import CompaServer, CompaBrowser
from engine.updater import Updater
from engine.usb_storage import AkaiStorageManager
from engine.network_manager import WifiManager, BluetoothManager
from engine.chromatic_keyboard import ChromaticKeyboard
from engine.controller_mapper import ControllerMapper
from ui.video_recorder import VideoRecorder, DemoScheduler, build_demo_sequence
from engine.p6_librarian import P6Librarian
from engine.sp404_librarian import SP404Librarian
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
        self.push2: Push2 | None = None
        self.push2_display = None
        self.push2_renderer = None
        # Which 8-slot window of Twister's flattened slots the Push 2
        # is currently showing. Wraps 0..push2_page_count()-1.
        self.push2_page: int = 0
        # Pad-bank page. 0 = A-D, 1 = E-H, 2 = I-J (SP-404 MK2 has 10 banks).
        self.push2_pad_page: int = 0
        # Control-mode pad layout. 0 = SP-style 2-row strips (default),
        # 1 = 4×4 quadrants. Cycled by the Push 2 Layout button. P-6
        # has its own variants (row-per-bank vs. quadrant); index meaning
        # is per-device — see _push2_control_layout_count().
        self.push2_control_layout: int = 0
        # Push 2 surface mode — tracks the focused device-workspace tab
        # so the Push 2 surface re-roles (control / keys / sequence /
        # pattern / etc.) in lockstep with the Compa touchscreen.
        self.push2_mode: str = "control"
        # pad_idx → MIDI note for actively-held keys-mode notes, so
        # we can convert release events back to the right note number.
        self._push2_keys_active: dict[int, int] = {}
        # Push 2 pad indices currently flashed because a note-on
        # arrived from the focused device. Cleared on the matching
        # note-off (mirrors the device's own pad-held lighting).
        self._push2_active_device_pads: set[int] = set()
        # Pending screenshot schedule — None when nothing pending.
        # Fires from `_maybe_fire_screenshot` during _update once the
        # countdown elapses. See `schedule_screenshot`.
        self._scheduled_screenshot: dict | None = None
        # First step displayed in pattern mode (0 = steps 1-8, 8 = 9-16
        # for 16-step patterns). Page-left/right cycles this in mode.
        self.push2_pattern_step_offset: int = 0
        # Device the pattern sequencer is currently configured for.
        # When the user switches device focus while in pattern mode we
        # reset the sequencer so per-device patterns don't bleed.
        self._push2_pattern_device: str = ""
        # Compa master clock — drives the pattern-mode sequencer with
        # a stable BPM regardless of device clock. Tempo encoder
        # (Push 2 CC 14) nudges this directly.
        self.master_clock = MasterClock(bpm=120.0)
        # (name, monotonic_ts) for the most recent Tempo / Master /
        # Swing encoder turn — the Push 2 renderer pops up the current
        # value for ~1.5s after each event.
        self._push2_last_special_encoder: tuple | None = None
        # Per-bus pre-mute volumes so Mute toggles restore the prior
        # level instead of jumping back to a fixed 100.
        self._sp_pre_mute_vol: dict[int, int] = {}
        # Pattern-launch page (Push 2's 8 launch buttons → patterns
        # page*8..page*8+7). D-pad left/right cycles in non-keys modes.
        self.push2_launch_page: int = 0
        # Combined-pattern-mode launch page — Push 2's top 2 rows show
        # 16 patterns at a time; this is the index in 16-pattern groups.
        self.push2_pattern_launch_page: int = 0
        # Per-(device, pattern) step grids. PiSequencer only holds the
        # currently-loaded pattern; we snapshot here on pattern launch
        # so each pattern keeps its own steps when the user switches.
        # Loaded from disk on startup, saved on shutdown.
        try:
            from engine.compa_step_persistence import load as _load_grids
            self._compa_step_grids = _load_grids(self._step_grids_path())
        except Exception:
            self._compa_step_grids = {}
        # Most recent num_steps value before the last Double Loop
        # press, so Undo can revert it. None = nothing to undo.
        self._push2_last_num_steps: int | None = None
        # Top-of-window pad offset for the step-sequencer area in
        # pattern mode. Scrolls the 6 visible rows over the 8-pad
        # SP-404 sequencer or a future larger-pad config.
        self.push2_pattern_pad_offset: int = 0
        # First bank visible in the top row of pattern-mode (used for
        # SP-404'"'"'s 10 banks — 8 visible at a time, Shift+D-pad pages).
        self.push2_pattern_bank_offset: int = 0
        # Tracks Push 2 Shift-button held state so other buttons can
        # check for "Shift+X" combos (e.g. Shift+D-pad → bank page).
        self._push2_shift_held: bool = False
        # Base MIDI note for the bottom-left pad in Keys mode. Octave
        # Up / Octave Down shift this by 12 in keys mode.
        self.push2_keys_base_note: int = 36
        # Keys-mode scale (index into engine.push2.SCALES) and root
        # pitch class (0=C..11=B). D-pad up/down cycles scale,
        # left/right cycles root. Scale-locked: off-scale pads silent
        # when scale != chromatic.
        self.push2_keys_scale: int = 0   # 0 = chromatic
        self.push2_keys_root: int = 0    # 0 = C
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

        # ── Auto updater ─────────────────────────────────────────────
        self.updater = Updater(PROJECT_ROOT)

        # ── Shared audio player modal ────────────────────────────────
        from ui.components.audio_player import AudioPlayer
        self.audio_player = AudioPlayer(self)

        # ── On-screen keyboard overlay (for WiFi password, etc.) ────
        from ui.components.keyboard import OnScreenKeyboard
        self.keyboard = OnScreenKeyboard(self)

        # ── Chromatic keyboard (any generic USB MIDI keyboard) ──────
        self.chromatic_kb = ChromaticKeyboard()

        # ── Controller mapper (generic MIDI controller profiles) ────
        # Loads JSON profiles from setup/midi_profiles/ and handles
        # user overrides in sessions/controller_overrides/. Claims any
        # port matching a profile before ChromaticKeyboard sees it.
        self.controller_mapper = ControllerMapper(
            app=self,
            profiles_dir=os.path.join(PROJECT_ROOT, "setup", "midi_profiles"),
            overrides_dir=os.path.join(PROJECT_ROOT, "sessions",
                                        "controller_overrides"),
        )
        self.controller_mapper.load_profiles()
        # ChromaticKeyboard asks the mapper which ports are claimed so
        # it doesn't try to grab the same port.
        self.chromatic_kb._controller_mapper = self.controller_mapper

        # Per-device current bank — used by pad.trigger.* actions and
        # kept in sync with the KEYS tab pad selector.
        self.current_bank: dict[str, int] = {}

        # ── Video recorder (live screen capture to MP4) ─────────────
        # Captures to MJPEG in RAM, re-encodes to H.264 on stop.
        # Pi 3B can handle MJPEG at the full UI frame rate.
        self.video_recorder = VideoRecorder(
            screen_size=(self._display_w, self._display_h), fps=self.fps)
        self.demo_scheduler: DemoScheduler | None = None

        # ── WiFi + Bluetooth managers (end-user connectivity) ───────
        self.wifi = WifiManager()
        self.bluetooth = BluetoothManager()

        # ── Compa-to-Compa network link ──────────────────────────────
        recordings_dir = self.config.get("P6_RECORDING_DIR",
                                          os.path.join(PROJECT_ROOT, "recordings"))
        samples_dir = os.path.join(PROJECT_ROOT, "samples")
        kits_dir = os.path.join(PROJECT_ROOT, "kits")
        self.compa_server = CompaServer(recordings_dir, samples_dir, kits_dir)
        self.compa_browser = CompaBrowser()
        try:
            self.compa_server.start()
            self.compa_browser.start()
            # Notify on inbound uploads (optional, toggleable in settings)
            self.compa_server.set_upload_callback(self._on_compa_upload)
        except Exception as e:
            print(f"Compa link init failed: {e}", flush=True)

        # Default: notifications on
        self.notify_uploads = self.config.get("NOTIFY_UPLOADS", "1") == "1"

        # ── Live CC state (for workspace parameter tracking + HUD) ───
        # Per-bus dict of {cc: value} updated by incoming SP-404 MIDI
        self.live_cc: dict[int, dict[int, int]] = {i: {} for i in range(16)}
        # HUD notification queue: [(text, color, timestamp), ...]
        self._hud_messages: list[tuple[str, tuple, float]] = []

        # ── Akai USB storage (Computer Mode file transfer) ───────────
        self.akai_storage = AkaiStorageManager()

        # ── Device librarians (P-6 + SP-404 MK2) ────────────────────
        sessions_dir = self.config.get(
            "P6_SESSIONS_DIR",
            os.path.join(PROJECT_ROOT, "sessions"),
        )
        images_dir = os.path.join(sessions_dir, "device_images")
        try:
            self.p6_lib = P6Librarian(os.path.join(images_dir, "p6"))
            self.sp404_lib = SP404Librarian(os.path.join(images_dir, "sp404"))
        except Exception as e:
            print(f"Librarian init failed: {e}", flush=True)
            self.p6_lib = None
            self.sp404_lib = None

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
            "files": FileBrowserScreen(self),
            "io": IOSettingsScreen(self),
            "controller": ControllerScreen(self),
        }
        self.current_screen_name = "session"

        # ── Nav bar (responsive) ─────────────────────────────────────
        # XFER is now a location inside the Files screen ("Device"), so
        # it no longer has its own nav slot. The transfer screen still
        # lives in self.screens["transfer"] and is delegated to by Files.
        nav_y = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT
        if theme.SCREEN_WIDTH >= 700:
            # Wide screen: full labels
            nav_labels = [
                ("SESSION", "session"),
                ("RECORD",  "record"),
                ("SAMPLE",  "sample"),
                ("RADIO",   "radio"),
                ("FILES",   "files"),
            ]
            font_name = "small"
        elif theme.SCREEN_WIDTH >= 400:
            # Medium screen: short labels
            nav_labels = [
                ("SES", "session"),
                ("REC", "record"),
                ("SMP", "sample"),
                ("RAD", "radio"),
                ("FIL", "files"),
            ]
            font_name = "tiny"
        else:
            # Tiny screen: icons/minimal
            nav_labels = [
                ("S", "session"),
                ("R", "record"),
                ("F", "sample"),
                ("~", "radio"),
                ("FB", "files"),
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

        # Ableton Push 2 (Phase 1: pad triggers + transport buttons)
        try:
            p2_ports = find_push2_ports()
            if p2_ports.get("user_in") is not None or p2_ports.get("live_in") is not None:
                self.push2 = Push2(p2_ports)
                self.push2.on_pad = self._on_push2_pad
                self.push2.on_button = self._on_push2_button
                self.push2.on_encoder = self._on_push2_encoder
                self.push2.on_special_encoder = self._on_push2_special_encoder
                self.push2.on_pitch_bend = self._on_push2_pitch_bend
                got_user = p2_ports.get("user_in") is not None
                got_live = p2_ports.get("live_in") is not None
                print(f"Push 2 connected (User={got_user}, Live={got_live})")
                # Light the page buttons so the user can see they're live.
                self._push2_paint_page_buttons()
        except Exception as e:
            print(f"Push 2 init failed: {e}")

        # Push 2 display (Phase 2: vendor-specific USB bulk framebuffer)
        if self.push2 is not None and Push2Display is not None:
            try:
                self.push2_display = Push2Display()
                self.push2_renderer = Push2Renderer(self, self.push2_display)
                self.push2_renderer.start()
                print("Push 2 display active")
            except Exception as e:
                print(f"Push 2 display init failed: {e}")
                self.push2_display = None
                self.push2_renderer = None

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

        # Wire incoming pad-note callbacks so Push 2 flashes pads when
        # the focused device plays them (its own hardware pad, sequencer,
        # or a remote trigger). Both SP-404 and P-6 send note events on
        # their MIDI Out — control mode lights the matching Push 2 pad
        # in real time.
        for _dev_key in ("SP-404MKII", "P-6"):
            _midi = self._midi_connections.get(_dev_key)
            if _midi is None:
                continue
            _orig_note = getattr(_midi, "on_note", None)
            def _make_note_handler(dev_key, orig):
                def _h(ch, note, vel):
                    try:
                        self._on_device_note(dev_key, ch, note, vel)
                    except Exception:
                        pass
                    if orig is not None:
                        try:
                            orig(ch, note, vel)
                        except Exception:
                            pass
                return _h
            _midi.on_note = _make_note_handler(_dev_key, _orig_note)

        # Twister Genius — auto-detect and connect
        if self.twister.detect():
            if self.twister.connect():
                # Target the FOCUSED device, not always SP-404
                focused_midi = self._midi_connections.get(self.device_name) or sp404_midi
                if focused_midi:
                    self.twister.set_target(focused_midi)
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

        # Controller mapper — starts scanning before ChromaticKeyboard so
        # profiled controllers get claimed first and the keyboard module
        # only sees unprofiled devices.
        self.controller_mapper.start()
        print(f"ControllerMapper: {len(self.controller_mapper._profiles)} "
              f"profiles loaded", flush=True)

        # Initialize current_bank for each connected device
        for short_name in self.device_manager.connected.keys():
            self.current_bank.setdefault(short_name, 0)

        # Chromatic keyboard — auto-detect any generic MIDI keyboard
        self.chromatic_kb.start()
        self._retarget_chromatic_keyboard()
        if self.chromatic_kb.connected:
            print(f"Chromatic KB: {self.chromatic_kb.device_name}", flush=True)
        else:
            print("Chromatic KB: scanning for keyboards", flush=True)

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

    def _draw_hud(self, surface):
        """Draw HUD notification overlay — works on any screen."""
        import time
        f_small = theme.font("small")
        now = time.monotonic()
        hud_lifetime = 2.5
        msgs = self._hud_messages

        # Prune expired
        msgs[:] = [(t, c, ts) for t, c, ts in msgs if now - ts < hud_lifetime]

        if not msgs:
            return

        hud_x = theme.SCREEN_WIDTH - 10
        hud_y = 46  # Below header

        for text, color, ts in reversed(msgs):
            age = now - ts
            alpha = min(1.0, (hud_lifetime - age) / 0.5)
            if alpha <= 0:
                continue

            surf = f_small.render(text, True, color)
            w = surf.get_width() + 16
            h = 26
            x = hud_x - w
            bg = pygame.Surface((w, h), pygame.SRCALPHA)
            a = int(200 * alpha)
            bg.fill((10, 10, 18, a))
            surface.blit(bg, (x, hud_y))
            bar_color = (*color[:3], a) if len(color) >= 3 else (*color, a)
            bar = pygame.Surface((3, h), pygame.SRCALPHA)
            bar.fill(bar_color)
            surface.blit(bar, (x, hud_y))
            text_surf = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
            text_surf.blit(surf, (0, 0))
            text_surf.set_alpha(int(255 * alpha))
            surface.blit(text_surf, (x + 8, hud_y + 4))

            hud_y += h + 4

    def _on_compa_upload(self, category: str, relpath: str, size: int):
        """Called (on the server thread) when a peer uploads a file to us."""
        print(f"_on_compa_upload: {category}/{relpath} ({size} bytes)", flush=True)
        if not getattr(self, "notify_uploads", True):
            print("  notify_uploads disabled", flush=True)
            return
        import os
        name = os.path.basename(relpath)
        if name.endswith(".meta.json"):
            print(f"  skipping sidecar {name}", flush=True)
            return
        kb = size / 1024
        size_str = f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"
        msg = f"Received: {name[:28]} ({size_str})"
        print(f"  pushing HUD: {msg}", flush=True)
        self.push_hud(msg, theme.BLUE)

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

    # ── Push 2 callbacks ─────────────────────────────────────────────

    def _on_push2_pad(self, idx: int, velocity: int):
        """Push 2 pad hit. Dispatch depends on the current push2_mode,
        which mirrors the focused device-workspace tab on Compa
        (control / keys / sequence / pattern / ...)."""
        if self.push2_mode == "keys":
            self._on_push2_keys_pad(idx, velocity)
            return
        if self.push2_mode == "pattern":
            self._on_push2_pattern_pad(idx, velocity)
            return
        if self.push2_mode == "dj":
            self._on_push2_dj_pad(idx, velocity)
            return
        if self.push2_mode == "looper":
            self._on_push2_looper_pad(idx, velocity)
            return

        if velocity == 0:
            return  # control mode acts only on press

        dev_key = getattr(self.device_manager, "focus_key", None)
        from engine.push2 import Push2

        if dev_key == "SP-404MKII":
            if self.push2_control_layout == 1:
                bank_in_page, pad_in_bank = Push2.quad_pad_to_bank_pad(idx)
            else:
                bank_in_page, pad_in_bank = Push2.two_row_pad_to_bank_pad(idx)
            if pad_in_bank < 0:
                return
            effective_bank = self.push2_pad_page * 4 + bank_in_page
            if effective_bank >= self._push2_sp_bank_count():
                return
            midi = self._midi_connections.get("SP-404MKII")
            if midi is None:
                return
            from engine.controller_actions import _compute_pad_note
            note, channel = _compute_pad_note(
                "SP-404MKII", effective_bank, pad_in_bank, midi,
            )
            if note < 0:
                return
            try:
                if velocity > 0:
                    midi.send_note_on(note, velocity, channel=channel)
                else:
                    midi.send_note_off(note, channel=channel)
            except Exception:
                pass
            return

        if dev_key == "P-6":
            if self.push2_control_layout == 1:
                bank_in_page, pad_in_bank = Push2.p6_quad_pad_to_bank_pad(idx)
                effective_bank = self.push2_pad_page * 4 + bank_in_page
            else:
                # Layout 0 (default): row-per-bank, all 8 banks visible.
                bank, pad_in_bank = Push2.p6_row_pad_to_bank_pad(idx)
                effective_bank = bank
            if pad_in_bank < 0:
                return
            if effective_bank < 0 or effective_bank >= 8:
                return
            midi = self._midi_connections.get("P-6")
            if midi is None:
                return
            from engine.controller_actions import _compute_pad_note
            note, channel = _compute_pad_note(
                "P-6", effective_bank, pad_in_bank, midi,
            )
            if note < 0:
                return
            try:
                if velocity > 0:
                    midi.send_note_on(note, velocity, channel=channel)
                else:
                    midi.send_note_off(note, channel=channel)
            except Exception:
                pass
            return

        # Generic fallback (other USB-class-compliant devices) — keep
        # the legacy bottom-strip behaviour: rows 0-1 fire pad.trigger.1
        # through pad.trigger.16 against whatever current bank Compa is
        # focused on.
        bank_offset = idx // 16
        pad_in_bank = idx % 16
        if bank_offset == 0:
            _dispatch_action(f"pad.trigger.{pad_in_bank + 1}", velocity, self)

    # ── Incoming-pad flash on Push 2 (control mode) ──────────────────

    def _device_note_to_bank_pad(self, dev_key: str, channel: int,
                                  note: int) -> tuple[int, int]:
        """Reverse of `_compute_pad_note`. Given an incoming note from
        the device, return (bank, pad_in_bank) or (-1, -1) if the note
        isn't a pad trigger (e.g. chromatic, transport, CC echo)."""
        if dev_key == "SP-404MKII":
            if not (36 <= note <= 51):
                return (-1, -1)
            if not (0 <= channel <= 9):
                return (-1, -1)
            midi_row = (note - 36) // 4         # 0 (bottom of SP) .. 3 (top)
            col = (note - 36) % 4
            sp_row = 3 - midi_row               # 0 (top) .. 3 (bottom)
            pad_in_bank = sp_row * 4 + col
            return (channel, pad_in_bank)
        if dev_key == "P-6":
            # P-6 sample pads documented as ch11 (sampler) notes 48-95
            # covering all 8 banks × 6 pads. We accept any channel in
            # the sampler/auto family so pad-press echoes that travel
            # on ch15 (Auto, "currently selected pad") also flash. The
            # note value alone disambiguates the bank+pad.
            if not (48 <= note <= 95):
                return (-1, -1)
            delta = note - 48
            bank = delta // 6
            pad_in_bank = delta % 6
            if bank > 7 or pad_in_bank > 5:
                return (-1, -1)
            return (bank, pad_in_bank)
        return (-1, -1)

    def _push2_pad_idx_for_device_pad(self, dev_key: str, bank: int,
                                       pad_in_bank: int) -> int:
        """Forward of the layout helpers. Brute-force search the 64
        Push 2 pads for one whose decoded (effective_bank, pad_in_bank)
        matches. Returns -1 if the bank isn't currently visible (off
        the active pad page) or pad_in_bank is invalid."""
        from engine.push2 import Push2
        layout = self.push2_control_layout
        page = self.push2_pad_page
        for idx in range(64):
            if dev_key == "SP-404MKII":
                if layout == 1:
                    bp, p = Push2.quad_pad_to_bank_pad(idx)
                else:
                    bp, p = Push2.two_row_pad_to_bank_pad(idx)
                if p < 0:
                    continue
                eff_bank = page * 4 + bp
            elif dev_key == "P-6":
                if layout == 1:
                    bp, p = Push2.p6_quad_pad_to_bank_pad(idx)
                    if p < 0:
                        continue
                    eff_bank = page * 4 + bp
                else:
                    eff_bank, p = Push2.p6_row_pad_to_bank_pad(idx)
                    if p < 0:
                        continue
            else:
                return -1
            if eff_bank == bank and p == pad_in_bank:
                return idx
        return -1

    def _on_device_note(self, dev_key: str, channel: int,
                         note: int, velocity: int) -> None:
        """Incoming note from SP/P-6. In control mode, flash the
        matching Push 2 pad while the device holds the note, restore
        on note-off. No-op if not in control mode, the device isn't
        focused, or the note doesn't decode to a visible pad."""
        if self.push2_mode != "control":
            return
        if getattr(self.device_manager, "focus_key", None) != dev_key:
            return
        push2 = getattr(self, "push2", None)
        if push2 is None:
            return
        bank, pad_in_bank = self._device_note_to_bank_pad(
            dev_key, channel, note)
        if bank < 0 or pad_in_bank < 0:
            return
        idx = self._push2_pad_idx_for_device_pad(
            dev_key, bank, pad_in_bank)
        if idx < 0:
            return
        try:
            if velocity > 0:
                # Bright white pops against every bank color (red,
                # blue, green, yellow, orange, magenta, teal, lime,
                # purple). Avoids the cyan-near-teal confusion the
                # earlier flash had on Bank G.
                push2.flash_pad(idx, 122)
                self._push2_active_device_pads.add(idx)
            else:
                push2.restore_pad(idx)
                self._push2_active_device_pads.discard(idx)
        except Exception:
            pass

    # ── Screenshot scheduling (Compa touchscreen + Push 2) ────────────

    def save_compa_screen(self, label: str = "") -> str | None:
        """Save the current Compa touchscreen frame to ~/compa/screenshots/
        as a timestamped PNG. Filename encodes the current screen name
        (and focused device for the device-workspace) so successive
        captures auto-distinguish."""
        try:
            ss_dir = "/home/pi/compa/screenshots"
            os.makedirs(ss_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            screen = self.current_screen_name or "screen"
            tag = screen
            if screen == "device_workspace":
                dev = (getattr(self.device_manager, "focus_key", None)
                       or "none")
                tag = f"{screen}_{dev}"
            parts = [f"compa_{tag}"]
            if label:
                parts.append(label.replace(" ", "-"))
            parts.append(ts)
            name = "_".join(parts) + ".png"
            path = os.path.join(ss_dir, name)
            pygame.image.save(self.screen, path)
            print(f"Compa screenshot saved: {path}", flush=True)
            return path
        except Exception as e:
            print(f"Compa screenshot failed: {e}", flush=True)
            return None

    def schedule_screenshot(self, delay_s: float = 0.0,
                            compa: bool = True,
                            push2: bool = False) -> None:
        """Schedule Compa / Push 2 captures to fire in `delay_s` seconds.
        A countdown overlay appears on the touchscreen during the wait
        so the user can navigate to the screen they want to capture
        before the timer expires. Replaces any pending schedule."""
        now = time.monotonic()
        self._scheduled_screenshot = {
            "fire_at": now + max(0.0, float(delay_s)),
            "started_at": now,
            "compa": bool(compa),
            "push2": bool(push2),
        }

    def _maybe_fire_screenshot(self) -> None:
        """Fire any pending screenshot whose scheduled time has passed.
        Called once per main-loop frame from `_draw` (after the UI is
        composed but before the overlay is painted, so the saved frame
        is the clean composed screen)."""
        sched = getattr(self, "_scheduled_screenshot", None)
        if sched is None:
            return
        try:
            if time.monotonic() < sched["fire_at"]:
                return
        except Exception:
            self._scheduled_screenshot = None
            return
        self._scheduled_screenshot = None
        if sched.get("compa"):
            try:
                self.save_compa_screen()
            except Exception as e:
                print(f"save_compa_screen error: {e}", flush=True)
        if sched.get("push2"):
            try:
                self.save_push2_screenshot()
            except Exception as e:
                print(f"save_push2_screenshot error: {e}", flush=True)

    def _draw_screenshot_overlay(self, surface) -> None:
        """Paint a small countdown badge in the top-right while a
        screenshot is scheduled. Positioned so it doesn't block the
        buttons the user needs to navigate with."""
        sched = getattr(self, "_scheduled_screenshot", None)
        if sched is None:
            return
        try:
            remaining = sched["fire_at"] - time.monotonic()
            if remaining <= 0:
                return
            secs = int(remaining) + 1
            targets = []
            if sched.get("compa"):
                targets.append("COMPA")
            if sched.get("push2"):
                targets.append("PUSH 2")
            target_txt = " + ".join(targets) if targets else "SCREEN"
            # Cache the fonts on the app — SysFont every frame churns
            # GC + heap and was almost certainly contributing to the
            # earlier UI hangs the user saw.
            if not hasattr(self, "_ss_overlay_fonts"):
                self._ss_overlay_fonts = (
                    pygame.font.SysFont("dejavusans-bold", 40),
                    pygame.font.SysFont("dejavusans-bold", 14),
                )
            big_font, sub_font = self._ss_overlay_fonts
            num_color = (255, 0, 62)
            big_surf = big_font.render(str(secs), True, num_color)
            sub_surf = sub_font.render(target_txt, True, (240, 240, 240))
            sw, _sh = surface.get_size()
            pad = 8
            box_w = max(big_surf.get_width(), sub_surf.get_width()) + pad * 2
            box_h = big_surf.get_height() + sub_surf.get_height() + pad * 2 + 2
            # Top-right, nudged inward so the device pill / nav bar
            # remain readable.
            box_x = sw - box_w - 12
            box_y = 80
            backdrop = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            backdrop.fill((0, 0, 0, 180))
            surface.blit(backdrop, (box_x, box_y))
            pygame.draw.rect(surface, num_color,
                             (box_x, box_y, box_w, box_h), 1,
                             border_radius=6)
            bx = box_x + (box_w - big_surf.get_width()) // 2
            by = box_y + pad
            surface.blit(big_surf, (bx, by))
            sx = box_x + (box_w - sub_surf.get_width()) // 2
            sy = by + big_surf.get_height() + 2
            surface.blit(sub_surf, (sx, sy))
        except Exception as e:
            # Never let an overlay paint failure break the render loop.
            print(f"Screenshot overlay draw error: {e}", flush=True)

    def save_push2_screenshot(self, label: str = "") -> str | None:
        """Save the current Push 2 display frame to recordings/ as a
        timestamped PNG. Filename encodes focused device + push2_mode
        + control layout so a sequence of captures auto-distinguishes
        each Push 2 screen.

        Returns the saved path or None on failure."""
        renderer = getattr(self, "push2_renderer", None)
        if renderer is None:
            print("Push 2 screenshot: renderer not running", flush=True)
            return None
        ts = time.strftime("%Y%m%d_%H%M%S")
        dev = (getattr(self.device_manager, "focus_key", None)
               or "none")
        mode = self.push2_mode or "control"
        parts = [f"push2_{dev}_{mode}"]
        if mode == "control":
            parts.append(f"L{self.push2_control_layout}")
            parts.append(f"P{self.push2_pad_page}")
        if label:
            parts.append(label.replace(" ", "-"))
        parts.append(ts)
        name = "_".join(parts) + ".png"
        path = os.path.join("/home/pi/compa/screenshots", name)
        ok = renderer.save_screenshot(path)
        if ok:
            print(f"Push 2 screenshot: {path}", flush=True)
            return path
        return None

    def _on_push2_keys_pad(self, idx: int, velocity: int):
        """Keys-mode pad — forward as a chromatic note to the focused
        device. Pad-to-note mapping depends on the active scale:
          - chromatic scale → chromatic layout (each pad = +1 semitone)
          - any other scale → in-key layout (each pad = next scale
            degree; every pad IS a scale note, no off-scale pads)."""
        from engine.push2 import Push2, SCALES
        kb = getattr(self, "chromatic_kb", None)
        if kb is None:
            return
        scale_name, scale_offsets = SCALES[
            self.push2_keys_scale % len(SCALES)]

        if velocity > 0:
            if scale_name == "chromatic":
                note = Push2.keys_pad_to_note(
                    idx, base_note=self.push2_keys_base_note)
            else:
                note = Push2.in_key_pad_to_note(
                    idx, scale_offsets,
                    root_pc=self.push2_keys_root,
                    base_note=self.push2_keys_base_note,
                )
            self._push2_keys_active[idx] = note
            try:
                kb._forward_note_on(note, velocity)
            except Exception:
                pass
        else:
            note = self._push2_keys_active.pop(idx, None)
            if note is None:
                return
            try:
                kb._forward_note_off(note)
            except Exception:
                pass

    def _on_push2_dj_pad(self, idx: int, velocity: int):
        """DJ mode pad — send the matching SP-404 DJ-mode CC.

        Matches Compa's touchscreen DJ button mapping 1:1 (CCs 20/23/
        22/24/25 on Ch1/Ch2). If a future Roland-confirmed DJ MIDI map
        differs, only DJ_BUTTON_CCS in engine.push2 needs updating."""
        if velocity == 0:
            return
        row = idx // 8
        col = idx % 8
        if row > 1 or col >= 5:
            return
        from engine.push2 import Push2
        cc = Push2.DJ_BUTTON_CCS[col]
        deck_ch = row  # 0 = Deck A (ch1), 1 = Deck B (ch2)
        midi = self._midi_connections.get("SP-404MKII")
        if midi is None:
            return
        try:
            midi.send_cc(cc, 127, channel=deck_ch)
        except Exception:
            pass

    def _on_push2_looper_pad(self, idx: int, velocity: int):
        """Looper mode pad — visual-only for now. Compa's looper UI
        doesn't have a verified CC map for SP-404 yet, so taps echo
        as a press-flash but don't fire MIDI. Ready for a CC table
        when one's confirmed."""
        return

    def _step_grids_path(self) -> str:
        """JSON file backing self._compa_step_grids."""
        sessions_dir = self.config.get(
            "P6_SESSIONS_DIR",
            os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "sessions"),
        )
        return os.path.join(sessions_dir, "compa_step_grids.json")

    def _push2_pattern_sequencer(self):
        """Resolve the sequencer object pattern mode should drive."""
        screen = self.screens.get("pattern")
        return getattr(screen, "sequencer", None) if screen else None

    def _ensure_push2_pattern_setup(self):
        """Wire the PiSequencer to the currently-focused device. On
        device change: save the outgoing device's grid for its current
        pattern, configure the sequencer for the new device, and load
        the new device's stored grid for its current pattern (or clear
        if none).

        Tempo comes from the Compa MasterClock, not the device's MIDI
        clock, so step timing stays stable independent of which sample
        is firing.

        Idempotent: subsequent calls with the same focus are no-ops."""
        seq = self._push2_pattern_sequencer()
        if seq is None:
            return
        cur_dev = getattr(self.device_manager, "focus_key", "") or ""
        if self._push2_pattern_device == cur_dev:
            return
        # Save outgoing device's grid for its currently-active pattern
        # before switching, so re-focus restores it.
        prev_dev = self._push2_pattern_device
        if prev_dev:
            try:
                prev_midi = self._midi_connections.get(prev_dev)
                if prev_midi is not None:
                    self._save_step_grid(prev_dev,
                                          int(prev_midi.state.active_pattern))
            except Exception:
                pass
        try:
            if seq.playing:
                seq.stop()
        except Exception:
            pass
        try:
            seq.configure_for_device(cur_dev)
        except Exception:
            pass
        midi = self._midi_connections.get(cur_dev)
        if midi is not None:
            try:
                seq.set_midi_out(midi)
            except Exception:
                pass
            try:
                midi.on_clock_tick = None
            except Exception:
                pass
        # Load step grid for the new device's currently-active pattern.
        try:
            cur_pat = int(midi.state.active_pattern) if midi else 0
        except Exception:
            cur_pat = 0
        self._push2_pattern_device = cur_dev
        self._load_step_grid(cur_dev, cur_pat)

    def _on_push2_pattern_pad(self, idx: int, velocity: int):
        """Pattern-mode pad layout: all 64 pads are step cells.

          row 7 (top)    = pad (pad_offset + 0)
          row 6          = pad (pad_offset + 1)
          ...
          row 0 (bottom) = pad (pad_offset + 7)
          col N → step (step_offset + N)

        Bank selector lives on the bottom select buttons (below the
        Push 2 display); pattern launchers live on the top select
        buttons (above the display). Page-Left/Right pages step
        columns; Octave Up/Down pages the pad window for devices
        with more than 8 pads (SP-404).

        Per-pattern step grids are snapshotted on launch so each
        pattern keeps its own step data."""
        if velocity == 0:
            return
        row = idx // 8
        col = idx % 8

        # Top row of the grid (row 7) maps to first visible pad
        # (pad_offset + 0); row 0 maps to pad_offset + 7.
        local_pad = 7 - row
        pad = self.push2_pattern_pad_offset + local_pad
        step = self.push2_pattern_step_offset + col
        seq = self._push2_pattern_sequencer()
        if seq is None:
            return
        if pad >= getattr(seq, "num_pads", 0):
            return
        if step >= getattr(seq, "num_steps", 0):
            return
        try:
            seq.toggle_step(pad, step)
        except Exception:
            pass

    def _push2_pattern_launch(self, pat_idx: int):
        """Save the current pattern's step grid, switch the device,
        then load the target pattern's stored grid (or clear)."""
        midi = self.p6
        dev_key = getattr(self.device_manager, "focus_key", "") or ""
        if midi is not None:
            try:
                cur = int(midi.state.active_pattern)
                self._save_step_grid(dev_key, cur)
            except Exception:
                pass
            try:
                midi.send_program_change(pat_idx)
                if hasattr(midi, "state"):
                    midi.state.active_pattern = pat_idx
            except Exception:
                pass
        self._load_step_grid(dev_key, pat_idx)

    def _save_step_grid(self, dev_key: str, pat_idx: int):
        """Snapshot PiSequencer.grid for (device, pattern). No-op if
        no sequencer wired."""
        seq = self._push2_pattern_sequencer()
        if seq is None or not dev_key:
            return
        try:
            snapshot = [
                [(cell.active, cell.velocity)
                 for cell in row]
                for row in seq.grid[:seq.num_pads]
            ]
            self._compa_step_grids[(dev_key, pat_idx)] = snapshot
        except Exception:
            pass

    def _load_step_grid(self, dev_key: str, pat_idx: int):
        """Restore step grid for (device, pattern) into PiSequencer.
        Clears the sequencer when no snapshot exists for that slot."""
        seq = self._push2_pattern_sequencer()
        if seq is None or not dev_key:
            return
        saved = self._compa_step_grids.get((dev_key, pat_idx))
        if saved is None:
            try:
                seq.clear_all()
            except Exception:
                pass
            return
        try:
            for p, row in enumerate(saved):
                if p >= seq.num_pads:
                    break
                for s, (active, vel) in enumerate(row):
                    if s >= seq.num_steps:
                        break
                    seq.grid[p][s].active = bool(active)
                    seq.grid[p][s].velocity = int(vel)
            # Clear pads/steps we didn't restore.
            for p in range(len(saved), seq.num_pads):
                for s in range(seq.num_steps):
                    seq.grid[p][s].active = False
        except Exception:
            pass

    def update_push2_mode(self) -> str:
        """Resolve the desired Push 2 mode from the current Compa
        screen + tab. Updates self.push2_mode and returns it. Called
        each frame by the Push 2 renderer."""
        new_mode = "control"
        if self.current_screen_name == "device_workspace":
            ws = self.screens.get("device_workspace")
            if ws is not None:
                tabs = getattr(ws, "_tabs", [])
                idx = getattr(ws, "_current_tab", 0)
                if 0 <= idx < len(tabs):
                    tab_key = tabs[idx][0]
                    if tab_key == "keys":
                        new_mode = "keys"
                    elif tab_key in ("pattern", "sequence"):
                        new_mode = "pattern"
                    elif tab_key == "dj":
                        new_mode = "dj"
                    elif tab_key == "looper":
                        new_mode = "looper"
                    else:
                        new_mode = "control"
        if new_mode != self.push2_mode:
            self._on_push2_mode_change(self.push2_mode, new_mode)
            self.push2_mode = new_mode
        # Pattern mode: keep the PiSequencer in sync with the focused
        # device every frame (cheap when nothing changed).
        if self.push2_mode == "pattern":
            self._ensure_push2_pattern_setup()
        return self.push2_mode

    def _push2_jump_to_tab(self, tab_key: str):
        """Switch the device-workspace screen and select a specific
        tab. Used by the Push 2 mode buttons (Note → keys tab, etc.)."""
        if self.current_screen_name != "device_workspace":
            try:
                self.switch_screen("device_workspace")
            except Exception:
                return
        ws = self.screens.get("device_workspace")
        if ws is None:
            return
        tabs = getattr(ws, "_tabs", [])
        for i, entry in enumerate(tabs):
            if entry and entry[0] == tab_key:
                ws._current_tab = i
                if hasattr(ws, "_build_knobs"):
                    try:
                        ws._build_knobs()
                    except Exception:
                        pass
                return

    def _on_push2_special_encoder(self, name: str, delta: int):
        """Tempo / Master / Swing encoders. Records a transient event
        so the Push 2 display can show a short popup with the current
        value when the user turns one of these knobs."""
        import time as _time
        self._push2_last_special_encoder = (name, _time.monotonic())
        if name == "tempo":
            try:
                self.master_clock.nudge_bpm(float(delta))
            except Exception:
                pass
            return
        if name == "master":
            midi = self.p6
            if midi is None:
                return
            ch = 0
            current = self.live_cc.get(ch, {}).get(7, 100)
            new_val = max(0, min(127, current + delta))
            if new_val == current:
                return
            try:
                midi.send_cc(7, new_val, channel=ch)
                self.live_cc.setdefault(ch, {})[7] = new_val
            except Exception:
                pass
            return
        if name == "swing":
            # Adjust the active overlay-sequencer's swing amount in
            # 1% steps. Range 0..50 (50% = max shuffle = odd step
            # halfway between two beats). Display popup shows the
            # current value via _push2_last_special_encoder above.
            seq = self._push2_pattern_sequencer()
            if seq is None:
                return
            try:
                cur = int(getattr(seq, "swing_amount", 0))
                new_val = max(0, min(50, cur + int(delta)))
                seq.swing_amount = new_val
            except Exception:
                pass
            return

    def _on_push2_pitch_bend(self, value: int):
        """Push 2 touch strip routing.

        - DJ mode on SP-404 → crossfader (CC 8 on Ch1).
        - All other modes / devices → CC 1 (modulation wheel) on the
          focused device's main channel. The 14-bit strip value scales
          to a 7-bit CC; touch at the bottom = 0, top = 127."""
        dev_key = getattr(self.device_manager, "focus_key", None)
        midi = self._midi_connections.get(dev_key) if dev_key else None
        if midi is None or getattr(midi, "_out", None) is None:
            return
        if self.push2_mode == "dj" and dev_key == "SP-404MKII":
            try:
                midi.send_cc(8, value >> 7, channel=0)
            except Exception:
                pass
            return
        # Mod wheel — universal 7-bit mapping; works on any device that
        # responds to CC 1 (most synths/samplers route it to filter,
        # vibrato depth, or similar).
        ch = 0 if dev_key == "SP-404MKII" else 9
        try:
            midi.send_cc(1, max(0, min(127, value >> 7)), channel=ch)
        except Exception:
            pass

    def _on_push2_mode_change(self, prev: str, new: str):
        """Handle a Push 2 mode transition. Releases held notes from
        the previous mode so they don't sustain across the switch."""
        if prev == "keys":
            kb = getattr(self, "chromatic_kb", None)
            for _idx, note in list(self._push2_keys_active.items()):
                try:
                    if kb:
                        kb._forward_note_off(note)
                except Exception:
                    pass
            self._push2_keys_active.clear()

    def push2_max_patterns(self) -> int:
        """Pattern count for the focused device — used by Launch 1-8
        + page paging."""
        dev_key = getattr(self.device_manager, "focus_key", None)
        if dev_key == "P-6":
            return 64
        if dev_key == "SP-404MKII":
            return 16
        return 8

    def push2_launch_page_count(self) -> int:
        return max(1, (self.push2_max_patterns() + 7) // 8)

    def push2_pattern_launch_page_count(self) -> int:
        """Pages of 8 patterns each — used by combined-pattern mode's
        single pattern row."""
        return max(1, (self.push2_max_patterns() + 7) // 8)

    def _push2_cycle_pattern_launch_page(self, delta: int):
        pages = self.push2_pattern_launch_page_count()
        self.push2_pattern_launch_page = (
            self.push2_pattern_launch_page + delta) % pages

    def push2_bank_count(self) -> int:
        """Bank count for the focused device (P-6: 8 / SP-404: 10)."""
        dev_key = getattr(self.device_manager, "focus_key", None)
        if dev_key == "P-6":
            return 8
        if dev_key == "SP-404MKII":
            return 10
        return 1

    def _push2_cycle_bank_window(self, delta: int):
        """Page the 8-bank top-row window in full 8-slot strides
        (so SP'"'"'s last page shows banks I/J cleanly with the other 6
        slots dark). Wraps at the ends."""
        total = self.push2_bank_count()
        pages = max(1, (total + 7) // 8)
        cur_page = self.push2_pattern_bank_offset // 8
        new_page = (cur_page + delta) % pages
        self.push2_pattern_bank_offset = new_page * 8

    def push2_active_bank(self) -> int:
        """Currently-active bank for the focused device (0-indexed)."""
        dev_key = getattr(self.device_manager, "focus_key", "") or ""
        return int(self.current_bank.get(dev_key, 0))

    def _push2_set_bank(self, bank_idx: int):
        """Switch the focused device's active bank and update the
        PiSequencer'"'"'s row-config routing so step triggers fire on the
        new bank (channel for SP, note offset for P-6)."""
        dev_key = getattr(self.device_manager, "focus_key", "") or ""
        if dev_key not in ("P-6", "SP-404MKII"):
            return
        bank_idx = max(0, min(self.push2_bank_count() - 1, int(bank_idx)))
        self.current_bank[dev_key] = bank_idx
        seq = self._push2_pattern_sequencer()
        if seq is None:
            return
        try:
            from engine.p6_sequencer import ROW_PAD
        except Exception:
            ROW_PAD = "pad"
        if dev_key == "SP-404MKII":
            # SP: bank index = MIDI channel for pad row triggers.
            for cfg in seq.row_configs:
                if cfg.row_type == ROW_PAD:
                    cfg.channel = bank_idx
        elif dev_key == "P-6":
            # P-6: 6 pads per bank, base_note + bank_idx * 6 + i.
            base = int(getattr(seq, "base_note", 48))
            for i, cfg in enumerate(seq.row_configs):
                if cfg.row_type == ROW_PAD:
                    cfg.note = base + bank_idx * 6 + i

    def _push2_sp_bank_count(self) -> int:
        """SP-404 MK2 supports 10 banks (A-J). Constant for now; could
        read dynamically in the future."""
        return 10

    def push2_pad_page_count(self) -> int:
        """How many 4-bank windows cover the focused device's banks
        under the current control layout."""
        dev_key = getattr(self.device_manager, "focus_key", None)
        if dev_key == "SP-404MKII":
            # Both layouts are 4 banks per page, just arranged differently.
            return (self._push2_sp_bank_count() + 3) // 4
        if dev_key == "P-6":
            # Layout 0 (row-per-bank) shows all 8 banks at once → 1 page.
            # Layout 1 (quadrant) shows 4 banks per page → 2 pages.
            if self.push2_control_layout == 1:
                return 2
            return 1
        return 1

    def _push2_cycle_pad_page(self, delta: int) -> None:
        count = self.push2_pad_page_count()
        self.push2_pad_page = (self.push2_pad_page + delta) % count

    def _push2_control_layout_count(self) -> int:
        """How many control-mode pad layouts are available for the
        focused device. Returning 1 means the Layout button is a
        no-op (LED stays dim)."""
        dev_key = getattr(self.device_manager, "focus_key", None)
        # Currently both SP and P-6 expose two layouts; other devices
        # only have the legacy fallback so layout cycling is disabled.
        if dev_key in ("SP-404MKII", "P-6"):
            return 2
        return 1

    def _push2_cycle_control_layout(self) -> None:
        """Advance push2_control_layout, wrapping per-device count.
        Resets pad_page to 0 since the new layout may have different
        paging."""
        if self.push2_mode != "control":
            return
        count = self._push2_control_layout_count()
        if count <= 1:
            return
        self.push2_control_layout = (self.push2_control_layout + 1) % count
        self.push2_pad_page = 0

    def _on_push2_button(self, name: str, value: int):
        """Push 2 transport / function buttons."""
        # Shift is a modifier — track press/release so other handlers
        # can combine it with other buttons (e.g. Shift+D-pad).
        if name == "shift":
            self._push2_shift_held = (value > 0)
            return
        if value <= 0:
            return  # release — no-op for the rest
        if name == "play":
            # In pattern mode, Play toggles both the Compa sequencer
            # AND the focused device's transport. Press 1 starts both;
            # press 2 stops both. In other modes, just dispatch the
            # standard transport.play action.
            if self.push2_mode == "pattern":
                self._ensure_push2_pattern_setup()
                seq = self._push2_pattern_sequencer()
                was_playing = bool(getattr(seq, "playing", False))
                if was_playing:
                    if seq is not None:
                        try:
                            seq.stop()
                        except Exception:
                            pass
                    try:
                        if seq is not None:
                            self.master_clock.remove_listener(seq.on_tick)
                        self.master_clock.stop()
                    except Exception:
                        pass
                    _dispatch_action("transport.stop", value, self)
                else:
                    if seq is not None:
                        try:
                            self.master_clock.add_listener(seq.on_tick)
                            self.master_clock.start()
                            seq.start()
                        except Exception:
                            pass
                    _dispatch_action("transport.play", value, self)
                return
            _dispatch_action("transport.play", value, self)
        elif name == "record":
            # In pattern mode: toggle the Compa recorder directly
            # (start_recording / stop_recording) so the Push 2 record
            # button arms a take of the current performance. Outside
            # pattern mode: legacy auto-record toggle action.
            if self.push2_mode == "pattern":
                rec = getattr(self, "recorder", None)
                if rec is None:
                    return
                try:
                    if rec.is_recording:
                        rec.stop_recording()
                    else:
                        if not rec._monitoring:
                            rec.start_monitoring()
                        meta = {}
                        if self.p6:
                            meta["bpm_at_record"] = self.p6.state.bpm
                            meta["pattern_at_record"] = (
                                self.p6.state.active_pattern)
                        rec.start_recording(metadata=meta)
                except Exception:
                    pass
                return
            _dispatch_action("transport.record", value, self)
        elif name == "stop_clip":
            if self.push2_mode == "pattern":
                seq = self._push2_pattern_sequencer()
                if seq is not None and seq.playing:
                    seq.stop()
            _dispatch_action("transport.stop", value, self)
        elif name == "double_loop" and self.push2_mode == "pattern":
            seq = self._push2_pattern_sequencer()
            if seq is not None:
                cur = int(getattr(seq, "num_steps", 16))
                self._push2_last_num_steps = cur
                seq.num_steps = 32 if cur < 32 else (64 if cur < 64 else 16)
                self.push2_pattern_step_offset = 0
        elif name == "duplicate" and self.push2_mode == "pattern":
            # Duplicate the current pattern: double the length AND copy
            # the existing step grid into the new second half so the
            # pattern plays twice in a row (vs Double Loop which just
            # extends with empty cells).
            seq = self._push2_pattern_sequencer()
            if seq is not None:
                self._push2_last_num_steps = int(getattr(
                    seq, "num_steps", 16))
                try:
                    seq.duplicate_pattern()
                except Exception:
                    pass
                self.push2_pattern_step_offset = 0
        elif name == "convert" and self.push2_mode == "pattern":
            # Zoom in: halve ticks_per_step, double num_steps, stretch
            # the grid so existing notes stay on the same beats while
            # adding sub-step cells between them. Lets the user draw
            # finer rhythms (e.g. 1/16 → 1/32 hi-hat rolls).
            seq = self._push2_pattern_sequencer()
            if seq is not None:
                self._push2_last_num_steps = int(getattr(
                    seq, "num_steps", 16))
                try:
                    seq.zoom_in()
                except Exception:
                    pass
                self.push2_pattern_step_offset = 0
        elif name == "fixed_length" and self.push2_mode == "pattern":
            # Zoom out: double ticks_per_step, halve num_steps. Pairs
            # of cells collapse with OR so any active step in either
            # half survives. Inverse of Convert / zoom-in.
            seq = self._push2_pattern_sequencer()
            if seq is not None:
                self._push2_last_num_steps = int(getattr(
                    seq, "num_steps", 16))
                try:
                    seq.zoom_out()
                except Exception:
                    pass
                self.push2_pattern_step_offset = 0
        elif name == "repeat" and self.push2_mode == "pattern":
            # Nudge left — rotate the entire pattern one step earlier.
            seq = self._push2_pattern_sequencer()
            if seq is not None:
                try:
                    seq.nudge(-1)
                except Exception:
                    pass
        elif name == "accent" and self.push2_mode == "pattern":
            # Nudge right — rotate the entire pattern one step later.
            seq = self._push2_pattern_sequencer()
            if seq is not None:
                try:
                    seq.nudge(+1)
                except Exception:
                    pass
        elif name == "undo" and self.push2_mode == "pattern":
            # In pattern mode, Undo reverts the most recent step-count
            # change made via Double Loop / Duplicate / Convert /
            # Fixed Length. Only the num_steps is restored — content
            # preserved by zoom_in/duplicate stays as transformed.
            if self._push2_last_num_steps is not None:
                seq = self._push2_pattern_sequencer()
                if seq is not None:
                    seq.num_steps = int(self._push2_last_num_steps)
                    self.push2_pattern_step_offset = 0
                self._push2_last_num_steps = None
        elif name in ("page_left", "page_right"):
            if self.push2_mode == "pattern":
                seq = self._push2_pattern_sequencer()
                if seq is not None:
                    num_steps = int(getattr(seq, "num_steps", 16))
                    pages = max(1, (num_steps + 7) // 8)
                    cur_page = self.push2_pattern_step_offset // 8
                    delta = +1 if name == "page_right" else -1
                    new_page = (cur_page + delta) % pages
                    self.push2_pattern_step_offset = new_page * 8
            else:
                self._push2_cycle_page(+1 if name == "page_right" else -1)
        elif name == "octave_up":
            if self.push2_mode == "keys":
                self.push2_keys_base_note = min(
                    115, self.push2_keys_base_note + 12)
            elif self.push2_mode == "pattern":
                # Octave Up scrolls the visible window UP to lower-
                # numbered pads (Pad 1 etc.) — the SP-404's pads 1-4
                # sit on the TOP physical row of the device, so "up"
                # = "see the top of the SP". Decreases pad_offset.
                self.push2_pattern_pad_offset = max(
                    0, self.push2_pattern_pad_offset - 8,
                )
            else:
                self._push2_cycle_pad_page(+1)
        elif name == "octave_down":
            if self.push2_mode == "keys":
                self.push2_keys_base_note = max(
                    0, self.push2_keys_base_note - 12)
            elif self.push2_mode == "pattern":
                # Octave Down scrolls DOWN to higher-numbered pads
                # (9-16) — those sit on the BOTTOM physical rows of
                # the SP. Increases pad_offset.
                seq = self._push2_pattern_sequencer()
                num_pads = int(getattr(seq, "num_pads", 8)) if seq else 8
                max_off = max(0, num_pads - 8)
                self.push2_pattern_pad_offset = min(
                    max_off,
                    self.push2_pattern_pad_offset + 8,
                )
            else:
                self._push2_cycle_pad_page(-1)
        elif name == "note":
            # Hardware shortcut to the KEYS tab of the focused device.
            self._push2_jump_to_tab("keys")
        elif name == "session":
            try:
                self.switch_screen("session")
            except Exception:
                pass
        elif name == "browse":
            try:
                self.switch_screen("files")
            except Exception:
                pass
        elif name in ("add_device", "add_track"):
            # Cycle through connected devices.
            devices = list(self.device_manager.connected.keys())
            if not devices:
                return
            cur = self.device_manager.focus_key
            try:
                i = devices.index(cur) if cur in devices else -1
                self.switch_focus(devices[(i + 1) % len(devices)])
            except Exception:
                pass
        elif name.startswith("launch_"):
            try:
                idx = int(name.rsplit("_", 1)[1]) - 1   # 0..7
            except Exception:
                return
            pat_idx = self.push2_launch_page * 8 + idx
            if pat_idx >= self.push2_max_patterns():
                return
            midi = self.p6
            if midi is None:
                return
            try:
                midi.send_program_change(pat_idx)
                if hasattr(midi, "state"):
                    midi.state.active_pattern = pat_idx
            except Exception:
                pass
        elif name == "new":
            # CHAIN tab: append a chain step. PATTERN/SEQUENCE tab in
            # pattern mode: clear the current pattern's step grid —
            # but require two presses within ~3 seconds so an
            # accidental tap doesn't wipe a take. First press arms
            # the confirm overlay; second press while armed actually
            # clears.
            if self.current_screen_name == "device_workspace":
                ws = self.screens.get("device_workspace")
                tabs = getattr(ws, "_tabs", [])
                idx = getattr(ws, "_current_tab", 0)
                if 0 <= idx < len(tabs):
                    tab_key = tabs[idx][0]
                    if tab_key == "chain":
                        ps = self.screens.get("pattern")
                        chain = getattr(ps, "_chain", None)
                        if chain is not None:
                            from engine.p6_chain import ChainStep
                            pat = 0
                            try:
                                pat = self.p6.state.active_pattern if self.p6 else 0
                            except Exception:
                                pass
                            chain.steps.append(ChainStep(pattern=pat, bars=4))
                    elif tab_key in ("pattern", "sequence"):
                        now = time.monotonic()
                        pending = getattr(
                            self, "_push2_new_confirm_until", 0.0)
                        if pending and now < pending:
                            # Second press within the confirm window
                            # — actually clear and dismiss the prompt.
                            seq = self._push2_pattern_sequencer()
                            if seq is not None:
                                try:
                                    seq.clear_all()
                                except Exception:
                                    pass
                            self._push2_new_confirm_until = 0.0
                        else:
                            # First press — arm the confirm window
                            # (the renderer reads this attr to draw
                            # the "press again to clear" overlay).
                            self._push2_new_confirm_until = now + 3.0
        elif name == "layout" and self.push2_mode == "control":
            # Cycle the active control-mode pad layout for the focused
            # device (SP: 2-row strips → quadrants; P-6: row-per-bank →
            # quadrants). The Layout button LED reflects the available
            # state (lit when more than one layout is on offer).
            self._push2_cycle_control_layout()
        elif name == "quantize" and self.push2_mode == "pattern":
            # Normalize all step velocities to 100 in the current
            # pattern. Steps stay where they are (already grid-aligned)
            # — this is the "even out the velocities" tweak.
            seq = self._push2_pattern_sequencer()
            if seq is not None:
                try:
                    for p in range(seq.num_pads):
                        for s in range(seq.num_steps):
                            cell = seq.grid[p][s]
                            if cell.active:
                                cell.velocity = 100
                except Exception:
                    pass
        elif name == "mute":
            # SP-404: toggle mute on the currently active bus by
            # flipping CC 7 (volume) between 0 and 100. Stores the
            # last non-zero value so re-mute restores the prior level.
            dev_key = getattr(self.device_manager, "focus_key", None)
            if dev_key != "SP-404MKII":
                return
            midi = self._midi_connections.get("SP-404MKII")
            if midi is None:
                return
            bus = (int(self.twister.active_bus)
                   if getattr(self, "twister", None) else 0)
            current = self.live_cc.get(bus, {}).get(7, 100)
            if current > 0:
                self._sp_pre_mute_vol = self._sp_pre_mute_vol or {}
                self._sp_pre_mute_vol[bus] = current
                new_val = 0
            else:
                new_val = (self._sp_pre_mute_vol or {}).get(bus, 100)
            try:
                midi.send_cc(7, new_val, channel=bus)
                self.live_cc.setdefault(bus, {})[7] = new_val
            except Exception:
                pass
        elif name == "device":
            self._push2_jump_to_tab("control")
        elif name.startswith("bot_select_") and self.push2_mode == "pattern":
            # Bottom-row select buttons in pattern mode = bank selector
            # (was on the top pad row before — moved here to free the
            # full 8×8 pad grid for step cells).
            try:
                idx = int(name.rsplit("_", 1)[1]) - 1
            except Exception:
                return
            bank_idx = self.push2_pattern_bank_offset + idx
            if bank_idx < self.push2_bank_count():
                self._push2_set_bank(bank_idx)
        elif name.startswith("bot_select_") and self.push2_mode == "control":
            # Bottom-row select buttons in SP control mode:
            #   1-5: bus selector (B1, B2, B3, B4, IN) — same colors
            #        as the touchscreen bus pills
            #   8:   toggle FX on/off (CC#19) on the active bus
            #   6-7: reserved
            dev_key = getattr(self.device_manager, "focus_key", None)
            if dev_key == "SP-404MKII":
                try:
                    idx = int(name.rsplit("_", 1)[1]) - 1
                except Exception:
                    return
                if 0 <= idx <= 4 and getattr(self, "twister", None) is not None:
                    self.twister.active_bus = idx
                elif idx == 7:
                    self._sp404_toggle_fx_onoff()
        elif name == "nav_up":
            if self.push2_mode == "keys":
                from engine.push2 import SCALES
                self.push2_keys_scale = (self.push2_keys_scale + 1) % len(SCALES)
        elif name == "nav_down":
            if self.push2_mode == "keys":
                from engine.push2 import SCALES
                self.push2_keys_scale = (self.push2_keys_scale - 1) % len(SCALES)
        elif name == "nav_right":
            if self.push2_mode == "keys":
                self.push2_keys_root = (self.push2_keys_root + 1) % 12
            elif self.push2_mode == "pattern":
                if self._push2_shift_held:
                    self._push2_cycle_bank_window(+1)
                else:
                    self._push2_cycle_pattern_launch_page(+1)
            else:
                pages = self.push2_launch_page_count()
                self.push2_launch_page = (self.push2_launch_page + 1) % pages
        elif name == "nav_left":
            if self.push2_mode == "keys":
                self.push2_keys_root = (self.push2_keys_root - 1) % 12
            elif self.push2_mode == "pattern":
                if self._push2_shift_held:
                    self._push2_cycle_bank_window(-1)
                else:
                    self._push2_cycle_pattern_launch_page(-1)
            else:
                pages = self.push2_launch_page_count()
                self.push2_launch_page = (self.push2_launch_page - 1) % pages
        elif name.startswith("top_select_") and self.push2_mode == "pattern":
            # Top-row select buttons in pattern mode = pattern launcher
            # (was on row 6 of the pad grid — moved up here so the pads
            # are pure step cells now). 8 patterns visible per page;
            # Nav-Left/Right pages through the launcher window.
            try:
                idx = int(name.rsplit("_", 1)[1]) - 1
            except Exception:
                return
            base = self.push2_pattern_launch_page * 8
            pat_idx = base + idx
            if pat_idx < self.push2_max_patterns():
                self._push2_pattern_launch(pat_idx)
        elif name.startswith("top_select_"):
            # Top-row select buttons 1-N jump directly to encoder page
            # N-1 in non-pattern modes.
            try:
                idx = int(name.rsplit("_", 1)[1]) - 1
            except Exception:
                return
            if 0 <= idx < self.push2_page_count():
                self.push2_page = idx

    def push2_slot_window(self) -> list:
        """Return the 8 Twister slots currently visible on the Push 2
        when in P-6 mode. Empty list for non-P-6 devices (they have
        their own encoder dispatch logic)."""
        dev_key = getattr(self.device_manager, "focus_key", None)
        if dev_key != "P-6":
            return []
        tw = getattr(self, "twister", None)
        pages = getattr(tw, "_pages", None) if tw else None
        if not pages:
            return []
        flat = [s for page in pages for s in page]
        if not flat:
            return []
        total = len(flat)
        page_count = max(1, (total + 7) // 8)
        self.push2_page = max(0, min(self.push2_page, page_count - 1))
        start = self.push2_page * 8
        return flat[start:start + 8]

    def push2_page_count(self) -> int:
        dev_key = getattr(self.device_manager, "focus_key", None)
        if dev_key != "P-6":
            return 1
        tw = getattr(self, "twister", None)
        pages = getattr(tw, "_pages", None) if tw else None
        if not pages:
            return 1
        flat_len = sum(len(p) for p in pages)
        return max(1, (flat_len + 7) // 8)

    def _push2_cycle_page(self, delta: int):
        count = self.push2_page_count()
        self.push2_page = (self.push2_page + delta) % count
        self._push2_paint_page_buttons()

    def _push2_paint_page_buttons(self):
        """Light page-left / page-right buttons dim when a move in
        that direction is available (we always loop, so both are on)."""
        if not self.push2:
            return
        count = self.push2_page_count()
        # Always dim when only one page, brighter when there are more.
        color = 22 if count > 1 else 3   # 22 = muted cyan-ish, 3 = very dim
        try:
            self.push2.set_button("page_left", color)
            self.push2.set_button("page_right", color)
        except Exception:
            pass

    # SP-404 MK2 FX control CCs (Ctrl 1-6).
    _SP404_CTRL_CCS = [16, 17, 18, 80, 81, 82]
    # SP-404 MK2 effect-select CC (per bus channel) and on/off CC.
    _SP404_FX_SELECT_CC = 83
    _SP404_FX_ONOFF_CC = 19

    def _sp404_active_bus_tab(self) -> str:
        """Map the Twister-tracked active bus index (0..4) to the
        sp404_effects.py tab key used to look up the right effect
        list. 0,1 = bus1/2 (BUS12_FX); 2,3 = bus3/4 (BUS34_FX);
        4 = INPUT (INPUT_FX)."""
        try:
            bus = int(self.twister.active_bus)
        except Exception:
            bus = 0
        if bus == 4:
            return "input_fx"
        if bus >= 2:
            return f"bus{bus + 1}_fx"
        return f"bus{bus + 1}_fx"

    def _sp404_cycle_fx(self, delta: int) -> None:
        """Cycle the active SP-404 effect on the focused bus by `delta`
        (+1 = next, -1 = prev). Sends CC#83 to the bus channel and
        wraps within the bus's effect list. Mirrors the touchscreen
        FX selector behaviour."""
        from engine.sp404_effects import fx_count_for_tab
        midi = self._midi_connections.get("SP-404MKII")
        if midi is None:
            return
        try:
            bus = int(self.twister.active_bus)
        except Exception:
            bus = 0
        tab = self._sp404_active_bus_tab()
        count = fx_count_for_tab(tab)
        if count <= 0:
            return
        current = int(self.live_cc.get(bus, {}).get(
            self._SP404_FX_SELECT_CC, 0))
        new_val = (current + delta) % count
        if new_val == current:
            return
        try:
            midi.send_cc(self._SP404_FX_SELECT_CC, new_val, channel=bus)
            self.live_cc.setdefault(bus, {})[self._SP404_FX_SELECT_CC] = new_val
        except Exception:
            pass

    def _sp404_toggle_fx_onoff(self) -> None:
        """Toggle the FX on/off state on the focused bus by flipping
        CC#19 between 127 (on) and 0 (off). Matches the touchscreen
        FX-toggle button."""
        midi = self._midi_connections.get("SP-404MKII")
        if midi is None:
            return
        try:
            bus = int(self.twister.active_bus)
        except Exception:
            bus = 0
        current = int(self.live_cc.get(bus, {}).get(
            self._SP404_FX_ONOFF_CC, 0))
        new_val = 0 if current >= 64 else 127
        try:
            midi.send_cc(self._SP404_FX_ONOFF_CC, new_val, channel=bus)
            self.live_cc.setdefault(bus, {})[self._SP404_FX_ONOFF_CC] = new_val
        except Exception:
            pass

    def _on_push2_encoder(self, idx: int, delta: int):
        """Push 2 performance encoder turn.

        Pattern mode (overlay sequencer): encoders 1-2 are the
        "remix" knobs — encoder 1 re-rolls the step grid at a
        density proportional to its position; encoder 2 randomizes
        velocities of active steps within a spread. Encoders 3-8
        fall through to the device-specific control mapping below.

        P-6 control: nudge the CC of the Twister slot at the current
        encoder page's offset (8 params per page, 4 pages).
        SP-404 control:
          - encoders 1-6 adjust Ctrl 1-6 of the active bus
          - encoder 7 reserved
          - encoder 8 cycles the active effect (CC#83)."""
        if self.push2_mode == "pattern":
            seq = self._push2_pattern_sequencer()
            if seq is None:
                return
            if idx == 0:
                # Encoder 1: density. Track 0..100 on the sequencer
                # itself so each detent re-rolls a fresh pattern at
                # the current density level.
                cur = int(getattr(seq, "_remix_density", 30))
                new_val = max(0, min(100, cur + int(delta) * 4))
                seq._remix_density = new_val
                try:
                    seq.randomize_density(new_val)
                except Exception:
                    pass
                return
            if idx == 1:
                # Encoder 2: velocity spread. 0 leaves velocities
                # alone; turning the knob re-rolls velocity for every
                # active step within a wider window.
                cur = int(getattr(seq, "_remix_vel_spread", 0))
                new_val = max(0, min(100, cur + int(delta) * 4))
                seq._remix_vel_spread = new_val
                try:
                    seq.randomize_velocities(new_val)
                except Exception:
                    pass
                return
            # Encoders 3-8 in pattern mode: no-op for now (reserved).
            return

        dev_key = getattr(self.device_manager, "focus_key", None)

        if dev_key == "SP-404MKII":
            if idx == 7:
                # Encoder 8: cycle effect select on the active bus.
                # Treat any positive delta as +1 step, any negative
                # as -1 step so a full sweep doesn't blast through 40
                # effects in one motion.
                step = 1 if delta > 0 else -1
                self._sp404_cycle_fx(step)
                return
            if idx == 6:
                return  # reserved
            if idx >= len(self._SP404_CTRL_CCS):
                return
            cc = self._SP404_CTRL_CCS[idx]
            try:
                bus = int(self.twister.active_bus)
            except Exception:
                bus = 0
            current = self.live_cc.get(bus, {}).get(cc, 64)
            new_val = max(0, min(127, current + delta))
            if new_val == current:
                return
            midi = self._midi_connections.get("SP-404MKII")
            if midi is None:
                return
            try:
                midi.send_cc(cc, new_val, channel=bus)
                self.live_cc.setdefault(bus, {})[cc] = new_val
            except Exception:
                pass
            return

        # P-6 path (original behavior).
        slots = self.push2_slot_window()
        if idx >= len(slots):
            return
        slot = slots[idx]
        cc = getattr(slot, "_p6_cc", None)
        if cc is None:
            return
        ch = 14
        current = self.live_cc.get(ch, {}).get(cc, 64)
        new_val = max(0, min(127, current + delta))
        if new_val == current:
            return
        try:
            if self.p6:
                self.p6.send_cc(cc, new_val, channel=ch)
            self.live_cc.setdefault(ch, {})[cc] = new_val
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
            # In Push 2 pattern mode the Record button is the explicit
            # way to arm a take, so suppress auto-record entirely there
            # — Play is just play. Outside pattern mode the existing
            # auto-record-on-transport behaviour is preserved.
            if (self.auto_record and not self.recorder.is_recording
                    and self.push2_mode != "pattern"):
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

    def _retarget_chromatic_keyboard(self):
        """Point the chromatic keyboard at the focused device's chromatic channel."""
        kb = self.chromatic_kb
        focus_key = self.device_manager.focus_key
        focused_midi = self._midi_connections.get(focus_key)
        if not focused_midi:
            return

        dev = self.device_manager.active
        if dev and dev.short_name == "SP-404MKII":
            # SP-404: Ch16 chromatic play. The target pad for Ch16 is
            # set on the SP-404 hardware (not controllable via MIDI).
            # Pad selector in KEYS tab preview-triggers pads so the user
            # can hear which one they want, then they select it on the SP.
            kb.set_target(focused_midi, 15, pitchbend_mode=False)
        elif dev and dev.short_name == "P-6":
            # P-6 granular engine on Ch 4 (0-indexed: 3) — direct chromatic
            ch_map = getattr(dev, "midi_channels", None)
            channel = ch_map.get("granular", 3) if ch_map else 3
            kb.set_target(focused_midi, channel, pitchbend_mode=False)
        else:
            # Generic fallback: use sampler channel
            channel = getattr(focused_midi, 'ch_sampler', 10)
            kb.set_target(focused_midi, channel, pitchbend_mode=False)

    def switch_focus(self, short_name: str) -> bool:
        """Switch which device the UI controls.

        Updates DeviceManager focus, rebuilds MIDI router, switches
        audio monitoring, and retargets Twister to the new device.
        """
        if not self.device_manager.set_focus(short_name):
            return False

        print(f"Focus → {short_name}", flush=True)

        # Reset Push 2 control-layout state on focus change — the
        # layout count is per-device, and we don't want a stale layout
        # index pointing past what the new device offers.
        self.push2_control_layout = 0
        self.push2_pad_page = 0

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

        # Retarget chromatic keyboard to the new focused device
        self._retarget_chromatic_keyboard()

        # Notify controller mapper so knob/pad actions retarget
        if hasattr(self, "controller_mapper"):
            self.controller_mapper.on_focus_changed(short_name)
            # Make sure the new device has a current_bank entry
            self.current_bank.setdefault(short_name, 0)

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

            # On-screen keyboard takes highest priority when visible
            if getattr(self, 'keyboard', None) and self.keyboard.visible:
                if self.keyboard.handle_event(event):
                    continue

            # Audio player modal takes priority when visible
            if getattr(self, 'audio_player', None) and self.audio_player.visible:
                if self.audio_player.handle_event(event):
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

        # Video recording triggers
        if os.path.exists("/tmp/compa_record_start"):
            os.remove("/tmp/compa_record_start")
            self.video_recorder.start()

        if os.path.exists("/tmp/compa_record_stop"):
            os.remove("/tmp/compa_record_stop")
            self.video_recorder.stop()

        # Auto-demo: starts recording, drives the sequence, then stops
        if os.path.exists("/tmp/compa_record_demo"):
            os.remove("/tmp/compa_record_demo")
            if not self.video_recorder.recording:
                if self.video_recorder.start():
                    self.demo_scheduler = DemoScheduler(build_demo_sequence())
                    self.demo_scheduler.start(self)

        # Advance the auto-demo if it's running
        if self.demo_scheduler is not None:
            self.demo_scheduler.tick(self)
            if self.demo_scheduler.finished:
                self.video_recorder.stop()
                self.demo_scheduler = None

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

        # Extended screenshot: device workspace with every focus + tab combo
        if os.path.exists("/tmp/compa_screenshot_workspaces"):
            os.remove("/tmp/compa_screenshot_workspaces")
            original_screen = self.current_screen_name
            original_focus = self.device_manager.focus_key

            workspace = self.screens.get("device_workspace")
            self.switch_screen("device_workspace")

            for short_name in list(self.device_manager.connected.keys()):
                self.switch_focus(short_name)
                if hasattr(workspace, "on_enter"):
                    workspace.on_enter()
                # Capture each tab
                for i, (tab_key, _tab_label) in enumerate(workspace._tabs):
                    workspace._current_tab = i
                    if tab_key == "control" and hasattr(workspace, "_build_knobs"):
                        workspace._build_knobs()
                    if tab_key == "keys" and hasattr(self, "chromatic_kb"):
                        self.chromatic_kb.enabled = True
                    # Let a few frames render so oscilloscope has data
                    for _ in range(4):
                        self.screen.fill(theme.BG)
                        if hasattr(workspace, "update"):
                            workspace.update()
                        workspace.draw(self.screen)
                        self._draw_nav()
                        pygame.display.flip()
                        self.clock.tick(self.fps)
                    safe = short_name.replace("/", "_").replace(" ", "_")
                    out = f"/tmp/compa_workspace_{safe}_{tab_key}.png"
                    pygame.image.save(self.screen, out)
                    print(f"Screenshot: {out}", flush=True)
                    if tab_key == "keys" and hasattr(self, "chromatic_kb"):
                        self.chromatic_kb.enabled = False

            # Restore
            self.switch_focus(original_focus)
            self.switch_screen(original_screen)
            print("Workspace screenshots captured", flush=True)

        if hasattr(self.current_screen, "update"):
            self.current_screen.update()
        for btn, screen_name in self.nav_buttons:
            btn.active = (screen_name == self.current_screen_name)

    def _draw(self):
        self.screen.fill(theme.BG)
        self.current_screen.draw(self.screen)
        self._draw_nav()
        # HUD overlay — visible on ALL screens
        self._draw_hud(self.screen)
        # Audio player overlays everything when visible
        if getattr(self, 'audio_player', None) and self.audio_player.visible:
            self.audio_player.draw(self.screen)
        # Keyboard overlays absolutely everything (including the audio player)
        if getattr(self, 'keyboard', None) and self.keyboard.visible:
            self.keyboard.draw(self.screen)

        # Fire any pending screenshot here so the saved Compa frame is
        # the fully composed UI WITHOUT the countdown overlay drawn on
        # top. The user navigated to whatever screen they wanted; save
        # it clean.
        self._maybe_fire_screenshot()

        # Countdown overlay — drawn after the screenshot save so it
        # appears on the live display + in any video recording but
        # does NOT end up baked into the saved PNG.
        self._draw_screenshot_overlay(self.screen)

        # Capture this frame into the video pipe BEFORE flipping display.
        # We copy the fully composed surface (nav + HUD + modals all included).
        if getattr(self, 'video_recorder', None) and self.video_recorder.recording:
            self.video_recorder.capture(self.screen)

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
        # Finalize any in-progress video recording first so the MP4 is valid
        if getattr(self, 'video_recorder', None) and self.video_recorder.recording:
            self.video_recorder.stop()
        # Stop the controller mapper + chromatic keyboard threads cleanly
        if hasattr(self, "controller_mapper"):
            try:
                self.controller_mapper.stop()
            except Exception:
                pass
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
        if self.push2_renderer:
            try:
                self.push2_renderer.shutdown()
            except Exception:
                pass
        try:
            self.master_clock.stop()
        except Exception:
            pass
        # Persist Compa step grids (snapshot the live PiSequencer first
        # so the currently-edited pattern doesn't lose its in-flight
        # changes).
        try:
            dev_key = getattr(self.device_manager, "focus_key", "") or ""
            midi = self._midi_connections.get(dev_key) if dev_key else None
            if midi is not None:
                self._save_step_grid(dev_key, int(midi.state.active_pattern))
            from engine.compa_step_persistence import save as _save_grids
            _save_grids(self._compa_step_grids, self._step_grids_path())
        except Exception:
            pass
        if self.push2_display:
            try:
                self.push2_display.close()
            except Exception:
                pass
        if self.push2:
            try:
                self.push2.shutdown()
            except Exception:
                pass
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

    # SIGUSR1 = save current Push 2 display frame as a PNG. Used by the
    # docs-capture workflow: navigate Compa to each tab, send SIGUSR1
    # from another shell, repeat.
    def _sigusr1(signum, frame):
        try:
            app.save_push2_screenshot()
        except Exception as e:
            print(f"SIGUSR1 handler error: {e}", flush=True)
    signal.signal(signal.SIGUSR1, _sigusr1)

    # First-time setup wizard (only if setup hasn't been completed)
    if not os.path.exists(SETUP_FLAG):
        run_wizard(app.screen, app.clock, app)

    # Animated splash screen (skip if splash disabled in config)
    if app.config.get("SKIP_SPLASH", "0") != "1":
        run_splash(app.screen, app.clock)

    app.run()


if __name__ == "__main__":
    main()
