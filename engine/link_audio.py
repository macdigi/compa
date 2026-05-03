"""Link Audio broadcaster — stream Compa's input audio to Live 12.4 over LAN.

Wraps pylinkaudio.LinkAudio + AudioSink so the recorder can fan its
captured audio out to the Link Audio mesh. Live 12.4 (or any other
Link Audio peer) sees Compa as a channel source and can route it to
a track input.

Architecture: the recorder hands us bursty ~85ms blocks (4096 frames
at 48kHz) for USB stability. Link Audio receivers expect a steady
stream — Live drops anything that doesn't arrive at a predictable
cadence. So producer and consumer are decoupled:

    recorder._audio_callback ──push()──► ring buffer
                                              │
                                              ▼
                                   _send_worker thread
                                              │
                                              ▼
                                    sink.write() at HOP-rate

The send worker drains the ring at the natural HOP/sample_rate
cadence, anchored to an absolute start time so cumulative drift can't
accumulate. Pi 3B note: USB capture itself caps at ~75% of real-time
on shared-USB-bus models, so playback in Live will be choppy on Pi 3B
regardless of how fast we send. Pi 5 (ethernet on PCIe, separate USB
controller) handles full real-time cleanly.

Runs alongside aalink (tempo) for now; consolidating onto a single
pylinkaudio Link instance is a separate cleanup.
"""
import logging
import threading
import time
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# Send-side hop. 2048 frames @ 48k = 42.7ms — well inside Live's default
# 100ms latency tolerance, with enough payload to amortise per-call
# overhead in pylinkaudio.
HOP_FRAMES = 2048
# Ring buffer holds ~250ms of audio at 48kHz to absorb input bursts +
# scheduling jitter. Keep small to avoid latency.
RING_SECONDS = 0.25
DEFAULT_SAMPLE_RATE = 48000
CHANNELS = 2


