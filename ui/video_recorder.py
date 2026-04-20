"""Real-time screen video recording via ffmpeg pipe.

Captures every rendered pygame frame, pipes it to an ffmpeg subprocess
that encodes H.264 MP4 on the fly. No disk I/O between frames — the
raw RGB data goes directly into ffmpeg's stdin, so Pi 3B can keep up
at 30fps with the `ultrafast` preset.

Also provides an auto-demo scheduler that drives Compa through a
scripted sequence of screens, focuses, and tabs while recording, so
users can produce a marketing-ready walkthrough video with a single
trigger file.

Trigger files (checked each _update() tick):
  /tmp/compa_record_start  — begin capturing every frame
  /tmp/compa_record_stop   — finalize, encode, write to /tmp/compa_video.mp4
  /tmp/compa_record_demo   — run the full auto-demo (~35s) then stop
"""

import logging
import datetime
import os
import subprocess
import time
from typing import Callable, Optional

import pygame

log = logging.getLogger(__name__)

# Default output directory — persistent, survives reboots, and is a
# Samba share so the user can pull videos directly from their Mac.
DEFAULT_VIDEO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "videos",
)


def default_output_path() -> str:
    """Generate a timestamped filename inside the videos directory.

    Ensures the directory exists and returns a path like
    ~/compa/videos/compa_20260420_143205.mp4 so multiple recordings
    never overwrite each other.
    """
    os.makedirs(DEFAULT_VIDEO_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(DEFAULT_VIDEO_DIR, f"compa_{ts}.mp4")


def latest_video_path() -> Optional[str]:
    """Return the most recent compa_*.mp4 file, or None if none exist."""
    if not os.path.isdir(DEFAULT_VIDEO_DIR):
        return None
    candidates = [f for f in os.listdir(DEFAULT_VIDEO_DIR)
                  if f.startswith("compa_") and f.endswith(".mp4")]
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return os.path.join(DEFAULT_VIDEO_DIR, candidates[0])


class VideoRecorder:
    """Pipes pygame surface frames into a live-encoding ffmpeg process."""

    def __init__(self, screen_size: tuple[int, int], fps: int = 30,
                 output_path: Optional[str] = None):
        """Two-stage pipeline: capture to MJPEG, re-encode to H.264.

        Pi 3B's H.264 encoder tops out around 8-10 fps at 1024x600,
        so encoding in real time produces videos that play back 3x
        too fast. MJPEG is orders of magnitude faster because each
        frame is just a JPEG — no inter-frame prediction — so the
        Pi can write at the full UI frame rate. After recording
        stops, we re-encode the MJPEG intermediate to H.264 at
        whatever pace ffmpeg wants (not real-time), producing a
        final MP4 that matches wall-clock duration exactly.

        The MJPEG intermediate is written to /dev/shm (tmpfs) so no
        SD card wear, then deleted after re-encoding.

        If output_path is None, each call to start() generates a new
        timestamped filename under DEFAULT_VIDEO_DIR so recordings
        never overwrite each other.
        """
        self._size = screen_size
        self._fps = fps
        self._frame_interval = 1.0 / fps
        # None = pick a fresh timestamped path on each start().
        # Caller can also pass a fixed path to override.
        self._fixed_output = output_path
        self._output: Optional[str] = output_path
        self._mjpeg_tmp = "/dev/shm/compa_capture.mkv"
        self._proc: Optional[subprocess.Popen] = None
        self._frames = 0
        self._start_time = 0.0
        self._last_capture = 0.0

    @property
    def recording(self) -> bool:
        return self._proc is not None

    @property
    def frames_written(self) -> int:
        return self._frames

    @property
    def duration_seconds(self) -> float:
        if not self._start_time:
            return 0.0
        return time.monotonic() - self._start_time

    def start(self) -> bool:
        """Spawn the MJPEG-capture ffmpeg and open the stdin pipe."""
        if self._proc is not None:
            return True
        try:
            # Pick a fresh timestamped path each time unless the caller
            # pinned a specific output path.
            if self._fixed_output:
                self._output = self._fixed_output
                if os.path.exists(self._output):
                    os.remove(self._output)
            else:
                self._output = default_output_path()
            # Clean up any leftover MJPEG temp
            if os.path.exists(self._mjpeg_tmp):
                os.remove(self._mjpeg_tmp)

            # Stage 1: raw RGB → MJPEG inside a Matroska container.
            # MJPEG per-frame JPEG encoding is fast enough on Pi 3B
            # to hit full 30 fps without throttling.
            self._proc = subprocess.Popen(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "rawvideo",
                    "-pix_fmt", "rgb24",
                    "-s", f"{self._size[0]}x{self._size[1]}",
                    "-r", str(self._fps),
                    "-i", "-",
                    "-c:v", "mjpeg",
                    "-q:v", "5",  # quality scale (2-31, lower=better; 5 ≈ good)
                    "-pix_fmt", "yuvj420p",
                    self._mjpeg_tmp,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._frames = 0
            self._start_time = time.monotonic()
            self._last_capture = 0.0
            log.info("Video recording started (MJPEG intermediate)")
            print("Video recording: /dev/shm/compa_capture.mkv → re-encode on stop",
                  flush=True)
            return True
        except Exception as e:
            log.error("ffmpeg spawn failed: %s", e)
            print(f"Video record failed to start: {e}", flush=True)
            self._proc = None
            return False

    def capture(self, surface: pygame.Surface):
        """Push one frame. Called from the main draw loop after flip().

        Throttles to self._fps — the UI may draw at 30 fps but we only
        feed every Nth frame into ffmpeg so the encoder doesn't fall
        behind on the Pi 3B.
        """
        if self._proc is None or self._proc.stdin is None:
            return
        now = time.monotonic()
        if now - self._last_capture < self._frame_interval:
            return  # too soon — skip this frame to stay on target fps
        self._last_capture = now
        try:
            # tostring returns RGB bytes in left-to-right, top-to-bottom order
            # pygame 2.x renamed to tobytes but tostring still works
            if hasattr(pygame.image, "tobytes"):
                data = pygame.image.tobytes(surface, "RGB")
            else:
                data = pygame.image.tostring(surface, "RGB")
            self._proc.stdin.write(data)
            self._frames += 1
        except BrokenPipeError:
            # ffmpeg died — stop silently
            log.warning("ffmpeg pipe broke after %d frames", self._frames)
            self._proc = None
        except Exception as e:
            log.debug("capture error: %s", e)

    def stop(self) -> Optional[str]:
        """Close MJPEG capture, re-encode to H.264 MP4, return the path."""
        if self._proc is None:
            return None
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                log.warning("mjpeg ffmpeg did not finalize in time, killing")
                self._proc.kill()
                self._proc.wait(timeout=5)
        except Exception as e:
            log.error("stop error: %s", e)

        if self._proc.stderr:
            try:
                err = self._proc.stderr.read().decode(errors="ignore").strip()
                if err:
                    log.info("mjpeg ffmpeg stderr: %s", err[:500])
            except Exception:
                pass

        duration = self.duration_seconds
        frames = self._frames
        self._proc = None
        self._frames = 0
        self._start_time = 0.0

        # Stage 2: re-encode MJPEG → H.264 MP4 at the actual captured rate.
        if not os.path.exists(self._mjpeg_tmp):
            log.warning("no MJPEG intermediate produced")
            return None

        # Calculate actual captured fps and use it as the nominal rate
        # so playback matches wall-clock time exactly.
        actual_fps = frames / duration if duration > 0 else self._fps
        log.info("Re-encoding %d frames (%.1fs, %.1f fps actual) → H.264",
                 frames, duration, actual_fps)
        print(f"Re-encoding {frames} frames at {actual_fps:.1f} fps → H.264...",
              flush=True)

        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-r", f"{actual_fps:.3f}",
                    "-i", self._mjpeg_tmp,
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-crf", "20",
                    "-pix_fmt", "yuv420p",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-r", f"{actual_fps:.3f}",
                    self._output,
                ],
                capture_output=True, text=True, timeout=180,
            )
            if result.returncode != 0:
                log.error("re-encode failed: %s", result.stderr[:500])
                print(f"Re-encode failed: {result.stderr[:200]}", flush=True)
        except subprocess.TimeoutExpired:
            log.error("re-encode timed out after 3 minutes")
            print("Re-encode timed out", flush=True)
        except Exception as e:
            log.error("re-encode error: %s", e)

        # Clean up the MJPEG intermediate
        try:
            os.remove(self._mjpeg_tmp)
        except Exception:
            pass

        if os.path.exists(self._output):
            size_mb = os.path.getsize(self._output) / (1024 * 1024)
            msg = (f"Video saved: {self._output} "
                   f"({frames} frames, {duration:.1f}s, {size_mb:.1f} MB)")
            log.info(msg)
            print(msg, flush=True)
            return self._output

        log.warning("no final MP4 produced")
        return None


