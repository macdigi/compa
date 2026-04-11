"""Visual sample slicer + editor engine.

Loads WAV files, computes waveform previews, manages slice markers,
and provides SampleTool-style editing: start/end trim, snap-to-zero,
truncate, normalize, downsample, stereo-to-mono, and undo.
Exports individual slices for P-6 transfer.
"""

import logging
import os
import shutil
import threading
from typing import Callable, Optional

import numpy as np

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    sd = None
    sf = None

log = logging.getLogger(__name__)

MAX_DURATION_SECS = 120
P6_MOUNT_PATH = "/media/pi/P-6"

# P-6 sample rate options and max durations
P6_SAMPLE_RATES = {
    44100: 5.9,
    22050: 11.8,
    14700: 17.8,
    11025: 23.7,
}


def _find_zero_crossing(audio: np.ndarray, frame: int, search_range: int = 200) -> int:
    """Find the nearest zero crossing to the given frame position."""
    if audio is None or len(audio) == 0:
        return frame
    mono = audio.mean(axis=1) if audio.ndim == 2 and audio.shape[1] > 1 else audio.ravel()
    start = max(0, frame - search_range)
    end = min(len(mono) - 1, frame + search_range)
    if start >= end:
        return frame

    segment = mono[start:end]
    # Find sign changes
    signs = np.sign(segment)
    crossings = np.where(np.diff(signs) != 0)[0] + start
    if len(crossings) == 0:
        return frame
    # Return the crossing nearest to the target frame
    nearest_idx = np.argmin(np.abs(crossings - frame))
    return int(crossings[nearest_idx])


