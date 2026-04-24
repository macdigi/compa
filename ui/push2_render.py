"""Push 2 display renderer (Phase 2 v1).

Runs a dedicated thread that draws onto a 960x160 pygame Surface,
encodes it, and pushes it to the Push 2 display via
engine.push2_display.Push2Display.

First view — minimal and visible:
  [device name]            BPM 120.0            COMPA
  ─────────────────────────────────────────────────────
  │              (reserved for scope/meters)         │
  ─────────────────────────────────────────────────────
  [ 8 encoder labels in a row across the bottom       ]

Iterates from there. The Push 2 display goes black after 2 seconds
without a frame (per Ableton's spec), so the render thread sends
continuously even when nothing on-screen has changed.
"""

import logging
import threading
import time

import pygame

log = logging.getLogger(__name__)

SURF_W = 960
SURF_H = 160
TARGET_FPS = 20       # Gentle on Pi 3B; Push 2 auto-refreshes held frames
FRAME_INTERVAL = 1.0 / TARGET_FPS

# Muted palette so the Push 2 isn't painfully bright at night
BG = (0, 0, 0)
TEXT = (230, 230, 230)
DIM = (120, 120, 120)
ACCENT = (255, 40, 60)   # Compa neonRed
DEVICE = (60, 180, 255)


class Push2Renderer:
    def __init__(self, app, display) -> None:
        self.app = app
        self.display = display

        # Push 2 display-specific fonts (sized for 960x160, not the Pi's
        # main 1024x600 screen). We don't go through ui.theme.font() since
        # those are auto-scaled for the Compa touchscreen.
        self._font_big = pygame.font.SysFont("dejavusans-bold", 46)
        self._font_med = pygame.font.SysFont("dejavusans-bold", 24)
        self._font_small = pygame.font.SysFont("dejavusans", 16)
        self._font_tiny = pygame.font.SysFont("dejavusans", 12)

        # Don't call .convert() — it requires an active display mode, and
        # we never blit this surface to a display. surfarray.pixels3d
        # reads pixels directly regardless of the surface's pixel format.
        self.surface = pygame.Surface((SURF_W, SURF_H))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                         name="Push2Render")

    # ── Lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        self._thread.start()
        log.info("Push 2 renderer started (%d fps)", TARGET_FPS)

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)
        try:
            self.display.fill_rgb(0, 0, 0)
        except Exception:
            pass
        log.info("Push 2 renderer stopped")

    # ── Render loop ────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self._render_frame(self.surface)
                self.display.send_surface(self.surface)
            except Exception as e:
                log.warning("Push 2 frame failed: %s", e)
                time.sleep(0.5)
            elapsed = time.monotonic() - t0
            sleep_for = FRAME_INTERVAL - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    # ── Scene drawing ──────────────────────────────────────────────

    def _render_frame(self, surf: pygame.Surface) -> None:
        surf.fill(BG)

        # ── Header row: device name + BPM + brand ──────────────────
        dev_name = getattr(self.app, "device_name", None) or "—"
        dev_surf = self._font_med.render(dev_name, True, DEVICE)
        surf.blit(dev_surf, (16, 12))

        bpm = self._safe_bpm()
        bpm_text = f"{bpm:.1f}" if bpm else "— —"
        bpm_surf = self._font_big.render(bpm_text, True, TEXT)
        bpm_label = self._font_tiny.render("BPM", True, DIM)
        bpm_x = SURF_W // 2 - bpm_surf.get_width() // 2
        surf.blit(bpm_surf, (bpm_x, 2))
        surf.blit(bpm_label, (bpm_x + bpm_surf.get_width() + 8, 32))

        brand = self._font_med.render("COMPA", True, ACCENT)
        surf.blit(brand, (SURF_W - brand.get_width() - 16, 12))

        # ── Middle band: placeholder (scope in Phase 2b) ───────────
        band_top = 60
        band_h = 60
        pygame.draw.rect(surf, (12, 12, 16),
                         pygame.Rect(16, band_top, SURF_W - 32, band_h),
                         border_radius=6)
        # A subtle centerline so it's visibly a scope slot
        mid_y = band_top + band_h // 2
        pygame.draw.line(surf, (40, 40, 48), (24, mid_y),
                         (SURF_W - 24, mid_y), 1)
        placeholder = self._font_tiny.render("SCOPE", True, DIM)
        surf.blit(placeholder, (24, band_top + 4))

        # ── Encoder label row ──────────────────────────────────────
        labels = self._encoder_labels()
        row_top = 130
        col_w = SURF_W // 8
        for i, label in enumerate(labels):
            lbl = self._font_small.render(label, True, TEXT)
            cx = i * col_w + col_w // 2
            surf.blit(lbl, (cx - lbl.get_width() // 2, row_top))

    # ── Data accessors (fail-safe) ─────────────────────────────────

    def _safe_bpm(self):
        try:
            return self.app.p6.state.bpm
        except Exception:
            return None

    def _encoder_labels(self) -> list[str]:
        """Return 8 labels for the Push 2's 8 performance encoders.

        Falls back to placeholder names when no mapping is active.
        Once the Twister integration is on, mirror its current page
        here so both surfaces agree on what each encoder does.
        """
        tw = getattr(self.app, "twister", None)
        if tw and getattr(tw, "slots", None):
            # Show the first 8 slot names from the current Twister page.
            return [str(getattr(s, "name", "—"))[:8] for s in tw.slots[:8]]
        return ["—"] * 8
