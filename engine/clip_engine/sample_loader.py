"""Sample loader — pulls a WAV/AIFF/FLAC into a float32 numpy array.

Used by AudioClip and DrumRack. Prefers soundfile if available, falls
back to scipy.io.wavfile, then to wave + numpy for mono PCM only.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

import numpy as np


def load_sample(path: str) -> Optional[Tuple[np.ndarray, int]]:
    """Load `path` into (audio[N, channels] float32, sample_rate).

    Returns None on failure.
    """
    if not os.path.exists(path):
        return None
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        return data.astype(np.float32), int(sr)
    except Exception:
        pass
    try:
        from scipy.io import wavfile
        sr, data = wavfile.read(path)
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        elif data.dtype != np.float32:
            data = data.astype(np.float32)
        if data.ndim == 1:
            data = data[:, None]
        return data, int(sr)
    except Exception:
        return None


def list_samples(directory: str) -> list[str]:
    """Return absolute paths of all sample files in directory."""
    if not os.path.isdir(directory):
        return []
    out = []
    for f in sorted(os.listdir(directory)):
        if f.lower().endswith((".wav", ".aif", ".aiff", ".flac", ".ogg")):
            out.append(os.path.join(directory, f))
    return out
