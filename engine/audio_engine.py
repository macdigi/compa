"""Core audio engine — real-time sample playback with mixing."""

import threading
import numpy as np
import sounddevice as sd
from dataclasses import dataclass
from typing import Optional

from .pad_bank import Pad, PlayMode


MAX_VOICES = 32
SAMPLE_RATE = 44100
BUFFER_SIZE = 256
CHANNELS = 2


@dataclass
class Voice:
    """A single playing voice instance."""
    pad: Pad
    position: int = 0           # current playback position in frames
    velocity: float = 1.0       # 0.0–1.0 from MIDI velocity
    active: bool = True
    age: int = 0                # increments each callback for voice stealing
    attack_pos: int = 0         # frames into attack phase
    decay_pos: int = 0          # frames into decay phase
    in_decay: bool = False


class AudioEngine:
    """Real-time audio engine with voice management and mixing."""

    def __init__(self, device=None, sample_rate=SAMPLE_RATE, buffer_size=BUFFER_SIZE):
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self.device = device
        self.voices: list[Voice] = []
        self._lock = threading.Lock()
        self.stream: Optional[sd.OutputStream] = None
        self.cpu_load: float = 0.0
        self._running = False
        # Preview voice (for browser preview)
        self._preview_voice: Optional[Voice] = None

    def start(self):
        """Start the audio output stream."""
        if self._running:
            return
        try:
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.buffer_size,
                channels=CHANNELS,
                dtype="float32",
                callback=self._audio_callback,
                device=self.device,
                latency="low",
            )
            self.stream.start()
            self._running = True
        except Exception as e:
            print(f"Audio engine failed to start: {e}")
            self._running = False

    def stop(self):
        """Stop the audio output stream."""
        self._running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        with self._lock:
            self.voices.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    def trigger_pad(self, pad: Pad, velocity: float = 1.0):
        """Trigger a pad — start playing its sample."""
        if not pad.has_sample:
            return

        # Handle choke groups — kill voices in same choke group
        if pad.choke_group > 0:
            with self._lock:
                for v in self.voices:
                    if v.active and v.pad.choke_group == pad.choke_group:
                        v.active = False

        # Handle mute groups
        if pad.mute_group > 0:
            with self._lock:
                for v in self.voices:
                    if v.active and v.pad.mute_group == pad.mute_group:
                        v.active = False

        voice = Voice(
            pad=pad,
            position=pad.start,
            velocity=velocity,
        )

        with self._lock:
            # Voice stealing: remove oldest if at max
            self.voices = [v for v in self.voices if v.active]
            if len(self.voices) >= MAX_VOICES:
                # Kill the oldest voice
                self.voices.sort(key=lambda v: v.age, reverse=True)
                self.voices[-1].active = False
                self.voices = [v for v in self.voices if v.active]
            self.voices.append(voice)

    def stop_pad(self, pad: Pad):
        """Stop all voices playing a given pad (for LOOP mode note-off)."""
        with self._lock:
            for v in self.voices:
                if v.active and v.pad is pad:
                    v.active = False

    def preview_sample(self, audio_data: np.ndarray):
        """Play a sample for preview (browser screen). Stops any current preview."""
        dummy_pad = Pad()
        dummy_pad.audio_data = audio_data
        dummy_pad.end = len(audio_data)
        dummy_pad.volume = 0.8
        preview_voice = Voice(pad=dummy_pad, velocity=1.0)
        with self._lock:
            # Stop existing preview
            if self._preview_voice is not None:
                self._preview_voice.active = False
            self._preview_voice = preview_voice

    def stop_preview(self):
        with self._lock:
            if self._preview_voice is not None:
                self._preview_voice.active = False
                self._preview_voice = None

    def get_active_pads(self) -> set:
        """Return set of (bank, pad_index) tuples for pads with active voices.
        Used by UI for glow feedback. Returns pad object ids for matching."""
        active = set()
        with self._lock:
            for v in self.voices:
                if v.active:
                    active.add(id(v.pad))
            if self._preview_voice and self._preview_voice.active:
                active.add(id(self._preview_voice.pad))
        return active

    def get_voice_count(self) -> int:
        with self._lock:
            return sum(1 for v in self.voices if v.active)

    def _render_voice(self, voice: Voice, frames: int) -> Optional[np.ndarray]:
        """Render a single voice into a stereo buffer. Returns None if voice finished."""
        pad = voice.pad
        data = pad.audio_data
        if data is None:
            voice.active = False
            return None

        end = pad.end if pad.end > 0 else len(data)
        remaining = end - voice.position
        if remaining <= 0:
            if pad.mode == PlayMode.LOOP:
                voice.position = pad.start
                remaining = end - voice.position
            else:
                voice.active = False
                return None

        n = min(frames, remaining)
        chunk = data[voice.position:voice.position + n]
        voice.position += n

        # Ensure stereo
        if chunk.ndim == 1:
            chunk = np.column_stack((chunk, chunk))

        # Apply envelope
        env = np.ones(len(chunk), dtype=np.float32)

        # Attack
        attack_frames = int(pad.attack * self.sample_rate / 1000.0)
        if attack_frames > 0 and voice.attack_pos < attack_frames:
            atk_remaining = attack_frames - voice.attack_pos
            atk_n = min(len(chunk), atk_remaining)
            ramp = np.linspace(
                voice.attack_pos / attack_frames,
                (voice.attack_pos + atk_n) / attack_frames,
                atk_n,
                dtype=np.float32,
            )
            env[:atk_n] *= ramp
            voice.attack_pos += atk_n

        # Decay
        decay_frames = int(pad.decay * self.sample_rate / 1000.0) if pad.decay > 0 else 0
        if decay_frames > 0:
            total_len = end - pad.start
            decay_start = total_len - decay_frames
            voice_pos_in_sample = voice.position - n - pad.start
            if voice_pos_in_sample + len(chunk) > decay_start:
                for i in range(len(chunk)):
                    pos = voice_pos_in_sample + i
                    if pos >= decay_start:
                        decay_progress = (pos - decay_start) / decay_frames
                        env[i] *= max(0.0, 1.0 - decay_progress)

        # Apply envelope, velocity, and volume
        gain = voice.velocity * pad.volume
        env *= gain
        chunk = chunk * env[:, np.newaxis]

        # Apply pan
        if pad.pan != 0.0:
            left_gain = min(1.0, 1.0 - pad.pan)
            right_gain = min(1.0, 1.0 + pad.pan)
            chunk[:, 0] *= left_gain
            chunk[:, 1] *= right_gain

        # Pad to full frame count if short
        if len(chunk) < frames:
            padded = np.zeros((frames, 2), dtype=np.float32)
            padded[:len(chunk)] = chunk
            chunk = padded

        # Check loop wrap
        if voice.position >= end and pad.mode == PlayMode.LOOP:
            voice.position = pad.start

        # Age the voice
        voice.age += 1

        return chunk

    def _audio_callback(self, outdata: np.ndarray, frames: int, time_info, status):
        """Real-time audio callback — mix all active voices."""
        mix = np.zeros((frames, CHANNELS), dtype=np.float32)

        with self._lock:
            all_voices = list(self.voices)
            if self._preview_voice and self._preview_voice.active:
                all_voices.append(self._preview_voice)

        for voice in all_voices:
            if not voice.active:
                continue
            rendered = self._render_voice(voice, frames)
            if rendered is not None:
                mix += rendered

        # Clean up dead voices periodically
        with self._lock:
            self.voices = [v for v in self.voices if v.active]
            if self._preview_voice and not self._preview_voice.active:
                self._preview_voice = None

        # Soft clip
        np.clip(mix, -1.0, 1.0, out=mix)
        outdata[:] = mix

        # Update CPU load estimate
        if self.stream is not None:
            try:
                self.cpu_load = self.stream.cpu_load
            except Exception:
                pass
