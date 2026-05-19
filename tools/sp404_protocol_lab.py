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

    p = sub.add_parser("send-hex", help="send captured hex bytes to the SP")
    p.add_argument("hex_bytes", type=_parse_hex)
    p.add_argument("--label", default="manual")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--log", default="")
    p.add_argument("--i-understand-this-can-write-to-the-sp", action="store_true")
    p.set_defaults(func=cmd_send_hex)

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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
