"""Async sample loading, format conversion, caching."""

import os
import shutil
import threading
import numpy as np
from typing import Optional, Callable

try:
    import soundfile as sf
except ImportError:
    sf = None

from .pad_bank import Pad

SUPPORTED_EXTENSIONS = {".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg"}
SAMPLE_RATE = 44100
MAX_WAVEFORM_POINTS = 800  # One point per pixel width for 800px display
MAX_MEMORY_MB = 600


class SampleLoader:
    """Handles loading audio files into numpy arrays with caching."""

    def __init__(self, local_cache_dir: str, sample_rate: int = SAMPLE_RATE):
        self.local_cache_dir = local_cache_dir
        self.sample_rate = sample_rate
        os.makedirs(local_cache_dir, exist_ok=True)

    def load_sample(self, file_path: str, pad: Pad,
                    on_complete: Optional[Callable] = None):
        """Load a sample file into a pad asynchronously.
        Copies to local cache if on network mount, then loads into RAM."""
        thread = threading.Thread(
            target=self._load_worker,
            args=(file_path, pad, on_complete),
            daemon=True,
        )
        thread.start()

    def load_sample_sync(self, file_path: str, pad: Pad) -> bool:
        """Load a sample synchronously. Returns True on success."""
        return self._load_worker(file_path, pad, None)

    def load_preview(self, file_path: str) -> Optional[np.ndarray]:
        """Load a sample for preview (returns audio data or None)."""
        try:
            data = self._read_audio_file(file_path)
            return data
        except Exception as e:
            print(f"Preview load error: {e}")
            return None

    def _load_worker(self, file_path: str, pad: Pad,
                     on_complete: Optional[Callable]) -> bool:
        """Worker that loads and processes a sample."""
        try:
            # Cache to local storage if from network mount
            local_path = self._ensure_cached(file_path)

            # Read audio file
            data = self._read_audio_file(local_path)
            if data is None:
                return False

            # Apply pitch shift if tune != 0
            if pad.tune != 0:
                data = self._pitch_shift(data, pad.tune)

            # Store in pad
            pad.audio_data = data
            pad.sample_path = file_path
            if pad.end == 0 or pad.end > len(data):
                pad.end = len(data)
            if pad.start >= pad.end:
                pad.start = 0

            # Pre-compute waveform preview
            pad.waveform_preview = self._compute_waveform(data)

            if on_complete:
                on_complete(pad, True)
            return True

        except Exception as e:
            print(f"Sample load error for {file_path}: {e}")
            if on_complete:
                on_complete(pad, False)
            return False

    def _read_audio_file(self, path: str) -> Optional[np.ndarray]:
        """Read an audio file and return as float32 numpy array at target sample rate."""
        if sf is None:
            print("soundfile not installed")
            return None

        try:
            data, sr = sf.read(path, dtype="float32", always_2d=True)
        except Exception as e:
            print(f"Cannot read {path}: {e}")
            return None

        # Resample if needed (simple linear interpolation for Pi 3B performance)
        if sr != self.sample_rate:
            ratio = self.sample_rate / sr
            new_len = int(len(data) * ratio)
            indices = np.linspace(0, len(data) - 1, new_len).astype(np.float32)
            idx_floor = indices.astype(np.int64)
            idx_ceil = np.minimum(idx_floor + 1, len(data) - 1)
            frac = indices - idx_floor
            data = data[idx_floor] * (1 - frac[:, np.newaxis]) + data[idx_ceil] * frac[:, np.newaxis]

        # Convert mono to stereo
        if data.shape[1] == 1:
            data = np.column_stack((data[:, 0], data[:, 0]))
        elif data.shape[1] > 2:
            data = data[:, :2]

        return data.astype(np.float32)

    def _pitch_shift(self, data: np.ndarray, semitones: int) -> np.ndarray:
        """Pitch shift by resampling. Positive = higher pitch = shorter."""
        ratio = 2.0 ** (semitones / 12.0)
        new_len = int(len(data) / ratio)
        if new_len < 1:
            return data

        indices = np.linspace(0, len(data) - 1, new_len).astype(np.float32)
        idx_floor = indices.astype(np.int64)
        idx_ceil = np.minimum(idx_floor + 1, len(data) - 1)
        frac = indices - idx_floor

        result = data[idx_floor] * (1 - frac[:, np.newaxis]) + data[idx_ceil] * frac[:, np.newaxis]
        return result.astype(np.float32)

    def _compute_waveform(self, data: np.ndarray) -> np.ndarray:
        """Downsample audio to waveform preview array (peak values)."""
        # Mix to mono for display
        if data.ndim == 2:
            mono = data.mean(axis=1)
        else:
            mono = data

        n_points = min(MAX_WAVEFORM_POINTS, len(mono))
        if n_points < 1:
            return np.zeros(MAX_WAVEFORM_POINTS, dtype=np.float32)

        chunk_size = max(1, len(mono) // n_points)
        trimmed = mono[:chunk_size * n_points]
        chunks = trimmed.reshape(n_points, chunk_size)
        peaks = np.max(np.abs(chunks), axis=1)
        return peaks.astype(np.float32)

    def _ensure_cached(self, file_path: str) -> str:
        """If file is on network mount, copy to local cache. Returns local path."""
        # Check if already local
        if file_path.startswith(self.local_cache_dir):
            return file_path

        # Check if it's on the network mount
        if file_path.startswith("/mnt/samples"):
            # Create subdirectory structure in cache
            rel_path = os.path.relpath(file_path, "/mnt/samples")
            local_path = os.path.join(self.local_cache_dir, rel_path)
            local_dir = os.path.dirname(local_path)
            os.makedirs(local_dir, exist_ok=True)

            # Copy if not already cached
            if not os.path.exists(local_path):
                try:
                    shutil.copy2(file_path, local_path)
                except Exception as e:
                    print(f"Cache copy failed: {e}")
                    return file_path  # Fallback to network path
            return local_path

        return file_path

    def get_cache_size_mb(self) -> float:
        """Get total size of local sample cache in MB."""
        total = 0
        for dirpath, _, filenames in os.walk(self.local_cache_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        return total / (1024 * 1024)

    @staticmethod
    def is_audio_file(filename: str) -> bool:
        """Check if a filename has a supported audio extension."""
        _, ext = os.path.splitext(filename)
        return ext.lower() in SUPPORTED_EXTENSIONS
