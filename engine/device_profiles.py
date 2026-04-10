"""
Device abstraction layer for Compa.

Defines DeviceProfile (dataclass describing any USB music device)
and DeviceManager (auto-detect + registry).  Built-in profiles
ship for Roland P-6, Roland SP-404 MK2, and a generic USB audio
fallback so the app works with any class-compliant interface.
"""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class MidiCC:
    """Single MIDI CC parameter descriptor."""
    cc: int
    name: str
    min_val: int = 0
    max_val: int = 127
    default: int = 64


@dataclass
class DeviceProfile:
    """Everything Compa needs to know about a connected device."""

    # ── Identity ─────────────────────────────────────────────────────
    name: str                       # "Roland P-6", "Roland SP-404 MK2"
    short_name: str                 # "P-6", "SP-404"
    usb_vendor: int                 # 0x0582 for Roland
    usb_products: list[int] = field(default_factory=list)  # [0x02fe] etc.

    # ── Audio ────────────────────────────────────────────────────────
    audio_hint: str = ""            # sounddevice search string
    audio_in_channels: int = 2
    audio_out_channels: int = 2
    supported_sample_rates: list[int] = field(
        default_factory=lambda: [44100])

    # ── MIDI ─────────────────────────────────────────────────────────
    midi_hint: str = ""             # Port-name search string
    midi_channels: dict[str, int] = field(default_factory=dict)
    midi_note_range: tuple[int, int] = (0, 127)

    # CC maps organised by category  { "granular": [MidiCC, ...], ... }
    cc_map: dict[str, list[MidiCC]] = field(default_factory=dict)

    # ── Patterns / program change ────────────────────────────────────
    pattern_count: int = 0
    pattern_pc_channel: int = 15    # MIDI channel for program change

    # ── Transport ────────────────────────────────────────────────────
    sends_clock: bool = True
    receives_clock: bool = True
    transport_works: bool = False   # can we send start/stop?

    # ── USB storage ──────────────────────────────────────────────────
    mount_path: str = ""            # "/media/pi/P-6"
    storage_vendor_product: str = ""

    # ── Feature flags (which Compa screens are useful) ───────────────
    has_granular: bool = False
    has_effects: bool = False
    has_dj_mode: bool = False
    has_looper: bool = False
    has_sequencer: bool = True

    # ── File formats ─────────────────────────────────────────────────
    sample_format: str = "wav"
    kit_format: str = ""            # "xpm" for Akai, "" for none


# ── Built-in profile constructors ────────────────────────────────────────

