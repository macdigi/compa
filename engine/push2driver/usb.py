"""Push 2 USB display I/O — pyusb wrapper for the bulk endpoint.

The MIDI ports on Push 2 are accessed through the OS's MIDI stack
(rtmidi); USB only matters for the display, which is on bulk endpoint
0x01 of the same device.

We attempt a libusb open of vendor 0x2982 / product 0x1967, claim the
display interface, and provide a `send_frame()` method that ships a
header + packed payload. If pyusb isn't available or the device is
absent, the methods become no-ops so the rest of Compa keeps running.
"""
from __future__ import annotations

import threading

from . import constants as C

try:
    import usb.core
    import usb.util
    _HAVE_PYUSB = True
except Exception:
    _HAVE_PYUSB = False


class Push2Display:
    """Holds a USB handle for the Push 2 display.

    Thread-safe `send_frame(payload)` writes the standard header then
    the payload. Caller is responsible for building the payload via
    pixel.pack_frame().
    """

    def __init__(self) -> None:
        self._dev = None
        self._iface = None
        self._lock = threading.Lock()
        self._open()

    @property
    def available(self) -> bool:
        return self._dev is not None

    def _open(self) -> None:
        if not _HAVE_PYUSB:
            print("Push 2 display: pyusb not installed", flush=True)
            return
        try:
            dev = usb.core.find(idVendor=C.USB_VENDOR_ID,
                                idProduct=C.USB_PRODUCT_ID)
        except Exception as e:
            print(f"Push 2 display: USB enumeration failed: {e}", flush=True)
            return
        if dev is None:
            return  # not plugged in — surface keeps running on MIDI alone

        try:
            # Detach kernel driver if present (Linux/macOS will sometimes
            # bind a generic driver to the bulk interface).
            for cfg in dev:
                for iface in cfg:
                    try:
                        if dev.is_kernel_driver_active(iface.bInterfaceNumber):
                            dev.detach_kernel_driver(iface.bInterfaceNumber)
                    except Exception:
                        pass
            dev.set_configuration()
            cfg = dev.get_active_configuration()
            # The display lives on the interface that has bulk-out
            # endpoint 0x01.
            target_iface = None
            for iface in cfg:
                for ep in iface:
                    if ep.bEndpointAddress == C.USB_DISPLAY_ENDPOINT_OUT:
                        target_iface = iface
                        break
                if target_iface is not None:
                    break
            if target_iface is None:
                print("Push 2 display: no bulk endpoint 0x01 found", flush=True)
                return
            usb.util.claim_interface(dev, target_iface.bInterfaceNumber)
            self._dev = dev
            self._iface = target_iface
            print("Push 2 display: USB handle open", flush=True)
        except Exception as e:
            print(f"Push 2 display: open failed: {e}", flush=True)
            self._dev = None
            self._iface = None

    def send_frame(self, payload: bytes) -> None:
        """Ship one frame: 16-byte header + payload bytes.

        Caller-provided payload should be exactly DISPLAY_FRAME_BYTES bytes
        (we do not validate to keep the hot path cheap).
        """
        if self._dev is None:
            return
        with self._lock:
            try:
                self._dev.write(C.USB_DISPLAY_ENDPOINT_OUT,
                                C.DISPLAY_HEADER, timeout=200)
                self._dev.write(C.USB_DISPLAY_ENDPOINT_OUT,
                                payload, timeout=500)
            except Exception as e:
                # Don't spam — print once and disable.
                print(f"Push 2 display: send_frame failed ({e}); disabling",
                      flush=True)
                try:
                    if self._iface is not None:
                        usb.util.release_interface(
                            self._dev, self._iface.bInterfaceNumber)
                except Exception:
                    pass
                self._dev = None
                self._iface = None

    def close(self) -> None:
        if self._dev is None:
            return
        with self._lock:
            try:
                if self._iface is not None:
                    usb.util.release_interface(
                        self._dev, self._iface.bInterfaceNumber)
                usb.util.dispose_resources(self._dev)
            except Exception:
                pass
            self._dev = None
            self._iface = None