# ── Auto-demo scheduler ───────────────────────────────────────────────


class DemoStep:
    """One step in the auto-demo sequence.

    action(app) is called once when the step begins. duration is how
    long (seconds) the step runs before advancing to the next.
    """

    def __init__(self, duration: float, label: str,
                 action: Callable[["object"], None]):
        self.duration = duration
        self.label = label
        self.action = action


def build_demo_sequence() -> list[DemoStep]:
    """The scripted walkthrough — ~35s covering every major area."""

    def goto(screen_name: str) -> Callable:
        def _fn(app):
            app.switch_screen(screen_name)
        return _fn

    def workspace_for(focus_key: str, tab_key: str = "control") -> Callable:
        def _fn(app):
            # Switch focus if needed
            if app.device_manager.focus_key != focus_key:
                app.switch_focus(focus_key)
            app.switch_screen("device_workspace")
            workspace = app.screens.get("device_workspace")
            if workspace is None:
                return
            if hasattr(workspace, "on_enter"):
                workspace.on_enter()
            # Pick the tab
            for i, (key, _lbl) in enumerate(workspace._tabs):
                if key == tab_key:
                    workspace._current_tab = i
                    if tab_key == "control" and hasattr(workspace, "_build_knobs"):
                        workspace._build_knobs()
                    if tab_key == "keys" and hasattr(app, "chromatic_kb"):
                        app.chromatic_kb.enabled = True
                        workspace._retarget_keys_for_device()
                    break
        return _fn

    return [
        DemoStep(3.5, "Session dashboard", goto("session")),
        DemoStep(3.0, "SP-404 control",    workspace_for("SP-404MKII", "control")),
        DemoStep(3.0, "SP-404 Twister",    workspace_for("SP-404MKII", "twister")),
        DemoStep(3.0, "SP-404 Keys",       workspace_for("SP-404MKII", "keys")),
        DemoStep(2.5, "Back to session",   goto("session")),
        DemoStep(3.0, "P-6 control",       workspace_for("P-6", "control")),
        DemoStep(3.0, "P-6 Keys",          workspace_for("P-6", "keys")),
        DemoStep(3.0, "P-6 Pattern",       workspace_for("P-6", "pattern")),
        DemoStep(2.5, "Back to session",   goto("session")),
        DemoStep(3.0, "Record",            goto("record")),
        DemoStep(3.0, "Radio",             goto("radio")),
        DemoStep(2.5, "Files",             goto("files")),
        DemoStep(2.5, "IO & Connectivity", goto("io")),
        DemoStep(2.0, "Settings",          goto("settings")),
        DemoStep(3.0, "Final dashboard",   goto("session")),
    ]


class DemoScheduler:
    """Drives an auto-demo sequence. Tick() every frame to advance steps."""

    def __init__(self, steps: list[DemoStep]):
        self._steps = steps
        self._index = -1
        self._step_start = 0.0
        self._running = False
        self._finished = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self._steps)

    def start(self, app):
        self._running = True
        self._finished = False
        self._index = -1
        self._step_start = time.monotonic()
        self._advance(app)

    def tick(self, app):
        if not self._running or self._finished:
            return
        now = time.monotonic()
        if self._index < 0:
            self._advance(app)
            return
        current = self._steps[self._index]
        if now - self._step_start >= current.duration:
            self._advance(app)

    def _advance(self, app):
        self._index += 1
        if self._index >= len(self._steps):
            self._running = False
            self._finished = True
            print("Demo sequence complete", flush=True)
            return
        step = self._steps[self._index]
        self._step_start = time.monotonic()
        print(f"Demo [{self._index + 1}/{len(self._steps)}] {step.label} "
              f"({step.duration:.1f}s)", flush=True)
        try:
            step.action(app)
        except Exception as e:
            log.error("Demo step failed: %s", e)
