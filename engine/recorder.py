"""
Audio recorder for pi-sampler.

Captures audio from a sounddevice InputStream and writes to a pad.
Designed for real-time use on Raspberry Pi 3B with pre-allocated buffers.
"""

import threading
import time
import math
import numpy as np


class Recorder:
    """
    Audio recorder that captures input and assigns recorded audio to a pad.

    Thread-safe: process_input() is called from the audio callback thread,
    while arm/start/stop may be called from the main or UI thread.
    """

    def __init__(self, sample_rate=44100, max_seconds=30):
        self._sample_rate = sample_rate
        self._max_seconds = max_seconds
        self._channels = 2

        # Pre-allocate the recording buffer (stereo float32)
        max_frames = int(self._sample_rate * self._max_seconds)
        self._buffer = np.zeros((max_frames, self._channels), dtype=np.float32)
        self._write_pos = 0

        # State
        self._armed = False
        self._recording = False
        self._armed_pad = -1
        self._monitor_enabled = False
        self._threshold = 0.0  # 0 = manual start only

        # Countdown
        self._countdown_active = False
        self._countdown_remaining = 0
        self._countdown_start_time = 0.0

        # Level metering (updated from audio thread, read from UI thread)
        self._peak_l = 0.0
        self._peak_r = 0.0
        self._rms_l = 0.0
        self._rms_r = 0.0

        # Lock for state transitions (arm/disarm/start/stop).
        # process_input uses lightweight atomic-style reads to avoid
        # locking in the audio callback.
        self._lock = threading.Lock()

        # Callbacks
        self.on_complete = None  # (pad_index, audio_data, waveform_preview)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_armed(self):
        return self._armed

    @property
    def is_recording(self):
        return self._recording

    @property
    def recorded_seconds(self):
        if not self._recording:
            return 0.0
        return self._write_pos / self._sample_rate

    @property
    def max_seconds(self):
        return self._max_seconds

    @max_seconds.setter
    def max_seconds(self, value):
        """
        Update maximum recording time. Only effective before the next arm().
        Reallocates the buffer.
        """
        with self._lock:
            if self._recording:
                return
            self._max_seconds = max(1.0, float(value))
            max_frames = int(self._sample_rate * self._max_seconds)
            self._buffer = np.zeros(
                (max_frames, self._channels), dtype=np.float32
            )

    @property
    def input_level(self):
        """Return (peak_l, peak_r, rms_l, rms_r) for metering display."""
        return (self._peak_l, self._peak_r, self._rms_l, self._rms_r)

    # ------------------------------------------------------------------
    # Arm / Disarm
    # ------------------------------------------------------------------

    def arm(self, pad_index):
        """Arm recording to a specific pad. Waits for start() trigger."""
        with self._lock:
            if self._recording:
                return
            self._armed_pad = int(pad_index)
            self._write_pos = 0
            self._armed = True

    def disarm(self):
        """Cancel armed state without recording."""
        with self._lock:
            if self._recording:
                return
            self._armed = False
            self._armed_pad = -1
            self._countdown_active = False

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start(self):
        """Begin recording. Must be armed first."""
        with self._lock:
            if not self._armed or self._recording:
                return
            self._write_pos = 0
            self._recording = True

    def start_with_countdown(self, beats=3):
        """
        Start a countdown before recording begins.
        Countdown ticks are in seconds at ~1 per beat based on an assumed
        120 BPM if no external clock is provided. The caller (or metronome)
        can drive this by checking countdown_remaining.
        """
        with self._lock:
            if not self._armed or self._recording:
                return
            self._countdown_remaining = int(beats)
            self._countdown_start_time = time.monotonic()
            self._countdown_active = True

    @property
    def countdown_remaining(self):
        """Seconds remaining in countdown, or 0 if not counting down."""
        if not self._countdown_active:
            return 0
        elapsed = time.monotonic() - self._countdown_start_time
        remaining = self._countdown_remaining - int(elapsed)
        if remaining <= 0:
            return 0
        return remaining

    def stop(self):
        """
        Stop recording and return the recorded audio as a numpy array.
        Also triggers on_complete callback if set.
        Returns numpy array of shape (frames, 2) or None if not recording.
        """
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            self._armed = False
            self._countdown_active = False

            if self._write_pos == 0:
                return None

            # Copy recorded portion out of pre-allocated buffer
            audio_data = self._buffer[: self._write_pos].copy()
            pad_index = self._armed_pad
            self._armed_pad = -1

        # Generate waveform preview outside the lock
        waveform = self._generate_waveform_preview(audio_data)

        if self.on_complete is not None:
            try:
                self.on_complete(pad_index, audio_data, waveform)
            except Exception:
                pass  # Don't let callback errors crash the recorder

        return audio_data

    # ------------------------------------------------------------------
    # Audio callback interface
    # ------------------------------------------------------------------

    def process_input(self, input_buffer):
        """
        Called from the audio callback thread with each input buffer.

        Args:
            input_buffer: numpy array of shape (frames, 2), float32.

        Returns:
            numpy array for monitoring output, or None if monitoring is off.
        """
        frames = input_buffer.shape[0]

        # --- Level metering (always active when armed or recording) ---
        if self._armed or self._recording:
            self._update_levels(input_buffer)

        # --- Countdown handling ---
        if self._countdown_active and not self._recording:
            if self.countdown_remaining <= 0:
                self._countdown_active = False
                self._write_pos = 0
                self._recording = True

        # --- Threshold-based auto-start ---
        if (
            self._armed
            and not self._recording
            and not self._countdown_active
            and self._threshold > 0.0
        ):
            peak = np.max(np.abs(input_buffer))
            if peak >= self._threshold:
                self._write_pos = 0
                self._recording = True

        # --- Recording ---
        if self._recording:
            max_frames = self._buffer.shape[0]
            space = max_frames - self._write_pos
            if space <= 0:
                # Buffer full, auto-stop (done outside lock to avoid
                # calling callback from audio thread with lock held)
                self._recording = False
                self._armed = False
                self._countdown_active = False
                self._finalize_async()
            else:
                n = min(frames, space)
                self._buffer[self._write_pos : self._write_pos + n] = (
                    input_buffer[:n]
                )
                self._write_pos += n
                # Check if we just filled up
                if self._write_pos >= max_frames:
                    self._recording = False
                    self._armed = False
                    self._finalize_async()

        # --- Monitoring ---
        if self._monitor_enabled:
            return input_buffer.copy()
        return None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_threshold(self, level):
        """
        Set auto-start threshold. When input exceeds this level,
        recording starts automatically (if armed).
        Set to 0 for manual-only start.
        """
        self._threshold = max(0.0, float(level))

    def set_monitor(self, enabled):
        """Enable or disable pass-through monitoring of input audio."""
        self._monitor_enabled = bool(enabled)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_levels(self, buf):
        """Compute peak and RMS levels for metering. Called from audio thread."""
        left = buf[:, 0]
        right = buf[:, 1]

        self._peak_l = float(np.max(np.abs(left)))
        self._peak_r = float(np.max(np.abs(right)))

        n = len(left)
        if n > 0:
            self._rms_l = float(math.sqrt(np.dot(left, left) / n))
            self._rms_r = float(math.sqrt(np.dot(right, right) / n))
        else:
            self._rms_l = 0.0
            self._rms_r = 0.0

    def _generate_waveform_preview(self, audio_data, num_points=128):
        """
        Generate a downsampled waveform preview for UI display.
        Returns a numpy array of shape (num_points,) with peak values
        from a mono mix-down.
        """
        if audio_data is None or len(audio_data) == 0:
            return np.zeros(num_points, dtype=np.float32)

        # Mono mixdown
        mono = (audio_data[:, 0] + audio_data[:, 1]) * 0.5
        total = len(mono)

        if total <= num_points:
            preview = np.zeros(num_points, dtype=np.float32)
            preview[:total] = np.abs(mono)
            return preview

        chunk_size = total // num_points
        # Trim to exact multiple
        trimmed = mono[: chunk_size * num_points]
        reshaped = trimmed.reshape(num_points, chunk_size)
        preview = np.max(np.abs(reshaped), axis=1).astype(np.float32)
        return preview

    def _finalize_async(self):
        """
        Finalize recording in a background thread to avoid blocking
        the audio callback with waveform generation or callbacks.
        """
        if self._write_pos == 0:
            return

        audio_data = self._buffer[: self._write_pos].copy()
        pad_index = self._armed_pad
        self._armed_pad = -1

        if self.on_complete is not None:
            def _run():
                waveform = self._generate_waveform_preview(audio_data)
                try:
                    self.on_complete(pad_index, audio_data, waveform)
                except Exception:
                    pass

            t = threading.Thread(target=_run, daemon=True)
            t.start()
