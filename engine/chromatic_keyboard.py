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
    "Force", "Push 2", "Ableton Push", "Midi Fighter Twister", "Midi Fighter Spectra",
    # Network-export/virtual session ports are not physical keyboards.
    "rtpmidid", "Network Export", "Announcements",
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

        # Pitch-bend routing mode (for SP-404 bank-channel chromatic)
        # When enabled, notes are sent on _pad_channel as _pad_note with
        # pitch bend to shift pitch, instead of using _target_channel.
        self._pitchbend_mode = False
        self._pad_channel = 0    # bank channel (Ch1=0 for bank A)
        self._pad_note = 36      # the pad's note number on that channel
        self._root_midi = 60     # piano root note = no pitch shift
        self._bend_range = 12    # pitch bend range in semitones (±12)

        # State
        self.active_notes: dict[int, int] = {}  # note → velocity
        self.octave_shift: int = 0               # -3 to +3
        self.enabled: bool = False               # Only forward when True
        self._held_pad_note = False  # whether pad note is currently sounding

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

    def set_target(self, midi_out, channel: int,
                   pitchbend_mode: bool = False):
        """Set the output destination and channel.

        Called by the app when the focused device changes.
        Releases all held notes first to avoid stuck notes.

        If pitchbend_mode is True, chromatic notes are routed through
        the pad's bank channel using pitch bend instead of a dedicated
        chromatic channel. Call set_pad() after this to set the pad.
        """
        self._all_notes_off()
        self._target_midi = midi_out
        self._target_channel = channel
        self._pitchbend_mode = pitchbend_mode

    def set_pad(self, channel: int, note: int, root_midi: int = 60):
        """Set the active pad for SP-404 chromatic mode.

        channel: the pad's bank MIDI channel (0-indexed, e.g. 0 = Ch1 = Bank A)
        note:    the pad's note number on that channel (36-51 for SP-404)
        root_midi: which piano key = no pitch shift (default 60 = C3)

        Releases any held pad trigger, sends all-notes-off on Ch16,
        then prepares to trigger the new pad on next note-on.
        """
        # Release any currently held pad trigger
        if self._held_pad_note and self._target_midi:
            try:
                self._target_midi.send_note_off(self._pad_note,
                                                 channel=self._pad_channel)
            except Exception:
                pass
            self._held_pad_note = False

        self._all_notes_off()
        self._pad_channel = channel
        self._pad_note = note
        self._root_midi = root_midi

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
        # Anything already claimed by the controller mapper — or that
        # WOULD be claimed by a loaded profile on the next scan — is
        # off-limits. This prevents timing races where the chromatic
        # keyboard grabs a profiled controller in the 2s window before
        # the mapper's scan runs.
        mapper = getattr(self, "_controller_mapper", None)
        if mapper is not None:
            try:
                for claimed in mapper.claimed_port_hints():
                    if claimed.lower() == lower:
                        return True
                if mapper.port_matches_any_profile(port_name):
                    return True
            except Exception:
                pass
        return False

    def _scan_and_connect(self) -> bool:
        """Scan rtmidi ports and connect to the first generic keyboard.

        Backoff: each call to rtmidi.MidiIn() opens an ALSA seq client.
        On a healthy system that's free, but on a Pi where /dev/snd/seq is
        starved (kernel resource exhausted) every attempt logs and may leak
        another seq client slot. We back off exponentially on consecutive
        failures + rate-limit the log so we don't fill the journal or pile
        kernel pressure on a system that's already starved.
        """
        import time as _time
        now = _time.monotonic()
        # Lazy-init backoff state
        if not hasattr(self, "_scan_next_at"):
            self._scan_next_at = 0.0
            self._scan_attempts = 0
            self._scan_last_log = 0.0
        if now < self._scan_next_at:
            return False

        try:
            midi_in = rtmidi.MidiIn()
            ports = midi_in.get_ports()
            if not ports:
                midi_in.delete()
                # No ports is normal (no keyboard plugged in) — short backoff
                self._scan_attempts = 0
                self._scan_next_at = now + 2.0
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
                    # Reset backoff on success
                    self._scan_attempts = 0
                    self._scan_next_at = 0.0
                    return True
                except Exception as e:
                    log.warning("ChromaticKB open port %d failed: %s", i, e)
                    continue

            midi_in.delete()
            # No matching candidate among the visible ports — short backoff
            self._scan_attempts = 0
            self._scan_next_at = now + 2.0
            return False
        except Exception as e:
            # Most common failure here is "open /dev/snd/seq failed: Cannot
            # allocate memory" when the kernel ALSA seq table is exhausted.
            # Each retry leaks more — backoff hard.
            self._scan_attempts += 1
            backoff = min(120.0, 2.0 * (2 ** max(0, self._scan_attempts - 1)))
            self._scan_next_at = now + backoff
            if now - self._scan_last_log > 60.0:
                log.warning("ChromaticKB scan error (%s); backing off %.0fs",
                            e, backoff)
                self._scan_last_log = now
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
                    # 5ms idle sleep — well under USB-MIDI latency,
                    # 5x lower CPU than a 1ms poll.
                    time.sleep(0.005)
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
        if not self._target_midi:
            return

        if self._pitchbend_mode:
            # SP-404 mode: the pad trigger on the bank channel "selects"
            # the sample, then we play chromatically on Ch16.
            #
            # SP-404 only accepts pitch bend on Ch16, not on bank channels
            # (Ch1-10). So we trigger the pad on its bank channel to make
            # it the active sample, then send the chromatic note on Ch16.
            if not self._held_pad_note:
                # Trigger the pad on the bank channel and HOLD it
                self._target_midi.send_note_on(self._pad_note, 1,
                                                channel=self._pad_channel)
                self._held_pad_note = True
            # Send chromatic note on Ch16 (channel 15, 0-indexed)
            self._target_midi.send_note_on(note, velocity, channel=15)
        else:
            # Direct chromatic: send the note number on the target channel
            self._target_midi.send_note_on(note, velocity,
                                            channel=self._target_channel)

    def _forward_note_off(self, note: int):
        if not self._target_midi:
            return

        if self._pitchbend_mode:
            # Release the chromatic note on Ch16
            self._target_midi.send_note_off(note, channel=15)
            # Keep the pad trigger held so subsequent notes still play
            # the same pad. It gets released when pad selection changes
            # or when _all_notes_off is called.
        else:
            self._target_midi.send_note_off(note,
                                             channel=self._target_channel)

    def _send_pitch_bend(self, semitones: int):
        """Send a pitch bend message for the given semitone offset.

        Assumes ±12 semitone bend range (SP-404 default).
        MIDI pitch bend: 14-bit, 0-16383, center = 8192.
        """
        if not self._target_midi or not self._target_midi._out:
            return
        # Clamp to bend range
        semitones = max(-self._bend_range, min(self._bend_range, semitones))
        # Map semitones to 14-bit pitch bend
        # Center (8192) = no shift, 0 = -bend_range, 16383 = +bend_range
        ratio = semitones / self._bend_range  # -1.0 to +1.0
        bend_val = int(8192 + ratio * 8191)
        bend_val = max(0, min(16383, bend_val))
        lsb = bend_val & 0x7F
        msb = (bend_val >> 7) & 0x7F
        self._target_midi._out.send_message(
            [0xE0 | self._pad_channel, lsb, msb])

    def _all_notes_off(self):
        """Release all held notes. Called on disconnect, mode switch, retarget."""
        if self._target_midi:
            # Release held pad trigger (bank channel)
            if self._pitchbend_mode and self._held_pad_note:
                try:
                    self._target_midi.send_note_off(self._pad_note,
                                                     channel=self._pad_channel)
                except Exception:
                    pass
                self._held_pad_note = False

            # Release all tracked chromatic notes
            for note in list(self.active_notes.keys()):
                try:
                    if self._pitchbend_mode:
                        # Chromatic notes went to Ch16
                        self._target_midi.send_note_off(note, channel=15)
                    else:
                        self._target_midi.send_note_off(note,
                                                         channel=self._target_channel)
                except Exception:
                    pass
        self.active_notes.clear()