def _make_p6_profile() -> DeviceProfile:
    """Roland AIRA Compact P-6."""
    return DeviceProfile(
        name="Roland P-6",
        short_name="P-6",
        usb_vendor=0x0582,
        usb_products=[0x02FE],

        # Audio
        audio_hint="P-6",
        audio_in_channels=2,
        audio_out_channels=2,
        supported_sample_rates=[44100],

        # MIDI
        midi_hint="P-6",
        midi_channels={
            "granular": 3,      # ch4
            "sampler": 10,      # ch11
            "auto": 14,         # ch15
            "program": 15,      # ch16
        },
        midi_note_range=(48, 95),

        # CC map — mirrors P6_CC_MAP from engine/p6_midi.py
        cc_map={
            "granular": [
                MidiCC(3,  "Grain Rev Prob", 0, 127, 0),
                MidiCC(13, "Detune",         0, 127, 0),
                MidiCC(15, "Grain Shape",    0, 127, 0),
                MidiCC(16, "Grain Time KF",  0, 127, 64),
                MidiCC(18, "Fine Tune",      0, 127, 64),
                MidiCC(19, "Head Position",  0, 127, 0),
                MidiCC(20, "Head Speed",     0, 127, 64),
                MidiCC(21, "Grains",         0, 127, 0),
                MidiCC(23, "Grain Size",     0, 127, 64),
                MidiCC(25, "Spread",         0, 127, 0),
                MidiCC(68, "Grain Jitter",   0, 127, 0),
                MidiCC(76, "Coarse Tune",    0, 127, 64),
                MidiCC(79, "Start Mode",     0, 127, 0),
                MidiCC(88, "Sample Select",  0, 127, 0),
            ],
            "filter": [
                MidiCC(74, "Cutoff",         0, 127, 127),
                MidiCC(71, "Resonance",      0, 127, 0),
                MidiCC(12, "Filter Type",    0, 127, 0),
                MidiCC(24, "Env Depth",      0, 127, 64),
                MidiCC(26, "Cutoff KF",      0, 127, 64),
                MidiCC(78, "Vel Sens",       0, 127, 64),
            ],
            "envelope": [
                MidiCC(73, "Attack",         0, 127, 0),
                MidiCC(75, "Decay",          0, 127, 64),
                MidiCC(30, "Sustain",        0, 127, 127),
                MidiCC(72, "Release",        0, 127, 32),
                MidiCC(28, "Amp Switch",     0, 127, 0),
                MidiCC(29, "Env Mode",       0, 127, 0),
                MidiCC(77, "Time KF",        0, 127, 64),
            ],
            "mixer": [
                MidiCC(7,  "Level",          0, 127, 100),
                MidiCC(10, "Pan",            0, 127, 64),
                MidiCC(9,  "Auto Pan",       0, 127, 0),
                MidiCC(14, "Level Jitter",   0, 127, 0),
                MidiCC(84, "Output Bus",     0, 127, 0),
                MidiCC(85, "Send Delay",     0, 127, 0),
                MidiCC(86, "Send Reverb",    0, 127, 0),
            ],
            "fx": [
                MidiCC(90, "Delay Time",     0, 127, 64),
                MidiCC(92, "Delay Level",    0, 127, 0),
                MidiCC(89, "Reverb Time",    0, 127, 64),
                MidiCC(91, "Reverb Level",   0, 127, 0),
                MidiCC(17, "Lo-fi Intensity", 0, 127, 0),
                MidiCC(87, "Lo-fi Switch",   0, 127, 0),
            ],
        },

        # Patterns
        pattern_count=64,
        pattern_pc_channel=15,  # ch16

        # Transport
        sends_clock=True,
        receives_clock=True,
        transport_works=False,

        # Storage
        mount_path="/media/pi/P-6",

        # Features
        has_granular=True,
        has_effects=False,
        has_dj_mode=False,
        has_looper=False,
        has_sequencer=True,

        sample_format="wav",
    )


