"""Push 2 display renderer — themed layout with live scope + meters.

ASCII logo source: docs/logo/compa_logo_ascii_only.png (593x224 RGBA).
Rendering the ASCII art as live text at Push 2's size comes out mushy
on the LCD, so we scale the authored PNG instead.


Runs a daemon render thread at 20fps painting a 960x160 pygame Surface
that mirrors Compa's visual language: device-colored accents, neonRed
Compa brand, scope with filled waveform + L/R level meters, and a row
of 8 encoder labels at the bottom.

Layout (960×160):

  ┌─────────────────────────────────────────────────────────────┐
  │ [DEVICE]               ### BPM                     COMPA    │  ~40px
  ├─────────────────────────────────────────────────────────────┤
  │                                                      L   R  │
  │     scope waveform (filled + outline, device color)  █   █  │  ~90px
  │                                                      █   █  │
  ├─────────────────────────────────────────────────────────────┤
  │  [enc1]  [enc2]  [enc3]  [enc4]  [enc5]  [enc6]  [enc7] [8] │  ~30px
  └─────────────────────────────────────────────────────────────┘

Data sources:
  - Device name: app.device_name
  - Device color: theme.get_device_color(device_name)
  - BPM + transport: app.p6.state.{bpm,playing}
  - Audio scope: app.recorder._recall_buf + _recall_write_pos
  - Encoder labels: app.twister.slots (auto-populated even without
    the Twister hardware, per the Phase-1 device_workspace fix)
"""

import logging
import os
import threading
import time

import numpy as np
import pygame

log = logging.getLogger(__name__)

SURF_W = 960
SURF_H = 160
TARGET_FPS = 20
FRAME_INTERVAL = 1.0 / TARGET_FPS

# Meter smoothing (per-frame decay).
METER_SMOOTH = 0.25

# Compa neonRed brand.
COMPA_RED = (255, 0, 62)
TEXT = (230, 230, 230)
DIM = (120, 120, 120)
VERYDIM = (60, 60, 60)
BG_SCOPE = (8, 8, 14)
GRID = (22, 22, 32)
GRID_DIM = (18, 18, 26)

# Fallback device color when theme.get_device_color is unavailable.
DEFAULT_DEVICE_COLOR = (60, 180, 255)


