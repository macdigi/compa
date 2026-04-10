"""Audio routing between USB devices.

Reads audio from one sounddevice input and plays it through another
sounddevice output, with optional sample rate conversion.  Designed
for routing audio between two USB music devices connected to the Pi
(e.g. SP-404 MK2 output → P-6 input).

Uses lock-free ring buffer between the input and output callbacks.
Sample rate conversion is linear interpolation — good enough for
monitoring/sampling, and very cheap on the Pi 3B CPU.
"""

import logging
import threading
import time
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

log = logging.getLogger(__name__)

CHANNELS = 2
BLOCK_SIZE = 2048
RING_SECONDS = 4  # 4 seconds of buffering between input and output


class AudioRoute:
    """Routes audio from one device to another with optional SRC.

    Example usage::

        route = AudioRoute(
            source_device=0,   # SP-404MKII
            source_rate=48000,
            dest_device=2,     # P-6
            dest_rate=44100,
        )
        route.start()
        # ... later ...
        route.stop()
    """

    def __init__(self, source_device: int, source_rate: int,
                 dest_device: int, dest_rate: int):
        self._src_dev = source_device
        self._src_rate = source_rate
        self._dst_dev = dest_device
        self._dst_rate = dest_rate

        self._in_stream: Optional["sd.InputStream"] = None
        self._out_stream: Optional["sd.OutputStream"] = None
        self._active = False

        # Ring buffer (source rate, converted on read for output)
        self._ring_frames = RING_SECONDS * source_rate
        self._ring = np.zeros((self._ring_frames, CHANNELS), dtype=np.float32)
        self._write_pos = 0
        self._read_pos = 0

        # SRC ratio
        self._needs_src = (source_rate != dest_rate)
        self._src_ratio = dest_rate / source_rate if self._needs_src else 1.0

        # Metering
        self._peak = 0.0

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def peak_level(self) -> float:
        return self._peak

    @property
    def source_name(self) -> str:
        if sd and self._src_dev is not None:
            try:
                return sd.query_devices(self._src_dev).get("name", "?").split(":")[0]
            except Exception:
                pass
        return "?"

    @property
    def dest_name(self) -> str:
        if sd and self._dst_dev is not None:
            try:
                return sd.query_devices(self._dst_dev).get("name", "?").split(":")[0]
            except Exception:
                pass
        return "?"

    def start(self) -> bool:
        """Start the audio route. Returns True on success."""
        if self._active or sd is None:
            return False

        try:
            self._write_pos = 0
            self._read_pos = 0
            self._ring[:] = 0

            # Input stream (source device)
            self._in_stream = sd.InputStream(
                device=self._src_dev,
                samplerate=self._src_rate,
                channels=CHANNELS,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=self._input_callback,
            )

            # Output stream (destination device)
            self._out_stream = sd.OutputStream(
                device=self._dst_dev,
                samplerate=self._dst_rate,
                channels=CHANNELS,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=self._output_callback,
            )

            # Pre-fill a small amount of silence to avoid underrun
            self._write_pos = int(self._src_rate * 0.1)  # 100ms head start

            self._in_stream.start()
            self._out_stream.start()
            self._active = True

            src_label = f"{self._src_rate // 1000}k"
            dst_label = f"{self._dst_rate // 1000}k"
            log.info("Audio route started: dev%d (%s) → dev%d (%s)%s",
                     self._src_dev, src_label, self._dst_dev, dst_label,
                     " [SRC]" if self._needs_src else "")
            return True

        except Exception as e:
            log.error("Failed to start audio route: %s", e)
            self.stop()
            return False

    def stop(self):
        """Stop the audio route and close streams."""
        self._active = False
        for stream in (self._in_stream, self._out_stream):
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        self._in_stream = None
        self._out_stream = None
        self._peak = 0.0
        log.info("Audio route stopped")

    # ── Callbacks ────────────────────────────────────────────────────

    def _input_callback(self, indata, frames, time_info, status):
        """Called by the source device's InputStream."""
        if status:
            log.debug("Input status: %s", status)

        data = indata.copy()
        n = len(data)

        # Write to ring buffer
        wp = self._write_pos
        space = self._ring_frames - wp
        if n <= space:
            self._ring[wp:wp + n] = data
        else:
            self._ring[wp:] = data[:space]
            self._ring[:n - space] = data[space:]
        self._write_pos = (wp + n) % self._ring_frames

        # Peak metering
        self._peak = float(np.abs(data).max())

    def _output_callback(self, outdata, frames, time_info, status):
        """Called by the destination device's OutputStream."""
        if status:
            log.debug("Output status: %s", status)

        if not self._active:
            outdata[:] = 0
            return

        if self._needs_src:
            # Read more frames from ring at source rate, resample to dest rate
            src_frames = int(frames / self._src_ratio) + 2
            self._read_and_resample(outdata, frames, src_frames)
        else:
            # Same rate — direct copy
            rp = self._read_pos
            n = frames
            space = self._ring_frames - rp
            if n <= space:
                outdata[:n] = self._ring[rp:rp + n]
            else:
                outdata[:space] = self._ring[rp:]
                outdata[space:n] = self._ring[:n - space]
            self._read_pos = (rp + n) % self._ring_frames

    def _read_and_resample(self, outdata, out_frames, src_frames):
        """Read from ring buffer and resample via linear interpolation."""
        rp = self._read_pos

        # Read source frames into a temporary buffer
        src_buf = np.empty((src_frames, CHANNELS), dtype=np.float32)
        space = self._ring_frames - rp
        if src_frames <= space:
            src_buf[:] = self._ring[rp:rp + src_frames]
        else:
            src_buf[:space] = self._ring[rp:]
            src_buf[space:src_frames] = self._ring[:src_frames - space]

        # Linear interpolation to target frame count
        src_indices = np.linspace(0, src_frames - 1, out_frames)
        idx_floor = src_indices.astype(np.int32)
        idx_ceil = np.minimum(idx_floor + 1, src_frames - 1)
        frac = (src_indices - idx_floor).reshape(-1, 1)

        outdata[:out_frames] = src_buf[idx_floor] * (1.0 - frac) + src_buf[idx_ceil] * frac

        self._read_pos = (rp + src_frames) % self._ring_frames


def find_device_index(hint: str) -> Optional[int]:
    """Find a sounddevice index by name hint."""
    if sd is None:
        return None
    for i, dev in enumerate(sd.query_devices()):
        name = dev.get("name", "")
        if hint.lower() in name.lower() and dev.get("max_output_channels", 0) >= 2:
            return i
    return None
