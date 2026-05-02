"""Touch Calibration Screen — 4-corner + center pygame UI for HID touchscreens.

Displays target points one at a time. User taps each. We record the raw
incoming pixel coordinate, then solve a least-squares affine transform that
maps raw → expected. Saves to ~/.config/compa/touch_calibration.json.

Bypasses calibration during the screen itself (app sets is_calibrating=True
so _handle_events knows to deliver raw coords).
"""
import pygame
from .. import theme
from engine.touch_calibration import compute_matrix, TouchCalibration


# Target inset from screen edges (pixels)
INSET = 60
TARGET_RADIUS_OUTER = 28
TARGET_RADIUS_INNER = 6


class TouchCalibrateScreen:
    def __init__(self, app):
        self.app = app
        self._targets: list[tuple[int, int]] = []     # screen coords to display
        self._raw: list[tuple[int, int]] = []         # raw touches recorded
        self._index = 0
        self._status = ""
        self._error = ""
        self._return_to = "settings"
        self._pulse = 0.0

    def on_enter(self):
        w, h = self.app._display_w, self.app._display_h
        # 4 corners + center
        self._targets = [
            (INSET, INSET),
            (w - INSET, INSET),
            (w - INSET, h - INSET),
            (INSET, h - INSET),
            (w // 2, h // 2),
        ]
        self._raw = []
        self._index = 0
        self._status = "Tap each target as it appears."
        self._error = ""
        self._pulse = 0.0
        # Disable calibration application while calibrating — we want raw coords
        self.app.is_calibrating = True

    def on_exit(self):
        self.app.is_calibrating = False

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._cancel()
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._record_tap(event.pos)

    def _record_tap(self, pos):
        if self._index >= len(self._targets):
            return
        self._raw.append(pos)
        self._index += 1

        if self._index >= len(self._targets):
            self._finish()

    def _finish(self):
        try:
            matrix = compute_matrix(self._raw, self._targets)
        except Exception as e:
            self._error = f"Calibration failed: {e}"
            return

        # Quick sanity check: verification target (center) should map close
        cal = TouchCalibration()
        cal.matrix = matrix
        vx, vy = cal.apply(*self._raw[-1])
        tx, ty = self._targets[-1]
        err = ((vx - tx) ** 2 + (vy - ty) ** 2) ** 0.5
        if err > 100:
            self._error = (f"Verification off by {err:.0f}px — "
                           "tap targets more accurately. Press ESC to cancel.")
            self._index = 0
            self._raw = []
            return

        # Save
        cal.save(matrix)
        # Reload calibration into the app's instance so it takes effect immediately
        if hasattr(self.app, "touch_calibration"):
            self.app.touch_calibration.load()
        self._status = f"Saved. Verification error: {err:.1f}px"
        self.app.is_calibrating = False
        # Return to settings after brief flash
        self.app.switch_screen(self._return_to)

    def _cancel(self):
        self.app.is_calibrating = False
        self.app.switch_screen(self._return_to)

    def update(self):
        self._pulse = (self._pulse + 0.06) % (2 * 3.14159)

    def draw(self, surface: pygame.Surface):
        surface.fill(theme.BG)
        w, h = surface.get_size()

        # Title
        title_font = pygame.font.SysFont("monospace", 22, bold=True)
        sub_font = pygame.font.SysFont("monospace", 14)
        small_font = pygame.font.SysFont("monospace", 12)

        title = title_font.render("TOUCH CALIBRATION", True, theme.ACCENT_BRIGHT)
        surface.blit(title, ((w - title.get_width()) // 2, h // 2 - 60))

        if self._error:
            err = sub_font.render(self._error, True, theme.RED)
            surface.blit(err, ((w - err.get_width()) // 2, h // 2 - 28))
        else:
            progress = f"{self._index} / {len(self._targets)}"
            prog = sub_font.render(progress, True, theme.TEXT)
            surface.blit(prog, ((w - prog.get_width()) // 2, h // 2 - 28))

        status = sub_font.render(self._status, True, theme.TEXT_DIM)
        surface.blit(status, ((w - status.get_width()) // 2, h // 2 - 6))

        hint = small_font.render("Press ESC to cancel", True, theme.TEXT_DIM)
        surface.blit(hint, ((w - hint.get_width()) // 2, h // 2 + 24))

        # Active target
        if self._index < len(self._targets):
            tx, ty = self._targets[self._index]
            self._draw_target(surface, tx, ty)

    def _draw_target(self, surface, x, y):
        import math
        # Pulsing outer ring
        pulse_r = TARGET_RADIUS_OUTER + int(6 * (1 + math.sin(self._pulse)) / 2)
        pygame.draw.circle(surface, theme.ACCENT_DIM, (x, y), pulse_r, 2)
        pygame.draw.circle(surface, theme.ACCENT_BRIGHT, (x, y),
                           TARGET_RADIUS_OUTER, 3)
        # Inner crosshair
        pygame.draw.line(surface, theme.ACCENT_BRIGHT,
                         (x - TARGET_RADIUS_OUTER, y),
                         (x + TARGET_RADIUS_OUTER, y), 1)
        pygame.draw.line(surface, theme.ACCENT_BRIGHT,
                         (x, y - TARGET_RADIUS_OUTER),
                         (x, y + TARGET_RADIUS_OUTER), 1)
        # Center dot
        pygame.draw.circle(surface, theme.TEXT_BRIGHT, (x, y),
                           TARGET_RADIUS_INNER)