class SampleSlicer:
    """Loads audio, manages slice markers, provides editing + export."""

    def __init__(self, staging_dir: str):
        self._staging_dir = staging_dir
        os.makedirs(staging_dir, exist_ok=True)

        # Audio data
        self._audio: Optional[np.ndarray] = None  # (frames, channels) float32
        self._sample_rate = 44100
        self._filepath = ""
        self._filename = ""

        # Waveform preview
        self._waveform: Optional[np.ndarray] = None
        self._waveform_width = 768

        # Slice markers
        self._markers: list[int] = []

        # Start/End trim points
        self._start_frame = 0
        self._end_frame = 0

        # Undo stack
        self._undo_stack: list[tuple[np.ndarray, int]] = []  # (audio, sample_rate)
        self._max_undo = 5

        # Playback state
        self._previewing = False

        # Export settings
        self.export_normalize = True
        self.export_sample_rate = 44100  # target rate for export
        self.export_mono = False

    # ── Loading ──────────────────────────────────────────────────────

    @property
    def loaded(self) -> bool:
        return self._audio is not None

    @property
    def filename(self) -> str:
        return self._filename

    @property
    def duration_secs(self) -> float:
        if self._audio is None:
            return 0.0
        return len(self._audio) / self._sample_rate

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def total_frames(self) -> int:
        return len(self._audio) if self._audio is not None else 0

    @property
    def channels(self) -> int:
        if self._audio is None:
            return 0
        return self._audio.shape[1] if self._audio.ndim == 2 else 1

    @property
    def waveform(self) -> Optional[np.ndarray]:
        return self._waveform

    @property
    def markers(self) -> list[int]:
        return list(self._markers)

    @property
    def start_frame(self) -> int:
        return self._start_frame

    @property
    def end_frame(self) -> int:
        return self._end_frame

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    def load(self, path: str, waveform_width: int = 768) -> bool:
        if sf is None:
            return False
        try:
            info = sf.info(path)
            if info.duration > MAX_DURATION_SECS:
                log.warning("File too long: %.1fs (max %ds)", info.duration, MAX_DURATION_SECS)
                return False
        except Exception as e:
            log.error("Cannot read file info: %s", e)
            return False

        try:
            audio, rate = sf.read(path, dtype="float32")
            if audio.ndim == 1:
                audio = audio.reshape(-1, 1)
        except Exception as e:
            log.error("Failed to load audio: %s", e)
            return False

        self.unload()
        self._audio = audio
        self._sample_rate = rate
        self._filepath = path
        self._filename = os.path.basename(path)
        self._waveform_width = waveform_width
        self._start_frame = 0
        self._end_frame = len(audio)
        self._undo_stack = []
        self._compute_waveform()
        self._markers = []

        log.info("Loaded: %s (%.1fs, %dHz, %dch)",
                 self._filename, info.duration, rate, audio.shape[1])
        return True

    def unload(self):
        self.stop_preview()
        self._audio = None
        self._waveform = None
        self._markers = []
        self._undo_stack = []
        self._filepath = ""
        self._filename = ""

    def _compute_waveform(self):
        if self._audio is None:
            return
        if self._audio.shape[1] > 1:
            mono = self._audio.mean(axis=1)
        else:
            mono = self._audio[:, 0]

        mono = np.abs(mono)
        width = self._waveform_width
        frames = len(mono)
        chunk_size = max(1, frames // width)
        n_chunks = min(width, frames // chunk_size)

        if n_chunks == 0:
            self._waveform = np.zeros(width, dtype=np.float32)
            return

        trimmed = mono[:n_chunks * chunk_size].reshape(n_chunks, chunk_size)
        peaks = np.max(trimmed, axis=1)
        if len(peaks) < width:
            peaks = np.pad(peaks, (0, width - len(peaks)))
        self._waveform = peaks.astype(np.float32)

    def _push_undo(self):
        """Save current state to undo stack."""
        if self._audio is not None:
            if len(self._undo_stack) >= self._max_undo:
                self._undo_stack.pop(0)
            self._undo_stack.append((self._audio.copy(), self._sample_rate))

    # ── Editing operations (SampleTool-style) ────────────────────────

    def set_start(self, frame: int, snap_zero: bool = False):
        """Set the start trim point."""
        if self._audio is None:
            return
        if snap_zero:
            frame = _find_zero_crossing(self._audio, frame)
        self._start_frame = max(0, min(frame, self.total_frames - 1))
        if self._start_frame >= self._end_frame:
            self._start_frame = self._end_frame - 1

    def set_end(self, frame: int, snap_zero: bool = False):
        """Set the end trim point."""
        if self._audio is None:
            return
        if snap_zero:
            frame = _find_zero_crossing(self._audio, frame)
        self._end_frame = max(1, min(frame, self.total_frames))
        if self._end_frame <= self._start_frame:
            self._end_frame = self._start_frame + 1

    def truncate(self):
        """Remove audio before start and after end points. Destructive."""
        if self._audio is None:
            return
        self._push_undo()
        self._audio = self._audio[self._start_frame:self._end_frame].copy()
        self._start_frame = 0
        self._end_frame = len(self._audio)
        self._markers = [m - self._start_frame for m in self._markers
                         if self._start_frame <= m < self._end_frame]
        self._markers = [m for m in self._markers if 0 < m < len(self._audio)]
        self._compute_waveform()
        log.info("Truncated to %d frames (%.1fs)", len(self._audio), self.duration_secs)

    def normalize(self):
        """Normalize audio so peak reaches 0.95 (-0.4dB). Destructive."""
        if self._audio is None:
            return
        self._push_undo()
        peak = np.max(np.abs(self._audio))
        if peak > 0:
            self._audio *= 0.95 / peak
            self._compute_waveform()
            log.info("Normalized: peak %.4f -> 0.95", peak)

    def stereo_to_mono(self, mode: str = "mix"):
        """Convert stereo to mono. Modes: 'mix', 'left', 'right'. Destructive."""
        if self._audio is None or self._audio.shape[1] < 2:
            return
        self._push_undo()
        if mode == "left":
            mono = self._audio[:, 0:1]
        elif mode == "right":
            mono = self._audio[:, 1:2]
        else:  # mix
            mono = self._audio.mean(axis=1, keepdims=True)
        self._audio = mono.astype(np.float32)
        self._compute_waveform()
        log.info("Converted to mono (mode=%s)", mode)

    def downsample(self, target_rate: int):
        """Downsample audio to a lower sample rate. Destructive.

        Valid targets: 22050, 14700, 11025
        """
        if self._audio is None:
            return
        if target_rate >= self._sample_rate:
            return

        self._push_undo()
        ratio = self._sample_rate / target_rate
        # Simple decimation with anti-alias averaging
        new_len = int(len(self._audio) / ratio)
        if new_len == 0:
            return

        indices = np.linspace(0, len(self._audio) - 1, new_len).astype(int)
        self._audio = self._audio[indices]
        self._sample_rate = target_rate

        # Adjust markers
        self._markers = [int(m / ratio) for m in self._markers]
        self._start_frame = int(self._start_frame / ratio)
        self._end_frame = min(len(self._audio), int(self._end_frame / ratio))

        self._compute_waveform()
        log.info("Downsampled to %d Hz (%d frames, %.1fs)",
                 target_rate, len(self._audio), self.duration_secs)

    def undo(self):
        """Restore previous state from undo stack."""
        if not self._undo_stack:
            return
        self._audio, self._sample_rate = self._undo_stack.pop()
        self._start_frame = 0
        self._end_frame = len(self._audio)
        self._compute_waveform()
        log.info("Undo: restored to %d frames", len(self._audio))

    # ── Markers ──────────────────────────────────────────────────────

    def add_marker(self, frame: int, snap_zero: bool = False):
        if self._audio is None:
            return
        if snap_zero:
            frame = _find_zero_crossing(self._audio, frame)
        frame = max(0, min(frame, self.total_frames))
        if frame not in self._markers:
            self._markers.append(frame)
            self._markers.sort()

    def remove_marker(self, frame: int):
        if not self._markers:
            return
        nearest = min(self._markers, key=lambda m: abs(m - frame))
        self._markers.remove(nearest)

    def remove_nearest_marker(self, frame: int, tolerance_frames: int = 0) -> bool:
        if not self._markers:
            return False
        nearest = min(self._markers, key=lambda m: abs(m - frame))
        if abs(nearest - frame) <= tolerance_frames:
            self._markers.remove(nearest)
            return True
        return False

    def clear_markers(self):
        self._markers = []

    def auto_slice(self, n: int):
        """Slice into N equal parts."""
        if self._audio is None or n < 2:
            return
        total = self.total_frames
        self._markers = [int(total * i / n) for i in range(1, n)]

    def transient_slice(self, sensitivity: float = 0.3,
                        min_gap_ms: float = 50.0,
                        max_slices: int = 64) -> int:
        """Auto-slice by detecting transients (sharp attacks).

        Analyzes the audio energy envelope to find sudden jumps
        that indicate drum hits, note onsets, etc.

        Args:
            sensitivity: 0.0 (few slices) to 1.0 (many slices).
                        Controls the threshold for what counts as a transient.
            min_gap_ms: Minimum gap between slices in milliseconds.
                        Prevents double-triggers on noisy transients.
            max_slices: Maximum number of slices to create.

        Returns:
            Number of markers placed.
        """
        if self._audio is None:
            return 0

        audio = self._audio
        if audio.ndim > 1:
            mono = audio.mean(axis=1)
        else:
            mono = audio

        # Compute energy envelope using RMS in short windows
        hop = max(1, self._sample_rate // 200)  # ~5ms hops
        window = max(1, self._sample_rate // 100)  # ~10ms window
        n_frames = len(mono)

        # Fast energy computation using reshaping
        n_hops = n_frames // hop
        envelope = np.zeros(n_hops)
        for i in range(n_hops):
            start = i * hop
            end = min(start + window, n_frames)
            chunk = mono[start:end]
            envelope[i] = np.sqrt(np.mean(chunk ** 2))

        if np.max(envelope) < 0.001:
            return 0  # Silence

        # Normalize envelope
        envelope = envelope / np.max(envelope)

        # Compute onset detection function (difference of energy)
        onset_fn = np.zeros(len(envelope))
        onset_fn[1:] = np.maximum(0, envelope[1:] - envelope[:-1])

        # Dynamic threshold based on sensitivity
        # sensitivity 0.0 → threshold = 0.5 (only big transients)
        # sensitivity 1.0 → threshold = 0.02 (catches everything)
        threshold = 0.5 - (sensitivity * 0.48)
        threshold = max(0.02, min(0.5, threshold))

        # Also use a local adaptive threshold (median of recent values)
        adapt_window = max(10, int(0.5 * self._sample_rate / hop))  # ~500ms
        local_median = np.zeros(len(onset_fn))
        for i in range(len(onset_fn)):
            start_idx = max(0, i - adapt_window)
            local_median[i] = np.median(onset_fn[start_idx:i + 1]) if i > 0 else 0

        # Minimum gap in frames
        min_gap_frames = int(min_gap_ms / 1000.0 * self._sample_rate)
        min_gap_hops = max(1, min_gap_frames // hop)

        # Find peaks above threshold
        markers = []
        last_marker_hop = -min_gap_hops * 2

        for i in range(1, len(onset_fn) - 1):
            val = onset_fn[i]
            # Must be above absolute threshold
            if val < threshold:
                continue
            # Must be above adaptive threshold
            if val < local_median[i] * 3.0:
                continue
            # Must be a local peak
            if val < onset_fn[i - 1] or val < onset_fn[i + 1]:
                continue
            # Must respect minimum gap
            if i - last_marker_hop < min_gap_hops:
                continue

            frame = i * hop
            # Snap to zero-crossing near the onset
            frame = self._snap_to_zero(mono, frame, search_range=hop * 2)
            markers.append(frame)
            last_marker_hop = i

            if len(markers) >= max_slices:
                break

        self._markers = markers
        log.info("Transient detection: %d markers (sensitivity=%.2f, threshold=%.3f)",
                 len(markers), sensitivity, threshold)
        return len(markers)

    def _snap_to_zero(self, mono: np.ndarray, frame: int,
                      search_range: int = 100) -> int:
        """Snap a frame position to the nearest zero-crossing."""
        start = max(0, frame - search_range)
        end = min(len(mono) - 1, frame + search_range)
        if start >= end:
            return frame
        segment = mono[start:end]
        # Find zero-crossings
        signs = np.sign(segment)
        crossings = np.where(np.diff(signs) != 0)[0] + start
        if len(crossings) == 0:
            return frame
        # Return the crossing closest to the original frame
        closest = crossings[np.argmin(np.abs(crossings - frame))]
        return int(closest)

    # ── Slices ───────────────────────────────────────────────────────

    def get_slices(self) -> list[tuple[int, int, float, float]]:
        if self._audio is None:
            return []
        boundaries = [0] + sorted(self._markers) + [self.total_frames]
        slices = []
        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            if end > start:
                slices.append((start, end,
                               start / self._sample_rate,
                               end / self._sample_rate))
        return slices

    # ── Preview ──────────────────────────────────────────────────────

    def preview_slice(self, index: int):
        if sd is None or self._audio is None:
            return
        slices = self.get_slices()
        if index < 0 or index >= len(slices):
            return

        self.stop_preview()
        start, end, _, _ = slices[index]
        chunk = self._audio[start:end].copy()
        peak = np.max(np.abs(chunk))
        if peak > 0:
            chunk *= 0.9 / peak

        self._previewing = True
        def _play():
            try:
                sd.play(chunk, samplerate=self._sample_rate)
                sd.wait()
            except Exception as e:
                log.error("Preview error: %s", e)
            finally:
                self._previewing = False
        threading.Thread(target=_play, daemon=True).start()

    def preview_range(self, start_frame: int, end_frame: int):
        """Preview a specific frame range (for start/end audition)."""
        if sd is None or self._audio is None:
            return
        self.stop_preview()
        start = max(0, min(start_frame, self.total_frames))
        end = max(start + 1, min(end_frame, self.total_frames))
        chunk = self._audio[start:end].copy()
        peak = np.max(np.abs(chunk))
        if peak > 0:
            chunk *= 0.9 / peak

        self._previewing = True
        def _play():
            try:
                sd.play(chunk, samplerate=self._sample_rate)
                sd.wait()
            except Exception:
                pass
            finally:
                self._previewing = False
        threading.Thread(target=_play, daemon=True).start()

    def stop_preview(self):
        if sd is not None:
            try:
                sd.stop()
            except Exception:
                pass
        self._previewing = False

    @property
    def is_previewing(self) -> bool:
        return self._previewing

    # ── Export ────────────────────────────────────────────────────────

    def export_slices(self, normalize: bool = True, target_rate: int = 0,
                      mono: bool = False) -> list[str]:
        """Export all slices as individual WAV files to staging dir.

        Args:
            normalize: Normalize each slice to 0.95 peak
            target_rate: Downsample to this rate (0 = keep original)
            mono: Convert to mono before export
        """
        if sf is None or self._audio is None:
            return []

        # Clear staging dir
        if os.path.isdir(self._staging_dir):
            for f in os.listdir(self._staging_dir):
                if f.endswith(".wav"):
                    os.remove(os.path.join(self._staging_dir, f))

        slices = self.get_slices()
        stem = os.path.splitext(self._filename)[0]
        exported = []
        rate = target_rate if target_rate > 0 else self._sample_rate

        for i, (start, end, _, _) in enumerate(slices):
            chunk = self._audio[start:end].copy()

            # Mono conversion
            if mono and chunk.shape[1] > 1:
                chunk = chunk.mean(axis=1, keepdims=True)

            # Normalize
            if normalize:
                peak = np.max(np.abs(chunk))
                if peak > 0:
                    chunk *= 0.95 / peak

            # Downsample if needed
            if target_rate > 0 and target_rate < self._sample_rate:
                ratio = self._sample_rate / target_rate
                new_len = int(len(chunk) / ratio)
                if new_len > 0:
                    indices = np.linspace(0, len(chunk) - 1, new_len).astype(int)
                    chunk = chunk[indices]

            # Check P-6 time limit
            duration = len(chunk) / rate
            max_time = P6_SAMPLE_RATES.get(rate, 5.9)
            if duration > max_time:
                log.warning("Slice %d exceeds P-6 limit: %.1fs > %.1fs at %dHz",
                           i + 1, duration, max_time, rate)

            name = f"{stem}_slice_{i + 1:02d}.wav"
            path = os.path.join(self._staging_dir, name)
            try:
                sf.write(path, chunk, rate, subtype="PCM_16")
                exported.append(path)
                log.info("Exported: %s (%.1fs @ %dHz%s%s)", name,
                         len(chunk) / rate, rate,
                         " mono" if mono else "",
                         " normalized" if normalize else "")
            except Exception as e:
                log.error("Export failed for slice %d: %s", i + 1, e)

        return exported

    def transfer_to_p6(self, mount_path: str = P6_MOUNT_PATH) -> int:
        if not os.path.ismount(mount_path) and not os.path.isdir(mount_path):
            log.warning("P-6 not mounted at %s", mount_path)
            return -1

        try:
            sample_dir = os.path.join(mount_path, "SAMPLE")
            os.makedirs(sample_dir, exist_ok=True)
        except PermissionError:
            log.warning("P-6 mount point exists but not writable (not actually mounted)")
            return -1
        except Exception as e:
            log.error("Cannot create SAMPLE dir: %s", e)
            return -1

        count = 0
        for fname in os.listdir(self._staging_dir):
            if not fname.endswith(".wav"):
                continue
            src = os.path.join(self._staging_dir, fname)
            dst = os.path.join(sample_dir, fname)
            try:
                shutil.copy2(src, dst)
                count += 1
            except Exception as e:
                log.error("Transfer failed for %s: %s", fname, e)

        log.info("Transferred %d slices to P-6", count)
        return count

    # ── Info ──────────────────────────────────────────────────────────

    def get_info(self) -> dict:
        """Get current sample info for display."""
        if self._audio is None:
            return {}
        peak = float(np.max(np.abs(self._audio)))
        return {
            "filename": self._filename,
            "duration": self.duration_secs,
            "sample_rate": self._sample_rate,
            "channels": self.channels,
            "frames": self.total_frames,
            "peak_db": round(20 * np.log10(peak + 1e-10), 1),
            "peak_linear": round(peak, 4),
            "memory_estimate_secs": {
                r: round(self.duration_secs * (self._sample_rate / r) if r <= self._sample_rate else self.duration_secs, 1)
                for r in P6_SAMPLE_RATES
            },
        }
