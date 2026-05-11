"""P-6 Session Screen — dashboard with live status, notes.

Backup/restore was moved out of this screen to Files → Device → P-6
(see engine/p6_librarian.py + ui/screens/transfer_screen.py).
"""

import json
import math
import os
import time
import pygame
from .. import theme
from ..components.text_area import TextArea


class P6SessionScreen:
    """Main dashboard: transport, BPM, pattern, levels, and session notes."""

    def __init__(self, app):
        self.app = app
        self._meter_decay = 0.92
        self._disp_peak_l = 0.0
        self._disp_peak_r = 0.0

        # Double-tap detection
        self._last_card_tap = 0.0
        self._last_card_name = ""
        self._double_tap_ms = 400  # ms window for double tap

        # Session notes
        notes_dir = app.config.get("P6_SESSIONS_DIR",
                                    os.path.join(os.path.dirname(os.path.dirname(
                                        os.path.abspath(__file__))), "sessions"))
        os.makedirs(notes_dir, exist_ok=True)
        self._notes_path = os.path.join(notes_dir, "notes.json")

        # Text area in right column
        self._text_area = TextArea(
            pygame.Rect(420, 100, 360, 320),
            text=self._load_notes(),
        )

        self._last_save = time.monotonic()
        self._save_interval = 2.0

    @property
    def wants_keyboard(self) -> bool:
        """Tell the app to skip global shortcuts when text area is focused."""
        return self._text_area.focused

    def _load_notes(self) -> str:
        if os.path.exists(self._notes_path):
            try:
                with open(self._notes_path) as f:
                    data = json.load(f)
                    return data.get("text", "")
            except Exception:
                pass
        return ""

    def _save_notes(self):
        try:
            with open(self._notes_path, "w") as f:
                json.dump({"text": self._text_area.text}, f)
        except Exception:
            pass

    def on_enter(self):
        # Single device auto-expand: skip card view, go straight to workspace
        connected = self.app.device_manager.connected
        if len(connected) == 1 and not getattr(self, "_auto_expanded", False):
            self._auto_expanded = True
            dev_name = list(connected.keys())[0]
            self.app.switch_screen("device_workspace", context={"device": dev_name})
            return

        # Start audio monitoring so meters work on this screen
        if not self.app.recorder._monitoring:
            # If recorder doesn't have a valid device, try the focused device
            if not self.app.recorder.available:
                dev = self.app.device
                if dev and dev.audio_hint:
                    self.app.recorder.switch_device(dev.audio_hint)
            self.app.recorder.start_monitoring()

    def on_exit(self):
        self._save_notes()
        # Stop monitoring if not recording — but ALSO leave it alone when
        # a MON route is active, otherwise navigating to another screen
        # silently tears down the route. The next screen's on_enter would
        # then call switch_device(focus.audio_hint), and if focus equals
        # the MON destination (e.g. SP), the recorder ends up reading the
        # same device it's writing to → instant feedback chamber when the
        # destination has External Source / loopback enabled.
        if (not self.app.recorder.is_recording
                and not getattr(self.app, "_monitor_source", "")):
            self.app.recorder.stop_monitoring()

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Card transport buttons (priority over card tap)
            if hasattr(self, "_card_buttons"):
                for rect, dev_name, action in self._card_buttons:
                    if rect.collidepoint(mx, my):
                        self._handle_card_button(dev_name, action)
                        return

            # Tap card: single = focus + monitor, double = open workspace
            if hasattr(self, "_card_rects"):
                import time
                now = time.monotonic()
                for rect, dev_name in self._card_rects:
                    if rect.collidepoint(mx, my):
                        if (dev_name == self._last_card_name and
                                (now - self._last_card_tap) * 1000 < self._double_tap_ms):
                            # Double tap → open workspace
                            self.app.switch_screen("device_workspace",
                                                   context={"device": dev_name})
                        else:
                            # Single tap → focus + start monitoring
                            self.app.switch_focus(dev_name)
                        self._last_card_tap = now
                        self._last_card_name = dev_name
                        return

    def _handle_card_button(self, dev_name: str, action: str):
        """Handle transport button taps on device cards."""
        if action == "toggle_link":
            cur = self.app.is_device_clock_enabled(dev_name)
            self.app.set_device_clock_enabled(dev_name, not cur)
            return
        if action == "toggle_rx":
            cur = getattr(self.app, "_link_rx_target", "")
            if cur == dev_name:
                self.app.stop_link_rx()
            else:
                self.app.start_link_rx(dev_name)
            return
        if action == "set_monitor":
            # MON on card X = "send X's audio out so I can hear it on
            # whichever other device my headphones are plugged into."
            # Compa picks the first OTHER connected device with audio
            # as the destination. Toggle off by pressing MON on the
            # same card again.
            cur_src = getattr(self.app, "_monitor_source", "")
            if cur_src == dev_name:
                self.app.stop_monitor_route()
                return
            connected = self.app.device_manager.connected
            src = connected.get(dev_name)
            if not src or not getattr(src, "audio_hint", None):
                print(f"Monitor: {dev_name} has no audio input", flush=True)
                return
            dest = None
            for k, p in connected.items():
                if k != dev_name and getattr(p, "audio_hint", None):
                    dest = k
                    break
            if not dest:
                print(f"Monitor: no other device to route to", flush=True)
                return
            # Determine the destination's preferred rate so we can bind
            # the source at the same rate — matching rates on both sides
            # of the ring buffer means no resampling is needed.
            dst_rate = 0
            dst_idx = None
            try:
                from engine.audio_router import find_device_index
                import sounddevice as sd
                dst_profile = connected.get(dest)
                dst_idx = find_device_index(dst_profile.audio_hint) if dst_profile else None
                if dst_idx is not None:
                    info = sd.query_devices(dst_idx)
                    dst_rate = int(info.get("default_samplerate", 0))
            except Exception as e:
                print(f"Monitor: rate probe failed: {e}", flush=True)
            # Release the destination device from any other route (RX
            # or radio) that's currently holding its OutputStream.
            if dst_idx is not None:
                self.app._release_device(dst_idx)
            # CRITICAL: close any existing monitor OUTPUT stream BEFORE
            # touching the recorder INPUT. Otherwise — if you're swapping
            # MON from "X→Y" to "Y→X" — there's a window where the input
            # is the new source (Y) but the output is still on the old
            # destination (also Y), creating a digital feedback loop the
            # moment SP-404 (or any device with USB main-mix loopback)
            # routes USB-IN back to USB-OUT.
            self.app.recorder.stop_monitor_output()
            # Bind recorder to source at the matching rate. Clear
            # _monitor_source first so switch_device isn't blocked by the
            # switch_focus guard.
            self.app._monitor_source = ""
            self.app.recorder.switch_device(src.audio_hint, preferred_rate=dst_rate)
            if not self.app.recorder._monitoring:
                self.app.recorder.start_monitoring()
            self.app.set_monitor_output(dest)
            self.app.route_monitor(dev_name)
            self.app._monitor_source = dev_name
            return

        midi = self.app._midi_connections.get(dev_name)
        if not midi:
            return
        if action == "play":
            midi.send_start()
        elif action == "stop":
            midi.send_stop()
            if self.app.recorder.is_recording:
                self.app.recorder.stop_recording()
        elif action == "recall":
            path = self.app.recorder.recall_buffer()
            if path:
                print(f"Recall saved: {path}", flush=True)
            return
        elif action == "rec":
            if self.app.recorder.is_recording:
                self.app.recorder.stop_recording()
            else:
                # Switch audio source to this device and start recording.
                # Note: any default pre-roll set in settings is applied
                # automatically inside start_recording.
                dev = self.app.device_manager.connected.get(dev_name)
                if dev and dev.audio_hint:
                    self.app.recorder.switch_device(dev.audio_hint)
                meta = {"bpm_at_record": midi.state.bpm,
                        "pattern_at_record": midi.state.active_pattern}
                self.app.recorder.start_recording(metadata=meta)
        elif action == "recall_rec":
            # +REC: dump entire recall buffer + keep recording in same file.
            # No-op if already recording (you'd be amending mid-take which
            # would give you nothing useful — STOP first, then +REC).
            if self.app.recorder.is_recording:
                return
            dev = self.app.device_manager.connected.get(dev_name)
            if dev and dev.audio_hint:
                self.app.recorder.switch_device(dev.audio_hint)
            meta = {"bpm_at_record": midi.state.bpm,
                    "pattern_at_record": midi.state.active_pattern,
                    "started_via": "recall_continue"}
            path = self.app.recorder.recall_and_continue(metadata=meta)
            if path:
                print(f"+REC: recall→continue → {path}", flush=True)

    def update(self):
        # Smooth level meters
        peak_l, peak_r = (0.0, 0.0)
        if self.app.recorder.available:
            peak_l, peak_r = self.app.recorder.peak_levels
        self._disp_peak_l = max(peak_l, self._disp_peak_l * self._meter_decay)
        self._disp_peak_r = max(peak_r, self._disp_peak_r * self._meter_decay)

        # Auto-save notes
        if self._text_area.changed:
            self._last_save = time.monotonic()
        now = time.monotonic()
        if now - self._last_save < self._save_interval + 0.5 and now - self._last_save > self._save_interval:
            self._save_notes()

    # ── ASCII art device names (playing card style) ────────────────
    _COMPA_LOGO = [
        "  ___ ___  __  __ ___  _   ",
        " / __/ _ \\|  \\/  | _ \\/ \\  ",
        "| (_| (_) | |\\/| |  _/ _ \\ ",
        " \\___\\___/|_|  |_|_|/_/ \\_\\",
    ]

    # Device → theme color name for card accent
    _DEVICE_COLORS = {
        "P-6": (255, 230, 0),       # Yellow
        "SP-404MKII": (0, 200, 180), # Teal
        "Force": (220, 50, 50),      # Red
    }

    def draw(self, surface: pygame.Surface):
        f_title = theme.font("title")
        f_hero = theme.font("hero")
        f_large = theme.font("large")
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")
        f_mono = theme.font("mono")

        # ── COMPA ASCII logo (top left) ──────────────────────────────
        for i, line in enumerate(self._COMPA_LOGO):
            surf = f_mono.render(line, True, theme.ACCENT)
            surface.blit(surf, (12, 6 + i * 14))

        # Version + subtitle
        surf = f_tiny.render("v1.0", True, theme.TEXT_DIM)
        surface.blit(surf, (240, 10))
        surf = f_tiny.render("raredata.net", True, theme.TEXT_DIM)
        surface.blit(surf, (240, 24))

        # ── Ableton Link indicator ─────────────────────────────────────
        link = getattr(self.app, "link", None)
        if link is not None and link.available:
            peers = link.num_peers
            tempo = link.tempo
            # Heartbeat: dot pulses bright for ~1s after a Link callback
            # (tempo or peers change), then settles to steady state.
            since = link.seconds_since_activity
            if since < 0.3:
                # Fresh activity — flash bright green
                dot_color = theme.GREEN
                dot_radius = 6
            elif peers > 0:
                # Steady connected state — solid green
                dot_color = theme.GREEN
                dot_radius = 4
            else:
                # Alone, no recent activity — dim
                dot_color = theme.TEXT_DIM
                dot_radius = 4
            label_color = theme.TEXT if peers > 0 else theme.TEXT_DIM
            link_x = 320
            link_y = 12
            pygame.draw.circle(surface, dot_color, (link_x, link_y + 6),
                               dot_radius)
            # "LINK" label + peer count
            if peers > 0:
                txt = (f"LINK · {peers} peer{'s' if peers != 1 else ''} · "
                       f"{tempo:.1f} BPM")
            else:
                txt = "LINK · alone"
            surf = f_tiny.render(txt, True, label_color)
            surface.blit(surf, (link_x + 10, link_y))

        # ── Network MIDI indicator ───────────────────────────────────
        nm = getattr(self.app, "network_midi", None)
        if nm is not None and nm.enabled:
            nm_peers = nm.peer_count
            if nm_peers > 0:
                nm_dot = theme.GREEN
                nm_label_color = theme.TEXT
                nm_txt = (f"NET MIDI · {nm_peers} peer"
                          f"{'s' if nm_peers != 1 else ''}")
            else:
                nm_dot = theme.TEXT_DIM
                nm_label_color = theme.TEXT_DIM
                nm_txt = "NET MIDI · ready"
            nm_x = 320
            nm_y = 30
            pygame.draw.circle(surface, nm_dot, (nm_x, nm_y + 6), 4)
            surf = f_tiny.render(nm_txt, True, nm_label_color)
            surface.blit(surf, (nm_x + 10, nm_y))

        # Reset card rects each frame
        self._card_buttons = []
        self._card_rects = []  # (rect, short_name) for tap-to-focus

        # ── Device playing cards (horizontal, max 3) ─────────────────
        connected = self.app.device_manager.connected
        focus_key = self.app.device_manager.focus_key
        devices = list(connected.items())[:3]  # Max 3 cards
        num_cards = len(devices)

        cards_y = 68
        cards_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50
        card_h = cards_bottom - cards_y
        card_gap = 10
        total_w = theme.SCREEN_WIDTH - 24
        card_w = (total_w - (num_cards - 1) * card_gap) // max(1, num_cards)

        for idx, (short_name, profile) in enumerate(devices):
            midi = self.app._midi_connections.get(short_name)
            is_focused = (short_name == focus_key)
            is_connected = midi and midi.connected
            device_color = theme.get_device_color(short_name)

            card_x = 12 + idx * (card_w + card_gap)
            card_rect = pygame.Rect(card_x, cards_y, card_w, card_h)

            # Store rect for tap-to-focus
            self._card_rects.append((card_rect, short_name))

            # Card background + border
            pygame.draw.rect(surface, theme.BG_PANEL, card_rect, border_radius=10)
            border_color = device_color if is_focused else theme.BORDER
            border_w = 2 if is_focused else 1
            pygame.draw.rect(surface, border_color, card_rect, border_w, border_radius=10)

            # Top accent stripe
            stripe = pygame.Rect(card_x + 1, cards_y + 1, card_w - 2, 4)
            pygame.draw.rect(surface, device_color, stripe,
                           border_radius=0)

            cx = card_x + 10
            cy = cards_y + 10
            inner_w = card_w - 20

            # ── Row 1: Device name + FOCUS tag ───────────────────────
            surf = f_large.render(short_name, True, device_color)
            surface.blit(surf, (cx, cy))
            if is_focused:
                tag_x = card_rect.right - 56
                tag_rect = pygame.Rect(tag_x, cy + 2, 46, 16)
                pygame.draw.rect(surface, device_color, tag_rect, border_radius=3)
                surf2 = f_tiny.render("FOCUS", True, theme.BG)
                surface.blit(surf2, surf2.get_rect(center=tag_rect.center))
            cy += 20

            # ── Row 1b: Manufacturer ────────────────────────────────
            mfr = profile.name.split()[0] if " " in profile.name else ""
            if mfr:
                surf = f_tiny.render(mfr, True, theme.TEXT_DIM)
                surface.blit(surf, (cx, cy))
            cy += 14

            # ── Row 2: Connection + audio specs + headphone button ────
            is_monitor_out = (short_name == getattr(self.app, "_monitor_source", ""))
            if is_connected:
                pygame.draw.circle(surface, theme.GREEN, (cx + 4, cy + 5), 3)
            else:
                pygame.draw.circle(surface, theme.RED, (cx + 4, cy + 5), 3, 1)
            audio_info = f"{profile.audio_in_channels}in/{profile.audio_out_channels}out"
            rates = "/".join(f"{r//1000}k" for r in profile.supported_sample_rates)
            midi_str = "MIDI" if is_connected else ""
            surf = f_tiny.render(f"{audio_info} {rates} {midi_str}", True, theme.TEXT_DIM)
            surface.blit(surf, (cx + 12, cy))

            # Headphone/monitor output button (right side of row 2)
            # Note: audio pass-through works best on Pi 4/5
            hp_rect = pygame.Rect(card_rect.right - 62, cy - 2, 52, 16)
            if is_monitor_out:
                pygame.draw.rect(surface, device_color, hp_rect, border_radius=3)
                surf = f_tiny.render("MON", True, theme.BG)
            else:
                pygame.draw.rect(surface, theme.BUTTON_BG, hp_rect, border_radius=3)
                pygame.draw.rect(surface, theme.BORDER, hp_rect, 1, border_radius=3)
                surf = f_tiny.render("MON", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=hp_rect.center))
            self._card_buttons.append((hp_rect, short_name, "set_monitor"))

            # RX — receive Link Audio (e.g. from Live) and play it on
            # this device's USB output. Mutex with MON for the same
            # device since both want the same OutputStream.
            is_link_rx = (short_name == getattr(self.app, "_link_rx_target", ""))
            rx_rect = pygame.Rect(card_rect.right - 174, cy - 2, 52, 16)
            if is_link_rx:
                pygame.draw.rect(surface, theme.ACCENT, rx_rect, border_radius=3)
                surf = f_tiny.render("RX", True, theme.BG)
            else:
                pygame.draw.rect(surface, theme.BUTTON_BG, rx_rect, border_radius=3)
                pygame.draw.rect(surface, theme.BORDER, rx_rect, 1, border_radius=3)
                surf = f_tiny.render("RX", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=rx_rect.center))
            self._card_buttons.append((rx_rect, short_name, "toggle_rx"))

            # LINK toggle (left of OUT) — drives whether master_clock
            # pushes 0xF8 ticks to this device. ON = device follows
            # Compa/Link tempo; OFF = device runs on its own clock.
            link_on = self.app.is_device_clock_enabled(short_name)
            link_rect = pygame.Rect(card_rect.right - 118, cy - 2, 52, 16)
            if link_on:
                pygame.draw.rect(surface, device_color, link_rect, border_radius=3)
                surf = f_tiny.render("LINK", True, theme.BG)
            else:
                pygame.draw.rect(surface, theme.BUTTON_BG, link_rect, border_radius=3)
                pygame.draw.rect(surface, theme.BORDER, link_rect, 1, border_radius=3)
                surf = f_tiny.render("LINK", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(center=link_rect.center))
            self._card_buttons.append((link_rect, short_name, "toggle_link"))

            cy += 14

            # ── Row 3: Audio level meters (only for monitored device) ──
            meter_w = inner_w - 4
            meter_h = 6
            # Check if this device is the one being monitored
            rec_hint = self.app.recorder._device_hint
            # Check if this device is being monitored
            # Compare the recorder's actual device name against the profile
            rec_dev_name = self.app.recorder.device_name
            is_monitored = (self.app.recorder._monitoring and
                           rec_dev_name and profile.audio_hint and
                           (profile.audio_hint in rec_dev_name or
                            rec_dev_name in profile.audio_hint or
                            profile.short_name in rec_dev_name))
            peak_l = self._disp_peak_l if is_monitored else 0.0
            peak_r = self._disp_peak_r if is_monitored else 0.0

            # L meter
            pygame.draw.rect(surface, theme.WAVEFORM_BG,
                            (cx, cy, meter_w, meter_h), border_radius=2)
            fill = int(meter_w * min(1.0, peak_l))
            if fill > 0:
                mc = theme.RED if peak_l > 0.9 else (
                    theme.YELLOW if peak_l > 0.7 else device_color)
                pygame.draw.rect(surface, mc,
                                (cx, cy, fill, meter_h), border_radius=2)
            if is_monitored:
                surf = f_tiny.render("L", True, theme.TEXT_DIM)
                surface.blit(surf, (cx + meter_w + 2, cy - 1))
            cy += meter_h + 2
            # R meter
            pygame.draw.rect(surface, theme.WAVEFORM_BG,
                            (cx, cy, meter_w, meter_h), border_radius=2)
            fill = int(meter_w * min(1.0, peak_r))
            if fill > 0:
                mc = theme.RED if peak_r > 0.9 else (
                    theme.YELLOW if peak_r > 0.7 else device_color)
                pygame.draw.rect(surface, mc,
                                (cx, cy, fill, meter_h), border_radius=2)
            if is_monitored:
                surf = f_tiny.render("R", True, theme.TEXT_DIM)
                surface.blit(surf, (cx + meter_w + 2, cy - 1))
            elif is_connected:
                # Show "tap to monitor" hint on inactive cards
                surf = f_tiny.render("tap card to monitor", True, theme.TEXT_DIM)
                surface.blit(surf, (cx, cy - meter_h))
            cy += meter_h + 6

            # ── Row 4: BPM + Pattern ─────────────────────────────────
            if midi:
                # When LINK is on for this device, master_clock is the
                # authoritative tempo. When LINK is off, the device
                # runs on its own clock so show what it echoes back
                # (state.bpm), falling back to master_clock if it
                # isn't echoing yet.
                mc = getattr(self.app, "master_clock", None)
                if self.app.is_device_clock_enabled(short_name):
                    bpm = mc.get_bpm() if mc is not None else midi.state.bpm
                else:
                    bpm = midi.state.bpm or (mc.get_bpm() if mc is not None else 120.0)
                surf = f_hero.render(f"{bpm:.0f}", True, device_color)
                surface.blit(surf, (cx, cy - 4))
                bpm_w = surf.get_width()
                surf = f_tiny.render("BPM", True, theme.TEXT_DIM)
                surface.blit(surf, (cx + bpm_w + 2, cy + 12))

                pat = midi.state.active_pattern + 1
                pat_max = getattr(profile, "pattern_count", 0)
                if pat_max > 0:
                    pat_text = f"Ptn {pat}/{pat_max}"
                    surf = f_small.render(pat_text, True, device_color)
                    surface.blit(surf, (card_rect.right - surf.get_width() - 14, cy + 6))
            else:
                surf = f_large.render("---", True, theme.TEXT_DIM)
                surface.blit(surf, (cx, cy))
            cy += 38

            # ── Row 5: Transport buttons (PLAY / REC / +REC / STOP / RCL) ──
            #   +REC = recall-and-continue: dump the entire current recall
            #   buffer to a new WAV, then keep recording into the same file.
            #   The "I forgot to hit record" rescue button.
            if midi:
                # 5 columns, 4 gaps. btn_w capped so labels stay readable.
                btn_h = 24
                gap = 4
                btn_w = min(50, max(36, (inner_w - 16 - 16 - 4 * gap) // 5))

                # Play/Stop state indicator
                if midi.state.playing:
                    pygame.draw.circle(surface, theme.GREEN, (cx + 5, cy + btn_h // 2), 4)
                else:
                    pygame.draw.circle(surface, theme.TEXT_DIM, (cx + 5, cy + btn_h // 2), 3, 1)

                # Transport buttons
                bx = cx + 16
                is_rec = self.app.recorder.is_recording

                play_rect = pygame.Rect(bx + 0 * (btn_w + gap), cy, btn_w, btn_h)
                play_bg = theme.GREEN if midi.state.playing else theme.BUTTON_BG
                pygame.draw.rect(surface, play_bg, play_rect, border_radius=4)
                surf = f_tiny.render("PLAY", True, theme.BG if midi.state.playing else theme.TEXT)
                surface.blit(surf, surf.get_rect(center=play_rect.center))

                rec_rect = pygame.Rect(bx + 1 * (btn_w + gap), cy, btn_w, btn_h)
                rec_bg = theme.RED if is_rec else theme.BUTTON_BG
                pygame.draw.rect(surface, rec_bg, rec_rect, border_radius=4)
                surf = f_tiny.render("REC", True, theme.TEXT_BRIGHT if is_rec else theme.TEXT)
                surface.blit(surf, surf.get_rect(center=rec_rect.center))

                # +REC = recall + continue. Dim while a take's already running.
                plus_rec_rect = pygame.Rect(bx + 2 * (btn_w + gap), cy, btn_w, btn_h)
                plus_bg = theme.BUTTON_BG if is_rec else (140, 35, 35)
                plus_tc = theme.TEXT_DIM if is_rec else theme.TEXT_BRIGHT
                pygame.draw.rect(surface, plus_bg, plus_rect := plus_rec_rect, border_radius=4)
                surf = f_tiny.render("+REC", True, plus_tc)
                surface.blit(surf, surf.get_rect(center=plus_rec_rect.center))

                stop_rect = pygame.Rect(bx + 3 * (btn_w + gap), cy, btn_w, btn_h)
                pygame.draw.rect(surface, theme.BUTTON_BG, stop_rect, border_radius=4)
                surf = f_tiny.render("STOP", True, theme.TEXT)
                surface.blit(surf, surf.get_rect(center=stop_rect.center))

                # RECALL button (save buffer only, no continue)
                recall_rect = pygame.Rect(bx + 4 * (btn_w + gap), cy, btn_w, btn_h)
                recall_secs = self.app.recorder.recall_seconds_available
                recall_bg = theme.ACCENT if recall_secs >= 1 else theme.BUTTON_BG
                recall_tc = theme.BG if recall_secs >= 1 else theme.TEXT_DIM
                pygame.draw.rect(surface, recall_bg, recall_rect, border_radius=4)
                surf = f_tiny.render(f"RCL {int(recall_secs)}s", True, recall_tc)
                surface.blit(surf, surf.get_rect(center=recall_rect.center))

                # Store button rects for click handling
                self._card_buttons.append((play_rect, short_name, "play"))
                self._card_buttons.append((rec_rect, short_name, "rec"))
                self._card_buttons.append((plus_rec_rect, short_name, "recall_rec"))
                self._card_buttons.append((stop_rect, short_name, "stop"))
                self._card_buttons.append((recall_rect, short_name, "recall"))

            cy += 30

            # ── Row 6: Live waveform (monitored card) or static info ──
            wave_h = max(20, card_rect.bottom - cy - 26)
            wave_rect = pygame.Rect(cx, cy, inner_w, wave_h)
            pygame.draw.rect(surface, (15, 15, 22), wave_rect, border_radius=3)

            if is_monitored:
                # Oscilloscope — filled waveform matching workspace style
                import numpy as np

                rec = self.app.recorder
                buf = rec._recall_buf
                wpos = rec._recall_write_pos
                display_frames = min(2048, len(buf))

                # Center + grid lines
                center_y = wave_rect.centery
                half_h = (wave_rect.height - 8) // 2
                pygame.draw.line(surface, (22, 22, 32),
                                (wave_rect.x + 2, center_y),
                                (wave_rect.right - 2, center_y))

                if wpos >= display_frames:
                    recent = buf[wpos - display_frames:wpos]
                else:
                    recent = np.concatenate([buf[-(display_frames - wpos):], buf[:wpos]])

                if len(recent) > 0 and float(np.max(np.abs(recent))) > 0.001:
                    mono = recent.mean(axis=1) if recent.ndim > 1 else recent

                    w = wave_rect.width - 4
                    step = max(1, len(mono) // w)
                    points = []
                    dc = device_color

                    for px in range(w):
                        si = px * step
                        if si < len(mono):
                            val = max(-1.0, min(1.0, float(mono[si]) * 3.0))
                            py = center_y - int(val * half_h)
                            points.append((wave_rect.x + 2 + px, py))

                    if len(points) > 1:
                        # Filled waveform — single polygon spanning
                        # the wave shape and back along the centerline.
                        # Replaces ~600 per-pixel draw_line calls per
                        # card per frame (the old loop pegged the Pi
                        # at 100%+ CPU on the multi-card session view).
                        dim = (dc[0] // 5, dc[1] // 5, dc[2] // 5)
                        poly = list(points)
                        poly.append((points[-1][0], center_y))
                        poly.append((points[0][0], center_y))
                        pygame.draw.polygon(surface, dim, poly)
                        pygame.draw.lines(surface, dc, False, points, 2)
                else:
                    pygame.draw.line(surface, (35, 35, 48),
                                   (wave_rect.x + 2, center_y),
                                   (wave_rect.right - 2, center_y))

                # Status label
                if self.app.recorder.is_recording:
                    dur = self.app.recorder.duration
                    surf = f_tiny.render(f"REC {dur:.0f}s", True, theme.RED)
                else:
                    recall = self.app.recorder.recall_seconds_available
                    surf = f_tiny.render(f"buf:{int(recall)}s", True, device_color)
                surface.blit(surf, (cx + 2, cy + 1))
            else:
                # Not monitored — show device info or "tap to monitor"
                pygame.draw.line(surface, theme.BORDER,
                                (wave_rect.x + 2, wave_rect.centery),
                                (wave_rect.right - 2, wave_rect.centery))
                surf = f_tiny.render("tap card to monitor", True, theme.TEXT_DIM)
                surface.blit(surf, surf.get_rect(center=wave_rect.center))

            cy += wave_h + 4

            # ── Feature badges (bottom of card) ──────────────────────
            badge_y = card_rect.bottom - 20
            bx = cx
            badges = []
            if getattr(profile, "has_granular", False):
                badges.append("GRAN")
            if getattr(profile, "has_effects", False):
                badges.append("FX")
            if getattr(profile, "has_looper", False):
                badges.append("LOOP")
            if getattr(profile, "has_dj_mode", False):
                badges.append("DJ")

            for label in badges:
                bw = len(label) * 7 + 8
                br = pygame.Rect(bx, badge_y, bw, 14)
                pygame.draw.rect(surface, device_color, br, border_radius=3)
                surf = f_tiny.render(label, True, theme.BG)
                surface.blit(surf, surf.get_rect(center=br.center))
                bx += bw + 3

        # ── No devices fallback ──────────────────────────────────────
        if not devices:
            surf = f_large.render("No devices connected", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=160))
            surf = f_small.render("Plug in a USB device to get started", True, theme.TEXT_DIM)
            surface.blit(surf, surf.get_rect(centerx=theme.SCREEN_WIDTH // 2, top=200))

        # ── Bottom info strip ────────────────────────────────────────
        mc = getattr(self.app, "master_clock", None)
        bpm = mc.get_bpm() if mc is not None else (self.app.p6.state.bpm if self.app.p6 else 120.0)
        content_bottom = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - 50
        col_y = content_bottom

        # ── Bottom info strip ─────────────────────────────────────────
        info_y = content_bottom + 4
        left_x = 16

        # Resample calc (compact inline)
        mc = getattr(self.app, "master_clock", None)
        bpm = mc.get_bpm() if mc is not None else (self.app.p6.state.bpm if self.app.p6 else 120.0)
        from engine.p6_presets import resample_calc
        calc = resample_calc(bpm)
        surf = f_tiny.render(f"RESAMPLE @ {bpm:.0f}", True, theme.TEXT_DIM)
        surface.blit(surf, (left_x, info_y))
        rx = left_x + surf.get_width() + 12
        for row in calc[:2]:  # Show first 2 rows only
            bars = row["bars"]
            secs = row["seconds"]
            fits = row["fits"]
            ok_rates = [f"{r//1000}k" for r, ok in fits.items() if ok]
            if len(ok_rates) == 4:
                text = f"{bars}bar={secs:.1f}s OK"
                color = theme.GREEN
            else:
                text = f"{bars}bar={secs:.1f}s {' '.join(ok_rates[:2])}"
                color = theme.TEXT_DIM
            surf = f_tiny.render(text, True, color)
            surface.blit(surf, (rx, info_y))
            rx += surf.get_width() + 16

        # Recording + buffer status
        info_y += 16
        if self.app.recorder.is_recording:
            dur = self.app.recorder.duration
            src = self.app.recorder.device_name
            pygame.draw.circle(surface, theme.RED, (left_x + 5, info_y + 6), 4)
            surf = f_small.render(f"REC {dur:.0f}s [{src}]", True, theme.RED)
            surface.blit(surf, (left_x + 14, info_y))
        else:
            if self.app.auto_record:
                surf = f_tiny.render("AUTO-REC ON", True, theme.GREEN)
                surface.blit(surf, (left_x, info_y))
            recall = self.app.recorder.recall_seconds_available
            if recall > 0:
                surf = f_tiny.render(f"Buffer: {int(recall)}s", True, theme.ACCENT)
                surface.blit(surf, (left_x + 100, info_y))

        # Level meters (inline, right side of bottom strip)
        meter_x = theme.SCREEN_WIDTH // 2 + 40
        meter_w = theme.SCREEN_WIDTH - meter_x - 20
        theme.draw_meter(surface, meter_x, info_y - 12, meter_w, 8,
                        self._disp_peak_l, "L")
        theme.draw_meter(surface, meter_x, info_y + 2, meter_w, 8,
                        self._disp_peak_r, "R")

        # Collapsed notepad (tap to expand — not implemented yet, just shows hint)
        notes_text = self._text_area.text.strip()
        if notes_text:
            info_y += 18
            preview = notes_text[:60].replace("\n", " ")
            surf = f_tiny.render(f"Notes: {preview}...", True, theme.TEXT_DIM)
            surface.blit(surf, (left_x, info_y))

    def _draw_meter(self, surface, x, y, w, h, level, label):
        f = theme.font("small")
        lbl = f.render(label, True, theme.TEXT_DIM)
        surface.blit(lbl, (x, y))
        bar_x = x + 20
        bar_w = w - 20
        pygame.draw.rect(surface, theme.WAVEFORM_BG,
                        (bar_x, y, bar_w, h), border_radius=2)
        fill_w = int(bar_w * min(1.0, level))
        if fill_w > 0:
            color = theme.RED if level > 0.9 else theme.YELLOW if level > 0.7 else theme.GREEN
            pygame.draw.rect(surface, color,
                           (bar_x, y, fill_w, h), border_radius=2)
