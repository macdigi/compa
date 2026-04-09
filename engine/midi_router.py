"""
MIDI routing hub — translates ATOM SQ input into P-6 commands.

The ATOM SQ acts as the physical controller, the P-6 is the sound source.
The router sits between them, mapping pads/buttons/touchstrip to P-6
notes, CCs, program changes, and transport based on the active layer.

Layers:
  - PAD:     Bottom 16 pads → P-6 notes 48-63, top 16 → 64-79
  - PATTERN: 32 pads → P-6 patterns 0-31 (bank button for 32-63)
  - CONTROL: Touchstrip → focused CC parameter

Transport buttons (play/stop/record) always route to P-6 transport.
Bank button always cycles ATOM SQ pad layer or pattern page.
"""

import logging
from enum import Enum
from typing import Callable, Optional

from .atom_sq import AtomSQ
from .p6_midi import P6Midi, CH_SAMPLER, CH_AUTO, CH_PROGRAM, PAD_NOTE_LO

log = logging.getLogger(__name__)


class Layer(Enum):
    PAD = "pad"
    PATTERN = "pattern"
    CONTROL = "control"


class MidiRouter:
    """Routes ATOM SQ events to P-6 MIDI commands."""

    def __init__(self, atom_sq: AtomSQ, p6: P6Midi) -> None:
        self.atom = atom_sq
        self.p6 = p6

        self._layer = Layer.PAD
        self._pattern_page = 0  # 0 = patterns 0-31, 1 = patterns 32-63
        self._focused_cc: int = 74  # Default: filter cutoff
        self._held_notes: set[int] = set()

        # UI callbacks (for screen updates)
        self.on_layer_change: Optional[Callable[[Layer], None]] = None
        self.on_pad_trigger: Optional[Callable[[int, float], None]] = None
        self.on_pattern_select: Optional[Callable[[int], None]] = None
        self.on_transport: Optional[Callable[[str], None]] = None
        self.on_cc_sent: Optional[Callable[[int, int], None]] = None

        # Wire up ATOM SQ callbacks
        self.atom.on_pad_hit = self._on_pad_hit
        self.atom.on_pad_release = self._on_pad_release
        self.atom.on_button = self._on_button
        self.atom.on_touchstrip = self._on_touchstrip

    @property
    def layer(self) -> Layer:
        return self._layer

    @layer.setter
    def layer(self, value: Layer) -> None:
        if value != self._layer:
            # Release any held notes when switching layers
            self._release_all_held()
            self._layer = value
            log.info("Layer: %s", value.value)
            if self.on_layer_change:
                self.on_layer_change(value)

    @property
    def focused_cc(self) -> int:
        return self._focused_cc

    @focused_cc.setter
    def focused_cc(self, cc: int) -> None:
        self._focused_cc = cc

    # ── Pad handlers ────────────────────────────────────────────────────

    def _on_pad_hit(self, pad_index: int, velocity: float) -> None:
        """ATOM SQ pad hit → route based on layer."""

        if self._layer == Layer.PAD:
            # Map ATOM SQ pads 0-31 to P-6 notes 48-79
            note = PAD_NOTE_LO + pad_index
            if note <= 95:  # Stay within P-6 range
                vel_midi = max(1, min(127, int(velocity * 127)))
                self.p6.send_note_on(note, vel_midi, CH_SAMPLER)
                self._held_notes.add(note)
                if self.on_pad_trigger:
                    self.on_pad_trigger(pad_index, velocity)

        elif self._layer == Layer.PATTERN:
            # Map pads 0-31 to patterns, offset by page
            pattern = pad_index + (self._pattern_page * 32)
            if 0 <= pattern <= 63:
                self.p6.send_program_change(pattern)
                if self.on_pattern_select:
                    self.on_pattern_select(pattern)

        elif self._layer == Layer.CONTROL:
            # Pads send CC value based on velocity (0-127)
            val = max(0, min(127, int(velocity * 127)))
            self.p6.send_cc(self._focused_cc, val)
            if self.on_cc_sent:
                self.on_cc_sent(self._focused_cc, val)

    def _on_pad_release(self, pad_index: int) -> None:
        """ATOM SQ pad release → send note off in pad layer."""
        if self._layer == Layer.PAD:
            note = PAD_NOTE_LO + pad_index
            if note in self._held_notes:
                self.p6.send_note_off(note, CH_SAMPLER)
                self._held_notes.discard(note)

    def _release_all_held(self) -> None:
        for note in list(self._held_notes):
            self.p6.send_note_off(note, CH_SAMPLER)
        self._held_notes.clear()

    # ── Button handlers ─────────────────────────────────────────────────

    def _on_button(self, name: str, pressed: bool) -> None:
        """ATOM SQ button → transport, bank, or navigation."""
        if not pressed:
            return

        # Transport buttons always work regardless of layer
        if name == "play":
            self.p6.send_start()
            if self.on_transport:
                self.on_transport("play")

        elif name == "stop":
            self.p6.send_stop()
            self._release_all_held()
            if self.on_transport:
                self.on_transport("stop")

        elif name == "record":
            # Record toggles the Pi's audio recorder, not P-6 transport
            if self.on_transport:
                self.on_transport("record")

        elif name == "bank":
            if self._layer == Layer.PATTERN:
                # Toggle pattern page 0/1
                self._pattern_page = 1 - self._pattern_page
                log.info("Pattern page: %d (patterns %d-%d)",
                         self._pattern_page,
                         self._pattern_page * 32,
                         self._pattern_page * 32 + 31)
            else:
                # Cycle layers: PAD → PATTERN → CONTROL → PAD
                layers = [Layer.PAD, Layer.PATTERN, Layer.CONTROL]
                idx = layers.index(self._layer)
                self.layer = layers[(idx + 1) % len(layers)]

        # Navigation — pass through to UI
        elif name in ("up", "down", "click", "select", "shift",
                      "btn_a", "btn_b",
                      "soft_1", "soft_2", "soft_3", "soft_4",
                      "soft_5", "soft_6"):
            # These are handled by the app for screen navigation
            # We expose them via a separate callback
            if self._ui_button_cb:
                self._ui_button_cb(name)

    # ── Touchstrip handler ──────────────────────────────────────────────

    def _on_touchstrip(self, position: int) -> None:
        """ATOM SQ touchstrip → send focused CC to P-6."""
        self.p6.send_cc(self._focused_cc, position)
        if self.on_cc_sent:
            self.on_cc_sent(self._focused_cc, position)

    # ── UI button callback ──────────────────────────────────────────────

    _ui_button_cb: Optional[Callable[[str], None]] = None

    def set_ui_button_callback(self, cb: Callable[[str], None]) -> None:
        """Set callback for navigation/UI buttons (up, down, soft_1, etc)."""
        self._ui_button_cb = cb
