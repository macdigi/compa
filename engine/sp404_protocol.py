"""SP-404MKII normal-mode librarian protocol helpers.

The SP-404MKII exposes two USB devices in normal mode:

* 0582:0281 audio/MIDI
* 0582:02e7 CDC ACM, exposed by Linux as /dev/ttyACM*

The official Roland librarian uses the CDC ACM device rather than a
Linux-visible storage mount.  This module only contains decoded read-only
traffic. Write/import/delete commands are intentionally not implemented.
"""

from __future__ import annotations

import glob
import fcntl
import os
import re
import select
import struct
import termios
import time
from dataclasses import dataclass
from typing import Optional


ROLAND_VENDOR_ID = "0582"
SP404_CDC_PRODUCT_ID = "02e7"
SP404_HANDSHAKE = bytes.fromhex("12 60 e0 05 fe 67 00 6d 33 31 31 03")
APP_HANDSHAKE = bytes.fromhex("12 60 e0 05 fe 67 00 74 68 2d 49 03")
PROJECT_INIT = bytes.fromhex("12 60 e0 05 9a 04 00 00 00 20 20 03")
PROJECT_LIST = bytes.fromhex("12 60 e0 05 fd 04 00 00 07 00 00 03")
FILE_OPEN_PREAMBLE = bytes.fromhex("12 60 e0 05 b3 00 00 00 c0 93 91 02")
READ_META_13 = bytes.fromhex(
    "13 60 e0 06 00 0c 00 00 80 00 a1 e2 11 00 00 00 "
    "f0 41 7a 03 13 00 00 00 03 0d 00 00 00 00 00 00 f7"
)
READ_META_07 = bytes.fromhex(
    "13 60 e0 06 00 0c 00 00 80 00 a1 e2 11 00 00 00 "
    "f0 41 7a 03 07 00 00 00 03 0d 00 00 00 00 00 00 f7"
)
READ_HEADER_04 = bytes.fromhex(
    "13 60 e0 06 00 0c 00 00 80 00 a1 e2 11 00 00 00 "
    "f0 41 7a 03 04 00 00 00 03 0d 00 00 00 04 00 00 f7"
)
READ_DONE_03 = bytes.fromhex(
    "13 60 e0 06 00 0c 00 00 80 00 a1 e2 11 00 00 00 "
    "f0 41 7a 03 03 00 00 00 03 0d 00 00 00 00 00 00 f7"
)
PATH_OPEN_RESPONSE_MARKER = bytes.fromhex("f0 41 7a 7a")
FOLLOWUP_REQUEST_MARKER = bytes.fromhex("f0 41 7a 03")


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


@dataclass(frozen=True)
class SP404SampleHeader:
    """Decoded read-only RFWV header from an SP sample slot."""

    project: str
    bank: int
    pad: int
    data_size: int
    sample_rate: int
    channels: int
    bit_depth: int
    path: str

    @property
    def duration(self) -> float:
        bytes_per_frame = self.channels * max(1, self.bit_depth // 8)
        if self.data_size <= 0 or self.sample_rate <= 0 or bytes_per_frame <= 0:
            return 0.0
        return self.data_size / bytes_per_frame / self.sample_rate

    @property
    def filename(self) -> str:
        return f"BANK{self.bank}-{self.pad:02d}.SMP"

    @property
    def pad_id(self) -> str:
        return f"{chr(ord('A') + self.bank - 1)}{self.pad:02d}"


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


def configure_sp404_librarian_fd(fd: int):
    """Configure an open SP-404 CDC ACM fd like the Roland app."""
    _set_raw_9600(fd)
    _set_dtr_rts(fd)


def _read_until_quiet(fd: int, timeout: float, quiet: float = 0.2) -> bytes:
    chunks: list[bytes] = []
    deadline = time.time() + timeout
    last_data = time.time()
    while time.time() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.05)
        if readable:
            try:
                data = os.read(fd, 8192)
            except BlockingIOError:
                continue
            if data:
                chunks.append(data)
                last_data = time.time()
        elif chunks and time.time() - last_data >= quiet:
            break
    return b"".join(chunks)


def _transact_many(payloads: list[tuple[str, bytes]],
                   timeout: float) -> tuple[str, list[tuple[str, bytes]]]:
    port = find_sp404_librarian_port()
    if not port:
        raise RuntimeError("SP-404 CDC port not found")

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    responses: list[tuple[str, bytes]] = []
    try:
        configure_sp404_librarian_fd(fd)
        time.sleep(0.2)
        for label, payload in payloads:
            os.write(fd, payload)
            responses.append((label, _read_until_quiet(fd, timeout)))
    finally:
        os.close(fd)
    return port, responses


def _remote_smp_path(project: str, bank: int, pad: int) -> str:
    if not re.fullmatch(r"PROJECT_[0-9]{2}", project):
        raise ValueError("project must look like PROJECT_05")
    if not 1 <= bank <= 10:
        raise ValueError("bank must be 1-10")
    if not 1 <= pad <= 16:
        raise ValueError("pad must be 1-16")
    return (
        f"/SP404REMOTE///ROLAND/SP-404MKII/{project}/SMPL/"
        f"BANK{bank}-{pad:02d}.SMP"
    )


