"""
Roland AIRA Compact P-6 MIDI interface.

Manages the MIDI connection to the P-6, tracks parameter state,
and provides methods to send CCs, notes, program changes, and clock.

P-6 MIDI channels (defaults):
  - ch4:  Granular sampler
  - ch11: Sample pads (notes 48-95)
  - ch15: Auto (currently selected pad)
  - ch16: Program change (patterns 0-63)

USB audio: 48kHz / 24-bit / stereo (class-compliant)
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
    """MIDI interface to the Roland P-6.

    Handles:
    - Sending CC, note, program change, and transport messages
    - Receiving MIDI from P-6 (clock, notes, CC feedback)
    - Tracking P-6 parameter state
    """

    def __init__(self, midi_in: "rtmidi.MidiIn", midi_out: "rtmidi.MidiOut") -> None:
        if rtmidi is None:
            raise RuntimeError("python-rtmidi not installed")

        self._in = midi_in
        self._out = midi_out
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self.state = P6State()

        # Callbacks
        self.on_note: Optional[Callable[[int, int, int], None]] = None  # (channel, note, velocity)
        self.on_cc: Optional[Callable[[int, int, int], None]] = None    # (channel, cc, value)
        self.on_clock_tick: Optional[Callable[[], None]] = None
        self.on_transport: Optional[Callable[[str], None]] = None       # "start"/"stop"/"continue"

        # Filter timing but keep clock
        self._in.ignore_types(sysex=True, timing=False, active_sense=True)

        self._start_polling()
        log.info("P-6 MIDI interface started")

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
                            if cc in ALL_P6_CCS:
                                self.state.cc_values[cc] = val
                        if self.on_cc:
                            self.on_cc(channel, cc, val)

                    # Program change
                    elif msg_type == 0xC0:
                        prog = data[1]
                        self.state.active_pattern = prog

                else:
                    time.sleep(0.001)

            except Exception:
                log.exception("P-6 MIDI poll error")
                time.sleep(0.1)

    # ── Send methods ────────────────────────────────────────────────────

    def send_cc(self, cc: int, value: int, channel: int = CH_AUTO) -> None:
        """Send a CC message to the P-6."""
        value = max(0, min(127, value))
        if self._out:
            self._out.send_message([0xB0 | channel, cc, value])
        with self._lock:
            if cc in ALL_P6_CCS:
                self.state.cc_values[cc] = value

    def send_note_on(self, note: int, velocity: int = 100,
                     channel: int = CH_SAMPLER) -> None:
        """Send note on to P-6 (default ch11 for sample pads)."""
        note = max(0, min(127, note))
        velocity = max(0, min(127, velocity))
        if self._out:
            self._out.send_message([0x90 | channel, note, velocity])

    def send_note_off(self, note: int, channel: int = CH_SAMPLER) -> None:
        """Send note off to P-6."""
        note = max(0, min(127, note))
        if self._out:
            self._out.send_message([0x80 | channel, note, 0])

    def send_program_change(self, program: int,
                           channel: int = CH_PROGRAM) -> None:
        """Send program change to select P-6 pattern (0-63)."""
        program = max(0, min(63, program))
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

    # ── State queries ───────────────────────────────────────────────────

    def get_cc_value(self, cc: int) -> int:
        """Get the current value of a CC parameter."""
        with self._lock:
            return self.state.cc_values.get(cc, 0)

    def get_category_values(self, category: str) -> Dict[str, int]:
        """Get all CC values for a category as {name: value}."""
        result = {}
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
        log.info("P-6 MIDI shut down")


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
        return None, None

    if in_port is not None:
        midi_in.open_port(in_port)
    else:
        midi_in = None

    if out_port is not None:
        midi_out.open_port(out_port)
    else:
        midi_out = None

    return midi_in, midi_out
