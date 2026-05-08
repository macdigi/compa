"""Synthesized drum samples — for the default Drum Rack content.

If no real samples are available we synthesize kick/snare/hat/clap
DSP at startup and feed them into the Drum Rack pads. The samples
are precomputed once into mono float32 arrays.
"""
from __future__ import annotations

import numpy as np


SR = 44100


def _saw(freq: float, dur: float) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    return (2.0 * (t * freq - np.floor(0.5 + t * freq))).astype(np.float32)


def make_kick(dur: float = 0.45) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    # Pitch envelope from 120 Hz down to 50 Hz exponentially over ~120ms
    pitch_env = 50.0 + 70.0 * np.exp(-t / 0.06)
    phase = np.cumsum(2.0 * np.pi * pitch_env / SR)
    sine = np.sin(phase)
    # Body amp envelope
    amp = np.exp(-t / 0.18)
    # Click transient (high-freq noise burst, very short)
    click = np.zeros(n, dtype=np.float32)
    click_n = int(0.005 * SR)
    click[:click_n] = (np.random.randn(click_n) * 0.3
                       * np.linspace(1.0, 0.0, click_n)).astype(np.float32)
    sig = (sine * amp + click).astype(np.float32)
    return sig / max(0.001, np.max(np.abs(sig)))


def make_snare(dur: float = 0.28) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    # Body: short tom-like sine around 200 Hz
    body = np.sin(2.0 * np.pi * 220.0 * t) * np.exp(-t / 0.06)
    # Noise tail
    noise = np.random.randn(n).astype(np.float32) * np.exp(-t / 0.12)
    # 1-pole HPF on noise via numpy diff (rough)
    noise = np.diff(np.concatenate([[0.0], noise]))
    sig = (body * 0.5 + noise * 0.6).astype(np.float32)
    return sig / max(0.001, np.max(np.abs(sig)))


def make_hat(dur: float = 0.06, closed: bool = True) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = np.random.randn(n).astype(np.float32)
    # Strong HPF: cascade of differentiators
    for _ in range(3):
        noise = np.diff(np.concatenate([[0.0], noise]))
    decay = 0.02 if closed else 0.18
    env = np.exp(-t / decay)
    sig = noise * env
    return sig / max(0.001, np.max(np.abs(sig)))


def make_clap(dur: float = 0.18) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    noise = np.random.randn(n).astype(np.float32)
    # Three clap bursts spaced ~10ms apart
    env = np.zeros(n, dtype=np.float32)
    for offset in (0.0, 0.011, 0.022):
        start = int(offset * SR)
        burst_n = min(n - start, int(0.025 * SR))
        if burst_n > 0:
            env[start:start + burst_n] += np.linspace(1.0, 0.2, burst_n)
    # Long tail
    tail = np.exp(-t / 0.08)
    env = (env + tail * 0.3).astype(np.float32)
    # HPF noise
    for _ in range(2):
        noise = np.diff(np.concatenate([[0.0], noise]))
    sig = noise * env
    return sig / max(0.001, np.max(np.abs(sig)))


def make_tom(freq: float = 120.0, dur: float = 0.25) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    pitch_env = freq * (1.0 + 0.3 * np.exp(-t / 0.04))
    phase = np.cumsum(2.0 * np.pi * pitch_env / SR)
    sig = np.sin(phase) * np.exp(-t / 0.12)
    return sig.astype(np.float32) / max(0.001, np.max(np.abs(sig)))


def make_rim(dur: float = 0.05) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    sig = np.sin(2.0 * np.pi * 1800.0 * t) * np.exp(-t / 0.012)
    return sig.astype(np.float32) / max(0.001, np.max(np.abs(sig)))


def default_kit() -> dict[int, tuple[str, np.ndarray]]:
    """Return {pad_idx: (name, sample)} for a default 16-pad rack.

    Layout:
      0 Kick   1 Snare   2 ClsdHat 3 OpnHat
      4 Clap   5 Rim     6 LowTom  7 MidTom
      8 HiTom  9 RvKick  10 SnRim  11 ShakerH
      12-15: copies / variants.
    """
    np.random.seed(42)
    samples = {
        0:  ("Kick",      make_kick()),
        1:  ("Snare",     make_snare()),
        2:  ("ClosedHat", make_hat(closed=True)),
        3:  ("OpenHat",   make_hat(0.18, closed=False)),
        4:  ("Clap",      make_clap()),
        5:  ("Rim",       make_rim()),
        6:  ("LowTom",    make_tom(80.0, 0.30)),
        7:  ("MidTom",    make_tom(140.0, 0.26)),
        8:  ("HiTom",     make_tom(220.0, 0.22)),
        9:  ("Kick2",     make_kick(0.55)),
        10: ("Snare2",    make_snare(0.32)),
        11: ("ShakerH",   make_hat(0.04, closed=True)),
        12: ("OpenHat2",  make_hat(0.22, closed=False)),
        13: ("Clap2",     make_clap(0.22)),
        14: ("RimH",      make_rim(0.04)),
        15: ("KickRoom",  make_kick(0.6)),
    }
    return samples
