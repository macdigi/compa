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

        # Callbacks
        self.on_level: Optional[Callable[[float, float, float, float], None]] = None
        self.on_recording_complete: Optional[Callable[[str, float], None]] = None

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
                    log.info("P6 recorder: device %d '%s' @ %dHz", dev_idx, dev_name, rate)
                    return
                except Exception:
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

        old_hint = self._device_hint
        old_rate = self._sample_rate

        self._device_hint = hint
        self._device_index = None
        self._find_device()

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
                blocksize=2048,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._monitoring = True
            log.info("Monitoring started")
        except Exception as e:
            log.error("Failed to start monitoring: %s", e)
            self._stream = None

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

        # Generate filename
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"p6_{session_name + '_' if session_name else ''}{ts}.wav"
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
        name = f"p6_recall_{session_name + '_' if session_name else ''}{ts}.wav"
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

    def play(self, filepath: str) -> None:
        """Play a WAV file through the audio output device."""
        if sd is None or sf is None:
            return
        self.stop_playback()

        def _play_thread():
            try:
                data, rate = sf.read(filepath, dtype="float32")
                # Normalize so peak reaches -1 dB (P-6 USB audio is very quiet)
                peak = float(np.max(np.abs(data)))
                if peak > 0:
                    data *= 0.9 / peak
                    log.info("Playback normalized: peak %.4f -> 0.9 (%.1f dB gain)",
                             peak, 20 * np.log10(0.9 / peak))
                # Find output device (same device or default)
                out_device = self._device_index
                sd.play(data, samplerate=rate, device=out_device)
                sd.wait()
            except Exception as e:
                log.error("Playback error: %s", e)
            finally:
                self._playing_back = False

        self._playing_back = True
        self._playback_file = filepath
        import threading
        t = threading.Thread(target=_play_thread, daemon=True)
        t.start()

    def stop_playback(self) -> None:
        """Stop any active playback."""
        try:
            sd.stop()
        except Exception:
            pass
        self._playing_back = False
        self._playback_file = None

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