class Push2Renderer:
    def __init__(self, app, display) -> None:
        self.app = app
        self.display = display

        # Push 2-specific fonts. DejaVu ships on Raspbian; the mono
        # variant is used for the ASCII logo so character widths align.
        self._font_hero = pygame.font.SysFont("dejavusans-bold", 44)
        self._font_big = pygame.font.SysFont("dejavusans-bold", 22)
        self._font_med = pygame.font.SysFont("dejavusans", 16)
        self._font_small = pygame.font.SysFont("dejavusans", 14)
        self._font_tiny = pygame.font.SysFont("dejavusans", 11)

        # Load + scale the Compa ASCII logo PNG. Scaling the authored
        # PNG produces much cleaner results than re-rendering ASCII
        # text at this pixel size.
        self._logo_surface = self._load_logo_png(target_h=38)

        self.surface = pygame.Surface((SURF_W, SURF_H))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                         name="Push2Render")

        # Peak-hold smoothing state (per L/R channel).
        self._smooth_l = 0.0
        self._smooth_r = 0.0

        # Last button-LED state we sent. Initialized to a sentinel so
        # the first pass through _update_button_leds always paints.
        self._last_play_led = -1
        self._last_record_led = -1
        self._last_topselect_leds = [-1] * 8
        self._last_octave_leds = (-1, -1)   # (down, up)

        # Track pad-frame state so we repaint pads only when device or
        # pad_page changes (not every frame).
        self._last_pad_frame_key: tuple | None = None

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
                self._update_button_leds()
            except Exception as e:
                log.warning("Push 2 frame failed: %s", e)
                time.sleep(0.3)
            elapsed = time.monotonic() - t0
            sleep_for = FRAME_INTERVAL - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    def _update_button_leds(self) -> None:
        """Push state-driven LED updates to the Push 2. Only sends MIDI
        when a value actually changed so we don't flood the bus."""
        push2 = getattr(self.app, "push2", None)
        if push2 is None:
            return

        # Transport: green Play when playing, dim white when idle;
        # red Record when recording, dim white when idle.
        playing = self._safe_playing()
        recording = False
        try:
            recording = bool(self.app.recorder.is_recording)
        except Exception:
            pass
        play_color = 126 if playing else 3
        rec_color = 127 if recording else 3

        if self._last_play_led != play_color:
            push2.set_button("play", play_color)
            self._last_play_led = play_color
        if self._last_record_led != rec_color:
            push2.set_button("record", rec_color)
            self._last_record_led = rec_color

        # Top select buttons = direct encoder-page jumps.
        try:
            current = self.app.push2_page
            count = self.app.push2_page_count()
        except Exception:
            current, count = 0, 1
        for i in range(8):
            if i >= count:
                color = 0
            elif i == current:
                color = 122   # bright white — active page
            else:
                color = 8     # dim amber — available page
            if self._last_topselect_leds[i] != color:
                push2.set_button(f"top_select_{i + 1}", color)
                self._last_topselect_leds[i] = color

        # Octave up/down: drive pad-page paging in control mode and
        # octave transposition in keys mode. Lit dim whenever they're
        # actionable.
        try:
            mode_for_oct = self.app.push2_mode
        except Exception:
            mode_for_oct = "control"
        if mode_for_oct == "keys":
            octave_color = 22  # always usable in keys mode
        else:
            try:
                pad_pages = self.app.push2_pad_page_count()
            except Exception:
                pad_pages = 1
            octave_color = 22 if pad_pages > 1 else 0
        if self._last_octave_leds != (octave_color, octave_color):
            push2.set_button("octave_down", octave_color)
            push2.set_button("octave_up", octave_color)
            self._last_octave_leds = (octave_color, octave_color)

        # Resolve Push 2 mode from the active Compa tab.
        try:
            mode = self.app.update_push2_mode()
        except Exception:
            mode = "control"

        try:
            dev_key = self.app.device_manager.focus_key
        except Exception:
            dev_key = None
        try:
            pad_page = self.app.push2_pad_page
        except Exception:
            pad_page = 0

        # Keys-mode state contributes to the frame key so the grid
        # repaints when the user transposes via Octave Up/Down or when
        # the SP-404 pad-note (and therefore playable range) shifts.
        keys_state: tuple = ()
        if mode == "keys":
            base_note = getattr(self.app, "push2_keys_base_note", 36)
            lo = hi = None
            if dev_key == "SP-404MKII":
                kb = getattr(self.app, "chromatic_kb", None)
                if kb is not None:
                    pn = getattr(kb, "_pad_note", 0) or 0
                    br = getattr(kb, "_bend_range", 12) or 12
                    if pn > 0:
                        lo = pn - br
                        hi = pn + br
            keys_state = (base_note, lo, hi)

        frame_key = (mode, dev_key, pad_page, keys_state)
        if frame_key != self._last_pad_frame_key:
            self._repaint_pad_frame(push2, mode, dev_key, pad_page, keys_state)
            self._last_pad_frame_key = frame_key

    def _repaint_pad_frame(self, push2, mode, dev_key, pad_page,
                           keys_state: tuple = ()) -> None:
        if mode == "keys":
            base_note = keys_state[0] if keys_state else 36
            lo = keys_state[1] if len(keys_state) > 1 else None
            hi = keys_state[2] if len(keys_state) > 2 else None
            push2.light_keys_layout(base_note=base_note,
                                    min_note=lo, max_note=hi)
            return
        if dev_key == "SP-404MKII":
            push2.light_bank_frame_for_page(pad_page, num_banks=10)
        else:
            push2.light_bank_frame()

    # ── Scene composition ─────────────────────────────────────────

    def _render_frame(self, surf: pygame.Surface) -> None:
        surf.fill((0, 0, 0))
        dev_color = self._device_color()

        self._draw_header(surf, dev_color)
        self._draw_scope(surf, dev_color, top=50, height=78)
        self._draw_encoder_labels(surf, top=130, height=28)

    # ── Header ────────────────────────────────────────────────────

    def _draw_header(self, surf, dev_color):
        # ── Device pill, top-left ─────────────────────────────────
        dev_name = getattr(self.app, "device_name", "") or "—"
        dev_surf = self._font_big.render(dev_name.upper(), True, dev_color)
        pill_rect = pygame.Rect(10, 6, dev_surf.get_width() + 18,
                                dev_surf.get_height() + 6)
        # Subtle device-color-tinted outline so the device identity reads
        # like one of Compa's tab headers.
        tint = (dev_color[0] // 6, dev_color[1] // 6, dev_color[2] // 6)
        pygame.draw.rect(surf, tint, pill_rect, border_radius=6)
        pygame.draw.rect(surf, dev_color, pill_rect, 1, border_radius=6)
        surf.blit(dev_surf, (pill_rect.x + 9, pill_rect.y + 2))

        # ── Centerpiece: BPM in most modes, held-note(s) in keys ────
        try:
            mode_now = self.app.push2_mode
        except Exception:
            mode_now = "control"
        held = (getattr(self.app, "_push2_keys_active", None) or {}) \
                if mode_now == "keys" else {}

        if held:
            # Replace BPM with the currently-held note(s) so the user
            # can see exactly which key is sounding without guessing.
            notes_sorted = sorted(set(held.values()))
            names = "  ".join(self._note_name(n) for n in notes_sorted)
            font = self._font_hero if len(notes_sorted) == 1 else self._font_big
            ns = font.render(names, True, dev_color)
            # Cap width so a wide chord doesn't crash into the COMPA logo.
            max_w = SURF_W - 280
            if ns.get_width() > max_w:
                ns = self._font_big.render(names, True, dev_color)
            cx = SURF_W // 2 - ns.get_width() // 2
            surf.blit(ns, (cx, 4))
        else:
            bpm = self._safe_bpm()
            bpm_text = f"{bpm:.1f}" if bpm is not None else "— —"
            bpm_surf = self._font_hero.render(bpm_text, True, TEXT)
            bpm_x = SURF_W // 2 - bpm_surf.get_width() // 2
            surf.blit(bpm_surf, (bpm_x, 2))
            bpm_label = self._font_tiny.render("BPM", True, DIM)
            surf.blit(bpm_label, (bpm_x + bpm_surf.get_width() + 6,
                                  2 + bpm_surf.get_height() - bpm_label.get_height() - 4))

            playing = self._safe_playing()
            ty = 18
            if playing:
                tri_x = bpm_x - 22
                pygame.draw.polygon(surf, dev_color,
                                    [(tri_x, ty), (tri_x, ty + 12),
                                     (tri_x + 10, ty + 6)])
            else:
                stop_surf = self._font_small.render("STOP", True, DIM)
                surf.blit(stop_surf, (bpm_x - stop_surf.get_width() - 10, ty + 1))

        # ── Compa ASCII logo, top-right ───────────────────────────
        lw = self._logo_surface.get_width()
        surf.blit(self._logo_surface, (SURF_W - lw - 10, 4))

        # ── Mode-specific status line(s) under the device pill ────
        # `mode_now` already resolved above when picking the centerpiece.
        y = 32
        if mode_now == "keys":
            # Show the current octave / note range on screen so the
            # user can see which octave the grid is in without
            # triggering a pad to find out.
            base = getattr(self.app, "push2_keys_base_note", 36)
            top = base + 7 * 5 + 7
            txt = f"KEYS  {self._note_name(base)} — {self._note_name(top)}"
            ksurf = self._font_tiny.render(txt, True, dev_color)
            surf.blit(ksurf, (14, y))
            return

        try:
            page = self.app.push2_page
            page_count = self.app.push2_page_count()
        except Exception:
            page, page_count = 0, 1
        if page_count > 1:
            txt = f"CTRL {page + 1}/{page_count}"
            psurf = self._font_tiny.render(txt, True, DIM)
            surf.blit(psurf, (14, y))
            y += psurf.get_height() + 1

        try:
            pad_page = self.app.push2_pad_page
            pad_pages = self.app.push2_pad_page_count()
        except Exception:
            pad_page, pad_pages = 0, 1
        if pad_pages > 1:
            first = pad_page * 4
            try:
                total = 10 if self.app.device_manager.focus_key == "SP-404MKII" else 4
            except Exception:
                total = 10
            last = min(first + 3, total - 1)
            letters = f"{chr(ord('A') + first)}-{chr(ord('A') + last)}"
            pbsurf = self._font_tiny.render(f"BANK {letters}", True, dev_color)
            surf.blit(pbsurf, (14, y))

    @staticmethod
    def _note_name(midi_note: int) -> str:
        """MIDI note number → "C2" / "F#3" style label.
        MIDI 60 = C4 (standard scientific pitch)."""
        names = ["C", "C#", "D", "D#", "E", "F",
                 "F#", "G", "G#", "A", "A#", "B"]
        if midi_note < 0 or midi_note > 127:
            return "?"
        octave = (midi_note // 12) - 1
        return f"{names[midi_note % 12]}{octave}"

    def _load_logo_png(self, target_h: int) -> pygame.Surface:
        """Load docs/logo/compa_logo_ascii_only.png and scale it to
        `target_h` pixels tall, preserving aspect ratio. Returns a
        fallback text surface if the PNG isn't found."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(project_root, "docs", "logo",
                            "compa_logo_ascii_only.png")
        try:
            img = pygame.image.load(path)
            w, h = img.get_size()
            scale = target_h / h
            scaled = pygame.transform.smoothscale(
                img, (int(w * scale), target_h),
            )
            return scaled
        except Exception as e:
            log.warning("Compa logo PNG load failed: %s — using text fallback", e)
            return self._font_big.render("COMPA", True, COMPA_RED)

    # ── Scope + meters ────────────────────────────────────────────

    def _draw_scope(self, surf, dev_color, top, height):
        pad_x = 14
        meter_total_w = 26
        meter_gap = 6

        scope_rect = pygame.Rect(
            pad_x, top,
            SURF_W - pad_x * 2 - meter_total_w - meter_gap,
            height,
        )
        pygame.draw.rect(surf, BG_SCOPE, scope_rect, border_radius=5)

        cy = scope_rect.centery
        half_h = (scope_rect.height - 10) // 2

        # Grid
        for frac in (0.25, 0.75):
            gy = scope_rect.y + int(scope_rect.height * frac)
            pygame.draw.line(surf, GRID_DIM,
                             (scope_rect.x + 2, gy),
                             (scope_rect.right - 2, gy))
        pygame.draw.line(surf, GRID,
                         (scope_rect.x + 2, cy),
                         (scope_rect.right - 2, cy))

        recent = self._get_recent_audio()
        peak_l = peak_r = 0.0

        if recent is not None and len(recent) > 0:
            mono = recent.mean(axis=1) if recent.ndim > 1 else recent
            peak = float(np.max(np.abs(mono))) if len(mono) else 0.0

            if peak > 0.001:
                wave_w = scope_rect.width - 4
                step = max(1, len(mono) // wave_w)
                points = []
                for px in range(wave_w):
                    si = px * step
                    if si < len(mono):
                        val = max(-1.0, min(1.0, float(mono[si]) * 3.0))
                        py = cy - int(val * half_h)
                        points.append((scope_rect.x + 2 + px, py))

                if len(points) > 1:
                    dim = (dev_color[0] // 5, dev_color[1] // 5, dev_color[2] // 5)
                    for px_x, py in points:
                        if py != cy:
                            pygame.draw.line(surf, dim, (px_x, cy), (px_x, py))
                    pygame.draw.lines(surf, dev_color, False, points, 2)

            if recent.ndim > 1 and recent.shape[1] > 0:
                peak_l = float(np.max(np.abs(recent[:, 0])))
                if recent.shape[1] > 1:
                    peak_r = float(np.max(np.abs(recent[:, 1])))
                else:
                    peak_r = peak_l
            else:
                peak_l = peak_r = peak
        else:
            msg = self._font_small.render("no audio", True, DIM)
            surf.blit(msg, msg.get_rect(center=scope_rect.center))

        # ── L/R meters ────────────────────────────────────────────
        self._smooth_l = max(peak_l, self._smooth_l * (1 - METER_SMOOTH))
        self._smooth_r = max(peak_r, self._smooth_r * (1 - METER_SMOOTH))

        # Channel labels go ABOVE the bars to keep the bottom clear for
        # the encoder row below the scope.
        meter_x = scope_rect.right + meter_gap
        meter_w = (meter_total_w - 2) // 2
        label_h = self._font_tiny.get_linesize()
        meter_h = height - label_h - 2
        meter_y = scope_rect.y + label_h + 2

        for i, lvl in enumerate([self._smooth_l, self._smooth_r]):
            x = meter_x + i * (meter_w + 2)
            label = self._font_tiny.render(("L", "R")[i], True, DIM)
            surf.blit(label, (x + meter_w // 2 - label.get_width() // 2,
                              scope_rect.y + 1))
            pygame.draw.rect(surf, (16, 16, 24), (x, meter_y, meter_w, meter_h))
            fill = int(min(1.0, lvl) * meter_h)
            if fill > 0:
                color = ((255, 40, 60) if lvl > 0.9
                         else (230, 200, 40) if lvl > 0.7
                         else dev_color)
                pygame.draw.rect(surf, color,
                                 (x, meter_y + meter_h - fill, meter_w, fill))

    # ── Encoder label row ─────────────────────────────────────────

    def _draw_encoder_labels(self, surf, top, height):
        slots = self._encoder_slots()
        dev_color = self._device_color()
        col_w = SURF_W // 8
        for i, slot in enumerate(slots):
            x = i * col_w
            rect = pygame.Rect(x + 6, top + 2, col_w - 12, height - 4)
            # Outer slot card.
            pygame.draw.rect(surf, (14, 14, 20), rect, border_radius=4)
            # Device-color tick on the left edge of each slot.
            pygame.draw.rect(surf, dev_color,
                             (rect.x, rect.y, 2, rect.height),
                             border_radius=1)

            label = (slot.get("name") or "—")[:11]
            value = slot.get("value")

            # Ableton-Push-style value bar — fills from left behind
            # the label as the CC rises. Uses a dimmed device color so
            # the label text still reads clearly on top.
            if value is not None:
                fill_w = int((rect.width - 4) * (max(0, min(127, value)) / 127.0))
                if fill_w > 0:
                    fill_col = (dev_color[0] // 3, dev_color[1] // 3, dev_color[2] // 3)
                    pygame.draw.rect(surf, fill_col,
                                     (rect.x + 2, rect.y + 2, fill_w, rect.height - 4),
                                     border_radius=3)

            lbl_surf = self._font_small.render(label, True, TEXT)
            surf.blit(lbl_surf, (rect.x + 6, rect.y + 2))

            if value is not None:
                val_surf = self._font_tiny.render(f"{int(value)}", True, dev_color)
                surf.blit(val_surf, (rect.right - val_surf.get_width() - 4,
                                     rect.bottom - val_surf.get_height() - 2))

    # ── Data accessors (all fail-safe: render loop must never raise) ─

    def _safe_bpm(self):
        try:
            return self.app.p6.state.bpm
        except Exception:
            return None

    def _safe_playing(self) -> bool:
        try:
            return bool(self.app.p6.state.playing)
        except Exception:
            return False

    def _device_color(self):
        try:
            from ui import theme
            return theme.get_device_color(self.app.device_name)
        except Exception:
            return DEFAULT_DEVICE_COLOR

    def _get_recent_audio(self):
        """Return an N×channels numpy slice of the most recent audio,
        or None if no audio is available."""
        rec = getattr(self.app, "recorder", None)
        if rec is None or not getattr(rec, "_monitoring", False):
            return None
        try:
            buf = rec._recall_buf
            wpos = rec._recall_write_pos
        except Exception:
            return None
        display_frames = min(2048, len(buf))
        if display_frames == 0:
            return None
        if wpos >= display_frames:
            return buf[wpos - display_frames:wpos]
        # wrap
        try:
            return np.concatenate([buf[-(display_frames - wpos):], buf[:wpos]])
        except Exception:
            return None

    def _encoder_slots(self) -> list[dict]:
        """Return 8 dicts describing each encoder slot: {name, value}."""
        try:
            dev_key = self.app.device_manager.focus_key
        except Exception:
            dev_key = None

        if dev_key == "SP-404MKII":
            return self._sp404_encoder_slots()
        return self._p6_encoder_slots()

    def _p6_encoder_slots(self) -> list[dict]:
        try:
            live = self.app.live_cc.get(14, {}) or {}
        except Exception:
            live = {}
        try:
            slots = self.app.push2_slot_window() or []
        except Exception:
            slots = []
        out = []
        for s in slots[:8]:
            cc = getattr(s, "_p6_cc", None)
            out.append({
                "name": str(getattr(s, "name", "—")),
                "value": live.get(cc) if cc is not None else None,
            })
        while len(out) < 8:
            out.append({"name": "—", "value": None})
        return out

    def _sp404_encoder_slots(self) -> list[dict]:
        """SP-404 encoder row: Ctrl 1-6 of the currently active bus,
        plus two placeholder slots. Active bus is tracked on the
        Twister object (shared source of truth with the touchscreen
        FX knobs)."""
        ctrl_ccs = [16, 17, 18, 80, 81, 82]
        names = ["Ctrl 1", "Ctrl 2", "Ctrl 3", "Ctrl 4", "Ctrl 5", "Ctrl 6"]
        try:
            bus = int(self.app.twister.active_bus)
        except Exception:
            bus = 0
        try:
            live = self.app.live_cc.get(bus, {}) or {}
        except Exception:
            live = {}
        out = []
        for i, cc in enumerate(ctrl_ccs):
            out.append({"name": names[i], "value": live.get(cc)})
        while len(out) < 8:
            out.append({"name": "—", "value": None})
        return out
