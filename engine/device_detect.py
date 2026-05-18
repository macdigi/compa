"""
USB device detection for Compa.

Scans the Linux USB sysfs tree (or falls back to ``lsusb``) to
enumerate connected devices.  Also provides helpers to locate
audio interfaces (via sounddevice) and MIDI ports (via rtmidi).

No dependency on pyusb — works with what the Pi has out of the box.
"""

import glob
import logging
import os
import subprocess
from typing import Optional

log = logging.getLogger(__name__)


# ── USB scanning ─────────────────────────────────────────────────────────

def scan_usb_devices() -> list[dict]:
    """Return a list of connected USB devices.

    Each entry is ``{"vendor": int, "product": int, "name": str}``.
    Tries ``/sys/bus/usb/devices/`` first, then ``lsusb`` as a fallback.
    """
    devices = _scan_sysfs()
    if devices:
        return devices
    return _scan_lsusb()


def _scan_sysfs() -> list[dict]:
    """Parse /sys/bus/usb/devices/*/idVendor + idProduct."""
    devices: list[dict] = []
    base = "/sys/bus/usb/devices"
    if not os.path.isdir(base):
        return devices

    for dev_path in glob.glob(os.path.join(base, "*")):
        vendor_file = os.path.join(dev_path, "idVendor")
        product_file = os.path.join(dev_path, "idProduct")
        if not (os.path.isfile(vendor_file) and os.path.isfile(product_file)):
            continue
        try:
            with open(vendor_file) as f:
                vendor = int(f.read().strip(), 16)
            with open(product_file) as f:
                product = int(f.read().strip(), 16)
            # Optional friendly name
            name = ""
            prod_name = os.path.join(dev_path, "product")
            if os.path.isfile(prod_name):
                with open(prod_name) as f:
                    name = f.read().strip()
            devices.append({"vendor": vendor, "product": product, "name": name})
        except (ValueError, OSError):
            continue

    return devices


def _scan_lsusb() -> list[dict]:
    """Fallback: parse ``lsusb`` output."""
    devices: list[dict] = []
    try:
        result = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            # Format: Bus 001 Device 003: ID 0582:02fe Roland Corp. P-6
            parts = line.split("ID ")
            if len(parts) < 2:
                continue
            id_and_name = parts[1]
            id_part = id_and_name.split()[0]  # "0582:02fe"
            vendor_s, _, product_s = id_part.partition(":")
            if not vendor_s or not product_s:
                continue
            name = id_and_name[len(id_part):].strip()
            devices.append({
                "vendor": int(vendor_s, 16),
                "product": int(product_s, 16),
                "name": name,
            })
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("lsusb unavailable: %s", exc)

    return devices


# ── Audio device discovery ───────────────────────────────────────────────

def find_audio_device(hint: str) -> Optional[tuple[int, int]]:
    """Find an audio input device matching *hint*.

    Returns ``(device_index, sample_rate)`` or ``None``.
    An empty *hint* matches any USB / non-default device.
    """
    try:
        import sounddevice as sd
    except ImportError:
        log.debug("sounddevice not installed")
        return None

    devices = sd.query_devices()

    for idx in range(len(devices)):
        try:
            dev = sd.query_devices(idx)
        except Exception:
            continue
        if not isinstance(dev, dict):
            continue
        if dev.get("max_input_channels", 0) < 1:
            continue
        name: str = dev.get("name", "")
        if hint and hint.lower() in name.lower():
            sr = int(dev.get("default_samplerate", 44100))
            log.info("Audio device matched: [%d] %s @ %d Hz", idx, name, sr)
            return (idx, sr)

    # Fallback: if no hint, return first USB-ish input
    if not hint:
        for idx, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) < 1:
                continue
            name = dev.get("name", "")
            # Skip the system default / built-in
            if "default" in name.lower() or "built-in" in name.lower():
                continue
            sr = int(dev.get("default_samplerate", 44100))
            log.info("Audio device fallback: [%d] %s @ %d Hz", idx, name, sr)
            return (idx, sr)

    return None


# ── MIDI port discovery ──────────────────────────────────────────────────

def find_midi_ports(hint: str):
    """Find MIDI in/out ports matching *hint*.

    Returns ``(rtmidi.MidiIn, rtmidi.MidiOut)`` with ports opened,
    or ``(None, None)`` if nothing matches.
    """
    try:
        import rtmidi
    except ImportError:
        log.debug("python-rtmidi not installed")
        return None, None

    if not hint:
        return None, None

    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()

    in_port = out_port = None

    for i in range(midi_in.get_port_count()):
        name = midi_in.get_port_name(i)
        if hint in name and "Through" not in name:
            in_port = i
            log.info("MIDI in matched: %s", name)
            break

    for i in range(midi_out.get_port_count()):
        name = midi_out.get_port_name(i)
        if hint in name and "Through" not in name:
            out_port = i
            log.info("MIDI out matched: %s", name)
            break

    if in_port is None and out_port is None:
        try:
            midi_in.delete()
        except Exception:
            pass
        try:
            midi_out.delete()
        except Exception:
            pass
        return None, None

    if in_port is not None:
        midi_in.open_port(in_port)
    else:
        try:
            midi_in.delete()
        except Exception:
            pass
        midi_in = None

    if out_port is not None:
        midi_out.open_port(out_port)
    else:
        try:
            midi_out.delete()
        except Exception:
            pass
        midi_out = None

    return midi_in, midi_out