class LinkAudioBroadcaster:
    """Owns a pylinkaudio LinkAudio session + AudioSink + send worker."""

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

        # Ring buffer (int16 stereo). Single producer (audio callback),
        # single consumer (send worker). Positions guarded by a lock.
        ring_frames = int(RING_SECONDS * DEFAULT_SAMPLE_RATE)
        self._ring = np.zeros((ring_frames, CHANNELS), dtype=np.int16)
        self._ring_frames = ring_frames
        self._wpos = 0
        self._rpos = 0
        self._ring_lock = threading.Lock()
        self._sample_rate = DEFAULT_SAMPLE_RATE

        # Send worker
        self._worker: Optional[threading.Thread] = None
        self._worker_stop = threading.Event()

        # Stats — readable from any thread for UI / debug.
        self._committed = 0
        self._dropped = 0
        self._overruns = 0  # ring buffer overruns (consumer too slow)

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
    def stats(self) -> tuple[int, int, int]:
        """(committed, dropped, overruns) since start."""
        return (self._committed, self._dropped, self._overruns)

    def start(self) -> bool:
        if self._pla is None:
            return False
        if self._link is not None:
            return True

        try:
            self._link = self._pla.LinkAudio(bpm=self._initial_bpm,
                                             name=self._peer_name)
            self._link.enabled = True
            self._link.link_audio_enabled = True
            self._sink = self._pla.AudioSink(
                self._link, self._channel_name, max_samples=HOP_FRAMES * 2)
            self._enabled = True
            log.info("Link Audio broadcaster: '%s' as '%s'",
                     self._channel_name, self._peer_name)
            print(f"Link Audio: broadcasting '{self._channel_name}' "
                  f"as peer '{self._peer_name}'", flush=True)

            self._worker_stop.clear()
            self._worker = threading.Thread(
                target=self._send_worker, name="LinkAudioSend", daemon=True)
            self._worker.start()
            return True
        except Exception as e:
            log.error("Link Audio start failed: %s", e)
            print(f"Link Audio: start failed ({e})", flush=True)
            self._link = None
            self._sink = None
            self._enabled = False
            return False

    def stop(self) -> None:
        self._enabled = False
        self._worker_stop.set()
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            self._worker = None
        self._sink = None
        if self._link is not None:
            try:
                self._link.link_audio_enabled = False
                self._link.enabled = False
            except Exception:
                pass
            self._link = None
        log.info("Link Audio broadcaster stopped")

    # ── Producer (called from audio callback) ────────────────────────

    def push(self, indata: np.ndarray, sample_rate: int) -> bool:
        """Append a buffer of float32 audio to the send ring.

        Safe to call from an audio callback. Converts float32 → int16
        and writes to the ring. Returns True if the data was queued;
        False if the broadcaster is disabled.
        """
        if not self._enabled:
            return False
        try:
            buf16 = (indata * 32767.0).clip(-32768, 32767).astype(np.int16)
            self._sample_rate = int(sample_rate)
            n = len(buf16)
            if n == 0:
                return True
            with self._ring_lock:
                used = (self._wpos - self._rpos) % self._ring_frames
                space = self._ring_frames - used - 1
                if n > space:
                    drop = n - space
                    self._rpos = (self._rpos + drop) % self._ring_frames
                    self._overruns += 1
                wp = self._wpos
                tail = self._ring_frames - wp
                if n <= tail:
                    self._ring[wp:wp + n] = buf16
                else:
                    self._ring[wp:] = buf16[:tail]
                    self._ring[:n - tail] = buf16[tail:]
                self._wpos = (wp + n) % self._ring_frames
            return True
        except Exception as e:
            log.debug("Link Audio push failed: %s", e)
            return False

    # ── Consumer (send worker thread) ────────────────────────────────

    def _send_worker(self) -> None:
        """Drain the ring at real-time rate using absolute-clock pacing.

        Anchored to a start time so cumulative drift is impossible.
        Each iteration calculates how many hops should have been sent
        by now and catches up if behind.
        """
        chunk = np.empty((HOP_FRAMES, CHANNELS), dtype=np.int16)
        sr = self._sample_rate
        period = HOP_FRAMES / float(sr)
        start = time.monotonic()
        n_sent = 0

        while not self._worker_stop.is_set():
            if self._sample_rate != sr:
                sr = self._sample_rate
                period = HOP_FRAMES / float(sr)
                start = time.monotonic()
                n_sent = 0

            elapsed = time.monotonic() - start
            target = int(elapsed / period) + 1

            while n_sent < target and not self._worker_stop.is_set():
                with self._ring_lock:
                    used = (self._wpos - self._rpos) % self._ring_frames
                    if used >= HOP_FRAMES:
                        rp = self._rpos
                        tail = self._ring_frames - rp
                        if HOP_FRAMES <= tail:
                            chunk[:] = self._ring[rp:rp + HOP_FRAMES]
                        else:
                            chunk[:tail] = self._ring[rp:]
                            chunk[tail:] = self._ring[:HOP_FRAMES - tail]
                        self._rpos = (rp + HOP_FRAMES) % self._ring_frames
                        have = True
                    else:
                        have = False

                if not have:
                    break

                try:
                    ok = self._sink.write(
                        chunk, sample_rate=sr, quantum=self._quantum)
                    if ok:
                        self._committed += 1
                    else:
                        self._dropped += 1
                except Exception as e:
                    log.debug("Link Audio sink.write failed: %s", e)
                    self._dropped += 1

                n_sent += 1

            now = time.monotonic()
            elapsed = now - start
            target_now = int(elapsed / period) + 1
            if n_sent >= target_now:
                next_deadline = start + n_sent * period
                sleep_dt = next_deadline - now
                if sleep_dt > 0.001:
                    time.sleep(sleep_dt - 0.0005)
            else:
                time.sleep(0.001)

            # Safety re-anchor for clock jumps > 10s.
            if abs(time.monotonic() - start - n_sent * period) > 10.0:
                start = time.monotonic()
                n_sent = 0
