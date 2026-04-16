"""Chromatic keyboard — generic MIDI keyboard input for melodic play.

Auto-detects any USB MIDI keyboard that isn't a known Compa device
(SP-404, P-6, ATOM SQ, Twister, Spectra, Force), and forwards notes
chromatically to the focused device on its designated channel:
  SP-404 MK2: MIDI Ch 16 (chromatic pad play)
  P-6:        MIDI Ch 4  (granular engine)

Thread model matches MidiInput: single daemon thread, 2s scan interval
when disconnected, 1ms poll when connected. Hot-plug/unplug is tracked.
"""

import logging
import threading
import time
from typing import Callable, Optional

try:
    import rtmidi
except ImportError:
    rtmidi = None

log = logging.getLogger(__name__)

# Port names containing any of these are NOT generic keyboards.
# These are devices that Compa already handles through their own modules.
EXCLUDED_PORT_HINTS = {
    "SP-404", "P-6", "Through", "RtMidi", "ATOM", "ATM SQ",
    "Force", "Midi Fighter Twister", "Midi Fighter Spectra",
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(midi_note: int) -> str:
    """Return human-readable note name: 'C4', 'F#2', etc."""
    octave = (midi_note // 12) - 1
    name = NOTE_NAMES[midi_note % 12]
    return f"{name}{octave}"


class ChromaticKeyboard:
    """Generic MIDI keyboard input with chromatic forwarding."""

    def __init__(self):
        self._midi_in = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._device_name = ""

        # Output target — set by the app when the focus changes
        self._target_midi = None   # P6Midi instance
        self._target_channel = 15  # 0-indexed (Ch 16 for SP-404)

        # State
        self.active_notes: dict[int, int] = {}  # note → velocity
        self.octave_shift: int = 0               # -3 to +3
        self.enabled: bool = False               # Only forward when True

        # UI callbacks (called from the MIDI thread — keep them fast)
        self.on_note_on: Optional[Callable[[int, int], None]] = None
        self.on_note_off: Optional[Callable[[int], None]] = None
        self.on_connect: Optional[Callable[[str], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

    # ── Properties ───────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def device_name(self) -> str:
        return self._device_name

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self):
        """Start the background scan/poll thread."""
        if rtmidi is None:
            print("ChromaticKB: rtmidi not installed — disabled", flush=True)
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._all_notes_off()
        self._close_port()

    def set_target(self, midi_out, channel: int):
        """Set the output destination and channel.

        Called by the app when the focused device changes.
        Releases all held notes first to avoid stuck notes.
        """
        self._all_notes_off()
        self._target_midi = midi_out
        self._target_channel = channel

    # ── Port management ──────────────────────────────────────────────

    def _close_port(self):
        if self._midi_in is not None:
            try:
                self._midi_in.close_port()
            except Exception:
                pass
            self._midi_in = None
        if self._connected:
            self._connected = False
            self._device_name = ""
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    pass

    def _is_excluded(self, port_name: str) -> bool:
        lower = port_name.lower()
        for hint in EXCLUDED_PORT_HINTS:
            if hint.lower() in lower:
                return True
        return False

    def _scan_and_connect(self) -> bool:
        """Scan rtmidi ports and connect to the first generic keyboard."""
        try:
            midi_in = rtmidi.MidiIn()
            ports = midi_in.get_ports()
            if not ports:
                midi_in.delete()
                return False

            for i, name in enumerate(ports):
                if self._is_excluded(name):
                    continue
                # Skip virtual/through
                lower = name.lower()
                if "virtual" in lower:
                    continue
                # Found a candidate — open it
                try:
                    midi_in.open_port(i)
                    midi_in.ignore_types(sysex=True, timing=True,
                                         active_sense=True)
                    self._midi_in = midi_in
                    self._connected = True
                    self._device_name = name
                    log.info("ChromaticKB connected: %s", name)
                    print(f"ChromaticKB: {name}", flush=True)
                    if self.on_connect:
                        self.on_connect(name)
                    return True
                except Exception as e:
                    log.warning("ChromaticKB open port %d failed: %s", i, e)
                    continue

            midi_in.delete()
            return False
        except Exception as e:
            log.warning("ChromaticKB scan error: %s", e)
            return False

    # ── Main thread ──────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            if not self._connected:
                if self._scan_and_connect():
                    continue
                time.sleep(2.0)
                continue

            try:
                msg = self._midi_in.get_message()
                if msg:
                    data, _ = msg
                    self._handle_message(data)
                else:
                    time.sleep(0.001)
            except Exception:
                # Device disconnected
                log.info("ChromaticKB disconnected")
                print("ChromaticKB: disconnected", flush=True)
                self._all_notes_off()
                self._close_port()
                time.sleep(1.0)

    # ── Message handling ─────────────────────────────────────────────

    def _handle_message(self, data: list):
        if len(data) < 2:
            return

        status = data[0] & 0xF0
        # We ignore the incoming channel — always re-route to target channel

        if status == 0x90 and len(data) >= 3:
            note, velocity = data[1], data[2]
            if velocity > 0:
                shifted = self._apply_shift(note)
                if self.enabled:
                    self._forward_note_on(shifted, velocity)
                self.active_notes[shifted] = velocity
                if self.on_note_on:
                    self.on_note_on(shifted, velocity)
            else:
                # Note On with velocity 0 = Note Off
                shifted = self._apply_shift(note)
                if self.enabled:
                    self._forward_note_off(shifted)
                self.active_notes.pop(shifted, None)
                if self.on_note_off:
                    self.on_note_off(shifted)

        elif status == 0x80 and len(data) >= 3:
            note = data[1]
            shifted = self._apply_shift(note)
            if self.enabled:
                self._forward_note_off(shifted)
            self.active_notes.pop(shifted, None)
            if self.on_note_off:
                self.on_note_off(shifted)

        elif status == 0xB0 and len(data) >= 3:
            # CC — forward on target channel
            if self.enabled and self._target_midi:
                self._target_midi.send_cc(data[1], data[2],
                                          channel=self._target_channel)

        elif status == 0xE0 and len(data) >= 3:
            # Pitch bend — forward raw
            if self.enabled and self._target_midi and self._target_midi._out:
                self._target_midi._out.send_message(
                    [0xE0 | self._target_channel, data[1], data[2]])

    def _apply_shift(self, note: int) -> int:
        shifted = note + (self.octave_shift * 12)
        return max(0, min(127, shifted))

    # ── Forwarding ───────────────────────────────────────────────────

    def _forward_note_on(self, note: int, velocity: int):
        if self._target_midi:
            self._target_midi.send_note_on(note, velocity,
                                            channel=self._target_channel)

    def _forward_note_off(self, note: int):
        if self._target_midi:
            self._target_midi.send_note_off(note,
                                             channel=self._target_channel)

    def _all_notes_off(self):
        """Release all held notes. Called on disconnect, mode switch, retarget."""
        if self._target_midi:
            for note in list(self.active_notes.keys()):
                try:
                    self._target_midi.send_note_off(note,
                                                     channel=self._target_channel)
                except Exception:
                    pass
        self.active_notes.clear()
