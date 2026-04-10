"""Internet radio stream player with capture buffer.

Decodes audio streams via ffmpeg subprocess, plays through HDMI output,
and maintains a 60-second rolling buffer for on-demand capture.
Captured audio is saved as WAV for slicing and P-6 transfer.
"""

import json
import logging
import os
import re
import struct
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

try:
    import urllib.request
except ImportError:
    urllib = None

import numpy as np

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    sd = None
    sf = None

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHANNELS = 2
CAPTURE_SECONDS = 60
BLOCK_SIZE = 2048


class RadioStream:
    """Plays internet radio streams and captures audio to a rolling buffer."""

    def __init__(self, recordings_dir: str):
        self._recordings_dir = recordings_dir
        os.makedirs(recordings_dir, exist_ok=True)

        # Stream state
        self._playing = False
        self._process: Optional[subprocess.Popen] = None
        self._decode_thread: Optional[threading.Thread] = None
        self._station_name = ""
        self._url = ""

        # Audio output
        self._output_device: Optional[int] = None
        self._output_stream: Optional[sd.OutputStream] = None
        self._find_output_device()

        # Playback buffer — fed by decode thread, consumed by output callback
        self._play_buf = np.zeros((SAMPLE_RATE * 8, CHANNELS), dtype=np.float32)  # 8s ring
        self._play_write = 0
        self._play_read = 0
        self._play_buf_size = len(self._play_buf)
        self._prebuffer_frames = SAMPLE_RATE * 3  # buffer 3s before starting playback
        self._prebuffered = False

        # Capture buffer — 60s rolling
        self._capture_buf = np.zeros((CAPTURE_SECONDS * SAMPLE_RATE, CHANNELS), dtype=np.float32)
        self._capture_pos = 0
        self._capture_total = 0

        # Level metering
        self._peak_l = 0.0
        self._peak_r = 0.0

        # Volume
        self.volume = 0.8

        # Track metadata
        self._track_title = ""
        self._metadata_thread: Optional[threading.Thread] = None

        # Threshold recording
        self._recording = False
        self._rec_writer = None
        self._rec_filepath: Optional[str] = None
        self._rec_frames = 0
        self._threshold = 0.02      # Level threshold to start recording
        self._silence_timeout = 3.0  # Seconds of silence before auto-stop
        self._silence_start = 0.0
        self._threshold_mode = False  # True = auto threshold recording

    def _find_output_device(self):
        """Find a working output device. Uses ALSA dmix/front plugins first
        (they allow sharing with the recorder's input stream), then falls back."""
        if sd is None:
            return

        devices = sd.query_devices()

        # Priority: ALSA plugins that support mixing (dmix/front/spdif),
        # then P-6 direct (only works when recorder isn't monitoring)
        for hint in ["dmix", "front", "spdif", "sysdefault", "P-6", "USB Audio"]:
            for i, dev in enumerate(devices):
                name = dev.get("name", "")
                if hint.lower() in name.lower() and dev.get("max_output_channels", 0) >= 2:
                    try:
                        s = sd.OutputStream(device=i, samplerate=SAMPLE_RATE,
                                           channels=CHANNELS, dtype="float32",
                                           blocksize=BLOCK_SIZE)
                        s.start(); s.stop(); s.close()
                        self._output_device = i
                        log.info("Radio output: device %d '%s'", i, name)
                        return
                    except Exception:
                        continue

        log.warning("No working audio output found for radio")

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def station_name(self) -> str:
        return self._station_name

    @property
    def url(self) -> str:
        return self._url

    @property
    def peak_levels(self) -> tuple[float, float]:
        return (self._peak_l, self._peak_r)

    @property
    def track_title(self) -> str:
        return self._track_title

    @property
    def capture_seconds(self) -> float:
        filled = min(self._capture_total, CAPTURE_SECONDS * SAMPLE_RATE)
        return filled / SAMPLE_RATE

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def threshold_mode(self) -> bool:
        return self._threshold_mode

    @property
    def rec_duration(self) -> float:
        return self._rec_frames / SAMPLE_RATE if self._recording else 0.0

    def start_recording(self) -> Optional[str]:
        """Start recording the radio stream to a WAV file."""
        if sf is None or self._recording:
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_station = "".join(c if c.isalnum() or c in "-_ " else "" for c in self._station_name)
        safe_station = safe_station.strip()[:20] or "radio"
        safe_track = ""
        if self._track_title:
            safe_track = "".join(c if c.isalnum() or c in "-_ " else "" for c in self._track_title)
            safe_track = "_" + safe_track.strip()[:30]
        name = f"radio_{safe_station}{safe_track}_{ts}.wav"
        filepath = os.path.join(self._recordings_dir, name)
        try:
            self._rec_writer = sf.SoundFile(filepath, mode="w", samplerate=SAMPLE_RATE,
                                            channels=CHANNELS, subtype="PCM_24", format="WAV")
            self._rec_filepath = filepath
            self._rec_frames = 0
            self._recording = True
            log.info("Radio recording started: %s", name)
            return filepath
        except Exception as e:
            log.error("Failed to start recording: %s", e)
            return None

    def stop_recording(self) -> Optional[str]:
        """Stop recording and close the WAV file."""
        if not self._recording:
            return None
        filepath = self._rec_filepath
        try:
            if self._rec_writer:
                self._rec_writer.close()
        except Exception:
            pass
        self._rec_writer = None
        self._recording = False
        dur = self._rec_frames / SAMPLE_RATE
        log.info("Radio recording stopped: %.1fs", dur)
        self._rec_frames = 0
        return filepath

    def toggle_threshold_mode(self):
        """Toggle threshold-based auto recording."""
        self._threshold_mode = not self._threshold_mode
        if not self._threshold_mode and self._recording:
            self.stop_recording()
        log.info("Threshold recording: %s", "ON" if self._threshold_mode else "OFF")

    def set_threshold(self, level: float):
        """Set the audio level threshold (0.0–1.0)."""
        self._threshold = max(0.005, min(0.5, level))

    # ── Playback ─────────────────────────────────────────────────────

    def play(self, url: str, station_name: str = "") -> bool:
        """Start playing a radio stream."""
        if sd is None:
            return False
        if self._output_device is None:
            self._find_output_device()
            if self._output_device is None:
                log.warning("No audio output available")
                return False
        self.stop()
        time.sleep(0.2)  # Let ALSA release the device cleanly

        self._url = url
        self._station_name = station_name or url
        self._play_write = 0
        self._play_read = 0
        self._prebuffered = False
        self._capture_pos = 0
        self._capture_total = 0

        # Start ffmpeg to decode stream to raw PCM
        try:
            self._process = subprocess.Popen(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-reconnect", "1",
                    "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5",
                    "-probesize", "64000",
                    "-analyzeduration", "500000",
                    "-i", url,
                    "-af", "aresample=async=1",
                    "-f", "s16le",
                    "-acodec", "pcm_s16le",
                    "-ar", str(SAMPLE_RATE),
                    "-ac", str(CHANNELS),
                    "-bufsize", "512k",
                    "pipe:1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=SAMPLE_RATE * CHANNELS * 4,  # large pipe buffer
            )
        except FileNotFoundError:
            log.error("ffmpeg not found — install with: sudo apt install ffmpeg")
            return False
        except Exception as e:
            log.error("Failed to start stream: %s", e)
            return False

        # Output stream created but NOT started yet — starts after pre-buffer fills
        self._output_stream = None

        # Start decode thread (will start output after pre-buffering)
        self._playing = True
        self._track_title = ""
        self._decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._decode_thread.start()

        # Start metadata reader
        self._metadata_thread = threading.Thread(target=self._metadata_loop,
                                                  args=(url,), daemon=True)
        self._metadata_thread.start()

        log.info("Radio playing: %s", self._station_name)
        return True

    def stop(self):
        """Stop the current stream. Order matters to prevent double-free."""
        self._playing = False

        # Stop decode thread FIRST (it writes to buffers)
        if self._decode_thread:
            self._decode_thread.join(timeout=3)
            self._decode_thread = None

        # Stop metadata thread
        if self._metadata_thread:
            self._metadata_thread.join(timeout=2)
            self._metadata_thread = None

        # Kill ffmpeg process
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                pass
            self._process = None

        # Stop output stream LAST (callback reads from buffers)
        stream = self._output_stream
        self._output_stream = None
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        # Stop any active recording
        if self._recording:
            try:
                if self._rec_writer:
                    self._rec_writer.close()
            except Exception:
                pass
            self._rec_writer = None
            self._recording = False

        self._peak_l = 0.0
        self._peak_r = 0.0
        self._station_name = ""
        self._prebuffered = False

    def _decode_loop(self):
        """Read decoded PCM from ffmpeg and feed to playback + capture buffers."""
        bytes_per_frame = CHANNELS * 2  # int16 = 2 bytes
        chunk_frames = BLOCK_SIZE
        chunk_bytes = chunk_frames * bytes_per_frame

        while self._playing and self._process:
            try:
                raw = self._process.stdout.read(chunk_bytes)
                if not raw:
                    break

                # Convert bytes to numpy (int16 from ffmpeg, convert to float32)
                frames = len(raw) // bytes_per_frame
                if frames == 0:
                    continue
                raw_audio = np.frombuffer(raw, dtype=np.int16).reshape(frames, CHANNELS)
                audio = raw_audio.astype(np.float32) / 32768.0

                # Apply volume
                audio = audio * self.volume

                # Level metering
                abs_audio = np.abs(audio)
                self._peak_l = float(abs_audio[:, 0].max())
                self._peak_r = float(abs_audio[:, 1].max())
                peak = max(self._peak_l, self._peak_r)

                # Threshold recording logic
                if self._threshold_mode:
                    if not self._recording and peak > self._threshold:
                        self.start_recording()
                        self._silence_start = 0
                    elif self._recording:
                        if peak > self._threshold:
                            self._silence_start = 0
                        elif self._silence_start == 0:
                            self._silence_start = time.time()
                        elif time.time() - self._silence_start > self._silence_timeout:
                            self.stop_recording()

                # Write to recording file if active
                if self._recording and self._rec_writer:
                    try:
                        self._rec_writer.write(audio)
                        self._rec_frames += frames
                    except Exception as e:
                        log.error("Rec write error: %s", e)

                # Write to playback ring buffer
                pos = self._play_write % self._play_buf_size
                if pos + frames <= self._play_buf_size:
                    self._play_buf[pos:pos + frames] = audio
                else:
                    first = self._play_buf_size - pos
                    self._play_buf[pos:] = audio[:first]
                    self._play_buf[:frames - first] = audio[first:]
                self._play_write += frames

                # Start output stream after pre-buffer fills
                if not self._prebuffered and self._play_write >= self._prebuffer_frames:
                    self._prebuffered = True
                    try:
                        self._output_stream = sd.OutputStream(
                            device=self._output_device,
                            samplerate=SAMPLE_RATE,
                            channels=CHANNELS,
                            dtype="float32",
                            blocksize=BLOCK_SIZE,
                            callback=self._output_callback,
                        )
                        self._output_stream.start()
                        log.info("Playback started (buffered %.1fs)",
                                 self._play_write / SAMPLE_RATE)
                    except Exception as e:
                        log.error("Playback error: %s", e)
                        # Don't crash — just continue buffering without output
                        self._prebuffered = False
                        self._play_write = 0
                        self._play_read = 0
                        time.sleep(1)  # Wait before retry

                # Write to capture buffer
                cap_pos = self._capture_pos
                cap_len = len(self._capture_buf)
                if cap_pos + frames <= cap_len:
                    self._capture_buf[cap_pos:cap_pos + frames] = audio
                else:
                    first = cap_len - cap_pos
                    self._capture_buf[cap_pos:] = audio[:first]
                    self._capture_buf[:frames - first] = audio[first:]
                self._capture_pos = (cap_pos + frames) % cap_len
                self._capture_total += frames

            except Exception as e:
                if self._playing:
                    log.error("Decode error: %s", e)
                break

        self._playing = False
        log.info("Radio stream ended")

    def _metadata_loop(self, url: str):
        """Read ICY metadata from the stream in a separate connection."""
        while self._playing:
            try:
                req = urllib.request.Request(url, headers={"Icy-MetaData": "1",
                                                            "User-Agent": "Compa/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    metaint = int(resp.headers.get("icy-metaint", 0))
                    if metaint == 0:
                        # No ICY metadata support — try parsing ffmpeg stderr instead
                        log.debug("No ICY metadata for %s", url)
                        break

                    while self._playing:
                        # Skip audio data
                        audio_chunk = resp.read(metaint)
                        if not audio_chunk:
                            break

                        # Read metadata length byte
                        meta_len_byte = resp.read(1)
                        if not meta_len_byte:
                            break
                        meta_len = meta_len_byte[0] * 16
                        if meta_len == 0:
                            continue

                        # Read metadata
                        meta_data = resp.read(meta_len).decode("utf-8", errors="ignore").strip("\x00")
                        # Parse StreamTitle='Artist - Title';
                        match = re.search(r"StreamTitle='(.*?)'", meta_data)
                        if match:
                            title = match.group(1).strip()
                            if title and title != self._track_title:
                                self._track_title = title
                                log.info("Now playing: %s", title)

            except Exception as e:
                if self._playing:
                    log.debug("Metadata read error: %s", e)
                time.sleep(5)  # Retry after delay

    def _output_callback(self, outdata: np.ndarray, frames: int,
                         time_info, status) -> None:
        """sounddevice output callback — reads from playback ring buffer."""
        available = self._play_write - self._play_read
        if available >= frames:
            pos = self._play_read % self._play_buf_size
            if pos + frames <= self._play_buf_size:
                outdata[:] = self._play_buf[pos:pos + frames]
            else:
                first = self._play_buf_size - pos
                outdata[:first] = self._play_buf[pos:]
                outdata[first:] = self._play_buf[:frames - first]
            self._play_read += frames
        elif available > 0:
            # Partial data — play what we have, fade to silence
            pos = self._play_read % self._play_buf_size
            avail = int(available)
            if pos + avail <= self._play_buf_size:
                outdata[:avail] = self._play_buf[pos:pos + avail]
            else:
                first = self._play_buf_size - pos
                outdata[:first] = self._play_buf[pos:]
                outdata[first:avail] = self._play_buf[:avail - first]
            # Fade out the end
            fade_len = min(256, avail)
            fade = np.linspace(1.0, 0.0, fade_len).reshape(-1, 1)
            outdata[avail - fade_len:avail] *= fade
            outdata[avail:] = 0
            self._play_read += avail
        else:
            outdata.fill(0)

    # ── Capture ──────────────────────────────────────────────────────

    def capture(self, session_name: str = "") -> Optional[str]:
        """Save the capture buffer to a WAV file. Returns filepath."""
        if sf is None:
            return None

        filled = min(self._capture_total, CAPTURE_SECONDS * SAMPLE_RATE)
        if filled < SAMPLE_RATE:
            log.warning("Capture buffer too short")
            return None

        # Read buffer in order
        pos = self._capture_pos
        cap_len = len(self._capture_buf)
        if self._capture_total >= cap_len:
            ordered = np.concatenate([
                self._capture_buf[pos:],
                self._capture_buf[:pos],
            ])
        else:
            ordered = self._capture_buf[:pos].copy()

        # Normalize
        peak = np.max(np.abs(ordered))
        if peak > 0:
            ordered *= 0.9 / peak

        # Save
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in self._station_name)
        safe_name = safe_name.strip()[:30] or "radio"
        name = f"radio_{safe_name}_{ts}.wav"
        filepath = os.path.join(self._recordings_dir, name)

        try:
            sf.write(filepath, ordered, SAMPLE_RATE, subtype="PCM_24")
            duration = len(ordered) / SAMPLE_RATE
            log.info("Captured: %s (%.1fs)", name, duration)
            return filepath
        except Exception as e:
            log.error("Capture failed: %s", e)
            return None

    # ── Lifecycle ────────────────────────────────────────────────────

    def shutdown(self):
        self.stop()


# ── Station list I/O ─────────────────────────────────────────────────

def load_stations(path: str) -> list[dict]:
    """Load station list from JSON."""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []
