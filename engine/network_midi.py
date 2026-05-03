"""Network MIDI bridge — share Compa's USB MIDI controllers over the LAN.

Wraps the rtpmidid systemd service so Compa's UI can flip a master
toggle to start/stop sharing all hardware ALSA seq ports as RTP-MIDI
peers (AppleMIDI). Mac receivers (Live, Logic, anything that uses
Audio MIDI Setup → Network) see compa-2 in their MIDI Studio Network
directory and can connect a session to receive notes from Push 2,
SP-404MKII, Twister, or any other USB-class-compliant MIDI device
plugged into Compa.

Defaults to OFF (opt-in). Persisted via NETWORK_MIDI_ENABLED in
config.env. Requires NOPASSWD sudo for `systemctl start|stop rtpmidid`
(the standard Compa pi-user setup).

Peer count is polled from `aconnect -l` — rtpmidid creates client 128
with port 0 = local export and ports 2+ = each discovered RTP-MIDI
peer (named after the remote session). We count the latter.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Optional

SERVICE_NAME = "rtpmidid"
_POLL_INTERVAL_S = 3.0
_PORT_LINE = re.compile(r"^\s+(\d+)\s+'([^']*)'\s*$")


class NetworkMidi:
    def __init__(self) -> None:
        self._enabled = self._is_active()
        self._peer_count = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._poller: Optional[threading.Thread] = None
        if self._enabled:
            self._refresh_peers()
            self._start_poller()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def peer_count(self) -> int:
        with self._lock:
            return self._peer_count

    def start(self) -> bool:
        if self._enabled and self._is_active():
            return True
        try:
            subprocess.run(
                ["sudo", "-n", "systemctl", "start", SERVICE_NAME],
                check=True, capture_output=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"[NetworkMidi] start failed: {e}", flush=True)
            return False
        # rtpmidid takes ~1s to bind ALSA seq + Avahi
        time.sleep(1.0)
        if not self._is_active():
            print("[NetworkMidi] start: service didn't stay active", flush=True)
            return False
        self._enabled = True
        self._refresh_peers()
        self._start_poller()
        return True

    def stop(self) -> bool:
        self._stop_poller()
        try:
            subprocess.run(
                ["sudo", "-n", "systemctl", "stop", SERVICE_NAME],
                check=True, capture_output=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"[NetworkMidi] stop failed: {e}", flush=True)
            return False
        self._enabled = False
        with self._lock:
            self._peer_count = 0
        return True

    @staticmethod
    def _is_active() -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", SERVICE_NAME],
                timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False

    def _start_poller(self) -> None:
        if self._poller and self._poller.is_alive():
            return
        self._stop.clear()
        self._poller = threading.Thread(
            target=self._poll_loop, name="NetworkMidi-poll", daemon=True,
        )
        self._poller.start()

    def _stop_poller(self) -> None:
        self._stop.set()
        if self._poller and self._poller.is_alive():
            self._poller.join(timeout=2.0)
        self._poller = None

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._refresh_peers()
            self._stop.wait(_POLL_INTERVAL_S)

    def _refresh_peers(self) -> None:
        n = self._query_peers()
        with self._lock:
            self._peer_count = n

    @staticmethod
    def _query_peers() -> int:
        try:
            r = subprocess.run(
                ["aconnect", "-l"],
                capture_output=True, text=True, timeout=2,
            )
        except Exception:
            return 0
        if r.returncode != 0:
            return 0
        peers = 0
        in_rtpmidid = False
        for line in r.stdout.splitlines():
            if line.startswith("client "):
                in_rtpmidid = "'rtpmidid'" in line
                continue
            if not in_rtpmidid:
                continue
            m = _PORT_LINE.match(line)
            if not m:
                continue
            port_num = int(m.group(1))
            port_name = m.group(2).strip()
            # Port 0 is the local "Network Export" aggregator,
            # port 1 is the alsa announce listener; both are local
            # plumbing, not network peers. Count anything else with
            # a non-empty name as a discovered remote session.
            if port_num >= 2 and port_name and not port_name.startswith("Network Export"):
                peers += 1
        return peers
