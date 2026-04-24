"""Ableton Push 2 display driver (Phase 2).

Talks to Push 2's vendor-specific USB interface 0 to push a 960x160
RGB565 framebuffer at ~30fps. The MIDI side (engine/push2.py) and
this display side coexist on the same physical device — the kernel
owns the MIDI interfaces (1 and 2), we own interface 0.

Protocol (per Ableton's published push-interface docs):
- Frame = 16-byte magic header + 160 lines
- Each line = 960 pixels × 2 bytes (RGB565 little-endian) + 128 byte
  filler = 2048 bytes/line → 327,680 bytes of line data per frame
- All bytes after the header are XOR'd with the repeating pattern
  0xE7 0xF3 0xE7 0xFF before transmission
- The entire frame is written to bulk EP 0x01 in one shot

Expected cost on a Pi 3B: ~15-20ms per frame to build (numpy) +
~5-10ms bulk transfer. Safe up to ~30fps.

Usage:
    disp = Push2Display()
    disp.fill_rgb(255, 0, 0)       # solid red test
    disp.send_surface(pygame_960x160_surface)
    disp.close()
"""

import logging
import threading
import numpy as np

try:
    import usb.core
    import usb.util
except ImportError:
    usb = None

log = logging.getLogger(__name__)

VID = 0x2982
PID = 0x1967

WIDTH = 960
HEIGHT = 160
LINE_FILLER = 128
LINE_BYTES = WIDTH * 2 + LINE_FILLER  # 2048
LINE_DATA_BYTES = HEIGHT * LINE_BYTES  # 327680
FRAME_BYTES = 16 + LINE_DATA_BYTES      # 327696
EP_DISPLAY = 0x01

FRAME_HEADER = bytes([0xff, 0xcc, 0xaa, 0x88,
                      0x00, 0x00, 0x00, 0x00,
                      0x00, 0x00, 0x00, 0x00,
                      0x00, 0x00, 0x00, 0x00])

# XOR mask pattern: 4-byte unit 0xE7 0xF3 0xE7 0xFF, tiled across the
# entire line-data region. Precomputed once at import time.
_XOR_MASK = np.tile(
    np.array([0xe7, 0xf3, 0xe7, 0xff], dtype=np.uint8),
    LINE_DATA_BYTES // 4,
)


def rgb_to_565(r: int, g: int, b: int) -> int:
    """Pack 8-bit RGB into a Push 2-format 16-bit pixel.

    Per Ableton's interface spec, the Push 2 pixel format is BGR565,
    not standard RGB565: high 5 bits = blue, middle 6 = green, low
    5 = red. Colors sent with an RGB565-packed value will appear with
    red/blue channels swapped.
    """
    return ((b & 0xf8) << 8) | ((g & 0xfc) << 3) | (r >> 3)


class Push2Display:
    """Vendor-specific USB display driver for the Ableton Push 2.

    Thread-safe: send_* calls are serialized by an internal lock so
    a Compa render thread and ad-hoc calls don't collide on bulk EP.
    """

    def __init__(self) -> None:
        if usb is None:
            raise RuntimeError("pyusb not installed")
        self._dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self._dev is None:
            raise RuntimeError("Push 2 not found on USB bus")
        # Don't call set_configuration — the device is already configured
        # (kernel owns MIDI interfaces 1 and 2). We only need interface 0.
        usb.util.claim_interface(self._dev, 0)
        self._lock = threading.Lock()
        log.info("Push 2 display interface claimed")

    # ── Core frame send ─────────────────────────────────────────────

    def send_frame_rgb565(self, fb: np.ndarray) -> None:
        """Send a full frame. fb must be shape (160, 960) dtype=uint16.

        Per Ableton's doc: send header as one bulk write, then the
        327,680-byte XOR'd pixel+filler payload as a separate write.
        Combining them into a single transfer has been observed to
        leave the device stuck on its blue boot splash.
        """
        if fb.shape != (HEIGHT, WIDTH):
            raise ValueError(f"framebuffer must be (160, 960), got {fb.shape}")
        if fb.dtype != np.uint16:
            fb = fb.astype(np.uint16)

        lines = np.zeros((HEIGHT, LINE_BYTES), dtype=np.uint8)
        lines[:, 0:WIDTH * 2:2] = (fb & 0xff).astype(np.uint8)
        lines[:, 1:WIDTH * 2:2] = ((fb >> 8) & 0xff).astype(np.uint8)

        flat = lines.reshape(-1)
        flat ^= _XOR_MASK
        payload = flat.tobytes()

        with self._lock:
            n1 = self._dev.write(EP_DISPLAY, FRAME_HEADER, timeout=1000)
            n2 = self._dev.write(EP_DISPLAY, payload, timeout=1000)
        if n1 != 16 or n2 != LINE_DATA_BYTES:
            log.warning("short write: header=%d/16, payload=%d/%d",
                        n1, n2, LINE_DATA_BYTES)

    # ── Convenience renderers ───────────────────────────────────────

    def fill_rgb(self, r: int, g: int, b: int) -> None:
        """Paint the whole display a solid RGB color."""
        color = rgb_to_565(r, g, b)
        fb = np.full((HEIGHT, WIDTH), color, dtype=np.uint16)
        self.send_frame_rgb565(fb)

    def send_surface(self, surface) -> None:
        """Convert a pygame Surface (960×160, RGB) to Push 2 BGR565 and send."""
        import pygame.surfarray as surfarray
        arr = surfarray.pixels3d(surface)     # (W, H, 3) uint8
        arr = np.transpose(arr, (1, 0, 2))    # (H, W, 3)
        r = arr[..., 0].astype(np.uint16)
        g = arr[..., 1].astype(np.uint16)
        b = arr[..., 2].astype(np.uint16)
        # Push 2 pixel format: BGR565 (blue high, red low).
        fb = ((b & 0xf8) << 8) | ((g & 0xfc) << 3) | (r >> 3)
        self.send_frame_rgb565(fb.astype(np.uint16))

    # ── Lifecycle ───────────────────────────────────────────────────

    def close(self) -> None:
        try:
            usb.util.release_interface(self._dev, 0)
        except Exception as e:
            log.debug("release_interface: %s", e)
        try:
            usb.util.dispose_resources(self._dev)
        except Exception:
            pass
        log.info("Push 2 display released")
