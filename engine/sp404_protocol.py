"""SP-404MKII normal-mode librarian protocol helpers.

The SP-404MKII exposes two USB devices in normal mode:

* 0582:0281 audio/MIDI
* 0582:02e7 CDC ACM, exposed by Linux as /dev/ttyACM*

The official Roland librarian uses the CDC ACM device rather than a
Linux-visible storage mount.  This module only handles discovery and a
small handshake probe for now; higher-level pad/file commands still need
to be decoded before Compa can read/write the SP in normal mode.
"""

from __future__ import annotations

import glob
import fcntl
import os
import select
import struct
import termios
import time
from dataclasses import dataclass
from typing import Optional


ROLAND_VENDOR_ID = "0582"
SP404_CDC_PRODUCT_ID = "02e7"
SP404_HANDSHAKE = bytes.fromhex("12 60 e0 05 fe 67 00 6d 33 31 31 03")


@dataclass(frozen=True)
class SP404ProtocolProbe:
    """Result from a low-level SP-404 CDC handshake probe."""

    port: str
    ok: bool
    response: bytes = b""
    error: str = ""

    @property
    def response_hex(self) -> str:
        return self.response.hex(" ")


def _usb_device_for_tty(tty_name: str) -> Optional[str]:
    """Return the parent USB device sysfs path for a ttyACM node."""
    device_link = f"/sys/class/tty/{tty_name}/device"
    try:
        path = os.path.realpath(device_link)
    except OSError:
        return None

    # ttyACM0 -> .../<usb-dev>:1.0/tty/ttyACM0. Walk up to the device.
    cur = path
    for _ in range(8):
        vendor = os.path.join(cur, "idVendor")
        product = os.path.join(cur, "idProduct")
        if os.path.isfile(vendor) and os.path.isfile(product):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def find_sp404_librarian_port() -> str:
    """Return the SP-404MKII CDC ACM port path, or an empty string."""
    for path in sorted(glob.glob("/dev/ttyACM*")):
        tty_name = os.path.basename(path)
        usb_device = _usb_device_for_tty(tty_name)
        if not usb_device:
            continue
        try:
            with open(os.path.join(usb_device, "idVendor"), "r", encoding="ascii") as f:
                vendor = f.read().strip().lower()
            with open(os.path.join(usb_device, "idProduct"), "r", encoding="ascii") as f:
                product = f.read().strip().lower()
        except OSError:
            continue
        if vendor == ROLAND_VENDOR_ID and product == SP404_CDC_PRODUCT_ID:
            return path
    return ""


def _set_raw_9600(fd: int):
    """Apply the termios shape captured from the Roland librarian app."""
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.HUPCL
    attrs[3] = 0
    attrs[4] = termios.B9600
    attrs[5] = termios.B9600
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def _set_dtr_rts(fd: int):
    """Raise DTR/RTS on the CDC ACM port."""
    tiocmget = getattr(termios, "TIOCMGET", 0x5415)
    tiocmset = getattr(termios, "TIOCMSET", 0x5418)
    dtr = getattr(termios, "TIOCM_DTR", 0x002)
    rts = getattr(termios, "TIOCM_RTS", 0x004)
    bits = struct.unpack("I", fcntl.ioctl(fd, tiocmget, struct.pack("I", 0)))[0]
    bits |= dtr | rts
    fcntl.ioctl(fd, tiocmset, struct.pack("I", bits))


def probe_sp404_librarian(timeout: float = 3.0) -> SP404ProtocolProbe:
    """Send the captured SP-404 handshake and return any response.

    This is intended for DEBUG/protocol research, not for normal redraw
    paths. It writes to the SP CDC ACM control endpoint.
    """
    port = find_sp404_librarian_port()
    if not port:
        return SP404ProtocolProbe(port="", ok=False, error="SP-404 CDC port not found")

    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as e:
        return SP404ProtocolProbe(port=port, ok=False, error=str(e))

    chunks: list[bytes] = []
    try:
        _set_raw_9600(fd)
        _set_dtr_rts(fd)
        time.sleep(0.2)
        os.write(fd, SP404_HANDSHAKE)

        deadline = time.time() + timeout
        while time.time() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.25)
            if not readable:
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if data:
                chunks.append(data)
    except OSError as e:
        return SP404ProtocolProbe(port=port, ok=False, error=str(e))
    finally:
        os.close(fd)

    response = b"".join(chunks)
    if response:
        return SP404ProtocolProbe(port=port, ok=True, response=response)
    return SP404ProtocolProbe(port=port, ok=False, error="No handshake response")
