"""
P-6 performance audio recorder.

Records USB audio from the P-6 (48kHz/24-bit stereo) to WAV files on disk.
Provides real-time level metering and waveform preview data for the UI.
"""

import json
import logging
import math
import os
import queue
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import numpy as np

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    sd = None
    sf = None


class _LinearResampler:
    """Stateful linear-interpolation resampler.

    Quality is OK for monitoring (linear interp introduces some aliasing
    above ~src_rate/2, which on music is a soft top-end smear — fine for
    "is this drum hitting?" monitoring, not mastering). Tracks fractional
    phase across chunks so we don't get clicks at chunk boundaries.
    """

    def __init__(self, src_rate: int, dst_rate: int, channels: int):
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self.ratio = self.src_rate / self.dst_rate  # input frames per output frame
        self.channels = int(channels)
        self.tail = np.zeros((1, channels), dtype=np.float32)
        self.frac = 0.0  # cumulative fractional input position [0, ratio)

    def process(self, indata: np.ndarray) -> np.ndarray:
        n_in = indata.shape[0]
        if n_in == 0 or self.src_rate == self.dst_rate:
            return indata.copy()
        ext = np.concatenate([self.tail, indata], axis=0)  # (n_in+1, ch)
        # Generous upper bound on output frame count, then filter strictly
        # to positions < n_in so we never read past the end of indata.
        n_out_cap = int(np.ceil((n_in - self.frac) / self.ratio)) + 1
        if n_out_cap < 0:
            n_out_cap = 0
        positions = self.frac + np.arange(n_out_cap) * self.ratio
        positions = positions[positions < n_in]
        n_out = positions.size
        if n_out == 0:
            self.tail = indata[-1:].copy()
            return np.zeros((0, indata.shape[1]), dtype=np.float32)
        ext_pos = positions + 1.0
        floors = np.floor(ext_pos).astype(np.int32)
        fracs = (ext_pos - floors)[:, None]
        ceils = np.minimum(floors + 1, ext.shape[0] - 1)
        out = (1.0 - fracs) * ext[floors] + fracs * ext[ceils]
        # Carry phase: leftover after consuming this chunk
        new_frac = self.frac + n_out * self.ratio - n_in
        if new_frac < 0:
            new_frac = 0.0
        self.frac = float(new_frac)
        self.tail = indata[-1:].copy()
        return out.astype(np.float32)

try:
    import samplerate as _sr
except ImportError:
    _sr = None

log = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 44100  # P-6 on Pi ALSA only supports 44.1kHz
P6_CHANNELS = 2
WAVEFORM_POINTS = 800  # Match screen width for display
RECALL_BUFFER_SECONDS = 60  # Default rolling buffer length — runtime configurable
RECALL_BUFFER_SECONDS_MIN = 15
RECALL_BUFFER_SECONDS_MAX = 1800  # 30 min absolute hard cap (RAM safety)
MONITOR_GAIN_DEFAULT = 1.5
MONITOR_GAIN_MIN = 0.25
MONITOR_GAIN_MAX = 3.0


def _clamp_monitor_gain(gain: float) -> float:
    return max(MONITOR_GAIN_MIN, min(MONITOR_GAIN_MAX, float(gain)))


def _apply_monitor_gain(indata: np.ndarray, gain: float) -> np.ndarray:
    """Return monitor-route audio with gain and a ceiling-safe soft limiter.

    The MON path is for sampling into another box, so keep it strictly
    linear until the boosted signal nears full-scale. Only peaks above the
    knee get shaped, avoiding the brittle sound of hard digital clipping.
    """
    gain = _clamp_monitor_gain(gain)
    if indata.size == 0 or abs(gain - 1.0) < 0.0001:
        return indata
    boosted = indata.astype(np.float32, copy=True)
    boosted *= gain
    knee = 0.95
    abs_boosted = np.abs(boosted)
    over = abs_boosted > knee
    if np.any(over):
        sign = np.sign(boosted[over])
        excess = (abs_boosted[over] - knee) / (1.0 - knee)
        limited = knee + (1.0 - knee) * np.tanh(excess)
        boosted[over] = sign * limited
    return boosted


