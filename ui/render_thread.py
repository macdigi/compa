"""Push 2 OLED + LED render thread.

Runs at 60 fps independent of audio + control. Reads the active mode's
draw_pads() / draw_oled(), pushes results to Push 2 hardware.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from engine.push2driver import constants as C
from ui.push2_oled.compositor import image_to_frame, blank


class RenderThread(threading.Thread):
    daemon = True

    def __init__(self, control, fps: int = 60) -> None:
        super().__init__(name="push2-render")
        self.control = control
        self.target_period = 1.0 / fps
        self._stop = threading.Event()
        self._last_pad_state: dict[tuple[int, int], tuple[int, int]] = {}
        self._last_button_state: dict[int, tuple[int, int]] = {}
        self._last_clock_send = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self._tick()
            except Exception as e:
                print(f"render thread tick error: {e}", flush=True)
            elapsed = time.monotonic() - t0
            sleep_for = self.target_period - elapsed
            if sleep_for > 0:
                self._stop.wait(timeout=sleep_for)

    def _tick(self) -> None:
        ctrl = self.control
        surface = ctrl.surface
        if surface is None or not surface.available:
            return
        # Idle when not active — leave the existing Compa Push 2
        # rendering alone.
        if not ctrl.is_active:
            return

        # MIDI clock for animation sync — 24 ppq.
        now = time.monotonic()
        bpm = max(20.0, ctrl.session.bpm)
        clock_period = 60.0 / (bpm * 24)
        if now - self._last_clock_send >= clock_period:
            self._last_clock_send = now
            try:
                surface.send_clock()
            except Exception:
                pass

        # Pads + buttons every tick (cheap; only writes diffs)
        mode = ctrl.active_mode
        pad_state = mode.draw_pads()
        for (col, row), (color, anim) in pad_state.items():
            prev = self._last_pad_state.get((col, row))
            if prev != (color, anim):
                surface.set_pad_color(col, row, color, anim)
        # Turn off pads no longer in state
        for key in list(self._last_pad_state.keys()):
            if key not in pad_state:
                col, row = key
                surface.set_pad_off(col, row)
        self._last_pad_state = dict(pad_state)

        button_state = mode.draw_buttons()
        for cc, (color, anim) in button_state.items():
            prev = self._last_button_state.get(cc)
            if prev != (color, anim):
                surface.set_button_color(cc, color, anim)
        for cc in list(self._last_button_state.keys()):
            if cc not in button_state:
                surface.set_button_color(cc, 0, 0)
        self._last_button_state = dict(button_state)

        # OLED — only repaint when dirty
        if not ctrl.consume_dirty():
            return
        try:
            img = mode.draw_oled(C.DISPLAY_WIDTH, C.DISPLAY_HEIGHT)
            if img is None:
                payload = blank()
            else:
                payload = image_to_frame(img)
            surface.send_display_payload(payload)
        except Exception as e:
            print(f"render OLED failed: {e}", flush=True)
