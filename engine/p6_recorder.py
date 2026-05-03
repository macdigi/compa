"""
P-6 performance audio recorder.

Records USB audio from the P-6 (48kHz/24-bit stereo) to WAV files on disk.
Provides real-time level metering and waveform preview data for the UI.
"""

import json
import logging
import math
import os
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

try:
    import samplerate as _sr
except ImportError:
    _sr = None

log = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 44100  # P-6 on Pi ALSA only supports 44.1kHz
P6_CHANNELS = 2
WAVEFORM_POINTS = 800  # Match screen width for display
RECALL_BUFFER_SECONDS = 60  # Rolling buffer for "forgot to press record"


class P6Recorder:
    """Records P-6 USB audio to WAV files with live metering.

    Opens a sounddevice InputStream on the P-6's USB audio device,
    streams audio to disk in a background thread, and provides
    real-time level/waveform data for UI display.
    """

    def __init__(self, recording_dir: str, device_hint: str = "P-6"):
        self._recording_dir = recording_dir
        self._device_hint = device_hint
        self._device_index: Optional[int] = None
        self._sample_rate = DEFAULT_SAMPLE_RATE

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

        # Monitor output — forward audio to a second device (headphones)
        self._monitor_out_stream: Optional["sd.OutputStream"] = None
        self._monitor_out_device: Optional[int] = None
        self._monitor_out_rate: int = 0

        # Threshold recording
        self._threshold = 0.02
        self._threshold_mode = False
        self._silence_timeout = 3.0
        self._silence_start = 0.0

        # Recall buffer — rolling circular buffer of last N seconds
        self._recall_buf_frames = RECALL_BUFFER_SECONDS * self._sample_rate
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

        self._find_device()

    def _find_device(self) -> None:
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

        # Fallback to default input
        if not candidates:
            try:
                default_in = sd.default.device[0]
                dev = sd.query_devices(default_in)
                if dev.get("max_input_channels", 0) >= 2:
                    candidates.append((default_in, dev.get("name", "default")))
            except Exception:
                pass

        if not candidates:
            print(f"No audio input matching '{self._device_hint}'", flush=True)
            return

        # Try each candidate with sample rate probing
        for dev_idx, dev_name in candidates:
            for rate in [44100, 48000, 96000]:
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

    def switch_device(self, hint: str, preferred_rate: int = 0) -> bool:
        """Switch to a different audio input device.

        Stops monitoring, re-detects with the new hint, resizes the
        recall buffer for the new sample rate, then restarts monitoring.

        Returns True if a device was found and switched to.
        """
        was_monitoring = self._monitoring
        if self._recording:
            self.stop_recording()
        self.stop_monitoring()

        # Small delay to let ALSA release the previous device
        import time
        time.sleep(0.15)

        old_hint = self._device_hint
        old_rate = self._sample_rate

        self._device_hint = hint
        self._device_index = None
        self._find_device()
        print(f"switch_device('{hint}'): idx={self._device_index}", flush=True)

        if self._device_index is None:
            # Revert on failure
            log.warning("switch_device failed for hint '%s', reverting", hint)
            self._device_hint = old_hint
            self._find_device()
            if was_monitoring:
                self.start_monitoring()
            return False

        # Resize recall buffer if sample rate changed
        if self._sample_rate != old_rate:
            self._recall_buf_frames = RECALL_BUFFER_SECONDS * self._sample_rate
            self._recall_buf = np.zeros((self._recall_buf_frames, P6_CHANNELS), dtype=np.float32)
            self._recall_write_pos = 0
            self._recall_total_written = 0
            log.info("Recall buffer resized for %dHz (%d frames)",
                     self._sample_rate, self._recall_buf_frames)

        if was_monitoring:
            self.start_monitoring()

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

    # ── Stream management ───────────────────────────────────────────────

    def start_monitoring(self) -> None:
        """Start the input stream for level metering (without recording)."""
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

    def start_monitor_output(self, device_idx: int, sample_rate: int = 0) -> bool:
        """Start forwarding audio to a second output device (headphones).

        The recorder's audio callback will write to this output stream
        in addition to recording/buffering. No second input stream needed.
        Opens at the recorder's own sample rate so no resampling is needed
        in the audio callback — ALSA plughw handles any conversion.
        """
        self.stop_monitor_output()
        if sd is None:
            return False

        # Use the recorder's input rate — avoids resampling in the callback
        out_rate = self._sample_rate

        try:
            self._monitor_out_rate = out_rate
            self._monitor_out_device = device_idx

            self._monitor_out_stream = sd.OutputStream(
                device=device_idx,
                samplerate=out_rate,
                channels=P6_CHANNELS,
                dtype="float32",
                blocksize=2048,
                latency="high",  # Larger buffer = fewer glitches
            )
            self._monitor_out_stream.start()
            log.info("Monitor output started: device %d @ %dHz", device_idx, out_rate)
            return True
        except Exception as e:
            # If source rate doesn't work, try the requested rate
            log.warning("Monitor at %dHz failed, trying %dHz", out_rate, sample_rate)
            try:
                self._monitor_out_rate = sample_rate
                self._monitor_out_stream = sd.OutputStream(
                    device=device_idx,
                    samplerate=sample_rate,
                    channels=P6_CHANNELS,
                    dtype="float32",
                    blocksize=4096,
                    latency="high",
                )
                self._monitor_out_stream.start()
                log.info("Monitor output started: device %d @ %dHz (fallback)", device_idx, sample_rate)
                return True
            except Exception as e2:
                log.error("Monitor output failed: %s", e2)
                self._monitor_out_stream = None
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

    def stop_monitoring(self) -> None:
        """Stop the input stream."""
        if self._recording:
            self.stop_recording()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._monitoring = False

    # ── Recording ───────────────────────────────────────────────────────

    def start_recording(self, session_name: str = "",
                        metadata: Optional[dict] = None) -> Optional[str]:
        """Start recording to a new WAV file.

        Args:
            session_name: Optional prefix for the filename.
            metadata: Optional dict with bpm_at_record, pattern_at_record, etc.
                      Saved as sidecar JSON when recording stops.
        """
        if sf is None:
            log.error("soundfile not available")
            return None

        # Ensure monitoring is active
        if self._stream is None:
            self.start_monitoring()
            if self._stream is None:
                return None

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

        with self._lock:
            self._writer = writer
            self._current_file = filepath
            self._samples_written = 0
            self._recording = True

        log.info("Recording started: %s", filepath)
        return filepath

    def stop_recording(self) -> Optional[str]:
        """Stop recording and close the WAV file."""
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

        # Forward to monitor output (headphones on another device)
        if self._monitor_out_stream and self._monitor_out_stream.active:
            try:
                self._monitor_out_stream.write(indata)
            except Exception:
                pass  # Don't crash the audio callback

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
        """Save the recall buffer to a WAV file.

        Returns the filepath, or None on failure.
        """
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

        # Pause monitoring so we don't fight for USB bandwidth
        was_monitoring = self._monitoring
        if was_monitoring:
            self.stop_monitoring()

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
                        self.start_monitoring()
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
        self.stop_monitoring()
