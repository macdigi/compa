"""Push 2 MIDI I/O — finds Live + User ports, exposes I/O queues.

Push 2 exposes two MIDI ports to the OS. Live's surface script
normally talks on the Live port; User mode talks on the User port.
We use the Live port for everything (LED writes, sysex, control input).

This file uses python-rtmidi (already a Compa dependency). It does
not block; input arrives via callback, output is direct send.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Callable, Deque, Optional

try:
    import rtmidi
    _HAVE_RTMIDI = True
except Exception:
    _HAVE_RTMIDI = False

from . import constants as C


def _find_port(port_class, fragment: str) -> Optional[int]:
    if not _HAVE_RTMIDI:
        return None
    inst = port_class()
    try:
        for i, name in enumerate(inst.get_ports()):
            if (fragment in name or
                    (C.MIDI_PORT_GENERIC_FRAGMENT in name and
                     fragment in name)):
                return i
    finally:
        del inst
    return None


class Push2Midi:
    """Holds rtmidi In + Out handles for the Push 2 Live port.

    Inputs arrive via `on_message(msg: list[int], delta: float)` callback
    (registered by the caller, runs on rtmidi's thread). Outputs go via
    `send(msg)`.

    If rtmidi isn't installed or the port isn't found, all methods are
    no-ops so the rest of Compa keeps running.
    """

    def __init__(self) -> None:
        self._in: Optional[object] = None
        self._out: Optional[object] = None
        self._on_message: Optional[Callable[[list[int]], None]] = None
        self._open()

    @property
    def available(self) -> bool:
        return self._in is not None and self._out is not None

    def _open(self) -> None:
        if not _HAVE_RTMIDI:
            print("Push 2 MIDI: rtmidi not installed", flush=True)
            return
        try:
            in_idx = _find_port(rtmidi.MidiIn, C.MIDI_LIVE_PORT_FRAGMENT)
            out_idx = _find_port(rtmidi.MidiOut, C.MIDI_LIVE_PORT_FRAGMENT)
            if in_idx is None or out_idx is None:
                # Try the generic Push-2-named port
                in_idx = in_idx or _find_port(rtmidi.MidiIn,
                                              C.MIDI_PORT_GENERIC_FRAGMENT)
                out_idx = out_idx or _find_port(rtmidi.MidiOut,
                                                C.MIDI_PORT_GENERIC_FRAGMENT)
            if in_idx is None or out_idx is None:
                print("Push 2 MIDI: ports not found", flush=True)
                return
            self._in = rtmidi.MidiIn()
            self._in.open_port(in_idx)
            self._in.ignore_types(sysex=False, timing=True, active_sense=True)
            self._in.set_callback(self._on_rtmidi_message)
            self._out = rtmidi.MidiOut()
            self._out.open_port(out_idx)
            print(f"Push 2 MIDI: opened in={in_idx} out={out_idx}",
                  flush=True)
        except Exception as e:
            print(f"Push 2 MIDI: open failed: {e}", flush=True)
            self._in = None
            self._out = None

    def set_message_callback(self, fn: Callable[[list[int]], None]) -> None:
        self._on_message = fn

    def _on_rtmidi_message(self, msg_and_delta, _user) -> None:
        msg, _delta = msg_and_delta
        cb = self._on_message
        if cb is not None:
            try:
                cb(msg)
            except Exception as e:
                print(f"Push 2 MIDI: handler raised {e}", flush=True)

    def send(self, msg: bytes | list[int]) -> None:
        if self._out is None:
            return
        try:
            self._out.send_message(list(msg))
        except Exception as e:
            print(f"Push 2 MIDI: send failed: {e}", flush=True)

    def send_many(self, msgs: list[bytes | list[int]]) -> None:
        if self._out is None:
            return
        for m in msgs:
            try:
                self._out.send_message(list(m))
            except Exception as e:
                print(f"Push 2 MIDI: send_many failed: {e}", flush=True)
                return

    def close(self) -> None:
        try:
            if self._in is not None:
                self._in.cancel_callback()
                self._in.close_port()
        except Exception:
            pass
        try:
            if self._out is not None:
                self._out.close_port()
        except Exception:
            pass
        self._in = None
        self._out = None
