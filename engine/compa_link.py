"""Compa-to-Compa network link.

Discovers other Compas on the local network via mDNS/zeroconf,
serves recordings/samples/kits via HTTP, and provides client methods
to browse and download files from peer Compas.

Architecture:
    - HTTP server on port 7474 serves /recordings, /samples, /kits
    - mDNS service "_compa._tcp.local." advertises hostname + port
    - Browser tracks discovered peers, polls for file lists
    - Client downloads files via standard HTTP GET

This is local-network only — no auth, no encryption. Designed for
trusted home/studio networks.
"""

import json
import logging
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import unquote, quote
from urllib.request import urlopen, Request

try:
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
except ImportError:
    Zeroconf = None
    ServiceBrowser = None
    ServiceInfo = None
    ServiceListener = object

log = logging.getLogger(__name__)

COMPA_PORT = 7474
SERVICE_TYPE = "_compa._tcp.local."


def _local_ip() -> str:
    """Get the primary local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ── HTTP Server ──────────────────────────────────────────────────────


class CompaHandler(BaseHTTPRequestHandler):
    """Serves files from compa's recordings/samples/kits directories."""

    # Set by CompaServer
    base_dirs: dict = {}
    hostname: str = "compa"

    def log_message(self, format, *args):
        pass  # Quiet

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str):
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        size = os.path.getsize(path)
        self.send_response(200)
        ext = os.path.splitext(path)[1].lower()
        ctype = {".wav": "audio/wav", ".json": "application/json",
                 ".txt": "text/plain"}.get(ext, "application/octet-stream")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self):
        path = unquote(self.path).lstrip("/")
        parts = path.split("/", 1)
        if not parts or not parts[0]:
            self._send_json({
                "name": self.hostname,
                "compa_version": "1.0",
                "endpoints": list(self.base_dirs.keys()),
            })
            return

        category = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        if category not in self.base_dirs:
            self.send_error(404, f"Unknown category: {category}")
            return

        base = self.base_dirs[category]

        if not rest:
            # List files in the directory
            try:
                files = []
                for fn in sorted(os.listdir(base)):
                    fp = os.path.join(base, fn)
                    if os.path.isfile(fp):
                        files.append({
                            "name": fn,
                            "size": os.path.getsize(fp),
                            "mtime": os.path.getmtime(fp),
                        })
                self._send_json({"category": category, "files": files})
            except Exception as e:
                self.send_error(500, str(e))
            return

        # Serve a specific file (prevent path traversal)
        safe = os.path.normpath(os.path.join(base, rest))
        if not safe.startswith(os.path.abspath(base)):
            self.send_error(403, "Forbidden")
            return
        self._send_file(safe)


class CompaServer:
    """HTTP file server + mDNS advertiser."""

    def __init__(self, recordings_dir: str, samples_dir: str = "",
                 kits_dir: str = "", port: int = COMPA_PORT):
        self._port = port
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._zc: Optional["Zeroconf"] = None
        self._service: Optional["ServiceInfo"] = None

        # Build base_dirs from what's available
        base_dirs = {}
        if recordings_dir and os.path.isdir(recordings_dir):
            base_dirs["recordings"] = os.path.abspath(recordings_dir)
        if samples_dir and os.path.isdir(samples_dir):
            base_dirs["samples"] = os.path.abspath(samples_dir)
        if kits_dir and os.path.isdir(kits_dir):
            base_dirs["kits"] = os.path.abspath(kits_dir)

        CompaHandler.base_dirs = base_dirs
        CompaHandler.hostname = socket.gethostname()

    def start(self) -> bool:
        """Start the HTTP server and mDNS advertisement."""
        if self._server is not None:
            return True

        try:
            self._server = ThreadingHTTPServer(("0.0.0.0", self._port), CompaHandler)
        except Exception as e:
            log.error("Failed to start Compa server: %s", e)
            return False

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        # Advertise via mDNS
        if Zeroconf is not None:
            try:
                ip = _local_ip()
                hostname = socket.gethostname()
                self._zc = Zeroconf()
                self._service = ServiceInfo(
                    SERVICE_TYPE,
                    f"{hostname}.{SERVICE_TYPE}",
                    addresses=[socket.inet_aton(ip)],
                    port=self._port,
                    properties={"version": "1.0"},
                    server=f"{hostname}.local.",
                )
                self._zc.register_service(self._service)
                log.info("Compa server: %s @ %s:%d", hostname, ip, self._port)
                print(f"Compa server: {hostname} @ {ip}:{self._port}", flush=True)
            except Exception as e:
                log.warning("mDNS advertise failed: %s", e)

        return True

    def stop(self):
        if self._zc is not None and self._service is not None:
            try:
                self._zc.unregister_service(self._service)
                self._zc.close()
            except Exception:
                pass
            self._zc = None

        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None


# ── Peer Discovery ───────────────────────────────────────────────────


class CompaPeerListener:
    """mDNS listener that tracks discovered Compa peers."""

    def __init__(self):
        self.peers: dict[str, dict] = {}  # name → {host, port, ip}

    def add_service(self, zc, type_, name):
        try:
            info = zc.get_service_info(type_, name)
            if info and info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                short = name.replace(f".{SERVICE_TYPE}", "")
                self.peers[short] = {
                    "name": short,
                    "host": info.server.rstrip("."),
                    "ip": ip,
                    "port": info.port,
                }
                log.info("Compa peer found: %s @ %s:%d", short, ip, info.port)
                print(f"Compa peer: {short} @ {ip}:{info.port}", flush=True)
        except Exception as e:
            log.warning("add_service error: %s", e)

    def remove_service(self, zc, type_, name):
        short = name.replace(f".{SERVICE_TYPE}", "")
        self.peers.pop(short, None)
        log.info("Compa peer left: %s", short)

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)


class CompaBrowser:
    """Browses for other Compas on the network."""

    def __init__(self):
        self._zc: Optional["Zeroconf"] = None
        self._browser: Optional["ServiceBrowser"] = None
        self.listener = CompaPeerListener()

    def start(self):
        if Zeroconf is None or self._zc is not None:
            return
        try:
            self._zc = Zeroconf()
            self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, self.listener)
            log.info("Compa browser started")
        except Exception as e:
            log.warning("Browser start failed: %s", e)

    def stop(self):
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:
                pass
            self._zc = None

    @property
    def peers(self) -> list[dict]:
        # Exclude self
        my_host = socket.gethostname()
        return [p for p in self.listener.peers.values() if p["name"] != my_host]


# ── Client (download files from a peer) ──────────────────────────────


def list_peer_files(peer: dict, category: str = "recordings", timeout: float = 5.0) -> list[dict]:
    """Get a file listing from a peer Compa."""
    url = f"http://{peer['ip']}:{peer['port']}/{category}"
    try:
        with urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data.get("files", [])
    except Exception as e:
        log.warning("list_peer_files failed: %s", e)
        return []


def download_peer_file(peer: dict, category: str, filename: str,
                       dest_dir: str, timeout: float = 30.0) -> Optional[str]:
    """Download a file from a peer to a local directory.

    Returns the local path on success, None on failure.
    """
    # URL-encode the filename to handle spaces and special characters
    safe_name = quote(filename)
    url = f"http://{peer['ip']}:{peer['port']}/{category}/{safe_name}"
    dest_path = os.path.join(dest_dir, filename)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        with urlopen(url, timeout=timeout) as resp:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        log.info("Downloaded %s from %s → %s", filename, peer["name"], dest_path)
        return dest_path
    except Exception as e:
        log.warning("download_peer_file failed: %s", e)
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        return None
