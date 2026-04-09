"""MIDI controller input handling via python-rtmidi."""

import threading
import time
from typing import Callable, Optional

try:
    import rtmidi
except ImportError:
    rtmidi = None


MIDI_NOTE_ON = 0x90
MIDI_NOTE_OFF = 0x80
MIDI_CC = 0xB0

# Default MPC-style mapping: notes 36–51 = pads 0–15
DEFAULT_BASE_NOTE = 36


class MidiInput:
    """Handles USB MIDI input with auto-detection and reconnection."""

    def __init__(self, base_note: int = DEFAULT_BASE_NOTE):
        self.base_note = base_note
        self._midi_in: Optional[object] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._device_name: str = ""

        # Callbacks
        self.on_pad_trigger: Optional[Callable[[int, float], None]] = None  # (pad_index, velocity)
        self.on_pad_release: Optional[Callable[[int], None]] = None         # (pad_index,)
        self.on_cc: Optional[Callable[[int, int], None]] = None             # (cc_number, value)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def device_name(self) -> str:
        return self._device_name

    def start(self):
        """Start MIDI input thread with auto-detection."""
        if rtmidi is None:
            print("python-rtmidi not installed — MIDI disabled")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop MIDI input."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._close_port()

    def _close_port(self):
        if self._midi_in is not None:
            try:
                self._midi_in.close_port()
            except Exception:
                pass
            self._midi_in = None
        self._connected = False
        self._device_name = ""

    def _try_connect(self) -> bool:
        """Try to find and open a MIDI input port."""
        try:
            midi_in = rtmidi.MidiIn()
            ports = midi_in.get_ports()
            if not ports:
                midi_in.delete()
                return False

            # Pick the first available port (USB MIDI controller)
            for i, port_name in enumerate(ports):
                # Skip through/virtual ports
                lower = port_name.lower()
                if "through" in lower or "virtual" in lower:
                    continue
                midi_in.open_port(i)
                midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
                self._midi_in = midi_in
                self._connected = True
                self._device_name = port_name
                print(f"MIDI connected: {port_name}")
                return True

            # Fallback: use first port if no non-through port found
            if ports:
                midi_in.open_port(0)
                midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
                self._midi_in = midi_in
                self._connected = True
                self._device_name = ports[0]
                return True

            midi_in.delete()
            return False
        except Exception as e:
            print(f"MIDI connect error: {e}")
            return False

    def _run_loop(self):
        """Main MIDI polling loop — handles connection and message reading."""
        while self._running:
            if not self._connected:
                if self._try_connect():
                    continue
                else:
                    time.sleep(2.0)  # Retry connection every 2s
                    continue

            # Poll for messages
            try:
                msg = self._midi_in.get_message()
                if msg:
                    message, delta_time = msg
                    self._handle_message(message)
                else:
                    time.sleep(0.001)  # 1ms poll interval
            except Exception:
                # Device disconnected
                self._close_port()
                time.sleep(1.0)

    def _handle_message(self, message: list):
        """Process a raw MIDI message."""
        if len(message) < 3:
            return

        status = message[0] & 0xF0
        note = message[1]
        value = message[2]

        if status == MIDI_NOTE_ON and value > 0:
            pad_index = note - self.base_note
            if 0 <= pad_index < 16:
                velocity = value / 127.0
                if self.on_pad_trigger:
                    self.on_pad_trigger(pad_index, velocity)

        elif status == MIDI_NOTE_OFF or (status == MIDI_NOTE_ON and value == 0):
            pad_index = note - self.base_note
            if 0 <= pad_index < 16:
                if self.on_pad_release:
                    self.on_pad_release(pad_index)

        elif status == MIDI_CC:
            cc_num = message[1]
            cc_val = message[2]
            if self.on_cc:
                self.on_cc(cc_num, cc_val)
