"""Push 2 surface — stateless event source.

The surface knows nothing about modes, scales, clips, or sessions.
It receives raw MIDI bytes and emits typed events that higher layers
consume. It also provides typed LED + sysex write helpers that
higher layers call to update the device.

Higher layers (ui/push2_control.py) manage state. This file does not.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import constants as C
from . import sysex
from .midi import Push2Midi
from .palette import build_palette, upload_palette_messages
from .usb import Push2Display


# ── Event types ────────────────────────────────────────────────────

@dataclass(frozen=True)
class PadEvent:
    col: int
    row: int
    velocity: int        # 0 = release
    is_press: bool


@dataclass(frozen=True)
class PadAftertouchEvent:
    col: int
    row: int
    pressure: int        # 0–127


@dataclass(frozen=True)
class ChannelAftertouchEvent:
    pressure: int        # 0–127


@dataclass(frozen=True)
class ButtonEvent:
    cc: int
    name: str
    is_press: bool


@dataclass(frozen=True)
class EncoderTurnEvent:
    cc: int
    name: str
    delta: int           # signed; magnitude indicates speed


@dataclass(frozen=True)
class EncoderTouchEvent:
    note: int
    is_touched: bool


@dataclass(frozen=True)
class TouchStripEvent:
    value: int           # 0–16383 from pitch bend
    is_touch: bool       # True on touch / release events; False on motion


# ── The surface ────────────────────────────────────────────────────

class Push2Surface:
    """Stateless Push 2 event distributor + write API.

    Two operating modes:
    - Standalone: opens its own MIDI + USB via Push2Midi / Push2Display.
    - Hosted: Compa's existing engine/push2.py + engine/push2_display.py
      own the hardware; this is a shim that delegates LED writes through
      them and accepts events fed via dispatch_*() from the existing
      driver's callbacks.
    """

    def __init__(self, existing_push2=None, existing_display=None) -> None:
        self._on_event: Optional[Callable[[object], None]] = None
        self._palette = build_palette()
        self._lock = threading.Lock()
        self._last_clock_send = 0.0
        self._existing_push2 = existing_push2
        self._existing_display = existing_display

        if existing_push2 is None:
            self.midi = Push2Midi()
            self.display = Push2Display()
            self.midi.set_message_callback(self._dispatch)
            self._initialize_device()
        else:
            self.midi = None
            self.display = None
            # In hosted mode we still need our palette uploaded to the
            # device so the indices we send (5, 11, 21, …) light up the
            # colors we drew on the touchscreen. Skip the named slots
            # the existing Compa code relies on (0 black, 8 yellow,
            # 122–127 white/grays/RGB).
            self._upload_palette_via_existing()

    @property
    def available(self) -> bool:
        if self._existing_push2 is not None:
            return True
        return self.midi.available if self.midi is not None else False

    @property
    def palette(self) -> list[tuple[int, int, int, int]]:
        return self._palette

    def _upload_palette_via_existing(self) -> None:
        """Upload our palette through the existing Push 2 driver's
        output ports. Skip the slots Compa 1 depends on so the existing
        renderer keeps painting the colors it expects."""
        ex = self._existing_push2
        if ex is None:
            return
        protected = {0, 1, 3, 8, 122, 123, 124, 125, 126, 127}
        try:
            for idx, (r, g, b, w) in enumerate(self._palette):
                if idx in protected:
                    continue
                msg = sysex.set_palette_entry(idx, r, g, b, w)
                ex._send_both(list(msg))
            ex._send_both(list(sysex.reapply_palette()))
        except Exception as e:
            print(f"hosted palette upload failed: {e}", flush=True)

    def set_event_handler(self, fn: Callable[[object], None]) -> None:
        self._on_event = fn

    # ── Initialization sysex burst ─────────────────────────────────
    def _initialize_device(self) -> None:
        if not self.midi.available:
            return
        msgs: list[bytes] = []
        msgs.append(sysex.set_midi_mode(C.MIDI_MODE_LIVE))
        msgs.append(sysex.set_aftertouch_mode(C.AFTERTOUCH_POLY))
        msgs.append(sysex.set_led_brightness(127))
        msgs.append(sysex.set_display_brightness(255))
        msgs.append(sysex.set_touch_strip_config(
            led_control_host=False,
            host_sends_values=False,
            pitch_bend=True,
            bar_display=True,
            bar_starts_bottom=False,
            autoreturn_off=False,
            autoreturn_to_bottom=False,
        ))
        msgs.extend(upload_palette_messages(self._palette))
        self.midi.send_many(msgs)

    # ── Event dispatch ─────────────────────────────────────────────
    def _dispatch(self, msg: list[int]) -> None:
        if not msg:
            return
        cb = self._on_event
        if cb is None:
            return

        status = msg[0] & 0xF0
        chan = msg[0] & 0x0F
        ev: Optional[object] = None

        if status == 0x90:  # Note On
            note, vel = msg[1], msg[2]
            if C.PAD_NOTE_LOW <= note <= C.PAD_NOTE_HIGH:
                col, row = self._pad_coords(note)
                ev = PadEvent(col, row, vel, is_press=(vel > 0))
            elif note in C.ENCODER_TOUCH_TRACK_NOTES:
                ev = EncoderTouchEvent(note, is_touched=(vel > 0))
            elif note in (C.ENCODER_TOUCH_MASTER_NOTE,
                          C.ENCODER_TOUCH_SWING_NOTE,
                          C.ENCODER_TOUCH_TEMPO_NOTE):
                ev = EncoderTouchEvent(note, is_touched=(vel > 0))
            elif note == C.TOUCH_STRIP_TOUCH_NOTE:
                ev = TouchStripEvent(value=0, is_touch=(vel > 0))
        elif status == 0x80:  # Note Off
            note = msg[1]
            if C.PAD_NOTE_LOW <= note <= C.PAD_NOTE_HIGH:
                col, row = self._pad_coords(note)
                ev = PadEvent(col, row, 0, is_press=False)
            elif note in C.ENCODER_TOUCH_TRACK_NOTES:
                ev = EncoderTouchEvent(note, is_touched=False)
            elif note in (C.ENCODER_TOUCH_MASTER_NOTE,
                          C.ENCODER_TOUCH_SWING_NOTE,
                          C.ENCODER_TOUCH_TEMPO_NOTE):
                ev = EncoderTouchEvent(note, is_touched=False)
            elif note == C.TOUCH_STRIP_TOUCH_NOTE:
                ev = TouchStripEvent(value=0, is_touch=False)
        elif status == 0xA0:  # Poly aftertouch
            note, pressure = msg[1], msg[2]
            if C.PAD_NOTE_LOW <= note <= C.PAD_NOTE_HIGH:
                col, row = self._pad_coords(note)
                ev = PadAftertouchEvent(col, row, pressure)
        elif status == 0xB0:  # CC
            cc, val = msg[1], msg[2]
            if cc in C.BUTTON_NAMES:
                ev = ButtonEvent(cc, C.BUTTON_NAMES[cc], is_press=(val > 0))
            elif cc in C.ENCODER_NAMES:
                # Two's-complement 7-bit delta: 0x01–0x3F = +1…+63,
                # 0x40–0x7F = -64…-1. Magnitude = velocity.
                delta = val if val < 0x40 else val - 128
                ev = EncoderTurnEvent(cc, C.ENCODER_NAMES[cc], delta)
        elif status == 0xD0:  # Channel pressure
            ev = ChannelAftertouchEvent(pressure=msg[1])
        elif status == 0xE0:  # Pitch bend (touch strip)
            lsb, msb = msg[1], msg[2]
            value = (msb << 7) | lsb
            ev = TouchStripEvent(value=value, is_touch=False)

        if ev is not None:
            try:
                cb(ev)
            except Exception as e:
                print(f"Push 2 surface: handler raised {e}", flush=True)

    @staticmethod
    def _pad_coords(note: int) -> tuple[int, int]:
        idx = note - C.PAD_NOTE_LOW
        return idx % C.PAD_GRID_COLS, idx // C.PAD_GRID_COLS

    # ── Write API ──────────────────────────────────────────────────
    def _send(self, msg: list[int]) -> None:
        ex = self._existing_push2
        if ex is not None:
            try:
                ex._send_both(msg)
            except Exception:
                pass
            return
        if self.midi is not None:
            self.midi.send(msg)

    def set_pad_color(self, col: int, row: int, color_idx: int,
                      animation_channel: int = C.ANIM_STATIC) -> None:
        note = C.PAD_NOTE_LOW + row * C.PAD_GRID_COLS + col
        status = 0x90 | (animation_channel & 0x0F)
        self._send([status, note, color_idx & 0x7F])

    def set_pad_off(self, col: int, row: int) -> None:
        self.set_pad_color(col, row, C.COLOR_BLACK, C.ANIM_STATIC)

    def set_button_color(self, cc: int, color_idx: int,
                         animation_channel: int = C.ANIM_STATIC) -> None:
        status = 0xB0 | (animation_channel & 0x0F)
        self._send([status, cc, color_idx & 0x7F])

    def all_pads_off(self) -> None:
        for note in range(C.PAD_NOTE_LOW, C.PAD_NOTE_HIGH + 1):
            self._send([0x80, note, 0])

    def all_buttons_off(self) -> None:
        for cc in C.BUTTON_NAMES:
            self._send([0xB0, cc, 0])

    def send_clock(self) -> None:
        self._send([0xF8])

    def send_sysex(self, msg: bytes) -> None:
        if self._existing_push2 is not None:
            try:
                self._existing_push2._send_both(list(msg))
            except Exception:
                pass
            return
        if self.midi is not None:
            self.midi.send(msg)

    # ── Display ────────────────────────────────────────────────────
    def send_display_image(self, img) -> None:
        """Ship a PIL RGB image to Push 2.

        Hosted mode: converts to BGR565 numpy and uses the existing
        Push2Display.send_frame_rgb565 (which handles XOR + filler +
        bulk write itself).

        Standalone mode: packs the image with our own pixel module and
        ships via our USB handle.
        """
        ex = self._existing_display
        if ex is not None:
            try:
                import numpy as np
                if img.mode != "RGB":
                    img = img.convert("RGB")
                if img.size != (C.DISPLAY_WIDTH, C.DISPLAY_HEIGHT):
                    img = img.resize((C.DISPLAY_WIDTH, C.DISPLAY_HEIGHT))
                arr = np.asarray(img, dtype=np.uint8)
                r = arr[..., 0].astype(np.uint16)
                g = arr[..., 1].astype(np.uint16)
                b = arr[..., 2].astype(np.uint16)
                fb = ((b & 0xf8) << 8) | ((g & 0xfc) << 3) | (r >> 3)
                ex.send_frame_rgb565(fb)
            except Exception as e:
                print(f"send_display_image (hosted) failed: {e}",
                      flush=True)
            return
        if self.display is not None:
            from .pixel import pack_frame
            import numpy as np
            if img.mode != "RGB":
                img = img.convert("RGB")
            if img.size != (C.DISPLAY_WIDTH, C.DISPLAY_HEIGHT):
                img = img.resize((C.DISPLAY_WIDTH, C.DISPLAY_HEIGHT))
            arr = np.asarray(img, dtype=np.uint8)
            payload = pack_frame(arr)
            self.display.send_frame(payload)

    def send_display_payload(self, payload: bytes) -> None:
        """Standalone-mode raw payload send. Hosted mode prefers
        send_display_image()."""
        if self.display is not None:
            self.display.send_frame(payload)

    # ── Hosted-mode event dispatch ────────────────────────────────
    def dispatch_pad(self, idx: int, velocity: int) -> None:
        """Translate Compa's existing pad-idx (0..63) into our event."""
        if not (0 <= idx < 64):
            return
        col = idx % C.PAD_GRID_COLS
        row = idx // C.PAD_GRID_COLS
        ev = PadEvent(col=col, row=row, velocity=velocity,
                      is_press=(velocity > 0))
        cb = self._on_event
        if cb is not None:
            try:
                cb(ev)
            except Exception:
                pass

    # Map Compa-1 button names to our standard Push 2 vocabulary.
    _NAME_TRANSLATIONS = {
        # Scene-launch column (right of pad grid)
        **{f"launch_{i+1}": f"scene_{i+1}" for i in range(8)},
        # Upper display row → "select track"
        **{f"top_select_{i+1}": f"upper_display_{i+1}" for i in range(8)},
        # Lower display row → "stop clip per track"
        **{f"bot_select_{i+1}": f"lower_display_{i+1}" for i in range(8)},
        # Compa 1 names a few transport / nav differently
        "nav_up": "up", "nav_down": "down",
        "nav_left": "left", "nav_right": "right",
    }

    def dispatch_button(self, name: str, value: int) -> None:
        # Translate Compa 1's button vocabulary to ours
        translated = self._NAME_TRANSLATIONS.get(name, name)
        cc = C.NAMES_TO_BUTTONS.get(translated, 0)
        ev = ButtonEvent(cc=cc, name=translated, is_press=(value > 0))
        cb = self._on_event
        if cb is not None:
            try:
                cb(ev)
            except Exception:
                pass

    def dispatch_encoder(self, idx: int, delta: int) -> None:
        # Existing driver passes encoder index 0..7 (track encoders).
        try:
            cc = C.ENCODER_TRACK_CCS[idx]
            name = C.ENCODER_NAMES[cc]
        except (IndexError, KeyError):
            return
        ev = EncoderTurnEvent(cc=cc, name=name, delta=delta)
        cb = self._on_event
        if cb is not None:
            try:
                cb(ev)
            except Exception:
                pass

    def dispatch_special_encoder(self, name: str, delta: int) -> None:
        """Tempo / Master / Swing encoder."""
        cc_map = {"tempo": C.ENCODER_TEMPO_CC,
                  "master": C.ENCODER_MASTER_CC,
                  "swing": C.ENCODER_SWING_CC}
        cc = cc_map.get(name)
        if cc is None:
            return
        ev = EncoderTurnEvent(cc=cc, name=name, delta=delta)
        cb = self._on_event
        if cb is not None:
            try:
                cb(ev)
            except Exception:
                pass

    def close(self) -> None:
        if self.midi is None and self.display is None:
            return  # hosted mode — caller manages
        try:
            self.all_pads_off()
            self.all_buttons_off()
        except Exception:
            pass
        if self.midi is not None:
            self.midi.close()
        if self.display is not None:
            self.display.close()
