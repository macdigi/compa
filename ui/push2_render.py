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
        self._last_botselect_leds = [-1] * 8
        self._last_octave_leds = (-1, -1)   # (down, up)
        # (up, down, left, right) — last D-pad LED colors sent.
        self._last_nav_leds = (-1, -1, -1, -1)
        # Last Double-Loop LED color sent (lit bright in pattern mode).
        self._last_dl_led = -1
        # Last Undo LED color sent (lit when there's a pattern-mode
        # action available to undo).
        self._last_undo_led = -1
        # Whether we've painted the "always-dim" named-button set yet.
        self._lit_static_buttons = False

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

    # Set of mapped buttons we light dim once at startup so they're
    # visible in a dark studio. Anything that gets a context-specific
    # color from the dynamic block below overrides this.
    _STATIC_DIM_BUTTONS = (
        "note", "session", "scale", "layout",
        "browse", "device", "mix", "clip",
        "add_device", "add_track", "master",
        "tap_tempo", "metronome", "delete", "undo",
        "convert", "double_loop", "quantize", "new", "fixed_length",
        "duplicate", "automate",
        "shift", "select", "setup", "user",
        "stop_clip", "mute", "solo",
        "launch_1", "launch_2", "launch_3", "launch_4",
        "launch_5", "launch_6", "launch_7", "launch_8",
    )

    def _update_button_leds(self) -> None:
        """Push state-driven LED updates to the Push 2. Only sends MIDI
        when a value actually changed so we don't flood the bus."""
        push2 = getattr(self.app, "push2", None)
        if push2 is None:
            return

        # One-shot: paint every mapped non-pad button at a dim level so
        # the user can see them in a dark room. Dynamic blocks below
        # override per-button as needed.
        if not self._lit_static_buttons:
            for btn in self._STATIC_DIM_BUTTONS:
                try:
                    push2.set_button(btn, 3)
                except Exception:
                    pass
            self._lit_static_buttons = True

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

        # Page-left / page-right LEDs in pattern mode — both always lit
        # dim cyan when there's more than one step page (paging cycles
        # around at the ends).
        if mode_for_oct == "pattern":
            try:
                seq = self.app._push2_pattern_sequencer()
                num_steps = getattr(seq, "num_steps", 16) if seq else 16
            except Exception:
                num_steps = 16
            page_color = 22 if num_steps > 8 else 3
            push2.set_button("page_left", page_color)
            push2.set_button("page_right", page_color)

        # Double Loop LED — bright pink in pattern mode (action: cycle
        # PiSequencer length 16→32→64→16). Dim everywhere else.
        dl_color = 60 if mode_for_oct == "pattern" else 3
        if self._last_dl_led != dl_color:
            push2.set_button("double_loop", dl_color)
            self._last_dl_led = dl_color

        # Undo LED — bright cyan in pattern mode when there's a
        # step-count change available to revert.
        undo_color = 3
        if mode_for_oct == "pattern":
            try:
                if self.app._push2_last_num_steps is not None:
                    undo_color = 50      # bright blue/cyan
            except Exception:
                pass
        if self._last_undo_led != undo_color:
            push2.set_button("undo", undo_color)
            self._last_undo_led = undo_color

        # D-pad LEDs (only sent when the desired color tuple changes,
        # to avoid spamming the bus with 80+ MIDI messages/sec).
        if mode_for_oct == "keys":
            nav_target = (22, 22, 22, 22)
        else:
            try:
                lpc = int(self.app.push2_launch_page_count())
            except Exception:
                lpc = 1
            lr = 22 if lpc > 1 else 0
            nav_target = (0, 0, lr, lr)   # up, down, left, right
        if nav_target != self._last_nav_leds:
            push2.set_button("nav_up",    nav_target[0])
            push2.set_button("nav_down",  nav_target[1])
            push2.set_button("nav_left",  nav_target[2])
            push2.set_button("nav_right", nav_target[3])
            self._last_nav_leds = nav_target

        # Bottom-row select buttons: SP-404 bus selector in control mode.
        # Active bus lit bright in its bus color; available buses dim;
        # unmapped slots off.
        try:
            dev_key_for_bot = self.app.device_manager.focus_key
        except Exception:
            dev_key_for_bot = None
        bot_colors = [-1] * 8
        if mode_for_oct == "control" and dev_key_for_bot == "SP-404MKII":
            try:
                active_bus = int(self.app.twister.active_bus)
            except Exception:
                active_bus = 0
            # B1=red, B2=blue, B3=green, B4=yellow, IN=orange
            bus_palette = [127, 125, 126, 8, 9]
            for i in range(5):
                bot_colors[i] = bus_palette[i] if i == active_bus else 1
            # 6-8 stay -1 → mapped to 0 below
        for i in range(8):
            color = bot_colors[i] if bot_colors[i] >= 0 else 0
            if self._last_botselect_leds[i] != color:
                push2.set_button(f"bot_select_{i + 1}", color)
                self._last_botselect_leds[i] = color

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
        # repaints when the user transposes, the SP-404 pad-note
        # (and therefore playable range) shifts, or scale/root changes.
        keys_state: tuple = ()
        if mode == "keys":
            base_note = getattr(self.app, "push2_keys_base_note", 36)
            scale_idx = getattr(self.app, "push2_keys_scale", 0)
            root_pc = getattr(self.app, "push2_keys_root", 0)
            lo = hi = None
            if dev_key == "SP-404MKII":
                kb = getattr(self.app, "chromatic_kb", None)
                if kb is not None:
                    pn = getattr(kb, "_pad_note", 0) or 0
                    br = getattr(kb, "_bend_range", 12) or 12
                    if pn > 0:
                        lo = pn - br
                        hi = pn + br
            keys_state = (base_note, lo, hi, scale_idx, root_pc)

        # Pattern-mode state — current active pattern + total + page +
        # step offset + a hash of the active sequencer's grid. Forces
        # a repaint when any of these change (pattern switch, step
        # toggle, page paging, or playhead movement).
        pattern_state: tuple = ()
        if mode == "pattern":
            cur_pat = 0
            try:
                p6 = self.app.p6
                if p6 is not None:
                    cur_pat = int(p6.state.active_pattern) + 1
            except Exception:
                cur_pat = 0
            try:
                total = int(self.app.push2_max_patterns())
            except Exception:
                total = 64
            try:
                lp = int(self.app.push2_pattern_launch_page)
            except Exception:
                lp = 0
            try:
                offset = int(self.app.push2_pattern_step_offset)
            except Exception:
                offset = 0
            seq = getattr(self.app, "_push2_pattern_sequencer",
                          lambda: None)()
            if seq is not None:
                grid_hash = self._sequencer_grid_hash(seq)
                cstep = int(getattr(seq, "current_step", 0))
                playing = bool(getattr(seq, "playing", False))
            else:
                grid_hash, cstep, playing = 0, 0, False
            pattern_state = (cur_pat, total, dev_key, lp, offset,
                             grid_hash, cstep, playing)

        frame_key = (mode, dev_key, pad_page, keys_state, pattern_state)
        if frame_key != self._last_pad_frame_key:
            self._repaint_pad_frame(push2, mode, dev_key, pad_page,
                                    keys_state, pattern_state)
            self._last_pad_frame_key = frame_key

    @staticmethod
    def _sequencer_grid_hash(seq) -> int:
        """Hash of the sequencer grid's active flags — drives the
        renderer's repaint trigger. Each (pad, step) gets a unique
        bit so any toggle changes the hash, regardless of how many
        steps the pattern actually has (up to 64)."""
        try:
            num_pads = getattr(seq, "num_pads", 0)
            num_steps = getattr(seq, "num_steps", 0)
            h = 0
            for p in range(min(num_pads, 8)):
                row = seq.grid[p]
                for s in range(min(num_steps, 64)):
                    if row[s].active:
                        h |= 1 << (p * 64 + s)
            return h
        except Exception:
            return 0

    def _repaint_pad_frame(self, push2, mode, dev_key, pad_page,
                           keys_state: tuple = (),
                           pattern_state: tuple = ()) -> None:
        if mode == "keys":
            from engine.push2 import SCALES
            base_note = keys_state[0] if len(keys_state) > 0 else 36
            lo = keys_state[1] if len(keys_state) > 1 else None
            hi = keys_state[2] if len(keys_state) > 2 else None
            scale_idx = keys_state[3] if len(keys_state) > 3 else 0
            root_pc = keys_state[4] if len(keys_state) > 4 else 0
            name, offsets = SCALES[scale_idx % len(SCALES)]
            if name == "chromatic":
                push2.light_keys_layout(
                    base_note=base_note, min_note=lo, max_note=hi,
                    scale=offsets, root_pc=root_pc,
                )
            else:
                push2.light_in_key_layout(
                    offsets, root_pc=root_pc,
                    base_note=base_note, min_note=lo, max_note=hi,
                )
            return
        if mode == "pattern":
            cur_pat = pattern_state[0] if len(pattern_state) > 0 else 0
            total = pattern_state[1] if len(pattern_state) > 1 else 64
            ps_dev = pattern_state[2] if len(pattern_state) > 2 else dev_key
            lp = pattern_state[3] if len(pattern_state) > 3 else 0
            offset = pattern_state[4] if len(pattern_state) > 4 else 0
            seq = getattr(self.app, "_push2_pattern_sequencer",
                          lambda: None)()
            if ps_dev == "P-6":
                bright, dim = 8, 15
            elif ps_dev == "SP-404MKII":
                bright, dim = 9, 11
            else:
                bright, dim = 122, 3
            push2.light_combined_pattern_layout(
                current_pattern=cur_pat,
                total_patterns=total,
                pattern_launch_page=lp,
                seq=seq,
                step_offset=offset,
                launch_bright=bright,
                launch_dim=dim,
            )
            return
        if mode == "dj":
            push2.light_dj_layout()
            return
        if mode == "looper":
            push2.light_looper_layout()
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

        # Special-encoder popup beats BPM but loses to held notes.
        encoder_overlay = self._special_encoder_overlay() if not held else None

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
        elif encoder_overlay is not None:
            label, value, sub = encoder_overlay
            big = self._font_hero.render(value, True, dev_color)
            bx = SURF_W // 2 - big.get_width() // 2
            surf.blit(big, (bx, 0))
            lbl = self._font_tiny.render(label, True, DIM)
            surf.blit(lbl, (bx + big.get_width() + 6,
                            big.get_height() - lbl.get_height() - 4))
            if sub:
                sb = self._font_tiny.render(sub, True, DIM)
                surf.blit(sb, (SURF_W // 2 - sb.get_width() // 2, 36))
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
        if mode_now == "dj":
            xf = 64
            try:
                xf = int(self.app.live_cc.get(0, {}).get(8, 64))
            except Exception:
                pass
            txt = f"DJ  Decks A · B  ·  Crossfade {xf}/127"
            psurf = self._font_tiny.render(txt, True, dev_color)
            surf.blit(psurf, (14, y))
            return
        if mode_now == "looper":
            txt = "LOOPER  REC · OVERDUB · STOP · DELETE · UNDO · REDO"
            psurf = self._font_tiny.render(txt, True, dev_color)
            surf.blit(psurf, (14, y))
            return
        if mode_now == "pattern":
            cur_pat = 0
            try:
                p6 = self.app.p6
                if p6 is not None:
                    cur_pat = int(p6.state.active_pattern) + 1
            except Exception:
                cur_pat = 0
            try:
                total = int(self.app.push2_max_patterns())
            except Exception:
                total = 64
            try:
                lp = int(self.app.push2_pattern_launch_page)
                lpc = int(self.app.push2_pattern_launch_page_count())
            except Exception:
                lp, lpc = 0, 1
            try:
                offset = int(self.app.push2_pattern_step_offset)
            except Exception:
                offset = 0
            launch_first = lp * 16 + 1
            launch_last = min(launch_first + 15, total)
            page_seg = f"  ·  {lp + 1}/{lpc}" if lpc > 1 else ""
            seq = getattr(self.app, "_push2_pattern_sequencer",
                          lambda: None)()
            num_steps = (int(getattr(seq, "num_steps", 16))
                         if seq is not None else 16)
            playing = bool(getattr(seq, "playing", False)) if seq else False
            play_glyph = "▶" if playing else " "
            seq_text = (f"{play_glyph} steps {offset + 1}-"
                        f"{min(offset + 8, num_steps)}/{num_steps}")
            txt = (f"PAT {cur_pat}/{total}  "
                   f"launch {launch_first}-{launch_last}{page_seg}  ·  "
                   f"{seq_text}")
            psurf = self._font_tiny.render(txt, True, dev_color)
            surf.blit(psurf, (14, y))
            return

        if mode_now == "keys":
            # Range + scale + root on a single status line. Compute the
            # actual range from the layout in use so in-key mode reports
            # the correct top note (it spans more octaves than chromatic).
            from engine.push2 import SCALES, ROOT_NAMES, Push2
            base = getattr(self.app, "push2_keys_base_note", 36)
            scale_idx = getattr(self.app, "push2_keys_scale", 0)
            root_pc = getattr(self.app, "push2_keys_root", 0)
            scale_name, offsets = SCALES[scale_idx % len(SCALES)]
            if scale_name == "chromatic":
                bottom_note = base
                top_note = base + 7 * 5 + 7
            else:
                bottom_note = Push2.in_key_pad_to_note(
                    0, offsets, root_pc=root_pc, base_note=base)
                top_note = Push2.in_key_pad_to_note(
                    63, offsets, root_pc=root_pc, base_note=base)
            root = ROOT_NAMES[root_pc % 12]
            range_txt = f"{self._note_name(bottom_note)}—{self._note_name(top_note)}"
            txt = f"KEYS  {root} {scale_name}  ·  {range_txt}"
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
            y += pbsurf.get_height() + 1

        # Current pattern + launch page (Launch 1-8 + D-pad ←/→).
        try:
            lp = int(self.app.push2_launch_page)
            lpc = int(self.app.push2_launch_page_count())
            total_pats = int(self.app.push2_max_patterns())
        except Exception:
            lp, lpc, total_pats = 0, 1, 0
        cur_pat = 0
        try:
            p6 = self.app.p6
            if p6 is not None:
                cur_pat = int(p6.state.active_pattern) + 1
        except Exception:
            cur_pat = 0
        if total_pats > 0:
            first_pat = lp * 8 + 1
            last_pat = min(first_pat + 7, lp * 8 + 8, total_pats)
            page_segment = (f"  ·  1-8 ({lp + 1}/{lpc})"
                            if lpc > 1 else "")
            txt = f"PAT {cur_pat}/{total_pats}{page_segment}"
            psurf = self._font_tiny.render(txt, True, dev_color)
            surf.blit(psurf, (14, y))

    def _special_encoder_overlay(self) -> tuple | None:
        """If the user turned a Tempo / Master / Swing encoder in the
        last 1.5s, return (label, value, sub) for an overlay. Else None."""
        import time as _time
        last = getattr(self.app, "_push2_last_special_encoder", None)
        if not last:
            return None
        name, ts = last
        if _time.monotonic() - ts > 1.5:
            return None
        if name == "tempo":
            try:
                bpm = self.app.master_clock.get_bpm()
            except Exception:
                bpm = 120.0
            return ("BPM", f"{bpm:.1f}", None)
        if name == "master":
            try:
                vol = int(self.app.live_cc.get(0, {}).get(7, 100))
            except Exception:
                vol = 100
            return ("VOL", f"{vol}", "Master  CC 7  Ch1")
        if name == "swing":
            return ("SWING", "—", "swing engine not implemented yet")
        return None

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
        # In pattern mode, the Compa master clock is authoritative
        # for tempo. Outside pattern mode, fall back to the focused
        # device's reported BPM.
        try:
            if getattr(self.app, "push2_mode", "") == "pattern":
                mc = getattr(self.app, "master_clock", None)
                if mc is not None:
                    return mc.get_bpm()
        except Exception:
            pass
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
