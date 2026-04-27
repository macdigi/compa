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
        # Last Layout LED color sent (lit bright in control mode when
        # the focused device exposes more than one pad layout).
        self._last_layout_led = -1
        # Whether we've painted the "always-dim" named-button set yet.
        self._lit_static_buttons = False

        # Track pad-frame state so we repaint pads only when device or
        # pad_page changes (not every frame).
        self._last_pad_frame_key: tuple | None = None

        # Path the next render loop pass should save the surface to,
        # cleared after the save. None when no screenshot is pending.
        self._screenshot_pending: str | None = None

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

    def save_screenshot(self, path: str) -> bool:
        """Request a Push 2 screenshot. The actual save runs from the
        render loop after the next frame, so the captured PNG always
        contains the fully-drawn surface (avoids racing fill() / draw
        passes). Returns True if the request was accepted."""
        self._screenshot_pending = path
        return True

    def _do_save_screenshot(self, path: str) -> None:
        """Perform the PNG save — called only from the render loop."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        try:
            pygame.image.save(self.surface, path)
            log.info("Push 2 screenshot saved: %s", path)
        except Exception as e:
            log.warning("Push 2 screenshot failed: %s", e)

    # ── Render loop ────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self._render_frame(self.surface)
                self.display.send_surface(self.surface)
                self._update_button_leds()
                # Capture screenshot if requested — done inline so the
                # surface still holds the frame we just sent. Racing
                # from the SIGUSR1 thread caused all-black PNGs because
                # save_screenshot could land between fill() and the
                # draw passes.
                pending = self._screenshot_pending
                if pending:
                    self._screenshot_pending = None
                    self._do_save_screenshot(pending)
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
        # Top-row select LEDs: pattern launchers in pattern mode,
        # encoder-page jumps elsewhere.
        try:
            mode_top = self.app.push2_mode
        except Exception:
            mode_top = "control"
        top_colors = [0] * 8
        if mode_top == "pattern":
            try:
                base = int(self.app.push2_pattern_launch_page) * 8
                total_pats = int(self.app.push2_max_patterns())
                # Active pattern within the current 8-window. p6.state
                # tracks the device's currently-loaded pattern; we
                # already use this in light_combined_pattern_layout.
                cur_pat = 0
                try:
                    p6 = self.app.p6
                    if p6 is not None:
                        cur_pat = int(p6.state.active_pattern) + 1
                except Exception:
                    cur_pat = 0
                # Pattern launchers tinted to match the focused device.
                bright = (8 if (getattr(self.app.device_manager,
                                          "focus_key", "") == "P-6")
                          else 9)
                dim = 15 if bright == 8 else 11
                for i in range(8):
                    pat = base + i + 1
                    if pat > total_pats:
                        top_colors[i] = 0
                    elif pat == cur_pat:
                        top_colors[i] = bright
                    else:
                        top_colors[i] = dim
            except Exception:
                pass
        else:
            try:
                current = self.app.push2_page
                count = self.app.push2_page_count()
            except Exception:
                current, count = 0, 1
            for i in range(8):
                if i >= count:
                    top_colors[i] = 0
                elif i == current:
                    top_colors[i] = 122   # bright white — active page
                else:
                    top_colors[i] = 8     # dim amber — available page
        for i in range(8):
            color = top_colors[i]
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
        elif mode_for_oct == "pattern":
            # Pattern mode: octave Up/Down strides the visible pad
            # window in 8-row jumps. Lit dim cyan when there's more
            # than 8 pads (SP-404 = 16 pads → 2 pages); off otherwise
            # (P-6 has 6 pads, all visible at once).
            try:
                seq = self.app._push2_pattern_sequencer()
                num_pads = (int(getattr(seq, "num_pads", 0))
                            if seq is not None else 0)
            except Exception:
                num_pads = 0
            octave_color = 22 if num_pads > 8 else 0
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

        # Pattern-edit cluster — Double Loop, Duplicate, Convert,
        # Fixed Length, New all light up in pattern mode and stay
        # dim everywhere else. New flashes bright red while a
        # confirm-to-clear is armed (3-second window after first
        # press).
        if mode_for_oct == "pattern":
            dl_color = 60       # pink — Double Loop (extend empty)
            dup_color = 60      # pink — Duplicate (extend with copy)
            cv_color = 50       # cyan — Convert (zoom in / finer)
            fl_color = 50       # cyan — Fixed Length (zoom out)
            try:
                pending = float(getattr(
                    self.app, "_push2_new_confirm_until", 0.0))
                if pending and time.monotonic() < pending:
                    new_color = 127   # bright red — clear armed
                else:
                    new_color = 9     # orange — clears the pattern
            except Exception:
                new_color = 9
        else:
            dl_color = dup_color = cv_color = fl_color = 3
            new_color = 3
        if self._last_dl_led != dl_color:
            push2.set_button("double_loop", dl_color)
            self._last_dl_led = dl_color
        if getattr(self, "_last_dup_led", -1) != dup_color:
            push2.set_button("duplicate", dup_color)
            self._last_dup_led = dup_color
        if getattr(self, "_last_convert_led", -1) != cv_color:
            push2.set_button("convert", cv_color)
            self._last_convert_led = cv_color
        if getattr(self, "_last_fl_led", -1) != fl_color:
            push2.set_button("fixed_length", fl_color)
            self._last_fl_led = fl_color
        if getattr(self, "_last_new_led", -1) != new_color:
            push2.set_button("new", new_color)
            self._last_new_led = new_color

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

        # Layout LED — bright white in control mode when the focused
        # device exposes more than one pad layout (cycled by tapping
        # the button). Dim everywhere else so the legend is still
        # visible in a dark room.
        layout_color = 3
        if mode_for_oct == "control":
            try:
                if int(self.app._push2_control_layout_count()) > 1:
                    layout_color = 122
            except Exception:
                pass
        if self._last_layout_led != layout_color:
            push2.set_button("layout", layout_color)
            self._last_layout_led = layout_color

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

        # Bottom-row select buttons: SP-404 bus selector in control mode,
        # bank selector in pattern mode, off otherwise.
        try:
            dev_key_for_bot = self.app.device_manager.focus_key
        except Exception:
            dev_key_for_bot = None
        bot_colors = [-1] * 8
        if mode_for_oct == "pattern":
            try:
                from engine.push2 import SP_BANK_COLORS
                active_bank = int(self.app.push2_active_bank())
                bank_offset = int(self.app.push2_pattern_bank_offset)
                bank_total = int(self.app.push2_bank_count())
                for i in range(8):
                    slot = bank_offset + i
                    if slot >= bank_total:
                        bot_colors[i] = 0
                    elif slot == active_bank:
                        # Active bank: full color from the bank palette.
                        cidx = slot if slot < len(SP_BANK_COLORS) else 0
                        bot_colors[i] = SP_BANK_COLORS[cidx]
                    else:
                        bot_colors[i] = 1   # very dim white
            except Exception:
                pass
        elif mode_for_oct == "control" and dev_key_for_bot == "SP-404MKII":
            try:
                active_bus = int(self.app.twister.active_bus)
            except Exception:
                active_bus = 0
            # B1=red, B2=blue, B3=green, B4=yellow, IN=orange
            bus_palette = [127, 125, 126, 8, 9]
            for i in range(5):
                bot_colors[i] = bus_palette[i] if i == active_bus else 1
            # Slot 8 (idx 7): FX on/off toggle. Bright green when the
            # FX is on, dim white when off — mirrors the touchscreen
            # FX-on toggle's color cue.
            try:
                fx_on = int(
                    self.app.live_cc.get(active_bus, {}).get(19, 0)) >= 64
            except Exception:
                fx_on = False
            bot_colors[7] = 126 if fx_on else 1
            # Slots 6-7 stay -1 → mapped to 0 below.
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
            try:
                pad_off = int(self.app.push2_pattern_pad_offset)
            except Exception:
                pad_off = 0
            try:
                act_bank = int(self.app.push2_active_bank())
                bank_off = int(self.app.push2_pattern_bank_offset)
                bank_total = int(self.app.push2_bank_count())
            except Exception:
                act_bank, bank_off, bank_total = 0, 0, 8
            seq = getattr(self.app, "_push2_pattern_sequencer",
                          lambda: None)()
            if seq is not None:
                grid_hash = self._sequencer_grid_hash(seq)
                cstep = int(getattr(seq, "current_step", 0))
                playing = bool(getattr(seq, "playing", False))
            else:
                grid_hash, cstep, playing = 0, 0, False
            pattern_state = (cur_pat, total, dev_key, lp, offset,
                             grid_hash, cstep, playing, pad_off,
                             act_bank, bank_off, bank_total)

        layout = getattr(self.app, "push2_control_layout", 0)
        frame_key = (mode, dev_key, pad_page, layout,
                     keys_state, pattern_state)
        if frame_key != self._last_pad_frame_key:
            self._repaint_pad_frame(push2, mode, dev_key, pad_page,
                                    keys_state, pattern_state)
            self._last_pad_frame_key = frame_key

    @staticmethod
    def _sequencer_grid_hash(seq) -> int:
        """Hash of the sequencer grid's active flags — drives the
        renderer's repaint trigger. Each (pad, step) gets a unique
        bit so any toggle changes the hash. Covers up to 16 pads ×
        64 steps (= 1024 bits, easy for a Python int) so toggling
        a step on pads 9-16 of the SP triggers an immediate LED
        repaint instead of waiting for a page flip."""
        try:
            num_pads = getattr(seq, "num_pads", 0)
            num_steps = getattr(seq, "num_steps", 0)
            h = 0
            for p in range(min(num_pads, 16)):
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
            offset = pattern_state[4] if len(pattern_state) > 4 else 0
            pad_off = pattern_state[8] if len(pattern_state) > 8 else 0
            seq = getattr(self.app, "_push2_pattern_sequencer",
                          lambda: None)()
            push2.light_step_only_layout(
                seq=seq,
                step_offset=offset,
                pad_offset=pad_off,
                num_pads_visible=8,
            )
            return
        if mode == "dj":
            push2.light_dj_layout()
            return
        if mode == "looper":
            push2.light_looper_layout()
            return
        # Control mode — dispatch on (device, layout). Layout 0 is the
        # default per-device variant, layout 1 is the quadrant variant.
        layout = getattr(self.app, "push2_control_layout", 0)
        if dev_key == "SP-404MKII":
            if layout == 1:
                push2.light_quad_bank_layout(pad_page, num_banks=10)
            else:
                push2.light_bank_frame_for_page(pad_page, num_banks=10)
        elif dev_key == "P-6":
            if layout == 1:
                push2.light_p6_quad_layout(pad_page, num_banks=8)
            else:
                push2.light_p6_row_layout(num_banks=8)
        else:
            push2.light_bank_frame()

    # ── Scene composition ─────────────────────────────────────────

    def _render_frame(self, surf: pygame.Surface) -> None:
        surf.fill((0, 0, 0))
        dev_color = self._device_color()

        # In pattern mode swap the body for a sequencer overview —
        # tracks × all-steps grid with the active 8-step window
        # focused. Header still draws at the top so device + BPM +
        # status info stays consistent across modes.
        try:
            mode = self.app.push2_mode
        except Exception:
            mode = "control"
        if mode == "pattern":
            self._draw_header(surf, dev_color)
            self._draw_pattern_overview(surf, dev_color,
                                          top=50, height=110)
            return

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
            launch_first = lp * 8 + 1
            launch_last = min(launch_first + 7, total)
            page_seg = f"  ·  {lp + 1}/{lpc}" if lpc > 1 else ""
            seq = getattr(self.app, "_push2_pattern_sequencer",
                          lambda: None)()
            num_steps = (int(getattr(seq, "num_steps", 16))
                         if seq is not None else 16)
            num_pads = (int(getattr(seq, "num_pads", 6))
                        if seq is not None else 6)
            try:
                pad_off = int(self.app.push2_pattern_pad_offset)
            except Exception:
                pad_off = 0
            try:
                act_bank = int(self.app.push2_active_bank())
            except Exception:
                act_bank = 0
            playing = bool(getattr(seq, "playing", False)) if seq else False
            play_glyph = "▶" if playing else " "
            pad_seg = (f"  pads {pad_off + 1}-{min(pad_off + 6, num_pads)}/{num_pads}"
                       if num_pads > 6 else "")
            seq_text = (f"{play_glyph} steps {offset + 1}-"
                        f"{min(offset + 8, num_steps)}/{num_steps}{pad_seg}")
            # Bank letter in the device color so it reads as part of
            # the same theme as the active bank/pattern pads.
            bank_letter = chr(ord("A") + act_bank)
            txt = (f"BANK {bank_letter}  ·  PAT {cur_pat}/{total}  "
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

        # Control-mode status line — collapse all signals onto a single
        # row so nothing falls behind the oscilloscope (which starts at
        # y=50). One row at y=32 fits comfortably above the scope.
        try:
            page = self.app.push2_page
            page_count = self.app.push2_page_count()
        except Exception:
            page, page_count = 0, 1
        try:
            pad_page = self.app.push2_pad_page
            pad_pages = self.app.push2_pad_page_count()
        except Exception:
            pad_page, pad_pages = 0, 1
        try:
            dev_key_for_status = self.app.device_manager.focus_key
        except Exception:
            dev_key_for_status = None
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
        parts: list[str] = []
        # P-6 control mode — the 4 encoder pages map cleanly onto the
        # device's tab structure on Compa. Label the page by its
        # section so the user knows which knobs are visible at a
        # glance.
        p6_section = None
        if dev_key_for_status == "P-6" and page_count > 1:
            P6_SECTIONS = ["GRANULAR", "GRANULAR EXT",
                           "FILTER + ENV", "ENV EXT + MIXER",
                           "FX SENDS"]
            if 0 <= page < len(P6_SECTIONS):
                p6_section = P6_SECTIONS[page]
        if p6_section is not None:
            parts.append(f"{p6_section} ({page + 1}/{page_count})")
        elif page_count > 1:
            parts.append(f"CTRL {page + 1}/{page_count}")
        if pad_pages > 1:
            first = pad_page * 4
            if dev_key_for_status == "SP-404MKII":
                total = 10
            elif dev_key_for_status == "P-6":
                total = 8
            else:
                total = 4
            last = min(first + 3, total - 1)
            letters = f"{chr(ord('A') + first)}-{chr(ord('A') + last)}"
            parts.append(f"BANK {letters}")
        # SP-404 active effect + on/off — sit between BANK and PAT so
        # the user can read the FX state while looking at the bank
        # letter and pattern info.
        if dev_key_for_status == "SP-404MKII":
            try:
                from engine.sp404_effects import fx_name_for_tab
                bus = int(self.app.twister.active_bus)
                tab = self.app._sp404_active_bus_tab()
                fx_idx = int(self.app.live_cc.get(bus, {}).get(83, 0))
                fx_name = fx_name_for_tab(tab, fx_idx) or "—"
                fx_on = int(self.app.live_cc.get(bus, {}).get(19, 0)) >= 64
                state = "ON" if fx_on else "OFF"
                parts.append(f"FX {fx_name} · {state}")
            except Exception:
                pass
        if total_pats > 0:
            page_segment = f" ({lp + 1}/{lpc})" if lpc > 1 else ""
            parts.append(f"PAT {cur_pat}/{total_pats}{page_segment}")
        if parts:
            txt = "  ·  ".join(parts)
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

    # ── Pattern-mode sequencer overview ───────────────────────────

    def _draw_pattern_overview(self, surf, dev_color,
                                top: int, height: int) -> None:
        """Draw the full sequencer grid (tracks × all steps) on the
        Push 2 display in pattern mode. The active 8-step page is
        outlined in the device color; steps outside that window are
        rendered dimmer so the user can see the whole pattern's shape
        at a glance and know which slice the pads are editing.

        Each track row corresponds to one pad (P-6 = 6 rows, SP = 16
        rows — but we only show 8 at a time matching the visible pad
        window via pad_offset, with the remaining hidden tracks
        compressed into dim spacers). The playhead column gets a
        bright vertical highlight while the sequencer is playing."""
        pad_x = 14
        x0 = pad_x
        y0 = top + 4
        w = SURF_W - pad_x * 2
        # Reserve a strip at the bottom for the status line + page
        # indicator so neither gets clipped at the bottom of the
        # 160px display.
        status_h = 14
        h = height - 8 - status_h

        seq = None
        try:
            seq = self.app._push2_pattern_sequencer()
        except Exception:
            seq = None
        if seq is None:
            txt = self._font_small.render(
                "no sequencer", True, (120, 120, 120))
            surf.blit(txt, txt.get_rect(center=(SURF_W // 2,
                                                  top + height // 2)))
            return

        num_pads = max(1, int(getattr(seq, "num_pads", 8)))
        num_steps = max(1, int(getattr(seq, "num_steps", 16)))
        try:
            step_offset = int(self.app.push2_pattern_step_offset)
        except Exception:
            step_offset = 0
        try:
            pad_offset = int(self.app.push2_pattern_pad_offset)
        except Exception:
            pad_offset = 0
        try:
            current_step = int(getattr(seq, "current_step", -1))
            playing = bool(getattr(seq, "playing", False))
        except Exception:
            current_step, playing = -1, False

        # Show all rows up to a sane cap. The whole pattern fits in
        # one row of height/num_pads tall cells; the visible-pad
        # window (8 rows) is outlined to indicate which tracks the
        # pads are currently editing.
        rows_to_show = min(num_pads, 16)
        cell_h = max(4, h // rows_to_show)
        grid_h = cell_h * rows_to_show
        cell_w = max(4, w // num_steps)
        grid_w = cell_w * num_steps

        # Active 8-step window bounds (in cell space).
        active_step_lo = step_offset
        active_step_hi = min(num_steps, step_offset + 8)
        # Active 8-pad window bounds (in row space).
        active_pad_lo = pad_offset
        active_pad_hi = min(num_pads, pad_offset + 8)

        for p in range(rows_to_show):
            for s in range(num_steps):
                cx = x0 + s * cell_w
                cy = y0 + p * cell_h
                in_active_step = active_step_lo <= s < active_step_hi
                in_active_pad = active_pad_lo <= p < active_pad_hi
                in_focus = in_active_step and in_active_pad

                try:
                    is_on = bool(seq.grid[p][s].active)
                except Exception:
                    is_on = False

                if is_on:
                    # Focused active step → device color. Out-of-focus
                    # active steps → dimmer device color so the shape
                    # of the pattern still reads.
                    if in_focus:
                        col = dev_color
                    else:
                        col = (dev_color[0] // 2,
                               dev_color[1] // 2,
                               dev_color[2] // 2)
                else:
                    # Empty cell. In-focus = dim grid line, out =
                    # almost off so the focus window pops.
                    if in_focus:
                        col = (40, 40, 40)
                    else:
                        col = (20, 20, 20)
                pygame.draw.rect(surf, col,
                                 (cx + 1, cy + 1, cell_w - 2, cell_h - 2))

        # Playhead column highlight (only while playing).
        if playing and 0 <= current_step < num_steps:
            ph_x = x0 + current_step * cell_w
            pygame.draw.rect(surf, (240, 240, 240),
                             (ph_x, y0, cell_w, grid_h), 1)

        # Active-window outline.
        ow_x = x0 + active_step_lo * cell_w
        ow_y = y0 + active_pad_lo * cell_h
        ow_w = (active_step_hi - active_step_lo) * cell_w
        ow_h = (active_pad_hi - active_pad_lo) * cell_h
        pygame.draw.rect(surf, dev_color, (ow_x, ow_y, ow_w, ow_h), 1)

        # Pattern info status line at bottom-right.
        try:
            cur_pat = 0
            p6 = self.app.p6
            if p6 is not None:
                cur_pat = int(p6.state.active_pattern) + 1
            total_pats = int(self.app.push2_max_patterns())
        except Exception:
            cur_pat, total_pats = 0, 0
        try:
            from engine.push2 import SP_BANK_COLORS  # noqa
            active_bank = int(self.app.push2_active_bank())
            bank_letter = chr(ord("A") + active_bank)
        except Exception:
            bank_letter = "?"
        # Step page index (8 steps per page).
        step_page = (step_offset // 8) + 1
        step_pages = max(1, (num_steps + 7) // 8)
        # Current step-resolution note value (1/4, 1/8, 1/16, 1/32).
        try:
            note_val = seq.step_note_value()
        except Exception:
            note_val = "?"
        # Pad-page indicator for devices with more than 8 pads (SP).
        # P-6 has 6 pads so this is always 1/1 and we omit it.
        pad_page_str = ""
        if num_pads > 8:
            pad_total_pages = (num_pads + 7) // 8
            cur_pad_page = (pad_offset // 8) + 1
            pad_page_str = f"  ·  Pads {cur_pad_page}/{pad_total_pages}"
        info = (f"Bank {bank_letter}  ·  Pat {cur_pat}/{total_pats}  "
                f"·  Step {step_page}/{step_pages}  ·  {note_val}"
                f"{pad_page_str}")
        info_surf = self._font_tiny.render(info, True, dev_color)
        # Place inside the reserved status strip so nothing clips off
        # the bottom of the 160px display.
        info_y = top + height - info_surf.get_height() - 2
        surf.blit(info_surf,
                   (SURF_W - info_surf.get_width() - 14, info_y))

        # Confirm-to-clear overlay — when New has been pressed once
        # in the pattern tab, draw a pulsing red prompt across the
        # middle of the display so the second press feels intentional.
        try:
            pending = float(getattr(
                self.app, "_push2_new_confirm_until", 0.0))
        except Exception:
            pending = 0.0
        if pending and time.monotonic() < pending:
            self._draw_new_confirm_overlay(surf, top, height, pending)

    def _draw_new_confirm_overlay(self, surf, top: int, height: int,
                                    pending_until: float) -> None:
        """Pop a centered "Press New again to clear" prompt while the
        confirm window is open. Counts down the seconds remaining so
        the user sees the window."""
        try:
            remaining = pending_until - time.monotonic()
            if remaining <= 0:
                return
            secs = int(remaining) + 1
            # Cache fonts for the overlay (allocating SysFont every
            # frame churns GC).
            if not hasattr(self, "_new_confirm_fonts"):
                self._new_confirm_fonts = (
                    pygame.font.SysFont("dejavusans-bold", 28),
                    pygame.font.SysFont("dejavusans-bold", 16),
                )
            big_font, small_font = self._new_confirm_fonts
            red = (255, 60, 90)
            big_surf = big_font.render("CLEAR PATTERN?", True, red)
            sub_surf = small_font.render(
                f"press NEW again to confirm  ({secs})",
                True, (235, 235, 235))
            pad = 14
            box_w = max(big_surf.get_width(),
                         sub_surf.get_width()) + pad * 2
            box_h = (big_surf.get_height() + sub_surf.get_height()
                      + pad * 2 + 4)
            box_x = SURF_W // 2 - box_w // 2
            box_y = top + (height - box_h) // 2
            backdrop = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            backdrop.fill((0, 0, 0, 220))
            surf.blit(backdrop, (box_x, box_y))
            pygame.draw.rect(surf, red,
                              (box_x, box_y, box_w, box_h), 2,
                              border_radius=6)
            big_x = SURF_W // 2 - big_surf.get_width() // 2
            surf.blit(big_surf, (big_x, box_y + pad))
            sub_x = SURF_W // 2 - sub_surf.get_width() // 2
            sub_y = box_y + pad + big_surf.get_height() + 4
            surf.blit(sub_surf, (sub_x, sub_y))
        except Exception:
            pass

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
                    # Single polygon for the fill — replaces ~900
                    # per-pixel draw_line calls per frame at 20fps.
                    dim = (dev_color[0] // 5, dev_color[1] // 5,
                           dev_color[2] // 5)
                    poly = list(points)
                    poly.append((points[-1][0], cy))
                    poly.append((points[0][0], cy))
                    pygame.draw.polygon(surf, dim, poly)
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
                # Prefer the slot's `value_text` override (used by SP-404
                # Ctrl knobs to show formatted values like "0.23 sec",
                # "OFF", "1/4", etc.). Fall back to the raw int.
                text = slot.get("value_text")
                if not text:
                    text = f"{int(value)}"
                val_surf = self._font_tiny.render(text, True, dev_color)
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
        """SP-404 encoder row: Ctrl 1-6 of the active bus (labeled with
        the active effect's parameter names — Length / Speed / Loop SW
        for DJFX Looper, Depth / Rate / Filter / Pitch / Resonance for
        Downer, etc. — falls back to generic "Ctrl N" if the loaded
        effect isn't in the parameter table). Slot 7 is reserved.
        Slot 8 shows the active effect name + CC#83 index."""
        ctrl_ccs = [16, 17, 18, 80, 81, 82]
        try:
            bus = int(self.app.twister.active_bus)
        except Exception:
            bus = 0
        try:
            live = self.app.live_cc.get(bus, {}) or {}
        except Exception:
            live = {}

        # Resolve the active effect on this bus so we can label the
        # Ctrl knobs with its actual parameter names AND format their
        # values in the SP's units (sec, dB, Hz, OFF/ON, sync divs, …).
        try:
            from engine.sp404_effects import fx_name_for_tab
            from engine.sp404_effect_params import (ctrl_label,
                                                     format_value)
            tab = self.app._sp404_active_bus_tab()
            fx_idx = int(live.get(83, 0))
            fx_name = fx_name_for_tab(tab, fx_idx) or "—"
        except Exception:
            fx_name = "—"
            fx_idx = 0
            ctrl_label = lambda *_: ""    # noqa — unused on failure
            format_value = lambda *_: ""

        out = []
        for i, cc in enumerate(ctrl_ccs):
            try:
                name = ctrl_label(fx_name, i)
            except Exception:
                name = f"Ctrl {i + 1}"
            if not name:
                name = f"Ctrl {i + 1}"
            raw = live.get(cc)
            slot = {"name": name, "value": raw}
            if raw is not None:
                try:
                    slot["value_text"] = format_value(fx_name, i, int(raw))
                except Exception:
                    pass
            out.append(slot)
        # Encoder 7 — reserved.
        out.append({"name": "—", "value": None})
        # Encoder 8 — active effect name with CC#83 index as value.
        out.append({"name": f"FX: {fx_name}", "value": fx_idx})
        return out