def _make_sp404mk2_profile() -> DeviceProfile:
    """Roland SP-404 MK2."""
    return DeviceProfile(
        name="Roland SP-404 MK2",
        short_name="SP-404",
        usb_vendor=0x0582,
        usb_products=[0x02E7, 0x0281],

        # Audio
        audio_hint="SP-404",
        audio_in_channels=2,
        audio_out_channels=4,
        supported_sample_rates=[44100, 48000],

        # MIDI
        midi_hint="SP-404",
        midi_channels={
            "bus1": 0,
            "bus2": 1,
            "bus3": 2,
            "bus4": 3,
            "input": 4,
        },
        midi_note_range=(35, 51),  # Mode A pads; chromatic on ch16

        # CC map
        cc_map={
            "bus_effects": [
                MidiCC(16, "Param 1",    0, 127, 64),
                MidiCC(17, "Param 2",    0, 127, 64),
                MidiCC(18, "Param 3",    0, 127, 64),
                MidiCC(19, "FX Switch",  0, 127, 0),
                MidiCC(80, "Param 4",    0, 127, 64),
                MidiCC(81, "Param 5",    0, 127, 64),
                MidiCC(82, "Param 6",    0, 127, 64),
                MidiCC(83, "FX Number",  0, 127, 0),
            ],
            "dj_mode": [
                MidiCC(7,  "Volume",     0, 127, 100),
                MidiCC(8,  "Crossfade",  0, 127, 64),
                MidiCC(20, "Play",       0, 127, 0),
                MidiCC(21, "Cue",        0, 127, 0),
                MidiCC(22, "Sync",       0, 127, 0),
                MidiCC(23, "Bend-",      0, 127, 0),
                MidiCC(24, "Bend+",      0, 127, 0),
                MidiCC(25, "BPM-",       0, 127, 0),
                MidiCC(26, "BPM+",       0, 127, 0),
                MidiCC(27, "BPM Tap",    0, 127, 0),
            ],
            "looper": [
                MidiCC(85, "Delete",     0, 127, 0),
                MidiCC(86, "Record",     0, 127, 0),
                MidiCC(87, "Resample",   0, 127, 0),
                MidiCC(88, "Pitch",      0, 127, 64),
                MidiCC(89, "Speed",      0, 127, 64),
                MidiCC(90, "Level",      0, 127, 100),
                MidiCC(91, "FX Send",    0, 127, 0),
            ],
        },

        # Patterns
        pattern_count=16,
        pattern_pc_channel=0,  # ch1, PC#0-15

        # Transport
        sends_clock=True,
        receives_clock=True,
        transport_works=True,

        # Storage
        mount_path="/media/pi/SP-404MKII",

        # Features
        has_granular=False,
        has_effects=True,
        has_dj_mode=True,
        has_looper=True,
        has_sequencer=True,

        sample_format="wav",
    )


def _make_generic_usb_audio_profile() -> DeviceProfile:
    """Fallback for any USB audio interface with no MIDI features."""
    return DeviceProfile(
        name="USB Audio Device",
        short_name="USB",
        usb_vendor=0,
        usb_products=[],

        audio_hint="",
        audio_in_channels=2,
        audio_out_channels=2,
        supported_sample_rates=[44100, 48000],

        midi_hint="",
        midi_channels={},
        midi_note_range=(0, 127),
        cc_map={},

        pattern_count=0,
        pattern_pc_channel=0,

        sends_clock=False,
        receives_clock=False,
        transport_works=False,

        mount_path="",

        has_granular=False,
        has_effects=False,
        has_dj_mode=False,
        has_looper=False,
        has_sequencer=False,

        sample_format="wav",
    )


# ── Helpers ──────────────────────────────────────────────────────────────

def cc_map_to_legacy(cc_map: dict[str, list[MidiCC]]) -> dict[str, list[tuple]]:
    """Convert a DeviceProfile cc_map to the tuple format used by p6_midi.py.

    Returns { category: [(cc, name, min, max, default), ...] }
    so existing screens that expect the old P6_CC_MAP layout keep working.
    """
    legacy: dict[str, list[tuple]] = {}
    for cat, ccs in cc_map.items():
        legacy[cat] = [(m.cc, m.name, m.min_val, m.max_val, m.default)
                       for m in ccs]
    return legacy


def build_cc_lookup(cc_map: dict[str, list[MidiCC]]) -> dict[int, tuple[str, str]]:
    """Build a flat  cc_number -> (category, name)  lookup from a cc_map."""
    lookup: dict[int, tuple[str, str]] = {}
    for cat, ccs in cc_map.items():
        for m in ccs:
            lookup[m.cc] = (cat, m.name)
    return lookup


# ── Device Manager ───────────────────────────────────────────────────────