def _build_file_read_path(path: str) -> bytes:
    path_bytes = path.encode("ascii")
    if len(path_bytes) > 220:
        raise ValueError("path is too long for one observed librarian packet")
    return b"".join([
        bytes.fromhex("13 60 e0 06 00 0c 00 00 a0 57 a5 e2"),
        bytes([len(path_bytes) + 17, 0x00, 0x00, 0x00]),
        bytes.fromhex("f0 41 7a 03 00 00 00 00 00 00 00 00 00 00"),
        bytes([len(path_bytes) + 1]),
        path_bytes,
        b"\x00\xf7",
    ])


def _file_read_payloads(path: str, *, preamble: bool) -> list[tuple[str, bytes]]:
    read_path = _build_file_read_path(path)
    if preamble:
        read_path = FILE_OPEN_PREAMBLE + read_path
    return [
        ("read_path", read_path),
        ("read_meta_13", READ_META_13),
        ("read_meta_07", READ_META_07),
        ("read_header_04", READ_HEADER_04),
        ("read_done_03", READ_DONE_03),
    ]


def _extract_file_key(open_response: bytes) -> Optional[bytes]:
    """Extract the two-byte per-path key required by follow-up read commands."""
    pos = open_response.rfind(PATH_OPEN_RESPONSE_MARKER)
    if pos < 0 or pos + 9 > len(open_response):
        return None
    status = open_response[pos + 4:pos + 9]
    if status == b"\x7f" * 5:
        return None
    key = open_response[pos + 7:pos + 9]
    if len(key) != 2 or key == b"\x7f\x7f":
        return None
    return key


def _apply_file_key(payload: bytes, file_key: bytes) -> bytes:
    data = bytearray(payload)
    pos = data.find(FOLLOWUP_REQUEST_MARKER)
    if pos < 0 or pos + 10 > len(data):
        raise ValueError("follow-up request marker not found")
    data[pos + 8:pos + 10] = file_key
    return bytes(data)


def _file_read_followup_payloads(file_key: bytes) -> list[tuple[str, bytes]]:
    return [
        ("read_meta_13", _apply_file_key(READ_META_13, file_key)),
        ("read_meta_07", _apply_file_key(READ_META_07, file_key)),
        ("read_header_04", _apply_file_key(READ_HEADER_04, file_key)),
        ("read_done_03", _apply_file_key(READ_DONE_03, file_key)),
    ]


def _parse_project_names(data: bytes) -> list[str]:
    projects: list[str] = []
    for match in re.finditer(rb"PROJECT_[0-9]{2}", data):
        name = match.group(0).decode("ascii")
        if name not in projects:
            projects.append(name)
    return projects


def _parse_rfwv_header(project: str, bank: int, pad: int,
                       path: str, data: bytes) -> Optional[SP404SampleHeader]:
    pos = data.find(b"RFWV")
    if pos < 0 or len(data) < pos + 20:
        return None
    return SP404SampleHeader(
        project=project,
        bank=bank,
        pad=pad,
        data_size=int.from_bytes(data[pos + 4:pos + 8], "big"),
        sample_rate=int.from_bytes(data[pos + 8:pos + 12], "big"),
        channels=int.from_bytes(data[pos + 12:pos + 16], "big"),
        bit_depth=int.from_bytes(data[pos + 16:pos + 20], "big"),
        path=path,
    )


def list_projects(timeout: float = 1.5) -> list[str]:
    """Return normal-mode SP projects through the read-only CDC protocol."""
    payloads = [
        ("app_handshake", APP_HANDSHAKE),
        ("project_init", PROJECT_INIT),
        ("project_list", PROJECT_LIST),
    ]
    _port, responses = _transact_many(payloads, timeout)
    combined = b"".join(response for _label, response in responses)
    return _parse_project_names(combined)


def read_bank_headers(project: str, bank: int,
                      timeout: float = 1.5) -> list[Optional[SP404SampleHeader]]:
    """Read RFWV headers for one 1-indexed SP bank via normal mode."""
    if not re.fullmatch(r"PROJECT_[0-9]{2}", project):
        raise ValueError("project must look like PROJECT_05")
    if not 1 <= bank <= 10:
        raise ValueError("bank must be 1-10")

    paths: dict[int, str] = {}
    responses_by_pad: dict[int, bytearray] = {
        pad: bytearray() for pad in range(1, 17)
    }

    port = find_sp404_librarian_port()
    if not port:
        raise RuntimeError("SP-404 CDC port not found")

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_sp404_librarian_fd(fd)
        time.sleep(0.2)
        for payload in (APP_HANDSHAKE, PROJECT_INIT, PROJECT_LIST):
            os.write(fd, payload)
            _read_until_quiet(fd, timeout)

        for pad in range(1, 17):
            path = _remote_smp_path(project, bank, pad)
            paths[pad] = path
            read_path = _build_file_read_path(path)
            if pad == 1:
                read_path = FILE_OPEN_PREAMBLE + read_path
            os.write(fd, read_path)
            response = _read_until_quiet(fd, timeout)
            responses_by_pad[pad].extend(response)

            file_key = _extract_file_key(response)
            if not file_key:
                continue
            for _label, payload in _file_read_followup_payloads(file_key):
                os.write(fd, payload)
                responses_by_pad[pad].extend(_read_until_quiet(fd, timeout))
    finally:
        os.close(fd)

    headers: list[Optional[SP404SampleHeader]] = []
    for pad in range(1, 17):
        headers.append(
            _parse_rfwv_header(project, bank, pad, paths[pad],
                               bytes(responses_by_pad[pad]))
        )
    return headers


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
        configure_sp404_librarian_fd(fd)
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
