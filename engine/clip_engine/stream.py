"""ClipEngine audio stream wrapper.

Owns an sd.OutputStream and pumps the engine's render() into the
callback. Pi 5 has enough audio bandwidth to have this stream coexist
with Compa's recorder input + audio_player output.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
    _HAVE_SD = True
except Exception:
    _HAVE_SD = False

from .engine import ClipEngine


class ClipStream:
    def __init__(self, engine: ClipEngine, link,
                 sample_rate: int = 44100, block_size: int = 256) -> None:
        self.engine = engine
        self.link = link
        self.sr = sample_rate
        self.block_size = block_size
        self._stream: Optional[object] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _find_output_device(self) -> tuple[Optional[int], int]:
        """Walk a preference list of audio outputs and return (idx, rate)
        for the first one that opens. Mirrors radio_stream's logic so
        ClipStream lands on the same working device the user is hearing.
        """
        if not _HAVE_SD:
            return None, self.sr
        devices = sd.query_devices()
        hints = ["SP-404", "P-6", "USB Audio", "dmix", "front",
                 "sysdefault", "default", "Headphones", "hdmi", "vc4"]
        for hint in hints:
            for i, dev in enumerate(devices):
                name = dev.get("name", "")
                if (hint.lower() in name.lower()
                        and dev.get("max_output_channels", 0) >= 2):
                    native_rate = int(dev.get("default_samplerate", 44100))
                    for rate in (self.sr, native_rate, 48000):
                        try:
                            s = sd.OutputStream(device=i, samplerate=rate,
                                               channels=2, dtype="float32",
                                               blocksize=self.block_size)
                            s.start(); s.stop(); s.close()
                            return i, rate
                        except Exception:
                            continue
        return None, self.sr

    def start(self, device: Optional[int] = None) -> bool:
        if self._running or not _HAVE_SD:
            return self._running
        if device is None:
            device, picked_rate = self._find_output_device()
            self.sr = picked_rate
        try:
            self._stream = sd.OutputStream(
                samplerate=self.sr,
                blocksize=self.block_size,
                channels=2,
                dtype="float32",
                callback=self._callback,
                device=device,
                latency="low",
            )
            self._stream.start()
            self._running = True
            self.engine.active = True
            try:
                dev_name = sd.query_devices(device).get("name", "?") if device is not None else "default"
            except Exception:
                dev_name = str(device)
            print(f"ClipStream: started on '{dev_name}' "
                  f"(sr={self.sr} block={self.block_size})",
                  flush=True)
            return True
        except Exception as e:
            print(f"ClipStream: start failed: {e}", flush=True)
            self._stream = None
            self._running = False
            return False

    def stop(self) -> None:
        self.engine.active = False
        self.engine.all_notes_off()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self._running = False

    def _callback(self, outdata, frames, time_info, status) -> None:
        outdata[:] = 0.0
        try:
            beat = float(self.link.beat) if self.link else 0.0
            tempo = float(self.link.tempo) if self.link else self.engine.session.bpm
        except Exception:
            beat = 0.0
            tempo = self.engine.session.bpm
        # beats per sample = bpm / 60 / sr
        bps = tempo / 60.0 / self.sr
        try:
            self.engine.render(outdata, frames, beat, bps)
        except Exception as e:
            # Never crash the audio thread
            print(f"ClipStream callback error: {e}", flush=True)
        # Apply session master volume + soft-clip
        try:
            mv = float(self.engine.session.master_volume)
        except Exception:
            mv = 0.85
        outdata *= mv
        np.clip(outdata, -1.0, 1.0, out=outdata)
