#!/usr/bin/env python3
"""SP-404MKII librarian protocol lab.

This tool is intentionally conservative. By default it only sends the
known Roland librarian handshake and records the response from the SP's
normal-mode CDC ACM port. Arbitrary packet replay is gated behind an
explicit flag so we do not accidentally fuzz a project-writing protocol.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import select
import sys
import time
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "sp404_protocol", ROOT / "engine" / "sp404_protocol.py")
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load engine/sp404_protocol.py")
sp404_protocol = importlib.util.module_from_spec(_SPEC)
sys.modules["sp404_protocol"] = sp404_protocol
_SPEC.loader.exec_module(sp404_protocol)


CAPTURE_DIR = ROOT / "sessions" / "sp404_protocol"
WRITE_TEMPLATE_CAPTURE = CAPTURE_DIR / "live_write_probe_retry_20260519T031709Z.jsonl"
WRITE_TEMPLATE_PROJECT = b"PROJECT_05"
WRITE_TEMPLATE_PAD = b"BANK1-02"
WRITE_TEMPLATE_SAMPLE_NAME = b"compa-sp404-write-probe"
WRITE_TEMPLATE_AUDIO_BYTES = 96_000

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _hex(data: bytes) -> str:
    return data.hex(" ")


def _ascii_preview(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _parse_hex(value: str) -> bytes:
    compact = "".join(ch for ch in value if ch not in " \n\t:_-")
    if len(compact) % 2:
        raise argparse.ArgumentTypeError("hex string has an odd number of digits")
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _open_log(path: str) -> tuple[Path, object]:
    if path:
        log_path = Path(path).expanduser()
    else:
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = CAPTURE_DIR / f"sp404_probe_{stamp}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path, log_path.open("a", encoding="utf-8")


def _write_event(handle, event: dict):
    event = {"ts": _now(), **event}
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    handle.flush()


def transact(payload: bytes, timeout: float, label: str) -> tuple[str, bytes]:
    """Open the SP port, write payload, collect response bytes."""
    port = sp404_protocol.find_sp404_librarian_port()
    if not port:
        raise RuntimeError("SP-404 CDC port not found")

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    chunks: list[bytes] = []
    try:
        sp404_protocol.configure_sp404_librarian_fd(fd)
        time.sleep(0.2)
        os.write(fd, payload)

        deadline = time.time() + timeout
        while time.time() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.1)
            if not readable:
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if data:
                chunks.append(data)
    finally:
        os.close(fd)
    return port, b"".join(chunks)


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


def _transact_many(payloads: list[tuple[str, bytes]], timeout: float) -> tuple[str, list[tuple[str, bytes, bytes]]]:
    port = sp404_protocol.find_sp404_librarian_port()
    if not port:
        raise RuntimeError("SP-404 CDC port not found")

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    results: list[tuple[str, bytes, bytes]] = []
    try:
        sp404_protocol.configure_sp404_librarian_fd(fd)
        time.sleep(0.2)
        for label, payload in payloads:
            os.write(fd, payload)
            response = _read_until_quiet(fd, timeout)
            results.append((label, payload, response))
    finally:
        os.close(fd)
    return port, results


def _sp404_pad_path(project: str, bank: int, pad: int) -> str:
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
    # Captured from Roland Librarian 4.05. The length fields are the path plus
    # its NUL terminator, and the full F0...F7 sub-payload size.
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


def _extract_file_key(open_response: bytes) -> bytes | None:
    """Return the two-byte file key from an SP path-open response.

    The official app copies these two bytes into later 0x13/0x07/0x04/0x03
    readback commands. They vary per opened path, so captured constants are
    only valid for the exact file handle seen in that capture.
    """
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
    if len(file_key) != 2:
        raise ValueError("file key must contain exactly two bytes")
    data = bytearray(payload)
    pos = data.find(FOLLOWUP_REQUEST_MARKER)
    if pos < 0 or pos + 10 > len(data):
        raise ValueError("follow-up request marker not found")
    data[pos + 8:pos + 10] = file_key
    return bytes(data)


def _file_read_followup_payloads(file_key: bytes | None) -> list[tuple[str, bytes]]:
    payloads = [
        ("read_meta_13", READ_META_13),
        ("read_meta_07", READ_META_07),
        ("read_header_04", READ_HEADER_04),
        ("read_done_03", READ_DONE_03),
    ]
    if file_key is None:
        return payloads
    return [
        (label, _apply_file_key(payload, file_key))
        for label, payload in payloads
    ]


def _transact_read_path(
    remote_path: str,
    timeout: float,
    *,
    preamble: bool,
    dynamic_key: bool,
) -> tuple[str, list[tuple[str, bytes, bytes]], bytes | None]:
    port = sp404_protocol.find_sp404_librarian_port()
    if not port:
        raise RuntimeError("SP-404 CDC port not found")

    read_path = _build_file_read_path(remote_path)
    if preamble:
        read_path = FILE_OPEN_PREAMBLE + read_path

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    results: list[tuple[str, bytes, bytes]] = []
    file_key: bytes | None = None
    try:
        sp404_protocol.configure_sp404_librarian_fd(fd)
        time.sleep(0.2)
        for label, payload in [
            ("app_handshake", APP_HANDSHAKE),
            ("project_init", PROJECT_INIT),
            ("project_list", PROJECT_LIST),
            ("read_path", read_path),
        ]:
            os.write(fd, payload)
            response = _read_until_quiet(fd, timeout)
            results.append((label, payload, response))
            if label == "read_path":
                file_key = _extract_file_key(response)

        followups = _file_read_followup_payloads(file_key if dynamic_key else None)
        for label, payload in followups:
            os.write(fd, payload)
            response = _read_until_quiet(fd, timeout)
            results.append((label, payload, response))
    finally:
        os.close(fd)
    return port, results, file_key if dynamic_key else None


def _parse_rfwv_header(data: bytes) -> dict | None:
    pos = data.find(b"RFWV")
    if pos < 0:
        return None
    header = data[pos:pos + 32]
    parsed = {
        "offset": pos,
        "header_hex": _hex(header),
    }
    if len(data) >= pos + 20:
        parsed.update({
            "size_be": int.from_bytes(data[pos + 4:pos + 8], "big"),
            "size_le": int.from_bytes(data[pos + 4:pos + 8], "little"),
            "sample_rate": int.from_bytes(data[pos + 8:pos + 12], "big"),
            "channels": int.from_bytes(data[pos + 12:pos + 16], "big"),
            "bit_depth": int.from_bytes(data[pos + 16:pos + 20], "big"),
        })
    return parsed


def _duration_from_rfwv(header: dict) -> float:
    data_size = int(header.get("size_be") or 0)
    sample_rate = int(header.get("sample_rate") or 0)
    channels = int(header.get("channels") or 0)
    bit_depth = int(header.get("bit_depth") or 0)
    bytes_per_frame = channels * max(1, bit_depth // 8)
    if data_size <= 0 or sample_rate <= 0 or bytes_per_frame <= 0:
        return 0.0
    return data_size / bytes_per_frame / sample_rate


def cmd_probe(args: argparse.Namespace) -> int:
    log_path, handle = _open_log(args.log)
    print(f"log: {log_path}")
    payload = sp404_protocol.SP404_HANDSHAKE
    ok_count = 0
    try:
        for idx in range(args.count):
            port = sp404_protocol.find_sp404_librarian_port()
            _write_event(handle, {
                "event": "open",
                "iteration": idx + 1,
                "port": port,
            })
            _write_event(handle, {
                "event": "tx",
                "iteration": idx + 1,
                "label": "handshake",
                "len": len(payload),
                "hex": _hex(payload),
            })
            try:
                port, response = transact(payload, args.timeout, "handshake")
            except Exception as exc:
                _write_event(handle, {
                    "event": "error",
                    "iteration": idx + 1,
                    "error": str(exc),
                })
                print(f"{idx + 1}: ERROR {exc}")
                continue

            _write_event(handle, {
                "event": "rx",
                "iteration": idx + 1,
                "label": "handshake_response",
                "len": len(response),
                "hex": _hex(response),
            })
            if response:
                ok_count += 1
                print(f"{idx + 1}: {len(response)} bytes <- {_hex(response)}")
            else:
                print(f"{idx + 1}: no response")
            if idx + 1 < args.count:
                time.sleep(args.delay)
    finally:
        handle.close()
    print(f"responses: {ok_count}/{args.count}")
    return 0 if ok_count else 1


def cmd_project_probe(args: argparse.Namespace) -> int:
    """Run the captured read-only project-list init sequence."""
    payloads = [
        ("app_handshake", APP_HANDSHAKE),
        ("project_init", PROJECT_INIT),
        ("project_list", PROJECT_LIST),
    ]
    try:
        port, results = _transact_many(payloads, args.timeout)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"port: {port}")
    combined = b""
    for label, payload, response in results:
        combined += response
        print(f"\n{label}")
        print(f"  tx {len(payload)}: {_hex(payload)}")
        print(f"  rx {len(response)}: {_hex(response[:args.bytes])}")
        if len(response) > args.bytes:
            print(f"  ... truncated {len(response) - args.bytes} bytes")

    projects = []
    for match in re.finditer(rb"PROJECT_[0-9]{2}", combined):
        name = match.group(0).decode("ascii")
        if name not in projects:
            projects.append(name)
    print(f"\nprojects: {', '.join(projects) if projects else '(none parsed)'}")
    return 0 if projects else 1


def cmd_read_path(args: argparse.Namespace) -> int:
    """Read the first header chunk for one SP-404 remote sample path."""
    try:
        remote_path = args.path or _sp404_pad_path(args.project, args.bank, args.pad)
        _build_file_read_path(remote_path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        port, results, file_key = _transact_read_path(
            remote_path,
            args.timeout,
            preamble=args.preamble,
            dynamic_key=args.dynamic_key,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"port: {port}")
    print(f"path: {remote_path}")
    if args.dynamic_key:
        key_text = file_key.hex(" ") if file_key else "(none)"
        print(f"file_key: {key_text}")
    combined = b""
    for label, payload, response in results:
        combined += response
        print(f"\n{label}")
        print(f"  tx {len(payload)}: {_hex(payload[:args.bytes])}")
        if len(payload) > args.bytes:
            print(f"  ... tx truncated {len(payload) - args.bytes} bytes")
        print(f"  rx {len(response)}: {_hex(response[:args.bytes])}")
        if len(response) > args.bytes:
            print(f"  ... rx truncated {len(response) - args.bytes} bytes")
        preview = _ascii_preview(response[:args.bytes])
        if preview.strip("."):
            print(f"  ascii: {preview}")

    rfwv = _parse_rfwv_header(combined)
    if not rfwv:
        print("\nRFWV: not found")
        return 1

    print("\nRFWV:")
    for key, value in rfwv.items():
        print(f"  {key}: {value}")
    duration = _duration_from_rfwv(rfwv)
    if duration:
        print(f"  duration: {duration:.2f}s")
    return 0


def cmd_read_bank(args: argparse.Namespace) -> int:
    """Read the first header chunk for every pad in one SP bank."""
    try:
        if not re.fullmatch(r"PROJECT_[0-9]{2}", args.project):
            raise ValueError("project must look like PROJECT_05")
        if not 1 <= args.bank <= 10:
            raise ValueError("bank must be 1-10")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    port = sp404_protocol.find_sp404_librarian_port()
    if not port:
        print("ERROR: SP-404 CDC port not found", file=sys.stderr)
        return 1

    responses_by_pad: dict[int, bytearray] = {
        pad: bytearray() for pad in range(1, 17)
    }
    file_keys: dict[int, bytes | None] = {}
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            sp404_protocol.configure_sp404_librarian_fd(fd)
            time.sleep(0.2)
            for _label, payload in [
                ("app_handshake", APP_HANDSHAKE),
                ("project_init", PROJECT_INIT),
                ("project_list", PROJECT_LIST),
            ]:
                os.write(fd, payload)
                _read_until_quiet(fd, args.timeout)

            for pad in range(1, 17):
                remote_path = _sp404_pad_path(args.project, args.bank, pad)
                read_path = _build_file_read_path(remote_path)
                if pad == 1:
                    read_path = FILE_OPEN_PREAMBLE + read_path
                os.write(fd, read_path)
                response = _read_until_quiet(fd, args.timeout)
                responses_by_pad[pad].extend(response)
                file_key = _extract_file_key(response) if args.dynamic_key else None
                file_keys[pad] = file_key
                if args.dynamic_key and file_key is None:
                    continue
                for _label, payload in _file_read_followup_payloads(file_key):
                    os.write(fd, payload)
                    responses_by_pad[pad].extend(_read_until_quiet(fd, args.timeout))
        finally:
            os.close(fd)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"port: {port}")
    print(f"project: {args.project}")
    print(f"bank: {args.bank}")
    loaded = 0
    for pad in range(1, 17):
        data = bytes(responses_by_pad[pad])
        header = _parse_rfwv_header(data)
        pad_id = f"{chr(ord('A') + args.bank - 1)}{pad:02d}"
        if not header:
            key = file_keys.get(pad)
            key_text = " key=-" if not args.dynamic_key else f" key={key.hex(' ') if key else '-'}"
            print(f"{pad_id}: empty or unreadable ({len(data)} rx bytes){key_text}")
            continue
        loaded += 1
        key = file_keys.get(pad)
        key_text = "" if not args.dynamic_key else f" key={key.hex(' ') if key else '-'}"
        duration = _duration_from_rfwv(header)
        print(
            f"{pad_id}: RFWV {header.get('sample_rate')} Hz "
            f"{header.get('channels')}ch {header.get('bit_depth')}bit "
            f"{header.get('size_be')} bytes {duration:.2f}s{key_text}"
        )
    print(f"loaded: {loaded}/16")
    return 0


def cmd_send_hex(args: argparse.Namespace) -> int:
    if not args.i_understand_this_can_write_to_the_sp:
        print("Refusing arbitrary send without --i-understand-this-can-write-to-the-sp", file=sys.stderr)
        return 2
    log_path, handle = _open_log(args.log)
    payload = args.hex_bytes
    try:
        _write_event(handle, {
            "event": "tx",
            "label": args.label,
            "len": len(payload),
            "hex": _hex(payload),
        })
        port, response = transact(payload, args.timeout, args.label)
        _write_event(handle, {
            "event": "rx",
            "label": f"{args.label}_response",
            "port": port,
            "len": len(response),
            "hex": _hex(response),
        })
    finally:
        handle.close()
    print(f"log: {log_path}")
    print(f"rx {len(response)} bytes: {_hex(response)}")
    return 0 if response else 1


def _load_rx_packets(path: Path) -> list[bytes]:
    packets: list[bytes] = []
    for event in _load_events(path):
        if event.get("event") != "rx":
            continue
        hx = event.get("hex", "")
        if hx:
            packets.append(_parse_hex(hx))
    return packets


def _checksum_candidates(packet: bytes) -> list[str]:
    if len(packet) < 3:
        return []
    body = packet[:-1]
    last = packet[-1]
    candidates: list[str] = []
    if (sum(body) & 0xFF) == last:
        candidates.append("sum8")
    if ((-sum(body)) & 0xFF) == last:
        candidates.append("neg_sum8")
    xor = 0
    for b in body:
        xor ^= b
    if xor == last:
        candidates.append("xor8")
    if len(packet) >= 4:
        tail_be = int.from_bytes(packet[-2:], "big")
        tail_le = int.from_bytes(packet[-2:], "little")
        body2 = packet[:-2]
        if (sum(body2) & 0xFFFF) == tail_be:
            candidates.append("sum16be")
        if (sum(body2) & 0xFFFF) == tail_le:
            candidates.append("sum16le")
    return candidates


def cmd_analyze(args: argparse.Namespace) -> int:
    packets = _load_rx_packets(Path(args.log).expanduser())
    if not packets:
        print("no rx packets found")
        return 1

    print(f"rx packets: {len(packets)}")
    lengths = sorted({len(p) for p in packets})
    print(f"lengths: {lengths}")
    for idx, packet in enumerate(packets, 1):
        print(f"\n#{idx} len={len(packet)} checksum_guess={_checksum_candidates(packet) or '-'}")
        print(_hex(packet))

    min_len = min(len(p) for p in packets)
    if len(packets) > 1:
        stable = []
        variable = []
        for pos in range(min_len):
            values = {p[pos] for p in packets}
            if len(values) == 1:
                stable.append((pos, next(iter(values))))
            else:
                variable.append((pos, sorted(values)))
        print("\nstable byte positions:")
        print(" ".join(f"{pos}:{value:02x}" for pos, value in stable) or "-")
        print("\nvariable byte positions:")
        print(" ".join(
            f"{pos}:[{','.join(f'{v:02x}' for v in values)}]"
            for pos, values in variable
        ) or "-")
    return 0


_DTRACE_HEADER_RE = re.compile(
    r"^(TX|RX|IOCTL|OPEN) pid=([0-9]+) exec=([^ ]+)"
    r"(?: fd=([0-9]+))?(?: len=([0-9]+) captured=([0-9]+))?"
)
_DTRACE_HEX_RE = re.compile(r"^ *[0-9a-f]+: *((?:[0-9a-f]{2} +){1,16})")


def _flush_dtrace_event(events: list[dict], current: dict | None):
    if current is None:
        return
    data = current.pop("_data", bytearray())
    captured = current.get("captured", 0)
    if current.get("event") in {"tx", "rx"}:
        current["hex"] = _hex(bytes(data[:captured]))
    events.append(current)


def _parse_dtrace_text(path: Path) -> list[dict]:
    events: list[dict] = []
    current: dict | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _DTRACE_HEADER_RE.match(line)
        if match:
            _flush_dtrace_event(events, current)
            kind, pid, exe, fd, length, captured = match.groups()
            current = {
                "event": kind.lower(),
                "pid": int(pid),
                "exec": exe,
            }
            if fd is not None:
                current["fd"] = int(fd)
            if length is not None and captured is not None:
                current["len"] = int(length)
                current["captured"] = int(captured)
                current["_data"] = bytearray()
            continue
        if current and current.get("event") in {"tx", "rx"}:
            hex_match = _DTRACE_HEX_RE.match(line)
            if hex_match:
                current["_data"].extend(
                    int(part, 16) for part in hex_match.group(1).split()
                )
    _flush_dtrace_event(events, current)
    return events


def cmd_parse_dtrace(args: argparse.Namespace) -> int:
    src = Path(args.trace).expanduser()
    events = _parse_dtrace_text(src)
    if args.only_fd is not None:
        events = [
            event for event in events
            if event.get("event") not in {"tx", "rx"} or event.get("fd") == args.only_fd
        ]
    if args.max_len:
        events = [
            event for event in events
            if event.get("event") not in {"tx", "rx"} or event.get("len", 0) <= args.max_len
        ]

    if args.out:
        out = Path(args.out).expanduser()
    else:
        out = src.with_suffix(".jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")

    print(f"events: {len(events)}")
    print(f"out: {out}")
    _print_capture_summary(events)
    return 0


def _print_capture_summary(events: list[dict]):
    counts = Counter(event.get("event", "?") for event in events)
    print(f"counts: {dict(counts)}")

    txrx = [event for event in events if event.get("event") in {"tx", "rx"}]
    lengths = Counter((event["event"], event.get("len")) for event in txrx)
    print("lengths:")
    for (event_name, length), count in lengths.most_common(20):
        print(f"  {event_name} len={length}: {count}")

    paths = []
    strings = []
    for event in txrx:
        hx = event.get("hex", "")
        if not hx:
            continue
        data = _parse_hex(hx)
        for match in re.finditer(rb"/SP404REMOTE///[^\x00\xff\xf7\s]+", data):
            paths.append(match.group(0).decode("ascii", "replace"))
        for match in re.finditer(rb"[A-Za-z0-9_ .'/()\-]{4,}", data):
            value = match.group(0).decode("ascii", "replace").strip()
            if value and not value.startswith("/SP404REMOTE///"):
                strings.append(value)

    if paths:
        print("remote paths:")
        for path, count in Counter(paths).most_common(20):
            print(f"  {count} {path}")
    if strings:
        print("printable strings:")
        for value, count in Counter(strings).most_common(20):
            print(f"  {count} {value}")


def _build_tx_stream(events: list[dict]) -> tuple[bytes, list[tuple[int, int, int]]]:
    stream_parts: list[bytes] = []
    spans: list[tuple[int, int, int]] = []
    offset = 0
    for idx, event in enumerate(events):
        if event.get("event") != "tx":
            continue
        hx = event.get("hex", "")
        if not hx:
            continue
        data = _parse_hex(hx)
        stream_parts.append(data)
        spans.append((offset, offset + len(data), idx))
        offset += len(data)
    return b"".join(stream_parts), spans


def _event_index_for_stream_offset(spans: list[tuple[int, int, int]], offset: int) -> int:
    for start, end, event_idx in spans:
        if start <= offset < end:
            return event_idx
    return -1


def _iter_serial_command_frames(
    stream: bytes,
    spans: list[tuple[int, int, int]],
) -> list[dict]:
    frames: list[dict] = []
    pos = 0
    prefix = bytes.fromhex("13 60 e0 06")
    sysex = bytes.fromhex("f0 41 7a 03")
    while True:
        start = stream.find(prefix, pos)
        if start < 0:
            break
        if start + 21 > len(stream) or stream[start + 16:start + 20] != sysex:
            pos = start + 1
            continue

        declared = int.from_bytes(stream[start + 12:start + 16], "little")
        end = start + 16 + declared
        if declared < 17 or end > len(stream):
            pos = start + 1
            continue

        body_len = max(0, declared - 16)
        body_start = start + 31
        body_end = min(body_start + body_len, end - 1)
        frames.append({
            "stream_offset": start,
            "event_index": _event_index_for_stream_offset(spans, start),
            "op": stream[start + 20],
            "declared": declared,
            "frame_len": end - start,
            "body_start": body_start,
            "body_end": body_end,
            "body_len": max(0, body_end - body_start),
            "has_f7": stream[end - 1] == 0xF7,
        })
        pos = end
    return frames


def _load_wav_as_big_endian_pcm(path: Path) -> tuple[dict, bytes]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.getnframes()
        pcm = handle.readframes(frames)
    if sample_width != 2:
        raise ValueError("only 16-bit PCM WAV files are supported for comparison")
    converted = b"".join(pcm[idx:idx + 2][::-1] for idx in range(0, len(pcm), 2))
    return {
        "channels": channels,
        "sample_width": sample_width,
        "sample_rate": sample_rate,
        "frames": frames,
        "pcm_bytes": len(pcm),
    }, converted


def _load_template_write_wav(path: Path) -> tuple[dict, bytes]:
    info, converted = _load_wav_as_big_endian_pcm(path)
    if info["sample_rate"] != 48000:
        raise ValueError("template writer requires a 48 kHz WAV")
    if info["channels"] != 1:
        raise ValueError("template writer requires a mono WAV")
    if info["sample_width"] != 2:
        raise ValueError("template writer requires a 16-bit PCM WAV")
    if info["frames"] != 48000 or len(converted) != WRITE_TEMPLATE_AUDIO_BYTES:
        raise ValueError("template writer requires exactly 1.000s / 48,000 frames")
    return info, converted


def _copy_replace_fixed(data: bytearray, old: bytes, new: bytes) -> None:
    if len(old) != len(new):
        raise ValueError(f"replacement length mismatch: {old!r} -> {new!r}")
    start = 0
    replaced = 0
    while True:
        pos = data.find(old, start)
        if pos < 0:
            break
        data[pos:pos + len(old)] = new
        replaced += 1
        start = pos + len(old)
    if replaced == 0:
        raise ValueError(f"template token not found: {old!r}")


def _patch_template_audio(stream: bytearray, pcm_be: bytes) -> int:
    frames = _iter_serial_command_frames(bytes(stream), [(0, len(stream), 0)])
    audio_frames = []
    for frame in frames:
        if frame["op"] != 0x06:
            continue
        body = stream[int(frame["body_start"]):int(frame["body_end"])]
        if len(body) >= 512 and b"RFWV" not in body:
            audio_frames.append(frame)

    total = sum(int(frame["body_len"]) for frame in audio_frames)
    if total != len(pcm_be):
        raise ValueError(f"template audio payload is {total} bytes, source is {len(pcm_be)} bytes")

    offset = 0
    for frame in audio_frames:
        body_start = int(frame["body_start"])
        body_end = int(frame["body_end"])
        chunk = pcm_be[offset:offset + (body_end - body_start)]
        stream[body_start:body_end] = chunk
        offset += len(chunk)
    return len(audio_frames)


def _patch_template_metadata(stream: bytearray, sample_name: str, pad_index: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9_. -]+", "_", sample_name).strip(" .")
    if not safe:
        safe = "sample"
    name_bytes = safe.encode("ascii", "ignore")[:len(WRITE_TEMPLATE_SAMPLE_NAME)]
    replacement = name_bytes + (b"\x00" * (len(WRITE_TEMPLATE_SAMPLE_NAME) - len(name_bytes)))

    pos = stream.find(WRITE_TEMPLATE_SAMPLE_NAME)
    if pos < 0:
        raise ValueError("template sample-name packet not found")
    packet_start = stream.rfind(bytes.fromhex("13 60 e0 05"), 0, pos)
    if packet_start < 0 or packet_start + 18 >= len(stream) or stream[packet_start + 16] != 0x1E:
        raise ValueError("template sample-name metadata packet shape changed")
    stream[pos:pos + len(WRITE_TEMPLATE_SAMPLE_NAME)] = replacement
    stream[packet_start + 17] = pad_index & 0xFF
    return name_bytes.decode("ascii", "ignore")


def _tx_event_offsets(events: list[dict]) -> list[tuple[int, int, int]]:
    offsets: list[tuple[int, int, int]] = []
    cursor = 0
    for idx, event in enumerate(events):
        if event.get("event") != "tx":
            continue
        data = _parse_hex(event.get("hex", ""))
        offsets.append((idx, cursor, cursor + len(data)))
        cursor += len(data)
    return offsets


def _ops_in_stream_slice(stream: bytes) -> set[int]:
    frames = _iter_serial_command_frames(stream, [(0, len(stream), 0)])
    return {int(frame["op"]) for frame in frames}


def _extract_response_status(response: bytes) -> bytes | None:
    pos = response.rfind(PATH_OPEN_RESPONSE_MARKER)
    if pos < 0 or pos + 9 > len(response):
        return None
    status = response[pos + 4:pos + 9]
    if status == b"\x7f" * 5:
        return None
    return status


def _patch_future_file_key(stream: bytearray, cursor: int, file_key: bytes) -> int:
    frames = _iter_serial_command_frames(bytes(stream[cursor:]), [(0, len(stream) - cursor, 0)])
    patched = 0
    for frame in frames:
        op = int(frame["op"])
        if op not in {0x03, 0x04, 0x06, 0x07, 0x13}:
            continue
        start = cursor + int(frame["stream_offset"])
        key_start = start + 24
        key_end = key_start + 2
        if key_end <= len(stream):
            stream[key_start:key_end] = file_key
            patched += 1
    return patched


def _patch_future_dir_key(stream: bytearray, cursor: int, dir_key: bytes) -> int:
    frames = _iter_serial_command_frames(bytes(stream[cursor:]), [(0, len(stream) - cursor, 0)])
    patched = 0
    for frame in frames:
        if int(frame["op"]) != 0x0D:
            continue
        start = cursor + int(frame["stream_offset"])
        key_start = start + 21
        key_end = key_start + 5
        if key_end <= len(stream):
            stream[key_start:key_end] = dir_key
            patched += 1
            break
    return patched


def _build_template_write_stream(
    *,
    template: Path,
    wav: Path,
    project: str,
    bank: int,
    pad: int,
    sample_name: str,
) -> tuple[list[dict], bytearray, dict]:
    if not re.fullmatch(r"PROJECT_[0-9]{2}", project):
        raise ValueError("project must look like PROJECT_03")
    if bank != 1:
        raise ValueError("template writer is currently limited to Bank A / bank=1")
    if not 1 <= pad <= 16:
        raise ValueError("pad must be 1-16")

    events = _load_events(template)
    if not events:
        raise ValueError(f"template capture has no events: {template}")

    info, pcm_be = _load_template_write_wav(wav)
    stream, _spans = _build_tx_stream(events)
    data = bytearray(stream)

    _copy_replace_fixed(data, WRITE_TEMPLATE_PROJECT, project.encode("ascii"))
    target_pad = f"BANK{bank}-{pad:02d}".encode("ascii")
    _copy_replace_fixed(data, WRITE_TEMPLATE_PAD, target_pad)
    audio_frames = _patch_template_audio(data, pcm_be)
    patched_name = _patch_template_metadata(data, sample_name, pad - 1)

    return events, data, {
        "audio_frames": audio_frames,
        "sample_name": patched_name,
        "wav_info": info,
        "target_path": _sp404_pad_path(project, bank, pad),
        "template": str(template),
    }


def cmd_write_pad_template(args: argparse.Namespace) -> int:
    if not args.i_understand_this_writes_to_the_sp:
        print("Refusing write without --i-understand-this-writes-to-the-sp", file=sys.stderr)
        return 2

    try:
        events, stream, plan = _build_template_write_stream(
            template=Path(args.template).expanduser(),
            wav=Path(args.wav).expanduser(),
            project=args.project,
            bank=args.bank,
            pad=args.pad,
            sample_name=args.sample_name or Path(args.wav).stem,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"template: {plan['template']}")
    print(f"target: {plan['target_path']}")
    print(
        "wav:"
        f" {plan['wav_info']['sample_rate']} Hz"
        f" {plan['wav_info']['channels']}ch"
        f" {plan['wav_info']['sample_width'] * 8}bit"
        f" {plan['wav_info']['frames']} frames"
    )
    print(f"sample_name: {plan['sample_name']}")
    print(f"audio_frames: {plan['audio_frames']}")
    if args.dry_run:
        print("dry_run: not sending")
        return 0

    port = sp404_protocol.find_sp404_librarian_port()
    if not port:
        print("ERROR: SP-404 CDC port not found", file=sys.stderr)
        return 1

    log_path, handle = _open_log(args.log)
    print(f"port: {port}")
    print(f"log: {log_path}")

    offsets = _tx_event_offsets(events)
    offset_by_event = {idx: (start, end) for idx, start, end in offsets}
    cursor = 0
    response_count = 0
    file_keys: list[str] = []
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        sp404_protocol.configure_sp404_librarian_fd(fd)
        time.sleep(0.2)
        last_response_cursor = 0
        for idx, event in enumerate(events):
            if event.get("event") != "tx":
                continue
            start, end = offset_by_event[idx]
            payload = bytes(stream[start:end])
            os.write(fd, payload)
            cursor = end
            _write_event(handle, {
                "event": "tx",
                "template_event": idx,
                "len": len(payload),
                "hex": _hex(payload),
            })

            next_is_rx = idx + 1 < len(events) and events[idx + 1].get("event") == "rx"
            if next_is_rx:
                response = _read_until_quiet(fd, args.timeout)
                response_count += 1
                _write_event(handle, {
                    "event": "rx",
                    "template_event": idx + 1,
                    "len": len(response),
                    "hex": _hex(response),
                })
                sent_since_response = bytes(stream[last_response_cursor:cursor])
                ops = _ops_in_stream_slice(sent_since_response)
                status = _extract_response_status(response)
                if 0x0C in ops:
                    if not status:
                        raise RuntimeError("directory open did not return a usable key")
                    patched = _patch_future_dir_key(stream, cursor, status)
                    _write_event(handle, {
                        "event": "info",
                        "label": "dir_key",
                        "hex": _hex(status),
                        "patched": patched,
                    })
                if 0x00 in ops:
                    file_key = _extract_file_key(response)
                    if not file_key:
                        raise RuntimeError("target path open did not return a usable file key")
                    patched = _patch_future_file_key(stream, cursor, file_key)
                    key_text = file_key.hex(" ")
                    file_keys.append(key_text)
                    _write_event(handle, {
                        "event": "info",
                        "label": "file_key",
                        "hex": key_text,
                        "patched": patched,
                    })
                last_response_cursor = cursor
            if args.delay:
                time.sleep(args.delay)
    except Exception as exc:
        _write_event(handle, {"event": "error", "error": str(exc)})
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        os.close(fd)
        handle.close()

    print(f"responses: {response_count}")
    if file_keys:
        print(f"file_keys: {', '.join(file_keys)}")

    if args.verify:
        verify_error = "RFWV not found"
        for attempt in range(1, 4):
            time.sleep(0.5 * attempt)
            try:
                _port, results, file_key = _transact_read_path(
                    plan["target_path"],
                    args.timeout,
                    preamble=True,
                    dynamic_key=True,
                )
                combined = b"".join(response for _label, _payload, response in results)
                header = _parse_rfwv_header(combined)
                key_text = file_key.hex(" ") if file_key else "-"
                print(f"verify_file_key: {key_text}")
                if not header:
                    continue
                duration = _duration_from_rfwv(header)
                print(
                    "verify:"
                    f" RFWV {header.get('sample_rate')} Hz"
                    f" {header.get('channels')}ch"
                    f" {header.get('bit_depth')}bit"
                    f" {header.get('size_be')} bytes"
                    f" {duration:.2f}s"
                )
                break
            except Exception as exc:
                verify_error = str(exc)
        else:
            print(f"verify failed: {verify_error}", file=sys.stderr)
            return 1

    return 0


def _prefix_match_len(left: bytes, right: bytes) -> int:
    limit = min(len(left), len(right))
    for idx in range(limit):
        if left[idx] != right[idx]:
            return idx
    return limit


def _infer_import_target(events: list[dict]) -> str:
    paths: Counter[str] = Counter()
    for event in events:
        if event.get("event") not in {"tx", "rx"}:
            continue
        hx = event.get("hex", "")
        if not hx:
            continue
        data = _parse_hex(hx)
        for match in re.finditer(rb"/SP404REMOTE///[^\x00\xff\xf7\s]+\.SMP", data):
            paths[match.group(0).decode("ascii", "replace")] += 1
    if not paths:
        return ""
    return paths.most_common(1)[0][0]


def _paths_in_packet(data: bytes) -> list[str]:
    return [
        match.group(0).decode("ascii", "replace")
        for match in re.finditer(rb"/SP404REMOTE///[^\x00\xff\xf7\s]+", data)
    ]


def _decode_nul_string(slot: bytes) -> str:
    return slot.split(b"\x00", 1)[0].decode("ascii", "replace")


def _frame_data(frame: dict, stream: bytes) -> bytes:
    start = int(frame["stream_offset"])
    return stream[start:start + int(frame["frame_len"])]


def _command_body(frame: dict, stream: bytes) -> bytes:
    return stream[int(frame["body_start"]):int(frame["body_end"])]


def _metadata_pad_index(packet: bytes) -> int | None:
    # Two captures targeting A1 and A2 only differ in this command field:
    # A1 has 0, A2 has 1. Treat it as a zero-based pad index candidate.
    if (
        len(packet) >= 191
        and packet.startswith(bytes.fromhex("13 60 e0 05"))
        and packet[16] == 0x1E
    ):
        return packet[17]
    return None


def _print_write_wav_match(frames: list[dict], stream: bytes, wav_path: Path):
    info, expected = _load_wav_as_big_endian_pcm(wav_path)
    print(
        "\nsource WAV:"
        f" {info['sample_rate']} Hz {info['channels']}ch"
        f" {info['sample_width'] * 8}bit {info['frames']} frames"
        f" ({info['pcm_bytes']} PCM bytes)"
    )

    upload_frames = [
        frame for frame in frames
        if frame["op"] == 0x06 and frame["body_len"] >= 512
    ]
    best: tuple[int, int, list[tuple[dict, int]]] = (0, 0, [])
    for start_idx in range(len(upload_frames)):
        expected_pos = 0
        matched: list[tuple[dict, int]] = []
        for frame in upload_frames[start_idx:]:
            body = stream[frame["body_start"]:frame["body_end"]]
            if not body:
                continue
            want = expected[expected_pos:expected_pos + len(body)]
            common = _prefix_match_len(body, want)
            if common <= 0:
                break
            matched.append((frame, common))
            expected_pos += common
            if common != len(body) or expected_pos >= len(expected):
                break
        if expected_pos > best[1]:
            best = (start_idx, expected_pos, matched)

    if not best[2]:
        print("upload match: no contiguous WAV payload match found")
        return

    complete = best[1] == len(expected)
    first_frame = best[2][0][0]
    last_frame = best[2][-1][0]
    print(
        "upload match:"
        f" {'complete' if complete else 'partial'}"
        f" {best[1]}/{len(expected)} bytes"
        f" across {len(best[2])} op=06 data frames"
        f" (events {first_frame['event_index']}..{last_frame['event_index']})"
    )
    if complete:
        print("upload conversion: WAV 16-bit PCM bytes are byte-swapped to big-endian on the wire")

    rfwv_frames = [
        frame for frame in frames
        if frame["op"] == 0x06 and b"RFWV" in _command_body(frame, stream)
    ]
    for frame in rfwv_frames[:3]:
        header = _parse_rfwv_header(_command_body(frame, stream))
        if not header:
            continue
        smp_size = int(header.get("size_be") or 0) + 8
        non_audio = smp_size - len(expected)
        print(
            "RFWV size interpretation:"
            f" field={header.get('size_be')}"
            f" full_smp_bytes={smp_size}"
            f" non_audio_bytes={non_audio}"
        )


def cmd_write_summary(args: argparse.Namespace) -> int:
    events = _load_events(Path(args.log).expanduser())
    if not events:
        print("no JSONL events found")
        return 1

    _print_capture_summary(events)
    target = args.target_path or _infer_import_target(events)
    tmp_target = target[:-4] + ".TMP" if target.endswith(".SMP") else ""
    if target:
        print(f"\nlikely import target: {target}")
        if tmp_target:
            print(f"temporary target: {tmp_target}")

    if target:
        print("\ntarget path events:")
        shown = 0
        for idx, event in enumerate(events):
            if event.get("event") not in {"tx", "rx"}:
                continue
            data = _parse_hex(event.get("hex", ""))
            paths = _paths_in_packet(data)
            if target not in paths and tmp_target not in paths:
                continue
            op = data[20] if len(data) > 21 and data.startswith(bytes.fromhex("13 60 e0 06")) else None
            op_label = f"op=0x{op:02x}" if op is not None else "op=-"
            print(
                f"  {idx:05d} {event['event']} len={event.get('len')} "
                f"{op_label} paths={', '.join(paths)}"
            )
            shown += 1
            if shown >= args.show_path_events:
                print("  ... truncated")
                break

    stream, spans = _build_tx_stream(events)
    frames = _iter_serial_command_frames(stream, spans)
    print(f"\nserial command frames: {len(frames)}")
    op_counts = Counter(frame["op"] for frame in frames)
    for op, count in op_counts.most_common(20):
        print(f"  op=0x{op:02x}: {count}")

    path_pair_frames = [frame for frame in frames if frame["op"] == 0x17]
    if path_pair_frames:
        print("\nop=0x17 path-pair frames:")
        for frame in path_pair_frames:
            body = _command_body(frame, stream)
            src_or_final = _decode_nul_string(body[:200])
            tmp_or_peer = _decode_nul_string(body[200:400])
            print(
                f"  event={frame['event_index']:05d}"
                f" slot0={src_or_final}"
                f" slot1={tmp_or_peer}"
            )

    interesting = [
        frame for frame in frames
        if (
            frame["op"] == 0x06 and frame["body_len"] >= 512
        ) or b"RFWV" in stream[frame["body_start"]:frame["body_end"]]
    ]
    if interesting:
        print("\nwrite/data frames:")
        for frame in interesting:
            data = _frame_data(frame, stream)
            body = _command_body(frame, stream)
            rfwv = _parse_rfwv_header(body)
            extra = ""
            if rfwv:
                extra = (
                    f" RFWV size={rfwv.get('size_be')}"
                    f" sr={rfwv.get('sample_rate')}"
                    f" ch={rfwv.get('channels')}"
                    f" bits={rfwv.get('bit_depth')}"
                )
            print(
                f"  event={frame['event_index']:05d}"
                f" op=0x{frame['op']:02x}"
                f" declared=0x{frame['declared']:x}"
                f" body={frame['body_len']}"
                f" file_handle_candidate=0x{data[25]:02x}"
                f" f7={frame['has_f7']}{extra}"
            )

    name = args.sample_name.encode("utf-8") if args.sample_name else b""
    if name:
        print("\nsample-name metadata events:")
        for idx, event in enumerate(events):
            if event.get("event") not in {"tx", "rx"}:
                continue
            data = _parse_hex(event.get("hex", ""))
            if name in data:
                op = data[20] if len(data) > 21 and data.startswith(bytes.fromhex("13 60 e0 06")) else None
                op_label = f"op=0x{op:02x}" if op is not None else "op=-"
                pad_index = _metadata_pad_index(data)
                pad_label = "" if pad_index is None else f" pad_index_candidate={pad_index}"
                print(f"  {idx:05d} {event['event']} len={event.get('len')} {op_label}{pad_label}")

    if args.wav:
        try:
            _print_write_wav_match(frames, stream, Path(args.wav).expanduser())
        except Exception as exc:
            print(f"\nsource WAV comparison failed: {exc}", file=sys.stderr)
            return 1
    return 0


def cmd_capture_summary(args: argparse.Namespace) -> int:
    events = _load_events(Path(args.log).expanduser())
    if not events:
        print("no JSONL events found")
        return 1
    _print_capture_summary(events)
    if args.show_first:
        shown = 0
        for idx, event in enumerate(events):
            if event.get("event") not in {"tx", "rx"}:
                continue
            hx = event.get("hex", "")
            data = _parse_hex(hx) if hx else b""
            print(
                f"{idx:05d} {event['event']} len={event.get('len')} "
                f"{_hex(data[:args.bytes])} {_ascii_preview(data[:args.bytes])}"
            )
            shown += 1
            if shown >= args.show_first:
                break
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="send the known handshake and log responses")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--delay", type=float, default=0.75)
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--log", default="")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("project-probe", help="run captured read-only project-list commands")
    p.add_argument("--timeout", type=float, default=1.5)
    p.add_argument("--bytes", type=int, default=256)
    p.set_defaults(func=cmd_project_probe)

    p = sub.add_parser("read-path", help="read one observed SP remote .SMP header chunk")
    p.add_argument("--project", default="PROJECT_05")
    p.add_argument("--bank", type=int, default=1)
    p.add_argument("--pad", type=int, default=1)
    p.add_argument("--path", default="", help="override the generated remote path")
    p.add_argument("--timeout", type=float, default=1.5)
    p.add_argument("--bytes", type=int, default=160)
    p.add_argument(
        "--no-preamble",
        dest="preamble",
        action="store_false",
        help="skip the observed file-open preamble before the first path read",
    )
    p.add_argument(
        "--static-key",
        dest="dynamic_key",
        action="store_false",
        help="use the old captured follow-up key instead of the path-open response key",
    )
    p.set_defaults(func=cmd_read_path, preamble=True, dynamic_key=True)

    p = sub.add_parser("read-bank", help="read observed SP remote .SMP headers for one bank")
    p.add_argument("--project", default="PROJECT_05")
    p.add_argument("--bank", type=int, default=1)
    p.add_argument("--timeout", type=float, default=1.5)
    p.add_argument(
        "--static-key",
        dest="dynamic_key",
        action="store_false",
        help="use the old captured follow-up key instead of per-path response keys",
    )
    p.set_defaults(func=cmd_read_bank, dynamic_key=True)

    p = sub.add_parser("send-hex", help="send captured hex bytes to the SP")
    p.add_argument("hex_bytes", type=_parse_hex)
    p.add_argument("--label", default="manual")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--log", default="")
    p.add_argument("--i-understand-this-can-write-to-the-sp", action="store_true")
    p.set_defaults(func=cmd_send_hex)

    p = sub.add_parser(
        "write-pad-template",
        help="lab-only 1s mono sample write using the verified write capture template",
    )
    p.add_argument("wav", help="48 kHz mono 16-bit 1-second WAV")
    p.add_argument("--project", required=True)
    p.add_argument("--bank", type=int, default=1)
    p.add_argument("--pad", type=int, required=True)
    p.add_argument("--sample-name", default="")
    p.add_argument("--template", default=str(WRITE_TEMPLATE_CAPTURE))
    p.add_argument("--timeout", type=float, default=1.5)
    p.add_argument("--delay", type=float, default=0.05)
    p.add_argument("--log", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-verify", dest="verify", action="store_false")
    p.add_argument("--i-understand-this-writes-to-the-sp", action="store_true")
    p.set_defaults(func=cmd_write_pad_template, verify=True)

    p = sub.add_parser("analyze", help="summarize rx packets from a JSONL log")
    p.add_argument("log")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("parse-dtrace", help="convert a Mac DTrace text capture to JSONL")
    p.add_argument("trace")
    p.add_argument("--out", default="")
    p.add_argument("--only-fd", type=int, default=None)
    p.add_argument("--max-len", type=int, default=4096)
    p.set_defaults(func=cmd_parse_dtrace)

    p = sub.add_parser("capture-summary", help="summarize TX/RX events from parsed JSONL")
    p.add_argument("log")
    p.add_argument("--show-first", type=int, default=0)
    p.add_argument("--bytes", type=int, default=96)
    p.set_defaults(func=cmd_capture_summary)

    p = sub.add_parser("write-summary", help="summarize an import/write capture without replaying packets")
    p.add_argument("log")
    p.add_argument("--wav", default="", help="optional source WAV to compare against upload frames")
    p.add_argument("--target-path", default="", help="override inferred SP remote .SMP path")
    p.add_argument("--sample-name", default="", help="sample name to locate in metadata packets")
    p.add_argument("--show-path-events", type=int, default=40)
    p.set_defaults(func=cmd_write_summary)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
