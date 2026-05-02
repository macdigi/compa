"""Ableton Link bridge — tempo + beat-phase sync over WiFi.

Link is Ableton's peer-to-peer tempo-sync protocol, supported by iOS apps
(AUM, Koala, Drambo, Loopy Pro, GarageBand), DAWs (Ableton Live), and
hardware (Push 3, Akai Force, others). Peers on the same WiFi network
auto-discover and share tempo, beat phase, and optionally start/stop.

Compa joins the Link session and:
 1. Updates its master clock when a peer changes tempo
 2. Pushes Compa's tempo changes back out to Link peers
 3. Reports peer count for the UI

This unlocks the iPad story we wanted: open AUM on the iPad, both devices
auto-sync, no USB cable required. Same goes for any Link-aware app/device
on the same network.

Implementation notes:
 - Uses aalink (https://pypi.org/project/aalink) under the hood
 - aalink requires a running asyncio event loop. We spin one up in a
   daemon thread on start() and create the Link instance inside that
   loop's context. All write operations (set_tempo, stop) are routed
   through the loop via run_coroutine_threadsafe.
 - Reads (tempo, num_peers, phase) are simple attribute fetches on the
   underlying C++ Link object and are safe from any thread.
 - aalink's tempo / num_peers callbacks fire from Link's internal
   protocol thread; we re-dispatch through a lock-guarded listener list
   so the rest of Compa can subscribe without thinking about threads.
 - If aalink isn't installed (e.g. older Compa images), the bridge stays
   in a no-op state — `.available` returns False and everything is safe
   to call.
"""
import asyncio
import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)


