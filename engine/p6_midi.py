"""
Device MIDI interface (originally P-6, now multi-device).

Manages the MIDI connection to any supported device, tracks parameter state,
and provides methods to send CCs, notes, program changes, and clock.

Default P-6 MIDI channels:
  - ch4:  Granular sampler
  - ch11: Sample pads (notes 48-95)
  - ch15: Auto (currently selected pad)
  - ch16: Program change (patterns 0-63)

When a DeviceProfile is provided, channels are read from the profile
instead of using the P-6 defaults above.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

try:
    import rtmidi
except ImportError:
    rtmidi = None

log = logging.getLogger(__name__)

# ── P-6 MIDI Channel Assignments (zero-indexed) ────────────────────────
CH_GRANULAR = 3    # ch4
CH_SAMPLER = 10    # ch11
CH_AUTO = 14       # ch15
CH_PROGRAM = 15    # ch16

# ── P-6 Note Range ──────────────────────────────────────────────────────
PAD_NOTE_LO = 48   # C3
PAD_NOTE_HI = 95   # B6

# ── Complete P-6 MIDI CC Map ────────────────────────────────────────────
# (cc_number, name, min_val, max_val, default, category)
P6_CC_MAP: Dict[str, List[Tuple[int, str, int, int, int]]] = {
    "granular": [
        (3,  "Grain Rev Prob", 0, 127, 0),
        (13, "Detune",         0, 127, 0),
        (15, "Grain Shape",    0, 127, 0),
        (16, "Grain Time KF",  0, 127, 64),
        (18, "Fine Tune",      0, 127, 64),
        (19, "Head Position",  0, 127, 0),
        (20, "Head Speed",     0, 127, 64),
        (21, "Grains",         0, 127, 0),
        (23, "Grain Size",     0, 127, 64),
        (25, "Spread",         0, 127, 0),
        (68, "Grain Jitter",   0, 127, 0),
        (76, "Coarse Tune",    0, 127, 64),
        (79, "Start Mode",     0, 127, 0),
        (88, "Sample Select",  0, 127, 0),
    ],
    "filter": [
        (74, "Cutoff",         0, 127, 127),
        (71, "Resonance",      0, 127, 0),
        (12, "Filter Type",    0, 127, 0),
        (24, "Env Depth",      0, 127, 64),
        (26, "Cutoff KF",      0, 127, 64),
        (78, "Vel Sens",       0, 127, 64),
    ],
    "envelope": [
        (73, "Attack",         0, 127, 0),
        (75, "Decay",          0, 127, 64),
        (30, "Sustain",        0, 127, 127),
        (72, "Release",        0, 127, 32),
        (28, "Amp Switch",     0, 127, 0),
        (29, "Env Mode",       0, 127, 0),
        (77, "Time KF",        0, 127, 64),
    ],
    "mixer": [
        (7,  "Level",          0, 127, 100),
        (10, "Pan",            0, 127, 64),
        (9,  "Auto Pan",       0, 127, 0),
        (14, "Level Jitter",   0, 127, 0),
        (84, "Output Bus",     0, 127, 0),
        (85, "Send Delay",     0, 127, 0),
        (86, "Send Reverb",    0, 127, 0),
    ],
    "fx": [
        (90, "Delay Time",     0, 127, 64),
        (92, "Delay Level",    0, 127, 0),
        (89, "Reverb Time",    0, 127, 64),
        (91, "Reverb Level",   0, 127, 0),
        (17, "Lo-fi Intensity",0, 127, 0),
        (87, "Lo-fi Switch",   0, 127, 0),
    ],
}

# Flat lookup: cc_number -> (category, name)
CC_LOOKUP: Dict[int, Tuple[str, str]] = {}
for _cat, _params in P6_CC_MAP.items():
    for _cc, _name, *_ in _params:
        CC_LOOKUP[_cc] = (_cat, _name)

# All CC numbers in a set for quick checking
ALL_P6_CCS = set(CC_LOOKUP.keys())


@dataclass
class P6State:
    """Tracks the last-known state of the P-6."""
    cc_values: Dict[int, int] = field(default_factory=lambda: {
        cc: default
        for params in P6_CC_MAP.values()
        for cc, name, lo, hi, default in params
    })
    active_pattern: int = 0
    bpm: float = 120.0
    playing: bool = False
    recording: bool = False


class P6Midi:
    """MIDI interface to a connected device.

    Handles:
    - Sending CC, note, program change, and transport messages
    - Receiving MIDI (clock, notes, CC feedback)
    - Tracking device parameter state

    When constructed with a DeviceProfile, channels and CC tracking
    are derived from the profile.  Without a profile, P-6 defaults.
    """

    def __init__(self, midi_in: "rtmidi.MidiIn", midi_out: "rtmidi.MidiOut",
                 profile=None) -> None:
        """
        Args:
            midi_in: Opened rtmidi.MidiIn for this device.
            midi_out: Opened rtmidi.MidiOut for this device.
            profile: Optional DeviceProfile for channel/CC configuration.
        """
        if rtmidi is None:
            raise RuntimeError("python-rtmidi not installed")

        self._in = midi_in
        self._out = midi_out
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._profile = profile

        # ── Channel assignments from profile or P-6 defaults ─────────
        if profile and hasattr(profile, "midi_channels") and profile.midi_channels:
            ch = profile.midi_channels
            self.ch_granular = ch.get("granular", ch.get("bus1", CH_GRANULAR))
            self.ch_sampler = ch.get("sampler", ch.get("bus1", CH_SAMPLER))
            self.ch_auto = ch.get("auto", ch.get("bus1", CH_AUTO))
            self.ch_program = getattr(profile, "pattern_pc_channel", CH_PROGRAM)
            self.pattern_max = getattr(profile, "pattern_count", 64) - 1
            self.device_name = getattr(profile, "short_name", "Device")
        else:
            self.ch_granular = CH_GRANULAR
            self.ch_sampler = CH_SAMPLER
            self.ch_auto = CH_AUTO
            self.ch_program = CH_PROGRAM
            self.pattern_max = 63
            self.device_name = "P-6"

        # ── CC tracking from profile or P-6 defaults ─────────────────
        if profile and hasattr(profile, "cc_map") and profile.cc_map:
            self._tracked_ccs: set = set()
            cc_defaults: Dict[int, int] = {}
            for cat_params in profile.cc_map.values():
                for mcc in cat_params:
                    cc_num = mcc.cc if hasattr(mcc, "cc") else mcc[0]
                    default = mcc.default if hasattr(mcc, "default") else (mcc[4] if len(mcc) > 4 else 0)
                    self._tracked_ccs.add(cc_num)
                    cc_defaults[cc_num] = default
        else:
            self._tracked_ccs = set(ALL_P6_CCS)
            cc_defaults = {
                cc: default
                for params in P6_CC_MAP.values()
                for cc, name, lo, hi, default in params
            }

        self.state = P6State()
        self.state.cc_values = cc_defaults

        # Callbacks
        self.on_note: Optional[Callable[[int, int, int], None]] = None
        self.on_cc: Optional[Callable[[int, int, int], None]] = None
        self.on_clock_tick: Optional[Callable[[], None]] = None
        self.on_transport: Optional[Callable[[str], None]] = None

        # Filter timing but keep clock
        self._in.ignore_types(sysex=True, timing=False, active_sense=True)

        self._start_polling()
        log.info("%s MIDI interface started", self.device_name)

    @property
    def profile(self):
        """The DeviceProfile this connection was created with (or None)."""
        return self._profile

    def _start_polling(self) -> None:
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        """Poll for MIDI input from P-6."""
        clock_count = 0
        last_clock = time.monotonic()
        # Rolling average of beat intervals for stable BPM display
        beat_intervals: list[float] = []
        BPM_AVG_BEATS = 4  # Average over 4 beats

        while self._running:
            try:
                msg = self._in.get_message()
                if msg:
                    data, _ = msg
                    if not data:
                        continue

                    status = data[0]

                    # System real-time messages
                    if status == 0xF8:  # Clock tick
                        clock_count += 1
                        if clock_count >= 24:  # 24 ticks per beat
                            now = time.monotonic()
                            dt = now - last_clock
                            if 0.15 < dt < 3.0:  # 20-400 BPM sanity check
                                beat_intervals.append(dt)
                                if len(beat_intervals) > BPM_AVG_BEATS:
                                    beat_intervals.pop(0)
                                avg_dt = sum(beat_intervals) / len(beat_intervals)
                                self.state.bpm = round(60.0 / avg_dt, 1)
                            last_clock = now
                            clock_count = 0
                        if self.on_clock_tick:
                            self.on_clock_tick()
                        continue
                    elif status == 0xFA:  # Start
                        self.state.playing = True
                        if self.on_transport:
                            self.on_transport("start")
                        continue
                    elif status == 0xFC:  # Stop
                        self.state.playing = False
                        if self.on_transport:
                            self.on_transport("stop")
                        continue
                    elif status == 0xFB:  # Continue
                        self.state.playing = True
                        if self.on_transport:
                            self.on_transport("continue")
                        continue

                    if len(data) < 2:
                        continue

                    channel = status & 0x0F
                    msg_type = status & 0xF0

                    # Note on/off
                    if msg_type in (0x90, 0x80) and len(data) >= 3:
                        note = data[1]
                        vel = data[2] if msg_type == 0x90 else 0
                        if self.on_note:
                            self.on_note(channel, note, vel)

                    # CC
                    elif msg_type == 0xB0 and len(data) >= 3:
                        cc = data[1]
                        val = data[2]
                        with self._lock:
                            if cc in self._tracked_ccs:
                                self.state.cc_values[cc] = val
                        if self.on_cc:
                            self.on_cc(channel, cc, val)

                    # Program change
                    elif msg_type == 0xC0:
                        prog = data[1]
                        self.state.active_pattern = prog

                else:
                    # 5ms idle sleep — keeps wake-up rate at 200/s
                    # without hurting MIDI responsiveness (well under
                    # typical USB-MIDI latency).
                    time.sleep(0.005)

            except Exception:
                log.exception("P-6 MIDI poll error")
                time.sleep(0.1)

    # ── Send methods ────────────────────────────────────────────────────

    def send_cc(self, cc: int, value: int, channel: int = -1) -> None:
        """Send a CC message. Default channel from profile (or P-6 ch15)."""
        if channel < 0:
            channel = self.ch_auto
        value = max(0, min(127, value))
        if self._out:
            self._out.send_message([0xB0 | channel, cc, value])
        with self._lock:
            if cc in self._tracked_ccs:
                self.state.cc_values[cc] = value

    def send_note_on(self, note: int, velocity: int = 100,
                     channel: int = -1) -> None:
        """Send note on. Default channel from profile (or P-6 ch11)."""
        if channel < 0:
            channel = self.ch_sampler
        note = max(0, min(127, note))
        velocity = max(0, min(127, velocity))
        if self._out:
            self._out.send_message([0x90 | channel, note, velocity])

    def send_note_off(self, note: int, channel: int = -1) -> None:
        """Send note off. Default channel from profile."""
        if channel < 0:
            channel = self.ch_sampler
        note = max(0, min(127, note))
        if self._out:
            self._out.send_message([0x80 | channel, note, 0])

    def send_program_change(self, program: int,
                           channel: int = -1) -> None:
        """Send program change to select pattern. Range from profile."""
        if channel < 0:
            channel = self.ch_program
        program = max(0, min(self.pattern_max, program))
        if self._out:
            self._out.send_message([0xC0 | channel, program])
        self.state.active_pattern = program

    def send_start(self) -> None:
        """Send MIDI start."""
        if self._out:
            self._out.send_message([0xFA])
        self.state.playing = True

    def send_stop(self) -> None:
        """Send MIDI stop."""
        if self._out:
            self._out.send_message([0xFC])
        self.state.playing = False

    def send_continue(self) -> None:
        """Send MIDI continue."""
        if self._out:
            self._out.send_message([0xFB])
        self.state.playing = True

    # ── Device Identity (firmware version) ────────────────────────────

    def query_identity(self, timeout: float = 1.0) -> dict:
        """Send MIDI Universal Identity Request and parse the response.

        Returns dict with manufacturer, family, model, firmware version.
        Empty dict if device doesn't respond.

        Protocol: Send F0 7E 7F 06 01 F7
        Expect:   F0 7E xx 06 02 [mfr...] [family_lo] [family_hi]
                  [model_lo] [model_hi] [ver1] [ver2] [ver3] [ver4] F7
        """
        if not self._out or not self._in:
            return {}

        import rtmidi
        # Temporarily create a new input that accepts SysEx
        syx_in = None
        try:
            syx_in = rtmidi.MidiIn()
            # Find the same port
            port_name = self._in.get_port_name(0) if self._in.is_port_open() else ""
            syx_port = None
            for i in range(syx_in.get_port_count()):
                if self._profile and self._profile.midi_hint in syx_in.get_port_name(i):
                    syx_port = i
                    break
            if syx_port is None:
                del syx_in
                return {}

            syx_in.open_port(syx_port)
            syx_in.ignore_types(sysex=False, timing=True, active_sense=True)

            # Send Identity Request
            self._out.send_message([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])

            # Wait for response
            import time
            deadline = time.monotonic() + timeout
            result = {}
            while time.monotonic() < deadline:
                msg = syx_in.get_message()
                if msg:
                    data, _ = msg
                    if (len(data) >= 15 and data[0] == 0xF0 and
                            data[1] == 0x7E and data[3] == 0x06 and data[4] == 0x02):
                        # Parse identity response
                        mfr = data[5]
                        if mfr == 0:
                            # Extended manufacturer ID (3 bytes)
                            mfr_id = (data[5], data[6], data[7])
                            family = data[8] | (data[9] << 8)
                            model = data[10] | (data[11] << 8)
                            v = data[12:16] if len(data) >= 16 else data[12:]
                        else:
                            mfr_id = mfr
                            family = data[6] | (data[7] << 8)
                            model = data[8] | (data[9] << 8)
                            v = data[10:14] if len(data) >= 14 else data[10:]

                        # Roland version bytes: [major_hi, major_lo, minor_hi, minor_lo]
                        # or sometimes [0, major, minor, patch]
                        # For SP-404 MK2: 00 03 00 00 → v3.0
                        # Interpret as: skip leading zeros, major.minor
                        non_zero = [b for b in v if b > 0]
                        if non_zero:
                            major = non_zero[0]
                            minor = non_zero[1] if len(non_zero) > 1 else 0
                            ver = f"{major}.{minor}" if minor else f"{major}.0"
                        else:
                            ver = "0.0"

                        raw = " ".join(f"{b:02X}" for b in data)
                        log.info("Identity response: %s", raw)
                        print(f"MIDI Identity: {raw}", flush=True)

                        result = {
                            "manufacturer": mfr_id,
                            "family": family,
                            "model": model,
                            "firmware": ver,
                            "raw_version": list(v),
                        }
                        break
                else:
                    time.sleep(0.01)

            return result
        except Exception as e:
            log.warning("Identity query failed: %s", e)
            return {}
        finally:
            if syx_in is not None:
                try:
                    syx_in.close_port()
                except Exception:
                    pass
                try:
                    syx_in.delete()
                except Exception:
                    pass

    # ── State queries ───────────────────────────────────────────────────

    def get_cc_value(self, cc: int) -> int:
        """Get the current value of a CC parameter."""
        with self._lock:
            return self.state.cc_values.get(cc, 0)

    def get_category_values(self, category: str) -> Dict[str, int]:
        """Get all CC values for a category as {name: value}."""
        result = {}
        # Try profile cc_map first, fall back to P6_CC_MAP
        if self._profile and hasattr(self._profile, "cc_map") and self._profile.cc_map:
            for mcc in self._profile.cc_map.get(category, []):
                cc_num = mcc.cc if hasattr(mcc, "cc") else mcc[0]
                cc_name = mcc.name if hasattr(mcc, "name") else mcc[1]
                result[cc_name] = self.state.cc_values.get(cc_num, 0)
        else:
            for cc, name, *_ in P6_CC_MAP.get(category, []):
                result[name] = self.state.cc_values.get(cc, 0)
        return result

    # ── Lifecycle ───────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._running

    def shutdown(self) -> None:
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
        try:
            self._in.close_port()
        except Exception:
            pass
        try:
            if self._out:
                self._out.close_port()
        except Exception:
            pass
        log.info("%s MIDI shut down", self.device_name)


def find_p6_ports(port_hint: str = "P-6"):
    """Find and open P-6 MIDI ports. Returns (MidiIn, MidiOut) or (None, None)."""
    if rtmidi is None:
        return None, None

    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()

    in_port = out_port = None

    for i in range(midi_in.get_port_count()):
        name = midi_in.get_port_name(i)
        if port_hint in name and "Through" not in name:
            in_port = i
            log.info("P-6 MIDI in: %s", name)
            break

    for i in range(midi_out.get_port_count()):
        name = midi_out.get_port_name(i)
        if port_hint in name and "Through" not in name:
            out_port = i
            log.info("P-6 MIDI out: %s", name)
            break

    if in_port is None and out_port is None:
        try:
            midi_in.delete()
        except Exception:
            pass
        try:
            midi_out.delete()
        except Exception:
            pass
        return None, None

    if in_port is not None:
        midi_in.open_port(in_port)
    else:
        try:
            midi_in.delete()
        except Exception:
            pass
        midi_in = None

    if out_port is not None:
        midi_out.open_port(out_port)
    else:
        try:
            midi_out.delete()
        except Exception:
            pass
        midi_out = None

    return midi_in, midi_out