class P6Recorder:
    """Records P-6 USB audio to WAV files with live metering.

    Opens a sounddevice InputStream on the P-6's USB audio device,
    streams audio to disk in a background thread, and provides
    real-time level/waveform data for UI display.
    """

    def __init__(self, recording_dir: str, device_hint: str = "P-6",
                 recall_buffer_seconds: int = RECALL_BUFFER_SECONDS,
                 record_pre_roll_seconds: float = 0.0,
                 monitor_gain: float = MONITOR_GAIN_DEFAULT):
        self._recording_dir = recording_dir
        self._device_hint = device_hint
        self._device_index: Optional[int] = None
        self._sample_rate = DEFAULT_SAMPLE_RATE

        # Recall buffer length (configurable). The rolling window of audio
        # always being captured for "forgot to hit record" recall. Lifting
        # this from a constant lets the user trade RAM for length.
        self._recall_buffer_seconds = max(RECALL_BUFFER_SECONDS_MIN,
                                           min(RECALL_BUFFER_SECONDS_MAX,
                                               int(recall_buffer_seconds)))
        # Default pre-roll for start_recording — when > 0, every REC press
        # automatically prepends this many seconds from the recall buffer.
        # The "forgot to hit record" case becomes impossible.
        self._record_pre_roll_seconds = max(0.0,
                                             min(float(self._recall_buffer_seconds),
                                                 float(record_pre_roll_seconds)))

        os.makedirs(recording_dir, exist_ok=True)

        # State
        self._armed = False
        self._recording = False
        self._stream: Optional["sd.InputStream"] = None
        self._writer: Optional["sf.SoundFile"] = None
        self._current_file: Optional[str] = None
        self._lock = threading.Lock()
        self._samples_written = 0

        # Level metering (lock-free via numpy)
        self._peak_l = 0.0
        self._peak_r = 0.0
        self._rms_l = 0.0
        self._rms_r = 0.0

        # Waveform preview (circular buffer of peak values)
        self._waveform = np.zeros(WAVEFORM_POINTS, dtype=np.float32)
        self._waveform_pos = 0

        # Monitoring
        self._monitoring = False

        # When True, switch_device() and stop_monitoring() become no-ops.
        # Set during screen-recording so the ALSA input stream can't be
        # torn down mid-recording (PortAudio close blocks in uninterruptible
        # sleep under recording load → main loop hangs → Compa freezes).
        # Set via block_audio_changes(); cleared when recording stops.
        self._block_audio_changes = False

        # Monitor output — forward audio to a second device (headphones).
        # Two USB audio devices have independent crystal clocks that drift
        # against each other, so we can't write input frames straight into
        # the output stream — that produces classic "play a chunk, drop a
        # chunk" glitching as the buffers under/overrun. Instead we run a
        # ring buffer between them: input callback pushes, output callback
        # pulls. Drift gets absorbed in the buffer fill level (oldest frames
        # dropped on overflow, silence emitted on underflow).
        self._monitor_out_stream: Optional["sd.OutputStream"] = None
        self._monitor_out_device: Optional[int] = None
        self._monitor_out_rate: int = 0
        self._monitor_out_buf: Optional[np.ndarray] = None
        self._monitor_out_buf_frames: int = 0
        self._monitor_out_buf_lock = threading.Lock()
        self._monitor_out_write: int = 0  # cumulative frames written
        self._monitor_out_read: int = 0   # cumulative frames read
        self._monitor_out_tail = np.zeros(P6_CHANNELS, dtype=np.float32)
        self._monitor_gain = _clamp_monitor_gain(monitor_gain)
        # Resampler (only used when input/output rates can't be matched —
        # e.g. P-6 is 44100-only and SP-404 is 48000-only)
        self._monitor_resampler: Optional["_LinearResampler"] = None

        # Threshold recording
        self._threshold = 0.02
        self._threshold_mode = False
        self._silence_timeout = 3.0
        self._silence_start = 0.0

        # Recall buffer — rolling circular buffer sized from configured length
        self._recall_buf_frames = self._recall_buffer_seconds * self._sample_rate
        self._recall_buf = np.zeros((self._recall_buf_frames, P6_CHANNELS), dtype=np.float32)
        self._recall_write_pos = 0
        self._recall_total_written = 0  # total frames ever written (for knowing how full)

        # Input overrun tracking — ALSA dropped audio because the callback fell behind.
        # Each input_overflow is missed audio. Reported via PortAudio status flags.
        self._input_overruns = 0
        self._input_underruns = 0
        self._input_overrun_log_t = 0.0      # monotonic time of last warning (rate-limit)
        self._input_overrun_console_count = 0  # times we've printed to stdout

        # Callbacks
        self.on_level: Optional[Callable[[float, float, float, float], None]] = None
        self.on_recording_complete: Optional[Callable[[str, float], None]] = None

        # Link Audio broadcaster — when set, every captured block is also
        # pushed to a Link Audio sink so peers like Live 12.4 can receive
        # Compa's input audio over the LAN.
        self.link_broadcaster = None

        # ── Recorder worker thread ──────────────────────────────────────
        # EVERY recorder operation the UI can trigger — switch_device,
        # start/stop_recording, recall_buffer, recall_and_continue,
        # start/stop_monitoring — does blocking I/O: PortAudio stream
        # open/close (blocks in ALSA kernel state under load) and big
        # synchronous WAV writes (the recall buffer is tens of MB). Those
        # public methods used to run inline on the pygame main loop, so a
        # block there froze the UI *and* the screen-capture frame feed.
        # Now the public methods just enqueue; this single worker thread
        # runs the actual `_impl` work serially. The main loop never does
        # recorder I/O — it can't freeze on it. The worker can block all
        # it likes; nothing user-visible depends on it returning fast.
        self._cmd_queue: "queue.Queue" = queue.Queue()
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="recorder-worker")
        self._worker.start()

        self._find_device()

    def _enqueue(self, fn, *args, **kwargs) -> None:
        """Hand a recorder operation to the worker thread. Returns at once."""
        self._cmd_queue.put((fn, args, kwargs))

    def _worker_loop(self) -> None:
        """Serially run queued recorder operations off the main loop."""
        while True:
            item = self._cmd_queue.get()
            if item is None:  # shutdown sentinel
                self._cmd_queue.task_done()
                break
            fn, args, kwargs = item
            try:
                fn(*args, **kwargs)
            except Exception as e:
                log.error("recorder worker: %s failed: %s",
                          getattr(fn, "__name__", fn), e)
            finally:
                self._cmd_queue.task_done()

    def _find_device(self, preferred_rate: int = 0) -> None:
        """Find the audio input device (P-6 or audio interface) and probe sample rate."""
        if sd is None:
            log.warning("sounddevice not available")
            return

        devices = sd.query_devices()
        candidates = []

        # Search by hint first
        for i, dev in enumerate(devices):
            name = dev.get("name", "")
            if self._device_hint in name and dev.get("max_input_channels", 0) >= 2:
                candidates.append((i, name))

        # Fallback: enumerate USB-ish stereo inputs in order.
        # Never use sd.default.device[0] — when multiple USB cards are on the
        # bus the system "default" pseudo-device is unreliable (often points
        # at HDMI or fails to open). Picking real USB cards explicitly is
        # robust against that. Skip names that are pseudo-devices, system
        # outputs, or known non-input cards.
        if not candidates:
            skip_tokens = ("default", "built-in", "hdmi", "monitor",
                           "pulse", "sysdefault", "dmix", "dsnoop",
                           "front", "surround", "rear", "iec958",
                           "spdif", "null", "samplerate", "speexrate",
                           "upmix", "vdownmix")
            # Preferred device-name fragments — earlier = higher priority.
            # Anything not in this list still qualifies, just lower priority.
            preferred = ("p-6", "id4", "id14", "id22", "id44", "scarlett",
                         "sp-404", "force", "usb audio", "usb")
            ranked = []
            for i, dev in enumerate(devices):
                if dev.get("max_input_channels", 0) < 2:
                    continue
                name = dev.get("name", "")
                lname = name.lower()
                if any(tok in lname for tok in skip_tokens):
                    continue
                # Rank by first matching preferred token; unmatched = end.
                rank = len(preferred)
                for idx, tok in enumerate(preferred):
                    if tok in lname:
                        rank = idx
                        break
                ranked.append((rank, i, name))
            ranked.sort(key=lambda r: r[0])
            for _rank, i, name in ranked:
                candidates.append((i, name))

        if not candidates:
            # Silenced from per-attempt to debug — the hot-plug retry in
            # P6App._check_hotplug is the single source of user-visible
            # "no audio device" logging, with rate limiting + backoff.
            log.debug("No audio input matching '%s'", self._device_hint)
            return

        # Try each candidate with sample rate probing. When a preferred
        # rate is supplied (e.g. matching another device we'll be routing
        # to), try it first so the resulting input rate matches.
        rate_order = [44100, 48000, 96000]
        if preferred_rate:
            rate_order = [preferred_rate] + [r for r in rate_order if r != preferred_rate]
        for dev_idx, dev_name in candidates:
            for rate in rate_order:
                try:
                    s = sd.InputStream(device=dev_idx, samplerate=rate,
                                      channels=P6_CHANNELS, dtype="float32",
                                      blocksize=2048)
                    s.start()
                    s.stop()
                    s.close()
                    self._device_index = dev_idx
                    self._sample_rate = rate
                    print(f"Audio input: {dev_name} @ {rate}Hz", flush=True)
                    return
                except Exception as e:
                    continue

        log.warning("No suitable audio input device found")

    @property
    def available(self) -> bool:
        return self._device_index is not None and sd is not None

    @property
    def device_name(self) -> str:
        """Friendly name of the current audio input device."""
        if self._device_index is not None and sd is not None:
            try:
                dev = sd.query_devices(self._device_index)
                return dev.get("name", "?").split(":")[0].strip()
            except Exception:
                pass
        return "---"

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def block_audio_changes(self, blocked: bool) -> None:
        """Toggle the audio-thread-stability guard.

        While True, switch_device() and stop_monitoring() are no-ops so
        the input stream can't be torn down. Call with True before
        starting screen-recording, False after stopping. Prevents ALSA
        close from blocking the main loop under recording load.
        """
        self._block_audio_changes = blocked

    def switch_device(self, hint: str, preferred_rate: int = 0, user_initiated: bool = False) -> bool:
        """Switch to a different audio input device (enqueued).

        The real work runs on the recorder worker thread — stream
        open/close blocks under load and must never run on the main
        loop. Returns immediately; `True` just means "queued".
        """
        self._enqueue(self._switch_device_impl, hint, preferred_rate, user_initiated)
        return True

    def switch_device_then_monitor_output(
        self, hint: str, output_device_idx: int, output_sample_rate: int = 0,
        preferred_rate: int = 0, user_initiated: bool = False
    ) -> bool:
        """Serially bind input, then open the monitor output.

        MON routing must not open the destination OutputStream while the
        recorder still has the old input stream. During SP↔P-6 handoffs that
        brief overlap can route a device into itself and create crackly
        feedback. This keeps the close/probe/open/output sequence on the
        recorder worker thread.
        """
        self._enqueue(
            self._switch_device_then_monitor_output_impl,
            hint, output_device_idx, output_sample_rate,
            preferred_rate, user_initiated,
        )
        return True

    def _switch_device_then_monitor_output_impl(
        self, hint: str, output_device_idx: int, output_sample_rate: int = 0,
        preferred_rate: int = 0, user_initiated: bool = False
    ) -> bool:
        ok = self._switch_device_impl(
            hint, preferred_rate=preferred_rate,
            user_initiated=user_initiated,
        )
        if not ok:
            return False
        if self._stream is None:
            self._start_monitoring_impl()
            if self._stream is None:
                return False
        return self.start_monitor_output(output_device_idx, output_sample_rate)

    def _switch_device_impl(self, hint: str, preferred_rate: int = 0,
                            user_initiated: bool = False) -> bool:
        """Worker-thread body of switch_device. Stops monitoring,
        re-detects with the new hint, resizes the recall buffer for the
        new sample rate, then restarts monitoring.

        When `user_initiated=True`, bypasses the screen-recording guard —
        a direct card-tap is always honored; internal/auto-switches still
        respect it.
        """
        if self._block_audio_changes and not user_initiated:
            log.debug("switch_device('%s') blocked — screen-recording active (auto)", hint)
            return True  # Pretend success; do not touch the stream
        was_monitoring = self._monitoring
        if self._recording:
            self._stop_recording_impl()
        # force=user_initiated: a deliberate card-tap switch must close
        # the old stream even mid-recording, or the rebind no-ops and we
        # keep capturing the previous device's (silent) input.
        self._stop_monitoring_impl(force=user_initiated)

        # Small delay to let ALSA release the previous device
        time.sleep(0.15)

        old_hint = self._device_hint
        old_rate = self._sample_rate

        self._device_hint = hint
        self._device_index = None
        self._find_device(preferred_rate=preferred_rate)
        print(f"switch_device('{hint}'): idx={self._device_index} rate={self._sample_rate}", flush=True)

        if self._device_index is None:
            # Revert on failure
            log.warning("switch_device failed for hint '%s', reverting", hint)
            self._device_hint = old_hint
            self._find_device()
            if was_monitoring:
                self._start_monitoring_impl()
            return False

        # Resize recall buffer if sample rate changed
        if self._sample_rate != old_rate:
            self._recall_buf_frames = self._recall_buffer_seconds * self._sample_rate
            self._recall_buf = np.zeros((self._recall_buf_frames, P6_CHANNELS), dtype=np.float32)
            self._recall_write_pos = 0
            self._recall_total_written = 0
            log.info("Recall buffer resized for %dHz (%d frames)",
                     self._sample_rate, self._recall_buf_frames)

        if was_monitoring:
            self._start_monitoring_impl()

        log.info("Switched audio input to '%s' @ %dHz", hint, self._sample_rate)
        return True

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_armed(self) -> bool:
        return self._armed

    @property
    def duration(self) -> float:
        """Current recording duration in seconds."""
        return self._samples_written / self._sample_rate if self._recording else 0.0

    @property
    def current_file(self) -> Optional[str]:
        return self._current_file

    @property
    def peak_levels(self) -> tuple[float, float]:
        return (self._peak_l, self._peak_r)

    @property
    def rms_levels(self) -> tuple[float, float]:
        return (self._rms_l, self._rms_r)

    @property
    def threshold_mode(self) -> bool:
        return self._threshold_mode

    def toggle_threshold_mode(self):
        self._threshold_mode = not self._threshold_mode
        if not self._threshold_mode and self._recording:
            self.stop_recording()

    def set_threshold(self, level: float):
        self._threshold = max(0.005, min(0.5, level))

    @property
    def waveform(self) -> np.ndarray:
        """Get the waveform preview as a numpy array."""
        return self._waveform.copy()

    @property
    def input_overruns(self) -> int:
        """ALSA input overflow events since recorder started. Each one is missed audio."""
        return self._input_overruns

    @property
    def input_underruns(self) -> int:
        """ALSA input underflow events since recorder started."""
        return self._input_underruns

    @property
    def monitor_gain(self) -> float:
        """Gain applied only to live MON output routing, not recorded WAVs."""
        return self._monitor_gain

    def set_monitor_gain(self, gain: float) -> float:
        """Set live MON output gain. Safe to call while a route is active."""
        self._monitor_gain = _clamp_monitor_gain(gain)
        return self._monitor_gain

    def adjust_monitor_gain(self, delta: float) -> float:
        """Bump live MON output gain by delta and return the clamped value."""
        return self.set_monitor_gain(self._monitor_gain + float(delta))

    # ── Stream management ───────────────────────────────────────────────

    def start_monitoring(self) -> None:
        """Start the input stream for level metering (enqueued — the
        PortAudio open can stall under load, so it runs on the worker)."""
        self._enqueue(self._start_monitoring_impl)

    def _start_monitoring_impl(self) -> None:
        """Worker-thread body: open the input stream for level metering."""
        if self._stream is not None:
            return
        if not self.available:
            log.warning("Cannot start monitoring — no audio device")
            return

        try:
            self._stream = sd.InputStream(
                device=self._device_index,
                samplerate=self._sample_rate,
                channels=P6_CHANNELS,
                dtype="float32",
                blocksize=4096,  # Larger buffer reduces glitches on Pi 3B USB bus
                latency="high",
                callback=self._audio_callback,
            )
            self._stream.start()
            self._monitoring = True
            log.info("Monitoring started")
        except Exception as e:
            log.error("Failed to start monitoring: %s", e)
            self._stream = None

    def _monitor_output_callback(self, outdata: np.ndarray, frames: int,
                                  time_info, status) -> None:
        """Pull `frames` of audio from the ring buffer for the output device."""
        if status and status.output_underflow:
            # Brief silence is fine — buffer will refill on the next input tick
            pass
        with self._monitor_out_buf_lock:
            buf = self._monitor_out_buf
            if buf is None:
                outdata.fill(0)
                return
            n = self._monitor_out_buf_frames
            avail = self._monitor_out_write - self._monitor_out_read
            if self._monitor_out_rate > 0:
                target = max(frames * 4, int(0.25 * self._monitor_out_rate))
                target = min(target, max(0, n - frames))
                if avail > target + frames:
                    drop = avail - target
                    self._monitor_out_read += drop
                    avail -= drop
            take = min(frames, max(0, avail))
            if take > 0:
                r = self._monitor_out_read % n
                if r + take <= n:
                    outdata[:take] = buf[r:r + take]
                else:
                    first = n - r
                    outdata[:first] = buf[r:n]
                    outdata[first:take] = buf[:take - first]
                self._monitor_out_read += take
            if take < frames:
                missing = frames - take
                tail = (
                    outdata[take - 1].copy()
                    if take > 0 else self._monitor_out_tail.copy()
                )
                if float(np.max(np.abs(tail))) > 0.00001:
                    fade = np.linspace(1.0, 0.0, missing,
                                       dtype=np.float32)[:, None]
                    outdata[take:] = tail[None, :] * fade
                else:
                    outdata[take:].fill(0)
                self._monitor_out_tail.fill(0)
            elif frames > 0:
                self._monitor_out_tail = outdata[frames - 1].copy()

    def start_monitor_output(self, device_idx: int, sample_rate: int = 0) -> bool:
        """Start forwarding audio to a second output device (headphones).

        Uses a ring buffer between the input callback (which pushes) and
        the output stream's callback (which pulls). This decouples the
        two devices' clocks so drift doesn't manifest as periodic dropouts.
        """
        self.stop_monitor_output()
        if sd is None:
            return False

        # Try the recorder's input rate first; fall back to the caller's
        # requested rate (the destination device's preferred rate). Same
        # rate on both sides means we don't need to resample in callback.
        rates = [self._sample_rate]
        if sample_rate and sample_rate != self._sample_rate:
            rates.append(sample_rate)

        for rate in rates:
            try:
                # 1.5s ring buffer absorbs USB scheduling jitter and clock
                # drift between devices (their crystals don't tick at the
                # same Hz).
                buf_frames = max(int(1.5 * rate), 16384)
                # Prefill ~250ms with silence — input pushes ~93ms at a
                # time on Pi audio, so this guarantees the output never
                # sees an empty buffer between input arrivals (which is
                # what produced the residual crackle/dropouts).
                prefill_frames = int(0.25 * rate)
                with self._monitor_out_buf_lock:
                    self._monitor_out_buf = np.zeros(
                        (buf_frames, P6_CHANNELS), dtype=np.float32)
                    self._monitor_out_buf_frames = buf_frames
                    self._monitor_out_write = prefill_frames
                    self._monitor_out_read = 0
                    self._monitor_out_tail = np.zeros(
                        P6_CHANNELS, dtype=np.float32)
                self._monitor_out_rate = rate
                self._monitor_out_device = device_idx

                self._monitor_out_stream = sd.OutputStream(
                    device=device_idx,
                    samplerate=rate,
                    channels=P6_CHANNELS,
                    dtype="float32",
                    blocksize=2048,
                    latency="high",
                    callback=self._monitor_output_callback,
                )
                self._monitor_out_stream.start()
                # If the output device locked at a different rate than
                # the recorder's input, set up a resampler. Otherwise
                # clear any stale resampler from a previous routing.
                if rate != self._sample_rate:
                    self._monitor_resampler = _LinearResampler(
                        self._sample_rate, rate, P6_CHANNELS)
                    print(f"Monitor: resampling {self._sample_rate}→{rate}Hz",
                          flush=True)
                else:
                    self._monitor_resampler = None
                print(f"Monitor: output @ {rate}Hz (buf {buf_frames} frames)",
                      flush=True)
                return True
            except Exception as e:
                log.warning("Monitor output @ %dHz failed: %s", rate, e)
                self._monitor_out_stream = None

        with self._monitor_out_buf_lock:
            self._monitor_out_buf = None
        log.error("Monitor output: no rate worked for device %d", device_idx)
        return False

    def stop_monitor_output(self):
        """Stop the monitor output stream."""
        if self._monitor_out_stream:
            try:
                self._monitor_out_stream.stop()
                self._monitor_out_stream.close()
            except Exception:
                pass
            self._monitor_out_stream = None
            self._monitor_out_device = None
            log.info("Monitor output stopped")
        with self._monitor_out_buf_lock:
            self._monitor_out_buf = None
            self._monitor_out_buf_frames = 0
            self._monitor_out_write = 0
            self._monitor_out_read = 0
            self._monitor_out_tail = np.zeros(P6_CHANNELS, dtype=np.float32)
        self._monitor_resampler = None

    def stop_monitoring(self, force: bool = False) -> None:
        """Stop the input stream (enqueued — `_stream.close()` blocks in
        ALSA kernel state under load and must never run on the main loop)."""
        self._enqueue(self._stop_monitoring_impl, force)

    def _stop_monitoring_impl(self, force: bool = False) -> None:
        """Worker-thread body: tear down the input stream.

        The `_block_audio_changes` guard normally no-ops this so an
        internal/auto switch can't tear the stream down mid-recording. A
        `force=True` call comes from a deliberate user action (a card tap
        via switch_device(user_initiated=True)) — that switch has
        committed, so the old stream MUST close or the rebind silently
        fails and we keep capturing the previous device.

        Close is SYNCHRONOUS. This method runs on the recorder worker
        thread, which is already off the pygame main loop — a blocking
        close here can't freeze the UI. And synchronous is REQUIRED for
        correctness: _switch_device_impl runs stop -> probe -> open in
        sequence on this same worker thread, so the old stream must be
        fully closed before the new one opens on the same USB device.
        An earlier daemon-thread reap closed the old stream concurrently
        with the new open — that race left the new SP-404 stream
        delivering pure-silence buffers (peak 0.0000). Closing inline
        serializes it and kills the race.
        """
        if self._block_audio_changes and not force:
            log.debug("stop_monitoring blocked — screen-recording active")
            return
        if self._recording:
            self._stop_recording_impl()
        stream = self._stream
        self._stream = None
        self._monitoring = False
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    # ── Recording ───────────────────────────────────────────────────────

    def start_recording(self, session_name: str = "",
                        metadata: Optional[dict] = None,
                        pre_roll_seconds: Optional[float] = None) -> Optional[str]:
        """Start recording to a new WAV file (enqueued).

        Creating the file and — for Recall+Continue — writing the entire
        recall buffer as pre-roll are big synchronous writes; they run on
        the recorder worker thread, never the main loop. Returns at once.
        """
        self._enqueue(self._start_recording_impl, session_name, metadata,
                      pre_roll_seconds)
        return None

    def _start_recording_impl(self, session_name: str = "",
                              metadata: Optional[dict] = None,
                              pre_roll_seconds: Optional[float] = None) -> Optional[str]:
        """Worker-thread body of start_recording.

        Args:
            session_name: Optional prefix for the filename.
            metadata: Optional dict with bpm_at_record, pattern_at_record, etc.
                      Saved as sidecar JSON when recording stops.
            pre_roll_seconds: Prepend this many seconds from the recall buffer
                      to the new file before live audio starts. None = use the
                      recorder's default (set at construction); 0 = disabled;
                      >0 = include up to that much (capped at buffer fill).
                      Use float("inf") or any number ≥ buffer length to dump
                      the entire current recall buffer (Recall + Continue).
        """
        if sf is None:
            log.error("soundfile not available")
            return None
        if self._recording:
            log.debug("start_recording: already recording — ignoring")
            return None

        # Ensure monitoring is active
        if self._stream is None:
            self._start_monitoring_impl()
            if self._stream is None:
                return None

        # Resolve pre-roll: caller's value wins, else fall back to default
        if pre_roll_seconds is None:
            pre_roll_seconds = self._record_pre_roll_seconds
        pre_roll_seconds = max(0.0, float(pre_roll_seconds))

        # Generate filename with device prefix
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dev = self.device_name.replace(":", "").replace(" ", "_")
        prefix = session_name if session_name else dev
        name = f"{prefix}_{ts}.wav"
        filepath = os.path.join(self._recording_dir, name)

        # Store metadata for writing on stop — auto-tag source device
        self._record_metadata = metadata or {}
        self._record_metadata["source_device"] = self.device_name
        self._record_metadata["sample_rate"] = self._sample_rate

        try:
            writer = sf.SoundFile(
                filepath,
                mode="w",
                samplerate=self._sample_rate,
                channels=P6_CHANNELS,
                subtype="PCM_24",
                format="WAV",
            )
        except Exception as e:
            log.error("Failed to create WAV file: %s", e)
            return None

        # Pre-roll: if requested, dump the tail of the recall buffer into the
        # new file as the first frames. Snapshot the buffer state without
        # the audio lock since numpy reads on a fixed-size array are safe
        # against concurrent writes (we may get a torn frame at the seam,
        # but that's a single-block hiccup, not corruption).
        pre_roll_frames_written = 0
        if pre_roll_seconds > 0.0 and self._recall_total_written > 0:
            wanted = int(pre_roll_seconds * self._sample_rate)
            filled = min(self._recall_total_written, self._recall_buf_frames)
            take = min(wanted, filled)
            if take > 0:
                pos = self._recall_write_pos
                if self._recall_total_written >= self._recall_buf_frames:
                    # Buffer wrapped — take the most recent `take` frames
                    # ending at write_pos (= now). That's the trailing
                    # `take` of the ordered buffer.
                    start = (pos - take) % self._recall_buf_frames
                    if start + take <= self._recall_buf_frames:
                        ordered = self._recall_buf[start:start + take]
                    else:
                        first = self._recall_buf_frames - start
                        ordered = np.concatenate([
                            self._recall_buf[start:],
                            self._recall_buf[:take - first],
                        ])
                else:
                    # Buffer hasn't wrapped — take the most recent `take`
                    # frames ending at write_pos.
                    start = max(0, pos - take)
                    ordered = self._recall_buf[start:pos].copy()
                try:
                    writer.write(ordered)
                    pre_roll_frames_written = len(ordered)
                except Exception as e:
                    log.error("Pre-roll write failed: %s", e)

        with self._lock:
            self._writer = writer
            self._current_file = filepath
            self._samples_written = pre_roll_frames_written
            self._recording = True

        # Tag pre-roll into metadata so DAWs / users can see it
        if pre_roll_frames_written > 0:
            pr_secs = pre_roll_frames_written / self._sample_rate
            self._record_metadata["pre_roll_seconds"] = round(pr_secs, 2)
            log.info("Recording started: %s (pre-roll %.1fs)", filepath, pr_secs)
        else:
            log.info("Recording started: %s", filepath)
        return filepath

    def recall_and_continue(self, session_name: str = "",
                             metadata: Optional[dict] = None) -> Optional[str]:
        """Start a recording that begins with the entire current recall
        buffer, then continues with live audio (enqueued). Convenience
        wrapper — the worker runs start_recording with a full-buffer
        pre-roll, so the big buffer write never touches the main loop."""
        self._enqueue(self._start_recording_impl, session_name, metadata,
                      float(self._recall_buffer_seconds))
        return None

    @property
    def recall_buffer_seconds(self) -> int:
        """Configured length of the rolling recall buffer."""
        return self._recall_buffer_seconds

    @property
    def record_pre_roll_seconds(self) -> float:
        """Default pre-roll length applied to every start_recording call."""
        return self._record_pre_roll_seconds

    def set_recall_buffer_seconds(self, seconds: int) -> int:
        """Resize the recall buffer (clamped to MIN/MAX). Briefly stops
        monitoring to swap the underlying numpy array safely. Returns the
        clamped value actually applied. The buffer fill is reset on resize
        — we don't try to preserve old audio across the resize because that
        adds complexity without real benefit (resize is a settings action,
        not something done mid-take)."""
        seconds = max(RECALL_BUFFER_SECONDS_MIN,
                       min(RECALL_BUFFER_SECONDS_MAX, int(seconds)))
        if seconds == self._recall_buffer_seconds:
            return seconds
        was_monitoring = self._monitoring
        # _impl directly — this is a synchronous settings call that needs
        # the array swapped before it returns. _stop_monitoring_impl still
        # honors the screen-recording guard (no-op mid-take), so it can't
        # block the main loop here.
        if was_monitoring:
            self._stop_monitoring_impl()
        self._recall_buffer_seconds = seconds
        self._recall_buf_frames = seconds * self._sample_rate
        self._recall_buf = np.zeros((self._recall_buf_frames, P6_CHANNELS),
                                     dtype=np.float32)
        self._recall_write_pos = 0
        self._recall_total_written = 0
        # Re-clamp the pre-roll default — can't pre-roll longer than the buffer
        if self._record_pre_roll_seconds > seconds:
            self._record_pre_roll_seconds = float(seconds)
        log.info("Recall buffer resized to %ds (%d frames @ %dHz)",
                 seconds, self._recall_buf_frames, self._sample_rate)
        if was_monitoring:
            self._start_monitoring_impl()
        return seconds

    def set_record_pre_roll_seconds(self, seconds: float) -> float:
        """Set the default pre-roll length applied to every REC press.
        Clamped to [0, recall_buffer_seconds]. Returns the clamped value."""
        seconds = max(0.0, min(float(self._recall_buffer_seconds),
                                float(seconds)))
        self._record_pre_roll_seconds = seconds
        log.info("Record pre-roll set to %.1fs", seconds)
        return seconds

    def stop_recording(self) -> Optional[str]:
        """Stop recording and close the WAV file (enqueued — the WAV
        close + sidecar write run on the worker, off the main loop)."""
        self._enqueue(self._stop_recording_impl)
        return None

    def _stop_recording_impl(self) -> Optional[str]:
        """Worker-thread body: close the WAV file and write the sidecar."""
        filepath = None
        duration = 0.0

        with self._lock:
            if self._writer:
                filepath = self._current_file
                duration = self._samples_written / self._sample_rate
                try:
                    self._writer.close()
                except Exception:
                    pass
                self._writer = None
            self._recording = False

        if filepath:
            log.info("Recording stopped: %s (%.1fs)", filepath, duration)
            # Write sidecar metadata JSON
            meta = getattr(self, '_record_metadata', {})
            meta.setdefault("user_name", "")
            meta.setdefault("starred", False)
            meta.setdefault("notes", "")
            meta["duration"] = round(duration, 1)
            meta["created_at"] = datetime.now().isoformat()
            try:
                with open(filepath + ".meta.json", "w") as f:
                    json.dump(meta, f, indent=2)
            except Exception as e:
                log.error("Failed to write metadata: %s", e)
            self._record_metadata = {}

            if self.on_recording_complete:
                self.on_recording_complete(filepath, duration)

        return filepath

    # ── Audio callback ──────────────────────────────────────────────────

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status) -> None:
        """Called by sounddevice for each audio block.

        Keep this as fast as possible — runs in the audio thread.
        Only do disk I/O when actually recording.
        """
        # Surface ALSA overruns — silently dropping these masks choppy recordings
        # (Pi 3B + USB capture under load can lose ~25% of buffers).
        if status:
            if status.input_overflow:
                self._input_overruns += 1
            if status.input_underflow:
                self._input_underruns += 1
            if status.input_overflow or status.input_underflow:
                now = time.monotonic()
                if now - self._input_overrun_log_t >= 1.0:
                    self._input_overrun_log_t = now
                    log.warning(
                        "Recorder: ALSA input overrun (overflow=%d underflow=%d total)",
                        self._input_overruns, self._input_underruns,
                    )
                    if self._input_overrun_console_count < 3:
                        self._input_overrun_console_count += 1
                        print(
                            f"[Compa recorder] ALSA input overrun "
                            f"(overflow={self._input_overruns} underflow={self._input_underruns})",
                            flush=True,
                        )

        # Write to disk FIRST if recording (highest priority)
        if self._recording and self._writer:
            try:
                self._writer.write(indata)
                self._samples_written += frames
            except Exception as e:
                log.error("Write error: %s", e)

        # Always fill the recall buffer (rolling last 60s)
        pos = self._recall_write_pos
        buf_len = self._recall_buf_frames
        if pos + frames <= buf_len:
            self._recall_buf[pos:pos + frames] = indata
        else:
            # Wrap around
            first = buf_len - pos
            self._recall_buf[pos:buf_len] = indata[:first]
            self._recall_buf[:frames - first] = indata[first:]
        self._recall_write_pos = (pos + frames) % buf_len
        self._recall_total_written += frames

        # Forward to monitor output (headphones on another device).
        # Resample to the output device's rate (no-op if matched), then
        # push into the ring buffer; the output stream's callback drains
        # it. If the ring buffer fills (input device clock runs faster
        # than output's), drop oldest frames to keep latency bounded.
        if self._monitor_out_buf is not None:
            mon_data = indata
            rs = self._monitor_resampler
            if rs is not None:
                mon_data = rs.process(indata)
            mon_data = _apply_monitor_gain(mon_data, self._monitor_gain)
            n_mon = mon_data.shape[0]
            if n_mon > 0:
                with self._monitor_out_buf_lock:
                    buf = self._monitor_out_buf
                    if buf is not None:
                        n = self._monitor_out_buf_frames
                        avail_write = n - (self._monitor_out_write - self._monitor_out_read)
                        if avail_write < n_mon:
                            self._monitor_out_read += (n_mon - avail_write)
                        w = self._monitor_out_write % n
                        if w + n_mon <= n:
                            buf[w:w + n_mon] = mon_data
                        else:
                            first = n - w
                            buf[w:n] = mon_data[:first]
                            buf[:n_mon - first] = mon_data[first:]
                        self._monitor_out_write += n_mon

        # Forward to Link Audio broadcaster (sends to Live 12.4 etc. over LAN)
        if self.link_broadcaster is not None:
            try:
                self.link_broadcaster.push(indata, self._sample_rate)
            except Exception:
                pass  # Don't crash the audio callback

        # Quick level metering — just peak, skip RMS to save CPU
        abs_data = np.abs(indata)
        if indata.shape[1] >= 2:
            self._peak_l = float(abs_data[:, 0].max())
            self._peak_r = float(abs_data[:, 1].max())
        else:
            self._peak_l = self._peak_r = float(abs_data.max())

        # Threshold auto-recording
        if self._threshold_mode:
            import time as _time
            peak = max(self._peak_l, self._peak_r)
            if not self._recording and peak > self._threshold:
                self.start_recording()
                self._silence_start = 0
            elif self._recording:
                if peak > self._threshold:
                    self._silence_start = 0
                elif self._silence_start == 0:
                    self._silence_start = _time.time()
                elif _time.time() - self._silence_start > self._silence_timeout:
                    self.stop_recording()

        # Waveform preview
        self._waveform[self._waveform_pos % WAVEFORM_POINTS] = (self._peak_l + self._peak_r) * 0.5
        self._waveform_pos += 1

    # ── Recording list ──────────────────────────────────────────────────

    def list_recordings(self) -> list[dict]:
        """List all recordings with metadata."""
        recordings = []
        if not os.path.isdir(self._recording_dir):
            return recordings

        for fname in sorted(os.listdir(self._recording_dir), reverse=True):
            if not fname.endswith(".wav"):
                continue
            fpath = os.path.join(self._recording_dir, fname)
            try:
                info = sf.info(fpath)
                rec = {
                    "filename": fname,
                    "path": fpath,
                    "duration": info.duration,
                    "sample_rate": info.samplerate,
                    "channels": info.channels,
                    "size_mb": os.path.getsize(fpath) / (1024 * 1024),
                }
            except Exception:
                rec = {
                    "filename": fname,
                    "path": fpath,
                    "duration": 0,
                    "size_mb": os.path.getsize(fpath) / (1024 * 1024),
                }
            # Merge sidecar metadata
            meta = self.load_metadata(fpath)
            rec.update(meta)
            recordings.append(rec)

        return recordings

    # ── Metadata ────────────────────────────────────────────────────────

    @staticmethod
    def load_metadata(wav_path: str) -> dict:
        """Load sidecar metadata for a WAV file."""
        meta_path = wav_path + ".meta.json"
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"user_name": "", "starred": False, "notes": ""}

    @staticmethod
    def save_metadata(wav_path: str, meta: dict) -> None:
        """Save sidecar metadata for a WAV file."""
        meta_path = wav_path + ".meta.json"
        try:
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            log.error("Failed to save metadata: %s", e)

    def delete_recording(self, wav_path: str) -> bool:
        """Delete a WAV file and its sidecar metadata."""
        try:
            if os.path.exists(wav_path):
                os.remove(wav_path)
            meta_path = wav_path + ".meta.json"
            if os.path.exists(meta_path):
                os.remove(meta_path)
            log.info("Deleted recording: %s", wav_path)
            return True
        except Exception as e:
            log.error("Failed to delete: %s", e)
            return False

    # ── Recall buffer ────────────────────────────────────────────────────

    @property
    def recall_seconds_available(self) -> float:
        """How many seconds of audio are in the recall buffer."""
        filled = min(self._recall_total_written, self._recall_buf_frames)
        return filled / self._sample_rate

    def recall_buffer(self, session_name: str = "") -> Optional[str]:
        """Save the recall buffer to a WAV file (enqueued).

        The buffer is tens of MB — writing it is a big synchronous
        soundfile write that froze the main loop when RCL was pressed
        mid-take. It now runs on the recorder worker thread. Returns at
        once; the worker logs the saved path and fires
        on_recording_complete so the Recordings tab picks it up.
        """
        self._enqueue(self._recall_buffer_impl, session_name)
        return None

    def save_recall(self, session_name: str = "") -> Optional[str]:
        """Compatibility wrapper for UI paths that still call save_recall."""
        return self.recall_buffer(session_name)

    def _recall_buffer_impl(self, session_name: str = "") -> Optional[str]:
        """Worker-thread body: snapshot the recall buffer and write it
        to a WAV file. Returns the filepath, or None on failure."""
        if sf is None:
            return None

        filled = min(self._recall_total_written, self._recall_buf_frames)
        if filled < self._sample_rate:  # Less than 1 second — not worth saving
            log.warning("Recall buffer too short (%.1fs)", filled / self._sample_rate)
            return None

        # Read the buffer in order (oldest to newest)
        pos = self._recall_write_pos
        if self._recall_total_written >= self._recall_buf_frames:
            # Buffer has wrapped — read from write_pos to end, then start to write_pos
            ordered = np.concatenate([
                self._recall_buf[pos:],
                self._recall_buf[:pos],
            ])
        else:
            # Buffer hasn't filled yet — read from start to write_pos
            ordered = self._recall_buf[:pos].copy()

        # Generate filename
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dev = self.device_name.replace(":", "").replace(" ", "_")
        prefix = session_name if session_name else dev
        name = f"recall_{prefix}_{ts}.wav"
        filepath = os.path.join(self._recording_dir, name)

        try:
            with sf.SoundFile(filepath, mode="w", samplerate=self._sample_rate,
                              channels=P6_CHANNELS, subtype="PCM_24", format="WAV") as f:
                f.write(ordered)
            duration = len(ordered) / self._sample_rate
            # Write metadata sidecar for recall captures
            meta = {
                "source_device": self.device_name,
                "sample_rate": self._sample_rate,
                "type": "recall",
                "duration": round(duration, 2),
            }
            self.save_metadata(filepath, meta)
            log.info("Recall saved: %s (%.1fs)", filepath, duration)
            print(f"Recall saved: {filepath} ({duration:.1f}s)", flush=True)
            # Notify the app so the Recordings tab picks it up immediately,
            # same as a normal stop_recording. Without this the recall file
            # only appears on a manual rescan.
            if self.on_recording_complete:
                try:
                    self.on_recording_complete(filepath, duration)
                except Exception as e:
                    log.error("on_recording_complete (recall) failed: %s", e)
            return filepath
        except Exception as e:
            log.error("Recall save failed: %s", e)
            return None

    # ── Playback ─────────────────────────────────────────────────────────

    def _find_playback_device(self, prefer_hint: str = "") -> tuple:
        """Find a working output device matching prefer_hint first.

        Returns (index, sample_rate) or (None, None).
        Cache is keyed on prefer_hint so focus changes invalidate it.
        """
        cache_key = prefer_hint
        cached = getattr(self, "_cached_play_devices", {}).get(cache_key)
        if cached is not None:
            return cached

        # Build hint priority: focused device first, then fallbacks
        hints = []
        if prefer_hint:
            hints.append(prefer_hint)
        for h in ["SP-404", "P-6", "USB Audio", "Headphones"]:
            if h not in hints:
                hints.append(h)

        devices = sd.query_devices()
        for hint in hints:
            for i, dev in enumerate(devices):
                name = dev.get("name", "")
                if hint.lower() in name.lower() and dev.get("max_output_channels", 0) >= 2:
                    native = int(dev.get("default_samplerate", 44100))
                    try:
                        s = sd.OutputStream(device=i, samplerate=native,
                                            channels=2, dtype="float32", blocksize=4096)
                        s.start(); s.stop(); s.close()
                        log.info("Playback device: %s @ %dHz", name, native)
                        print(f"Playback device: {name} @ {native}Hz (prefer={prefer_hint})", flush=True)
                        if not hasattr(self, "_cached_play_devices"):
                            self._cached_play_devices = {}
                        self._cached_play_devices[cache_key] = (i, native)
                        return (i, native)
                    except Exception:
                        continue
        return (None, None)

    def clear_playback_cache(self):
        """Clear cached playback devices — call when focus or devices change."""
        self._cached_play_devices = {}

    def play(self, filepath: str) -> None:
        """Play a WAV file through the best available output device."""
        print(f"recorder.play({filepath!r}) called", flush=True)
        if sd is None or sf is None:
            print("  sd or sf is None — aborting", flush=True)
            return
        self.stop_playback()

        # Pause monitoring so we don't fight for USB bandwidth.
        # _impl directly — playback setup runs synchronously and needs the
        # input stream released before it opens the output stream.
        was_monitoring = self._monitoring
        if was_monitoring:
            self._stop_monitoring_impl()

        # Playback state flags (accessed from UI thread)
        self._playback_stop = False
        self._playback_paused = False
        self._playback_seek_frame: int | None = None  # request to seek
        self._playback_speed = 1.0  # 0.25 - 4.0
        self._playback_reverse = False
        self._playback_position = 0  # current frame (for UI)
        self._playback_total_frames = 0
        self._playback_rate = 44100  # source sample rate

        def _play_thread():
            stream = None
            try:
                data, rate = sf.read(filepath, dtype="float32")
                if len(data) == 0:
                    return
                if data.ndim == 1:
                    data = np.column_stack([data, data])
                peak = float(np.max(np.abs(data)))
                if peak > 0:
                    data = data * (0.9 / peak)

                # Use focused device as the playback target
                out_device, out_rate = self._find_playback_device(self._device_hint)
                if out_device is None:
                    log.warning("No working playback device found")
                    print("Playback: no working device", flush=True)
                    return
                if out_rate is None:
                    out_rate = rate

                # Resample if needed (use libsamplerate for quality, fall back to linear)
                if out_rate != rate:
                    ratio = out_rate / rate
                    if _sr is not None:
                        # High-quality SRC (sinc interpolation)
                        data = _sr.resample(data, ratio, "sinc_medium").astype(np.float32)
                    else:
                        new_len = int(len(data) * ratio)
                        new_data = np.zeros((new_len, 2), dtype=np.float32)
                        x_old = np.arange(len(data))
                        x_new = np.linspace(0, len(data), new_len)
                        new_data[:, 0] = np.interp(x_new, x_old, data[:, 0])
                        new_data[:, 1] = np.interp(x_new, x_old, data[:, 1])
                        data = new_data

                # Manual playback with pause/seek/speed/reverse support
                blocksize = 4096
                self._playback_total_frames = len(data)
                self._playback_rate = out_rate
                stream = sd.OutputStream(device=out_device, samplerate=out_rate,
                                          channels=2, dtype="float32", blocksize=blocksize)
                stream.start()
                pos = 0

                while pos < len(data) and not self._playback_stop:
                    # Handle seek request
                    if self._playback_seek_frame is not None:
                        pos = max(0, min(self._playback_seek_frame, len(data) - 1))
                        self._playback_seek_frame = None

                    # Handle pause
                    if self._playback_paused:
                        # Write silence while paused so the stream stays alive
                        silence = np.zeros((blocksize, 2), dtype=np.float32)
                        stream.write(silence)
                        time.sleep(0.02)
                        continue

                    speed = max(0.25, min(4.0, self._playback_speed))
                    reverse = self._playback_reverse

                    # Read a chunk
                    if reverse:
                        start_f = max(0, pos - blocksize)
                        chunk = data[start_f:pos][::-1].copy()
                        pos = start_f
                    else:
                        end_f = min(pos + blocksize, len(data))
                        chunk = data[pos:end_f].copy()
                        pos = end_f

                    # Apply speed change via resampling (if needed)
                    if speed != 1.0 and _sr is not None and len(chunk) > 0:
                        try:
                            # ratio > 1 makes output longer (slower), < 1 shorter (faster)
                            chunk = _sr.resample(chunk, 1.0 / speed, "sinc_fastest").astype(np.float32)
                        except Exception:
                            pass

                    # Pad to blocksize if needed
                    if len(chunk) < blocksize:
                        if len(chunk) == 0:
                            break
                        padded = np.zeros((blocksize, 2), dtype=np.float32)
                        padded[:len(chunk)] = chunk[:blocksize]
                        chunk = padded
                    elif len(chunk) > blocksize:
                        chunk = chunk[:blocksize]

                    self._playback_position = pos
                    stream.write(chunk)

                stream.stop()
                stream.close()
                stream = None
            except Exception as e:
                log.error("Playback error: %s", e)
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
            finally:
                self._playing_back = False
                if was_monitoring:
                    try:
                        self._start_monitoring_impl()
                    except Exception:
                        pass

        self._playing_back = True
        self._playback_file = filepath
        import threading
        t = threading.Thread(target=_play_thread, daemon=True)
        t.start()

    def stop_playback(self) -> None:
        """Signal playback thread to stop."""
        self._playback_stop = True
        self._playback_paused = False
        self._playback_file = None

    def toggle_playback_pause(self) -> bool:
        """Toggle pause. Returns new pause state."""
        self._playback_paused = not getattr(self, "_playback_paused", False)
        return self._playback_paused

    def seek_playback(self, frame: int) -> None:
        """Seek to a specific frame position."""
        self._playback_seek_frame = max(0, int(frame))

    def seek_playback_relative(self, seconds: float) -> None:
        """Seek relative to current position."""
        rate = getattr(self, "_playback_rate", 44100)
        delta = int(seconds * rate)
        current = getattr(self, "_playback_position", 0)
        self._playback_seek_frame = max(0, current + delta)

    def set_playback_speed(self, speed: float) -> None:
        """Set playback speed (0.25 to 4.0)."""
        self._playback_speed = max(0.25, min(4.0, float(speed)))

    def set_playback_reverse(self, reverse: bool) -> None:
        """Toggle reverse playback."""
        self._playback_reverse = bool(reverse)

    @property
    def playback_progress(self) -> float:
        """Returns 0.0-1.0 based on current position."""
        total = getattr(self, "_playback_total_frames", 0)
        if total <= 0:
            return 0.0
        return min(1.0, getattr(self, "_playback_position", 0) / total)

    @property
    def playback_paused(self) -> bool:
        return getattr(self, "_playback_paused", False)

    @property
    def playback_speed(self) -> float:
        return getattr(self, "_playback_speed", 1.0)

    @property
    def playback_reverse(self) -> bool:
        return getattr(self, "_playback_reverse", False)

    @property
    def is_playing_back(self) -> bool:
        return getattr(self, '_playing_back', False)

    @property
    def playback_file(self) -> Optional[str]:
        return getattr(self, '_playback_file', None)

    def shutdown(self) -> None:
        """Clean shutdown."""
        self.stop_playback()
        self._stop_monitoring_impl()
        # Stop the recorder worker thread.
        try:
            self._cmd_queue.put(None)
        except Exception:
            pass