class DeviceManager:
    """Registry of device profiles + multi-device USB auto-detection.

    Detects ALL connected devices simultaneously and provides a focus
    mechanism to select which device the UI is currently controlling.
    """

    def __init__(self):
        self._profiles: dict[str, DeviceProfile] = {}
        self._active_device: DeviceProfile | None = None
        # Multi-device state
        self._connected: dict[str, DeviceProfile] = {}  # short_name -> profile
        self._focus_key: str | None = None
        self._register_builtin_profiles()

    # ── Built-in profiles ────────────────────────────────────────────

    def _register_builtin_profiles(self):
        self.register_profile(_make_p6_profile())
        self.register_profile(_make_sp404mk2_profile())
        # Generic fallback is NOT registered — it's used only when nothing matches

    # ── Public API ───────────────────────────────────────────────────

    def register_profile(self, profile: DeviceProfile):
        """Add (or overwrite) a device profile."""
        self._profiles[profile.short_name] = profile

    def detect(self) -> DeviceProfile | None:
        """Scan USB bus, match ALL registered profiles, activate first.

        Populates `connected` dict with every matched device.
        Falls back to generic USB audio if no known device is found but
        *some* USB audio interface is present.  Returns the focused
        device profile (first match) or None.
        """
        from engine.device_detect import scan_usb_devices, find_audio_device

        usb_devices = scan_usb_devices()
        log.info("USB scan found %d device(s)", len(usb_devices))

        self._connected.clear()

        # Try each profile against the bus — match ALL, not just first
        for profile in self._profiles.values():
            for dev in usb_devices:
                if (dev["vendor"] == profile.usb_vendor
                        and dev["product"] in profile.usb_products):
                    log.info("Matched device: %s (vendor=%04x product=%04x)",
                             profile.name, dev["vendor"], dev["product"])
                    self._connected[profile.short_name] = profile
                    break  # Don't double-match same profile

        if self._connected:
            # Focus on first matched device
            first_key = next(iter(self._connected))
            self._focus_key = first_key
            self._active_device = self._connected[first_key]
            if len(self._connected) > 1:
                names = ", ".join(self._connected.keys())
                log.info("Multi-device hub: %s (focus: %s)", names, first_key)
            return self._active_device

        # No known device — look for any USB audio interface
        audio = find_audio_device("")
        if audio is not None:
            log.info("No known device matched; using generic USB audio fallback")
            generic = _make_generic_usb_audio_profile()
            self._connected[generic.short_name] = generic
            self._focus_key = generic.short_name
            self._active_device = generic
            return generic

        log.warning("No USB audio device detected")
        return None

    # ── Multi-device focus ──────────────────────────────────────────

    @property
    def connected(self) -> dict[str, DeviceProfile]:
        """All currently connected device profiles (keyed by short_name)."""
        return dict(self._connected)

    @property
    def focus(self) -> DeviceProfile | None:
        """The currently focused device profile."""
        if self._focus_key:
            return self._connected.get(self._focus_key)
        return None

    @property
    def focus_key(self) -> str | None:
        """Short name of the focused device (e.g. 'P-6', 'SP-404')."""
        return self._focus_key

    def set_focus(self, short_name: str) -> bool:
        """Switch focus to a connected device by short_name.

        Returns True if focus changed, False if device not connected.
        """
        if short_name not in self._connected:
            log.warning("Cannot focus '%s' — not connected", short_name)
            return False
        if short_name == self._focus_key:
            return False  # Already focused
        self._focus_key = short_name
        self._active_device = self._connected[short_name]
        log.info("Focus switched to: %s", short_name)
        return True

    def cycle_focus(self) -> str | None:
        """Cycle focus to the next connected device. Returns new focus key."""
        if len(self._connected) < 2:
            return self._focus_key
        keys = list(self._connected.keys())
        idx = keys.index(self._focus_key) if self._focus_key in keys else -1
        next_idx = (idx + 1) % len(keys)
        self.set_focus(keys[next_idx])
        return self._focus_key

    # ── Backward-compatible single-device API ────────────────────────

    @property
    def active(self) -> DeviceProfile | None:
        """Currently active (focused) device profile."""
        return self._active_device

    @active.setter
    def active(self, profile: DeviceProfile | None):
        self._active_device = profile

    @property
    def profiles(self) -> dict[str, DeviceProfile]:
        """All registered profiles (keyed by short_name)."""
        return dict(self._profiles)

    def get_profile(self, short_name: str) -> DeviceProfile | None:
        """Look up a profile by short name."""
        return self._profiles.get(short_name)
