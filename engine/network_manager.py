"""WiFi and Bluetooth configuration for Compa end users.

Wraps nmcli (NetworkManager) and bluetoothctl command-line tools so that
the touchscreen UI can scan, select, and connect without a terminal.

All operations that take more than a few hundred milliseconds run in
background threads with callback delivery, so the pygame loop never
blocks.

This is end-user configuration — it intentionally does NOT expose
advanced features like enterprise WPA, static IP, routes, or tethering.
Those are edge cases a technical user can handle via SSH.
"""

import logging
import re
import socket
import subprocess
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a command and capture output. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not installed"
    except Exception as e:
        return 1, "", str(e)


def _in_thread(fn, *args, **kwargs):
    """Fire-and-forget background execution."""
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


# ── Basic host info ──────────────────────────────────────────────────


def get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "compa"


def get_ip_address() -> str:
    """Primary IP address — whatever route 8.8.8.8 uses."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "—"


def get_active_connection() -> dict:
    """Return {'type', 'name', 'device'} of the primary active connection.

    Uses `nmcli -t connection show --active`.
    """
    rc, out, _ = _run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection",
                       "show", "--active"])
    if rc != 0:
        return {"type": "", "name": "", "device": ""}

    for line in out.strip().splitlines():
        # nmcli -t output: NAME:TYPE:DEVICE
        parts = line.split(":")
        if len(parts) < 3:
            continue
        name, ctype, device = parts[0], parts[1], parts[2]
        if ctype in ("802-11-wireless", "wifi") or ctype == "802-3-ethernet" or ctype == "ethernet":
            return {
                "type": "wifi" if "wireless" in ctype or "wifi" in ctype else "ethernet",
                "name": name,
                "device": device,
            }
    return {"type": "", "name": "", "device": ""}


# ── WiFi ─────────────────────────────────────────────────────────────


class WifiManager:
    """Asynchronous wrapper around nmcli for WiFi operations."""

    def __init__(self):
        self._scanning = False
        self._connecting = False
        self.networks: list[dict] = []  # [{ssid, signal, security, active}, ...]
        self.last_error: str = ""
        self.status: str = ""  # human-readable status for UI

    # ── Status ───────────────────────────────────────────────────────

    @property
    def is_scanning(self) -> bool:
        return self._scanning

    @property
    def is_connecting(self) -> bool:
        return self._connecting

    def radio_on(self) -> bool:
        """Return True if wifi radio is enabled."""
        rc, out, _ = _run(["nmcli", "radio", "wifi"])
        return rc == 0 and "enabled" in out.lower()

    def set_radio(self, on: bool):
        state = "on" if on else "off"
        _run(["nmcli", "radio", "wifi", state])

    def current_ssid(self) -> str:
        """SSID currently connected to (empty if none)."""
        rc, out, _ = _run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list"])
        if rc != 0:
            return ""
        for line in out.strip().splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0] == "yes":
                return parts[1]
        return ""

    # ── Scan ─────────────────────────────────────────────────────────

    def scan_async(self, on_done: Callable[[list[dict]], None] | None = None):
        """Scan for networks in a background thread."""
        if self._scanning:
            return
        self._scanning = True
        self.status = "Scanning..."

        def _do():
            try:
                # Ask NetworkManager to rescan, then list
                _run(["nmcli", "device", "wifi", "rescan"], timeout=6.0)
                rc, out, err = _run(
                    ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                     "device", "wifi", "list"],
                    timeout=10.0,
                )
                if rc != 0:
                    self.last_error = err.strip() or "Scan failed"
                    self.status = "Scan failed"
                    self.networks = []
                    return

                results: list[dict] = []
                seen_ssids: set[str] = set()
                for line in out.strip().splitlines():
                    # Parse tab-terminated nmcli output — fields can contain colons
                    # if SSID has one, but -t quotes with \:. Simple split works here.
                    parts = self._split_nmcli_line(line)
                    if len(parts) < 4:
                        continue
                    in_use, ssid, signal, security = parts[:4]
                    if not ssid:
                        continue
                    if ssid in seen_ssids:
                        continue
                    seen_ssids.add(ssid)
                    try:
                        sig = int(signal) if signal else 0
                    except ValueError:
                        sig = 0
                    results.append({
                        "ssid": ssid,
                        "signal": sig,
                        "security": security or "--",
                        "active": in_use == "*",
                    })

                # Strongest first, but keep active network pinned on top
                results.sort(key=lambda n: (-1 if n["active"] else 0, -n["signal"]))
                self.networks = results
                self.status = f"{len(results)} networks found"
            except Exception as e:
                self.last_error = str(e)
                self.status = "Scan error"
                log.warning("wifi scan failed: %s", e)
            finally:
                self._scanning = False
                if on_done is not None:
                    try:
                        on_done(self.networks)
                    except Exception as e:
                        log.warning("wifi scan callback error: %s", e)

        _in_thread(_do)

    @staticmethod
    def _split_nmcli_line(line: str) -> list[str]:
        """Split nmcli -t output, honoring backslash-escaped colons."""
        parts: list[str] = []
        current = []
        i = 0
        while i < len(line):
            c = line[i]
            if c == "\\" and i + 1 < len(line):
                current.append(line[i + 1])
                i += 2
                continue
            if c == ":":
                parts.append("".join(current))
                current = []
                i += 1
                continue
            current.append(c)
            i += 1
        parts.append("".join(current))
        return parts

    # ── Connect ──────────────────────────────────────────────────────

    def connect_async(self, ssid: str, password: str | None = None,
                       on_done: Callable[[bool, str], None] | None = None):
        """Connect to an SSID. Callback receives (success, message)."""
        if self._connecting:
            return
        self._connecting = True
        self.status = f"Connecting to {ssid}..."

        def _do():
            try:
                cmd = ["nmcli", "device", "wifi", "connect", ssid]
                if password:
                    cmd += ["password", password]
                rc, out, err = _run(cmd, timeout=45.0)
                if rc == 0:
                    self.status = f"Connected: {ssid}"
                    ok, msg = True, "Connected"
                else:
                    # Scrape useful error
                    message = (err or out).strip()
                    if "Secrets were required" in message:
                        message = "Password required or incorrect"
                    elif "No network with SSID" in message:
                        message = "Network not found"
                    self.last_error = message
                    self.status = f"Failed: {message[:36]}"
                    ok, msg = False, message
            except Exception as e:
                ok, msg = False, str(e)
                self.status = f"Error: {msg[:36]}"
            finally:
                self._connecting = False
                if on_done is not None:
                    try:
                        on_done(ok, msg)
                    except Exception:
                        pass

        _in_thread(_do)

    def disconnect_async(self, on_done: Callable[[bool], None] | None = None):
        """Disconnect the wifi interface."""
        def _do():
            # Find wifi device first
            rc, out, _ = _run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], timeout=5.0,
            )
            dev = ""
            for line in out.strip().splitlines():
                parts = line.split(":")
                if len(parts) >= 2 and parts[1] == "wifi":
                    dev = parts[0]
                    break
            if dev:
                _run(["nmcli", "device", "disconnect", dev], timeout=10.0)
                self.status = "Disconnected"
            if on_done is not None:
                try:
                    on_done(True)
                except Exception:
                    pass

        _in_thread(_do)


# ── Bluetooth ────────────────────────────────────────────────────────


class BluetoothManager:
    """Asynchronous wrapper around bluetoothctl for pairing devices."""

    def __init__(self):
        self._scanning = False
        self.devices: list[dict] = []  # [{mac, name, paired, connected, trusted}, ...]
        self.status: str = ""
        self.last_error: str = ""
        self._scan_thread: Optional[threading.Thread] = None

    @property
    def is_scanning(self) -> bool:
        return self._scanning

    def powered(self) -> bool:
        """Return True if Bluetooth adapter is powered on."""
        rc, out, _ = _run(["bluetoothctl", "show"], timeout=3.0)
        if rc != 0:
            return False
        return "Powered: yes" in out

    def set_power(self, on: bool):
        state = "on" if on else "off"
        _run(["bluetoothctl", "power", state], timeout=5.0)

    def set_discoverable(self, on: bool):
        state = "on" if on else "off"
        _run(["bluetoothctl", "discoverable", state], timeout=3.0)

    def set_pairable(self, on: bool):
        state = "on" if on else "off"
        _run(["bluetoothctl", "pairable", state], timeout=3.0)

    # ── List ─────────────────────────────────────────────────────────

    def refresh_devices(self):
        """Refresh device list from bluetoothctl (paired + recently seen)."""
        # `bluetoothctl devices` lists all known devices
        rc, out, _ = _run(["bluetoothctl", "devices"], timeout=3.0)
        if rc != 0:
            return
        results: list[dict] = []
        for line in out.strip().splitlines():
            # "Device AA:BB:CC:DD:EE:FF Friendly Name"
            m = re.match(r"Device\s+([0-9A-F:]{17})\s+(.*)", line)
            if not m:
                continue
            mac, name = m.group(1), m.group(2)
            info = self._device_info(mac)
            results.append({
                "mac": mac,
                "name": name or mac,
                "paired": info.get("Paired") == "yes",
                "connected": info.get("Connected") == "yes",
                "trusted": info.get("Trusted") == "yes",
                "icon": info.get("Icon", ""),
            })
        # Sort: connected, then paired, then by name
        results.sort(key=lambda d: (
            0 if d["connected"] else 1,
            0 if d["paired"] else 1,
            d["name"].lower(),
        ))
        self.devices = results

    def _device_info(self, mac: str) -> dict:
        """Parse `bluetoothctl info <mac>`."""
        rc, out, _ = _run(["bluetoothctl", "info", mac], timeout=3.0)
        if rc != 0:
            return {}
        info = {}
        for line in out.splitlines():
            line = line.strip()
            # "Paired: yes" / "Icon: input-keyboard"
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
        return info

    # ── Scan ─────────────────────────────────────────────────────────

    def scan_async(self, duration: float = 10.0,
                   on_update: Callable[[list[dict]], None] | None = None,
                   on_done: Callable[[list[dict]], None] | None = None):
        """Scan for devices. on_update is called periodically as new devices appear."""
        if self._scanning:
            return
        self._scanning = True
        self.status = "Scanning..."

        def _do():
            try:
                # Make sure the adapter is up
                _run(["bluetoothctl", "power", "on"], timeout=3.0)
                _run(["bluetoothctl", "agent", "on"], timeout=3.0)
                _run(["bluetoothctl", "default-agent"], timeout=3.0)

                # Start a scan in a child process — we'll kill it after `duration`
                proc = subprocess.Popen(
                    ["bluetoothctl", "--timeout", str(int(duration)), "scan", "on"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                # While scanning, periodically refresh the device list
                import time as _time
                start = _time.monotonic()
                while _time.monotonic() - start < duration:
                    self.refresh_devices()
                    if on_update is not None:
                        try:
                            on_update(self.devices)
                        except Exception:
                            pass
                    _time.sleep(1.5)

                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass

                # Final refresh
                self.refresh_devices()
                self.status = f"{len(self.devices)} devices"
            except Exception as e:
                log.warning("bluetooth scan failed: %s", e)
                self.last_error = str(e)
                self.status = "Scan error"
            finally:
                self._scanning = False
                if on_done is not None:
                    try:
                        on_done(self.devices)
                    except Exception:
                        pass

        self._scan_thread = _in_thread(_do)

    # ── Pair / Connect / Remove ─────────────────────────────────────

    def pair_async(self, mac: str,
                    on_done: Callable[[bool, str], None] | None = None):
        """Pair + trust + connect a device."""
        def _do():
            try:
                self.status = f"Pairing {mac[-5:]}..."
                # Pair
                rc, out, err = _run(["bluetoothctl", "pair", mac], timeout=30.0)
                if rc != 0 or "Failed" in out:
                    msg = (err or out).strip()[:60]
                    self.last_error = msg
                    self.status = f"Pair failed: {msg[:30]}"
                    if on_done:
                        on_done(False, msg)
                    return
                # Trust (so auto-reconnect works after reboot)
                _run(["bluetoothctl", "trust", mac], timeout=5.0)
                # Connect
                rc2, out2, err2 = _run(["bluetoothctl", "connect", mac], timeout=20.0)
                ok = rc2 == 0 and "Failed" not in out2
                if ok:
                    self.status = f"Connected {mac[-5:]}"
                    self.refresh_devices()
                    if on_done:
                        on_done(True, "Connected")
                else:
                    msg = (err2 or out2).strip()[:60]
                    self.status = f"Connect failed: {msg[:30]}"
                    if on_done:
                        on_done(False, msg)
            except Exception as e:
                self.last_error = str(e)
                if on_done:
                    on_done(False, str(e))

        _in_thread(_do)

    def connect_async(self, mac: str,
                       on_done: Callable[[bool, str], None] | None = None):
        """Connect to a previously-paired device."""
        def _do():
            try:
                self.status = f"Connecting {mac[-5:]}..."
                rc, out, err = _run(["bluetoothctl", "connect", mac], timeout=20.0)
                ok = rc == 0 and "Failed" not in out
                if ok:
                    self.status = f"Connected {mac[-5:]}"
                    self.refresh_devices()
                    if on_done:
                        on_done(True, "Connected")
                else:
                    msg = (err or out).strip()[:60]
                    self.status = f"Failed: {msg[:30]}"
                    if on_done:
                        on_done(False, msg)
            except Exception as e:
                if on_done:
                    on_done(False, str(e))

        _in_thread(_do)

    def disconnect_async(self, mac: str):
        def _do():
            _run(["bluetoothctl", "disconnect", mac], timeout=10.0)
            self.refresh_devices()

        _in_thread(_do)

    def remove_async(self, mac: str):
        def _do():
            _run(["bluetoothctl", "remove", mac], timeout=10.0)
            self.refresh_devices()

        _in_thread(_do)
