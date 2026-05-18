"""MIDI Controller Mapper — route external controllers to device CCs.

Detects external MIDI controllers (Midi Fighter Twister, Spectra,
generic knob controllers) and maps their CC output to SP-404 FX
buses, P-6 parameters, or any target device.

Solves the "SP 404 Unfucker" problem: external controllers send CCs
on one channel, Compa translates and routes them to the correct
multi-channel bus architecture.

Usage::

    mapper = MidiMapper()
    mapper.detect_controllers()
    mapper.auto_map_sp404()  # Maps first 8 knobs to Bus 1 FX
    mapper.start()
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    import rtmidi
except ImportError:
    rtmidi = None

log = logging.getLogger(__name__)

# Known controller profiles
KNOWN_CONTROLLERS = {
    "Midi Fighter Twister": {
        "hint": "Midi Fighter Twister",
        "num_knobs": 16,
        "num_buttons": 16,
        "cc_range": (0, 15),  # Knobs send CC 0-15
        "has_rgb": True,
    },
    "Midi Fighter Spectra": {
        "hint": "Midi Fighter Spectra",
        "num_knobs": 0,
        "num_buttons": 16,
        "cc_range": (0, 15),
        "has_rgb": True,
    },
    "nanoKONTROL": {
        "hint": "nanoKONTROL",
        "num_knobs": 8,
        "num_buttons": 16,
        "cc_range": (0, 7),
        "has_rgb": False,
    },
}


@dataclass
class CCMapping:
    """One CC mapping: source → target."""
    src_cc: int          # CC number from controller
    src_channel: int     # MIDI channel from controller (0-indexed)
    dst_cc: int          # CC to send to target device
    dst_channel: int     # MIDI channel on target device
    label: str = ""      # Display label
    min_val: int = 0     # Output range min
    max_val: int = 127   # Output range max
    invert: bool = False # Flip the value


@dataclass
class MacroAction:
    """One action in a macro — a CC value to send."""
    cc: int
    value: int
    channel: int
    delay_ms: int = 0  # Delay before sending (for sequenced macros)


@dataclass
class Macro:
    """A macro — multiple CC actions triggered by one button."""
    name: str
    trigger_cc: int           # CC from controller that triggers this
    trigger_channel: int = 0
    actions: list[MacroAction] = field(default_factory=list)
    toggle: bool = False      # If True, alternates between on/off states
    _state: bool = False      # Current toggle state


class MidiMapper:
    """Maps external MIDI controllers to target devices."""

    def __init__(self):
        self._controller_in = None
        self._controller_out = None
        self._target_midi = None  # P6Midi instance for the target device
        self._mappings: list[CCMapping] = []
        self._macros: list[Macro] = []
        self._running = False
        self._thread = None
        self._controller_name = ""
        self._controller_profile = None

        # Detection cache (avoid creating rtmidi objects every frame)
        self._last_scan = 0.0
        self._cached_controllers: Optional[list] = None

        # Callback for UI updates
        self.on_cc_received: Optional[Callable[[int, int, int], None]] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def controller_name(self) -> str:
        return self._controller_name

    @property
    def mappings(self) -> list[CCMapping]:
        return list(self._mappings)

    @property
    def macros(self) -> list[Macro]:
        return list(self._macros)

    def detect_controllers(self) -> list[dict]:
        """Scan for external MIDI controllers (not SP-404, P-6, etc.).

        Caches result to avoid creating new rtmidi objects every frame.
        """
        now = time.monotonic()
        if now - self._last_scan < 3.0 and self._cached_controllers is not None:
            return self._cached_controllers

        self._last_scan = now

        if rtmidi is None:
            self._cached_controllers = []
            return []

        mi = None
        try:
            mi = rtmidi.MidiIn()
            found = []
            device_hints = {"SP-404", "P-6", "Through", "RtMidi", "ATOM", "Force"}

            for i in range(mi.get_port_count()):
                name = mi.get_port_name(i)
                if any(h in name for h in device_hints):
                    continue
                profile = None
                prof_name = "Generic"
                for pn, prof in KNOWN_CONTROLLERS.items():
                    if prof["hint"] in name:
                        profile = prof
                        prof_name = pn
                        break
                found.append({
                    "index": i,
                    "name": name,
                    "profile": profile,
                    "profile_name": prof_name,
                })
        except Exception:
            self._cached_controllers = []
            return []
        finally:
            if mi is not None:
                try:
                    mi.delete()
                except Exception:
                    pass

        self._cached_controllers = found
        return found

    def connect_controller(self, port_name_hint: str) -> bool:
        """Connect to a specific controller by name hint."""
        if rtmidi is None:
            return False

        mi = None
        mo = None
        try:
            mi = rtmidi.MidiIn()
            mo = rtmidi.MidiOut()

            in_port = out_port = None
            for i in range(mi.get_port_count()):
                if port_name_hint in mi.get_port_name(i):
                    in_port = i
                    self._controller_name = mi.get_port_name(i)
                    break

            for i in range(mo.get_port_count()):
                if port_name_hint in mo.get_port_name(i):
                    out_port = i
                    break
        finally:
            if mi is not None:
                try:
                    mi.delete()
                except Exception:
                    pass
            if mo is not None:
                try:
                    mo.delete()
                except Exception:
                    pass

        if in_port is None:
            return False

        self._controller_in = rtmidi.MidiIn()
        self._controller_in.open_port(in_port)
        self._controller_in.ignore_types(sysex=True, timing=True, active_sense=True)

        if out_port is not None:
            self._controller_out = rtmidi.MidiOut()
            self._controller_out.open_port(out_port)

        # Find profile
        for prof_name, prof in KNOWN_CONTROLLERS.items():
            if prof["hint"] in self._controller_name:
                self._controller_profile = prof
                break

        log.info("Controller connected: %s", self._controller_name)
        return True

    def set_target(self, midi_out):
        """Set the target device's P6Midi instance."""
        self._target_midi = midi_out

    def add_mapping(self, src_cc: int, dst_cc: int, dst_channel: int,
                    label: str = "", src_channel: int = 0) -> CCMapping:
        """Add a CC mapping."""
        m = CCMapping(src_cc=src_cc, src_channel=src_channel,
                     dst_cc=dst_cc, dst_channel=dst_channel, label=label)
        self._mappings.append(m)
        return m

    def clear_mappings(self):
        self._mappings.clear()

    def add_macro(self, name: str, trigger_cc: int,
                  actions: list[tuple], toggle: bool = False) -> Macro:
        """Add a macro. Actions are (cc, value, channel) tuples."""
        macro = Macro(
            name=name,
            trigger_cc=trigger_cc,
            actions=[MacroAction(cc=a[0], value=a[1], channel=a[2])
                    for a in actions],
            toggle=toggle,
        )
        self._macros.append(macro)
        return macro

    def auto_map_sp404(self):
        """Auto-map controller knobs to SP-404 Bus 1 FX parameters.

        Maps first 8 knobs to:
          CC0→FX Select (CC83 Ch1), CC1→FX On/Off (CC19 Ch1),
          CC2→Ctrl1 (CC16 Ch1), CC3→Ctrl2 (CC17 Ch1),
          CC4→Ctrl3 (CC18 Ch1), CC5→Ctrl4 (CC80 Ch1),
          CC6→Ctrl5 (CC81 Ch1), CC7→Ctrl6 (CC82 Ch1)

        Knobs 8-15 map to Bus 2 (same CCs, Ch2).
        """
        self.clear_mappings()

        # Bus 1 (Ch1 = channel 0)
        bus1_map = [
            (0, 83, "FX Select"), (1, 19, "FX On/Off"),
            (2, 16, "Ctrl 1"), (3, 17, "Ctrl 2"),
            (4, 18, "Ctrl 3"), (5, 80, "Ctrl 4"),
            (6, 81, "Ctrl 5"), (7, 82, "Ctrl 6"),
        ]
        for src, dst, label in bus1_map:
            self.add_mapping(src, dst, dst_channel=0, label=f"B1 {label}")

        # Bus 2 (Ch2 = channel 1)
        bus2_map = [
            (8, 83, "FX Select"), (9, 19, "FX On/Off"),
            (10, 16, "Ctrl 1"), (11, 17, "Ctrl 2"),
            (12, 18, "Ctrl 3"), (13, 80, "Ctrl 4"),
            (14, 81, "Ctrl 5"), (15, 82, "Ctrl 6"),
        ]
        for src, dst, label in bus2_map:
            self.add_mapping(src, dst, dst_channel=1, label=f"B2 {label}")

        log.info("Auto-mapped 16 knobs to SP-404 Bus 1+2")

    def start(self):
        """Start the mapping thread."""
        if self._running or not self._controller_in:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log.info("Mapper started")

    def stop(self):
        """Stop the mapping thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._controller_in:
            self._controller_in.close_port()
            self._controller_in = None
        if self._controller_out:
            self._controller_out.close_port()
            self._controller_out = None
        log.info("Mapper stopped")

    def _poll_loop(self):
        """Poll for MIDI input and route to target."""
        while self._running:
            msg = self._controller_in.get_message()
            if msg:
                data, _ = msg
                if len(data) >= 3:
                    status = data[0] & 0xF0
                    channel = data[0] & 0x0F

                    if status == 0xB0:  # CC
                        cc = data[1]
                        val = data[2]
                        self._handle_cc(channel, cc, val)

                    elif status == 0x90:  # Note On
                        note = data[1]
                        vel = data[2]
                        self._handle_note(channel, note, vel)
            else:
                time.sleep(0.001)

    def _handle_cc(self, channel: int, cc: int, value: int):
        """Route a CC from the controller to the target device."""
        # Check mappings
        for m in self._mappings:
            if m.src_cc == cc and m.src_channel == channel:
                out_val = value
                if m.invert:
                    out_val = 127 - value
                out_val = max(m.min_val, min(m.max_val, out_val))
                if self._target_midi:
                    self._target_midi.send_cc(m.dst_cc, out_val,
                                              channel=m.dst_channel)
                if self.on_cc_received:
                    self.on_cc_received(m.dst_cc, out_val, m.dst_channel)
                return

        # Check macros
        for macro in self._macros:
            if macro.trigger_cc == cc and macro.trigger_channel == channel:
                if value >= 64:  # Trigger on press
                    self._fire_macro(macro)
                return

    def _handle_note(self, channel: int, note: int, velocity: int):
        """Handle note messages (for pad controllers)."""
        # Check macros triggered by notes
        for macro in self._macros:
            if macro.trigger_cc == note + 128:  # Notes offset by 128
                if velocity > 0:
                    self._fire_macro(macro)
                return

    def _fire_macro(self, macro: Macro):
        """Execute a macro — send all its CC actions."""
        if macro.toggle:
            macro._state = not macro._state

        for action in macro.actions:
            val = action.value
            if macro.toggle and not macro._state:
                val = 0  # Toggle off = send 0
            if self._target_midi:
                self._target_midi.send_cc(action.cc, val,
                                          channel=action.channel)
            if action.delay_ms > 0:
                time.sleep(action.delay_ms / 1000.0)

        log.info("Macro fired: %s (%d actions)", macro.name, len(macro.actions))

    def send_led(self, cc: int, color: int, channel: int = 0):
        """Send LED color to controller (if supported)."""
        if self._controller_out:
            self._controller_out.send_message([0xB0 | channel, cc, color])