class AbletonLinkBridge:
    """Lifecycle wrapper around an aalink.Link session."""

    def __init__(self, initial_bpm: float = 120.0, quantum: float = 4.0):
        self._initial_bpm = float(initial_bpm)
        self._quantum = float(quantum)
        self._link = None  # type: Optional[object]
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._tempo_listeners: list[Callable[[float], None]] = []
        self._peers_listeners: list[Callable[[int], None]] = []
        self._lock = threading.Lock()
        # Source of the most recent tempo change: "boot", "local", "link"
        self._tempo_source = "boot"
        # Monotonic timestamp of the most recent Link activity (tempo or
        # peers callback). UI uses this to show a heartbeat dot.
        self._last_activity = 0.0
        self._aalink = None
        try:
            import aalink
            self._aalink = aalink
        except ImportError:
            log.warning("aalink not installed — Ableton Link disabled. "
                        "Install with: pip install aalink")

    # ── Lifecycle ──────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True if aalink is importable AND the session is started."""
        return self._aalink is not None and self._link is not None

    def start(self) -> None:
        """Open the Link session and join the network.

        Spins up an asyncio event loop in a daemon thread, creates the
        Link instance inside that loop, and enables it. Returns once the
        Link is alive.
        """
        if self._aalink is None or self._link is not None:
            return

        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name="AbletonLinkLoop",
            daemon=True,
        )
        self._loop_thread.start()
        if not self._loop_ready.wait(timeout=2.0):
            log.error("Ableton Link asyncio loop did not start in time")
            return

        future = asyncio.run_coroutine_threadsafe(
            self._async_init_link(), self._loop)
        try:
            future.result(timeout=2.0)
            print(f"Ableton Link: started @ {self._initial_bpm:.1f} BPM "
                  f"(quantum={self._quantum:.1f})", flush=True)
        except Exception as e:
            print(f"Ableton Link: failed to start ({e})", flush=True)
            self._link = None

    def stop(self) -> None:
        if self._link is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._async_stop_link(), self._loop).result(timeout=1.0)
            except Exception:
                pass
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=1.0)
        self._link = None
        self._loop = None
        self._loop_thread = None
        log.info("Ableton Link stopped")

    # ── Asyncio loop runner ───────────────────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _async_init_link(self) -> None:
        self._link = self._aalink.Link(self._initial_bpm)
        self._link.quantum = self._quantum
        self._link.set_tempo_callback(self._on_link_tempo)
        self._link.set_num_peers_callback(self._on_link_peers)
        self._link.enabled = True

    async def _async_stop_link(self) -> None:
        if self._link is not None:
            try:
                self._link.enabled = False
            except Exception:
                pass

    async def _async_set_tempo(self, bpm: float) -> None:
        if self._link is not None:
            self._link.tempo = bpm

    # ── State (read-only — safe from any thread) ──────────────────────

    @property
    def tempo(self) -> float:
        if self._link is not None:
            try:
                return float(self._link.tempo)
            except Exception:
                pass
        return self._initial_bpm

    @property
    def num_peers(self) -> int:
        if self._link is not None:
            try:
                return int(self._link.num_peers)
            except Exception:
                pass
        return 0

    @property
    def quantum(self) -> float:
        if self._link is not None:
            try:
                return float(self._link.quantum)
            except Exception:
                pass
        return self._quantum

    @property
    def beat(self) -> float:
        """Current Link beat (float, monotonically increasing)."""
        if self._link is not None:
            try:
                return float(self._link.beat)
            except Exception:
                pass
        return 0.0

    @property
    def phase(self) -> float:
        """Phase within the quantum (0..quantum)."""
        if self._link is not None:
            try:
                return float(self._link.phase)
            except Exception:
                pass
        return 0.0

    # ── Tempo control ─────────────────────────────────────────────────

    def set_tempo(self, bpm: float) -> None:
        """Push a tempo change out to Link peers (and stay in sync locally).

        Safe to call from any thread — routed through the asyncio loop.
        Marks the tempo source as 'local' so the UI can show it came from
        this Compa rather than from a Link peer.
        """
        if self._link is None or self._loop is None:
            return
        self._tempo_source = "local"
        self._last_activity = time.monotonic()
        try:
            asyncio.run_coroutine_threadsafe(
                self._async_set_tempo(float(bpm)), self._loop)
        except Exception as e:
            log.debug("set_tempo failed: %s", e)

    @property
    def tempo_source(self) -> str:
        """Source of the current tempo: 'boot', 'local', or 'link'."""
        return self._tempo_source

    @property
    def seconds_since_activity(self) -> float:
        """How long since the last Link tempo or peers callback fired.

        Returns a large number (>1e6) if we've never seen activity.
        UI uses this to show a heartbeat — recent activity = live signal.
        """
        if self._last_activity == 0.0:
            return 1e9
        return time.monotonic() - self._last_activity

    # ── Listener registration ─────────────────────────────────────────

    def add_tempo_listener(self, cb: Callable[[float], None]) -> None:
        """Subscribe to tempo changes triggered by Link peers."""
        with self._lock:
            if cb not in self._tempo_listeners:
                self._tempo_listeners.append(cb)

    def remove_tempo_listener(self, cb: Callable[[float], None]) -> None:
        with self._lock:
            try:
                self._tempo_listeners.remove(cb)
            except ValueError:
                pass

    def add_peers_listener(self, cb: Callable[[int], None]) -> None:
        with self._lock:
            if cb not in self._peers_listeners:
                self._peers_listeners.append(cb)

    def remove_peers_listener(self, cb: Callable[[int], None]) -> None:
        with self._lock:
            try:
                self._peers_listeners.remove(cb)
            except ValueError:
                pass

    # ── Internal callbacks (fire from Link's protocol thread) ────────

    def _on_link_tempo(self, bpm) -> None:
        try:
            value = float(bpm)
        except (TypeError, ValueError):
            return
        # Mark source as 'link' since this fires when a peer changed it.
        # set_tempo() also sets _tempo_source='local' just before pushing
        # the value to Link, so a Local change followed by Link's own
        # echo callback will momentarily flip to 'link' — that's accurate
        # because by the time the callback fires, the value is in the
        # shared session.
        self._tempo_source = "link"
        self._last_activity = time.monotonic()
        log.debug("Link tempo -> %.2f BPM", value)
        with self._lock:
            listeners = list(self._tempo_listeners)
        for cb in listeners:
            try:
                cb(value)
            except Exception as e:
                log.debug("Tempo listener failed: %s", e)

    def _on_link_peers(self, count) -> None:
        try:
            value = int(count)
        except (TypeError, ValueError):
            return
        self._last_activity = time.monotonic()
        print(f"Ableton Link: peers={value}", flush=True)
        with self._lock:
            listeners = list(self._peers_listeners)
        for cb in listeners:
            try:
                cb(value)
            except Exception as e:
                log.debug("Peers listener failed: %s", e)
