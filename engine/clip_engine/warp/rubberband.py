"""Real-time Rubber Band Library wrapper via ctypes.

Wraps the C API of librubberband.so (LGPL — dynamic-linking only,
keeps Compa MIT-compatible). Used by AudioClipVoice when the clip's
warp_mode is Tones / Texture / Complex / Complex-Pro.

If librubberband.so isn't installed, Stretcher falls back to a no-op
that signals callers to use Re-Pitch instead.
"""
from __future__ import annotations

import ctypes
import ctypes.util
from typing import Optional

import numpy as np


# ── Find and load the library ──────────────────────────────────────
def _load_librubberband():
    candidates = [
        "librubberband.so.2",
        "librubberband.so",
        "/usr/lib/aarch64-linux-gnu/librubberband.so.2",
        "/usr/lib/x86_64-linux-gnu/librubberband.so.2",
    ]
    for name in candidates:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    found = ctypes.util.find_library("rubberband")
    if found:
        try:
            return ctypes.CDLL(found)
        except OSError:
            pass
    return None


_lib = _load_librubberband()
HAVE_RUBBERBAND = _lib is not None


# ── C API binding ──────────────────────────────────────────────────
if HAVE_RUBBERBAND:
    # RubberBandState is an opaque pointer
    _RBState = ctypes.c_void_p

    _lib.rubberband_new.restype = _RBState
    _lib.rubberband_new.argtypes = [
        ctypes.c_uint, ctypes.c_uint, ctypes.c_int,
        ctypes.c_double, ctypes.c_double,
    ]

    _lib.rubberband_delete.argtypes = [_RBState]

    _lib.rubberband_reset.argtypes = [_RBState]

    _lib.rubberband_set_time_ratio.argtypes = [_RBState, ctypes.c_double]
    _lib.rubberband_set_pitch_scale.argtypes = [_RBState, ctypes.c_double]

    _lib.rubberband_get_samples_required.restype = ctypes.c_uint
    _lib.rubberband_get_samples_required.argtypes = [_RBState]

    _lib.rubberband_process.argtypes = [
        _RBState, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
        ctypes.c_uint, ctypes.c_int,
    ]

    _lib.rubberband_available.restype = ctypes.c_int
    _lib.rubberband_available.argtypes = [_RBState]

    _lib.rubberband_retrieve.restype = ctypes.c_uint
    _lib.rubberband_retrieve.argtypes = [
        _RBState, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
        ctypes.c_uint,
    ]

    _lib.rubberband_get_latency.restype = ctypes.c_uint
    _lib.rubberband_get_latency.argtypes = [_RBState]


# ── Option flags (mirroring rubberband-c.h) ────────────────────────
OPT_PROCESS_OFFLINE = 0x00000000
OPT_PROCESS_REALTIME = 0x00000001
OPT_TRANSIENTS_CRISP = 0x00000000
OPT_TRANSIENTS_MIXED = 0x00000100
OPT_TRANSIENTS_SMOOTH = 0x00000200
OPT_DETECTOR_COMPOUND = 0x00000000
OPT_DETECTOR_PERCUSSIVE = 0x00000400
OPT_DETECTOR_SOFT = 0x00000800
OPT_PHASE_LAMINAR = 0x00000000
OPT_PHASE_INDEPENDENT = 0x00002000
OPT_THREADING_AUTO = 0x00000000
OPT_THREADING_NEVER = 0x00010000
OPT_THREADING_ALWAYS = 0x00020000
OPT_WINDOW_STANDARD = 0x00000000
OPT_WINDOW_SHORT = 0x00100000
OPT_WINDOW_LONG = 0x00200000
OPT_FORMANT_SHIFTED = 0x00000000
OPT_FORMANT_PRESERVED = 0x01000000
OPT_PITCH_HIGH_SPEED = 0x00000000
OPT_PITCH_HIGH_QUALITY = 0x02000000
OPT_CHANNELS_APART = 0x00000000
OPT_CHANNELS_TOGETHER = 0x10000000
OPT_ENGINE_FASTER = 0x00000000  # R2
OPT_ENGINE_FINER = 0x20000000    # R3


