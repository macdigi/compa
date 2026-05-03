"""Link Audio broadcaster — stream Compa's input audio to Live 12.4 over LAN.

Wraps pylinkaudio.LinkAudio + AudioSink so the recorder can fan its
captured audio out to the Link Audio mesh. Live 12.4 (or any other
Link Audio peer) sees Compa as a channel source and can route it to
a track input.

Runs alongside the existing Ableton Link tempo bridge (aalink). They
each create their own Link session — the Pi shows up as two peers
on the network, which is harmless. Consolidating onto a single
pylinkaudio Link instance is a separate cleanup.

Usage:
    bcast = LinkAudioBroadcaster(peer_name="compa-2", channel_name="compa-2")
    bcast.start()
    # From audio callback:
    bcast.push(indata, sample_rate=44100)  # indata = float32 stereo [-1, 1]
    bcast.stop()
"""
import logging
import threading
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


class LinkAudioBroadcaster:
    """Owns a pylinkaudio LinkAudio session + a single AudioSink channel."""

    def __init__(self, peer_name: str = "compa",
                 channel_name: str = "compa", initial_bpm: float = 120.0,
                 quantum: float = 4.0):
        self._peer_name = peer_name
        self._channel_name = channel_name
        self._initial_bpm = float(initial_bpm)
        self._quantum = float(quantum)

        self._link = None
        self._sink = None
        self._enabled = False
        self._lock = threading.Lock()

        # Stats — readable from any thread, updated lock-free from push()
        self._committed = 0
        self._dropped = 0
        # Last seen RMS of incoming audio (rolling) so we can tell whether
        # push() is being called with non-silent data.
        self._last_rms = 0.0
        self._stats_thread: Optional[threading.Thread] = None
        self._stats_stop = threading.Event()

        # Try to import pylinkaudio — graceful no-op if missing.
        self._pla = None
        try:
            import pylinkaudio
            self._pla = pylinkaudio
        except ImportError:
            log.warning("pylinkaudio not installed — Link Audio broadcast disabled. "
                        "Install with: pip install pylinkaudio")

    # ── Lifecycle ──────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._pla is not None and self._link is not None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def channel_name(self) -> str:
        return self._channel_name

    @property
    def peer_name(self) -> str:
        return self._peer_name

    @property
    def num_peers(self) -> int:
        if self._link is not None:
            try:
                return int(self._link.num_peers())
            except Exception:
                pass
        return 0

    @property
    def stats(self) -> tuple[int, int]:
        """(committed_blocks, dropped_blocks) since start."""
        return (self._committed, self._dropped)

    def start(self) -> bool:
        """Open the LinkAudio session and announce the channel.

        Returns True on success. Safe to call when pylinkaudio is missing
        (returns False) or already started (no-op, returns True).
        """
        if self._pla is None:
            return False
        if self._link is not None:
            return True

        try:
            self._link = self._pla.LinkAudio(bpm=self._initial_bpm,
                                             name=self._peer_name)
            self._link.enabled = True
            self._link.link_audio_enabled = True
            # Buffer of 2x our typical write so commits don't block while
            # the previous block is still being sent on the wire.
            self._sink = self._pla.AudioSink(
                self._link, self._channel_name, max_samples=4096)
            self._enabled = True
            log.info("Link Audio broadcaster: '%s' as '%s'",
                     self._channel_name, self._peer_name)
            print(f"Link Audio: broadcasting '{self._channel_name}' "
                  f"as peer '{self._peer_name}'", flush=True)
            # Background stats thread so we can see what's happening
            # without instrumenting every call site.
            self._stats_stop.clear()
            self._stats_thread = threading.Thread(
                target=self._stats_loop, name="LinkAudioStats", daemon=True)
            self._stats_thread.start()
            return True
        except Exception as e:
            log.error("Link Audio start failed: %s", e)
            print(f"Link Audio: start failed ({e})", flush=True)
            self._link = None
            self._sink = None
            self._enabled = False
            return False

    def _stats_loop(self) -> None:
        """Print push() stats every 5s so the journal shows what's flowing."""
        last_committed = 0
        last_dropped = 0
        while not self._stats_stop.wait(5.0):
            c = self._committed
            d = self._dropped
            dc = c - last_committed
            dd = d - last_dropped
            last_committed = c
            last_dropped = d
            print(f"Link Audio: peers={self.num_peers} "
                  f"+committed={dc} +dropped={dd} "
                  f"rms={self._last_rms:.4f}", flush=True)

    def stop(self) -> None:
        self._enabled = False
        self._stats_stop.set()
        if self._stats_thread is not None:
            self._stats_thread.join(timeout=1.0)
            self._stats_thread = None
        if self._sink is not None:
            try:
                # AudioSink doesn't have an explicit stop — releasing it
                # is enough (its destructor stops the channel).
                self._sink = None
            except Exception:
                pass
        if self._link is not None:
            try:
                self._link.link_audio_enabled = False
                self._link.enabled = False
            except Exception:
                pass
            self._link = None
        log.info("Link Audio broadcaster stopped")

    # ── Real-time push ────────────────────────────────────────────────

    def push(self, indata: np.ndarray, sample_rate: int) -> bool:
        """Send a buffer of float32 audio to the Link Audio channel.

        Safe to call from an audio callback. Converts float32 [-1, 1]
        to int16 in-place and forwards to the AudioSink. Returns True
        if the block was committed to the wire (i.e., a peer is
        subscribed); False if dropped (no subscriber, or backpressure).

        The channel is announced as soon as start() succeeds; commits
        only happen when at least one peer (e.g. Live 12.4 with a track
        routed) subscribes to the channel.
        """
        if not self._enabled or self._sink is None:
            return False
        try:
            # float32 [-1, 1] → int16. clip just in case of overshoot.
            buf16 = (indata * 32767.0).clip(-32768, 32767).astype(np.int16)
            # Track RMS for diagnostics — cheap.
            self._last_rms = float(np.sqrt(np.mean(buf16.astype(np.float32) ** 2))) / 32768.0
            ok = self._sink.write(
                buf16,
                sample_rate=int(sample_rate),
                quantum=self._quantum,
            )
            if ok:
                self._committed += 1
            else:
                self._dropped += 1
            return bool(ok)
        except Exception as e:
            # Don't ever raise from the audio callback.
            log.debug("Link Audio push failed: %s", e)
            self._dropped += 1
            return False