# Preset bundles for each Live warp mode
def _options_for_mode(mode: str) -> int:
    base = OPT_PROCESS_REALTIME | OPT_THREADING_NEVER | OPT_CHANNELS_TOGETHER
    if mode == "beats":
        # Drum / rhythmic loop preset — percussive detector + crisp
        # transients + short window for tight transient preservation.
        return (base | OPT_DETECTOR_PERCUSSIVE | OPT_TRANSIENTS_CRISP
                | OPT_WINDOW_SHORT)
    if mode == "tones":
        return base | OPT_DETECTOR_SOFT | OPT_TRANSIENTS_SMOOTH
    if mode == "texture":
        return base | OPT_WINDOW_LONG | OPT_PHASE_INDEPENDENT
    if mode == "complex":
        return base | OPT_DETECTOR_COMPOUND | OPT_PITCH_HIGH_QUALITY
    if mode == "complex_pro":
        return (base | OPT_ENGINE_FINER | OPT_DETECTOR_COMPOUND
                | OPT_PITCH_HIGH_QUALITY | OPT_FORMANT_PRESERVED)
    # Fallback — drum-friendly (matches "beats")
    return (base | OPT_DETECTOR_PERCUSSIVE | OPT_TRANSIENTS_CRISP
            | OPT_WINDOW_SHORT)


class RubberBandStretcher:
    """Real-time time-stretcher for one stereo audio voice.

    Caller pushes input frames via process(), pulls output via retrieve().
    Time ratio = (output samples) / (input samples). >1 = slower /
    longer; <1 = faster / shorter. Pitch scale 1.0 = no pitch change.
    """

    CHANNELS = 2

    def __init__(self, sample_rate: int = 44100,
                 mode: str = "tones",
                 time_ratio: float = 1.0,
                 pitch_scale: float = 1.0) -> None:
        self.available = HAVE_RUBBERBAND
        self.sr = sample_rate
        self._handle = None
        if not self.available:
            return
        opts = _options_for_mode(mode)
        self._handle = _lib.rubberband_new(
            sample_rate, self.CHANNELS, opts,
            time_ratio, pitch_scale,
        )

    def set_time_ratio(self, ratio: float) -> None:
        if self._handle:
            _lib.rubberband_set_time_ratio(self._handle, ratio)

    def set_pitch_scale(self, scale: float) -> None:
        if self._handle:
            _lib.rubberband_set_pitch_scale(self._handle, scale)

    def reset(self) -> None:
        if self._handle:
            _lib.rubberband_reset(self._handle)

    def samples_required(self) -> int:
        if not self._handle:
            return 0
        return int(_lib.rubberband_get_samples_required(self._handle))

    def latency(self) -> int:
        if not self._handle:
            return 0
        return int(_lib.rubberband_get_latency(self._handle))

    def process(self, audio: np.ndarray, final: bool = False) -> None:
        """Push samples in. audio: (N, 2) float32."""
        if not self._handle or audio.size == 0:
            return
        n = audio.shape[0]
        # Need pointers to per-channel buffers
        if audio.ndim == 1:
            audio = np.column_stack([audio, audio])
        if not audio.flags['C_CONTIGUOUS']:
            audio = np.ascontiguousarray(audio, dtype=np.float32)
        elif audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        ch0 = audio[:, 0].ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        ch1 = audio[:, 1].ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        arr = (ctypes.POINTER(ctypes.c_float) * 2)(ch0, ch1)
        _lib.rubberband_process(self._handle, arr, n, 1 if final else 0)

    def available_output(self) -> int:
        if not self._handle:
            return 0
        return int(_lib.rubberband_available(self._handle))

    def retrieve(self, frames: int) -> np.ndarray:
        """Pull `frames` samples of stretched output. Returns (N, 2) float32."""
        if not self._handle or frames <= 0:
            return np.zeros((0, 2), dtype=np.float32)
        out_l = np.zeros(frames, dtype=np.float32)
        out_r = np.zeros(frames, dtype=np.float32)
        ch0 = out_l.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        ch1 = out_r.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        arr = (ctypes.POINTER(ctypes.c_float) * 2)(ch0, ch1)
        n = int(_lib.rubberband_retrieve(self._handle, arr, frames))
        if n == 0:
            return np.zeros((0, 2), dtype=np.float32)
        return np.column_stack([out_l[:n], out_r[:n]])

    def close(self) -> None:
        if self._handle:
            try:
                _lib.rubberband_delete(self._handle)
            except Exception:
                pass
            self._handle = None

    def __del__(self):
        self.close()
