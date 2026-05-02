"""P-6 Settings Screen — configurable options with toggles and adjustments."""

import os
import subprocess
import pygame
from .. import theme

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class P6SettingsScreen:
    """Scrollable settings list with toggles, adjustments, and action buttons."""

    # Pixels of motion (between mouse-down and -up) under which a
    # press counts as a tap; over this it's a drag/scroll and the
    # tap-action is suppressed. Tuned so resting-finger jitter
    # doesn't trip it but a deliberate drag-to-scroll does.
    TAP_DRAG_THRESHOLD = 8

    def __init__(self, app):
        self.app = app
        self._scroll_y = 0
        self._row_height = 36
        self._rows = []  # rebuilt each frame
        self._content_height = 0
        self._update_status = ""
        self._button_flash: dict | None = None
        # Drag-vs-tap state for the touchscreen scroll/tap fix.
        # _press_pos: where MOUSEBUTTONDOWN landed (None when no press).
        # _press_scroll_y: scroll offset at press time, so we can
        #                  compute how far the user has dragged.
        # _is_dragging: latches True as soon as the user moves past
        #               TAP_DRAG_THRESHOLD; subsequent MOUSEBUTTONUP
        #               then suppresses the tap-action firing.
        self._press_pos: tuple[int, int] | None = None
        self._press_scroll_y: int = 0
        self._is_dragging: bool = False

    def on_enter(self):
        self._scroll_y = 0
        self._press_pos = None
        self._is_dragging = False

    def on_exit(self):
        pass

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_threshold(self) -> int:
        """Get recording threshold level from config."""
        return int(self.app.config.get("REC_THRESHOLD", "30"))

    def _set_threshold(self, val: int):
        from ui.p6_app import save_config_key
        val = max(0, min(100, val))
        self.app.config["REC_THRESHOLD"] = str(val)
        save_config_key("REC_THRESHOLD", str(val))

    def _get_audio_device(self) -> str:
        return self.app.config.get("AUDIO_DEVICE_HINT", "default")

    def _get_resolution(self) -> str:
        return f"{theme.SCREEN_WIDTH}x{theme.SCREEN_HEIGHT}"

    def _get_p6_status(self) -> str:
        if self.app.p6 and self.app.p6.connected:
            return "Connected"
        return "Not found"

    def _toggle_mouse_mode(self):
        from ui.p6_app import save_config_key
        self.app.mouse_mode = not self.app.mouse_mode
        pygame.mouse.set_visible(self.app.mouse_mode)
        save_config_key("MOUSE_MODE", "1" if self.app.mouse_mode else "0")

    def _toggle_auto_record(self):
        from ui.p6_app import save_config_key
        self.app.auto_record = not self.app.auto_record
        save_config_key("P6_AUTO_RECORD", "1" if self.app.auto_record else "0")

    def _toggle_splash(self):
        from ui.p6_app import save_config_key
        current = self.app.config.get("SKIP_SPLASH", "0")
        new_val = "0" if current == "1" else "1"
        self.app.config["SKIP_SPLASH"] = new_val
        save_config_key("SKIP_SPLASH", new_val)

    def _connect_controller(self, ctrl: dict):
        mapper = self.app.midi_mapper
        if mapper.connect_controller(ctrl["name"].split(":")[0]):
            mapper.set_target(self.app.p6)
            mapper.auto_map_sp404()
            mapper.start()
            print(f"Controller mapped: {ctrl['name']}", flush=True)
        else:
            print(f"Failed to connect: {ctrl['name']}", flush=True)

    def _stop_mapper(self):
        self.app.midi_mapper.stop()
        print("Controller mapping stopped", flush=True)

    def _toggle_upload_notifications(self):
        from ui.p6_app import save_config_key
        self.app.notify_uploads = not getattr(self.app, "notify_uploads", True)
        save_config_key("NOTIFY_UPLOADS", "1" if self.app.notify_uploads else "0")

    def _open_network_transfer(self):
        """Navigate to the Files tab and select the Network location."""
        files_screen = self.app.screens.get("files")
        if files_screen:
            files_screen._current_loc = "network"
            files_screen._switch_location("network")
        self.app.switch_screen("files")

    def _pull_from_peer(self, peer: dict):
        """Pull all recordings from a peer Compa that we don't already have."""
        from engine.compa_link import list_peer_files, download_peer_file
        import threading
        print(f"_pull_from_peer({peer['name']}) called", flush=True)

        def _pull():
            try:
                print(f"  Listing files from {peer['ip']}:{peer['port']}", flush=True)
                files = list_peer_files(peer, "recordings")
                print(f"  Got {len(files)} files", flush=True)
                if not files:
                    print(f"No files on {peer['name']}", flush=True)
                    return
                local_dir = self.app.config.get("P6_RECORDING_DIR")
                existing = set(os.listdir(local_dir)) if os.path.isdir(local_dir) else set()
                downloaded = 0
                for f in files:
                    name = f["name"]
                    if name in existing or name.startswith("."):
                        continue
                    if name.endswith(".wav") or name.endswith(".json"):
                        path = download_peer_file(peer, "recordings", name, local_dir)
                        if path:
                            downloaded += 1
                print(f"Pulled {downloaded} files from {peer['name']}", flush=True)
            except Exception as e:
                print(f"Pull error: {e}", flush=True)

        t = threading.Thread(target=_pull, daemon=True)
        t.start()

    def _start_recording(self):
        rec = getattr(self.app, "video_recorder", None)
        if rec and not rec.recording:
            rec.start()

    def _stop_recording(self):
        """Stop recording in a background thread — re-encoding takes ~20s."""
        rec = getattr(self.app, "video_recorder", None)
        if rec is None or not rec.recording:
            return
        import threading
        # Also cancel any running demo so both stop together
        if getattr(self.app, "demo_scheduler", None) is not None:
            self.app.demo_scheduler = None

        def _worker():
            path = rec.stop()
            if path:
                self.app.push_hud(f"Video saved: {os.path.basename(path)}",
                                   None)

        threading.Thread(target=_worker, daemon=True).start()

    def _start_demo(self):
        """Trigger the auto-demo walkthrough via the file trigger."""
        try:
            with open("/tmp/compa_record_demo", "w") as f:
                f.write("")
        except Exception as e:
            print(f"Demo trigger failed: {e}", flush=True)

    # ── Screenshot helpers ───────────────────────────────────────────

    def _capture_push2(self):
        """Save a Push 2 screenshot immediately. The Push 2's surface
        is independent of the touchscreen so no timer is needed —
        whatever the focused device-workspace tab shows is what gets
        captured."""
        path = self.app.save_push2_screenshot()
        if path:
            self.app.push_hud(
                f"Push 2 saved: {os.path.basename(path)}", None)
        else:
            self.app.push_hud("Push 2 capture failed (no Push 2?)", None)

    def _capture_compa_timed(self):
        """Schedule a Compa screenshot in 5 seconds — a countdown
        overlay appears in the top-right so the user can navigate
        to whichever screen they want captured before the timer
        fires."""
        self.app.schedule_screenshot(delay_s=5.0, compa=True, push2=False)
        self.app.push_hud("Compa screenshot in 5s — navigate now", None)

    def _capture_both_timed(self):
        """Schedule a Compa + Push 2 capture in 5 seconds. Both fire
        on the same tick so they reflect the same instant."""
        self.app.schedule_screenshot(delay_s=5.0, compa=True, push2=True)
        self.app.push_hud("Compa + Push 2 in 5s — navigate now", None)

    def _open_updates_screen(self):
        """Jump to the rich Updates screen (changelog + apply flow)."""
        try:
            self.app.switch_screen("updates")
        except Exception:
            pass

    def _check_updates(self):
        """Check for Compa updates in the background."""
        if not hasattr(self.app, 'updater'):
            return
        self._update_status = "Checking..."

        def _on_check(result):
            if result.get("error"):
                self._update_status = f"Error: {result['error'][:40]}"
            elif result.get("update_available"):
                behind = result["behind"]
                self._update_status = f"Update available: {behind} commit{'s' if behind != 1 else ''} behind"
            else:
                self._update_status = "Up to date"

        self.app.updater.check_async(_on_check)

    def _apply_update(self):
        """Pull and restart."""
        if not hasattr(self.app, 'updater'):
            return
        self._update_status = "Updating..."
        import threading

        def _do():
            result = self.app.updater.apply(restart=True)
            self._update_status = result.get("message", "Update done")[:50]

        threading.Thread(target=_do, daemon=True).start()

    def _toggle_twister_mode(self):
        tw = self.app.twister
        tw.mode = "toggle" if tw.mode == "momentary" else "momentary"
        print(f"Twister mode → {tw.mode}", flush=True)

    def _cycle_theme(self):
        names = list(theme.THEMES.keys())
        current = theme.active_theme_name()
        idx = names.index(current) if current in names else 0
        next_name = names[(idx + 1) % len(names)]
        theme.apply_theme(next_name)
        print(f"Theme → {next_name}", flush=True)

    def _start_clock_relay(self, source_key: str, dest_key: str):
        if self.app.start_clock_relay(source_key, dest_key):
            print(f"Clock relay started: {source_key} → {dest_key}", flush=True)

    def _stop_clock_relay(self):
        self.app.stop_clock_relay()

    def _start_audio_route(self, source_key: str, dest_key: str):
        if self.app.start_audio_route(source_key, dest_key):
            print(f"Audio route started: {source_key} → {dest_key}", flush=True)
        else:
            print(f"Failed to start audio route: {source_key} → {dest_key}", flush=True)

    def _stop_audio_route(self):
        self.app.stop_audio_route()
        print("Audio route stopped", flush=True)

    def _run_calibrate(self):
        """Launch the in-app touch calibration screen.

        Works for any touchscreen delivering events through pygame (HID USB
        capacitive panels, DSI panels via SDL, etc.). Replaces the legacy
        ts_calibrate which only worked for resistive ADS7846 touchscreens.
        """
        self.app.switch_screen("touch_calibrate")

    # ── Settings row definitions ────────────────────────────────────

    def _build_rows(self):
        """Build the list of setting rows with current values."""
        threshold = self._get_threshold()
        splash_off = self.app.config.get("SKIP_SPLASH", "0") == "1"

        self._rows = []

        # ── Compa updates row at the top (prominent placement) ─────
        # The full update flow + changelog history lives in the
        # dedicated UpdatesScreen — this row is the discoverable
        # entry point. Shows a badge on the right when updates are
        # pending so a glance at Settings tells you something's
        # available.
        if hasattr(self.app, 'updater') and self.app.updater.is_git_repo:
            try:
                pending = self.app.updater.update_available
                behind = self.app.updater.commits_behind
            except Exception:
                pending = False
                behind = 0
            if pending and behind > 0:
                badge = (f"{behind} new change"
                         f"{'s' if behind != 1 else ''} available")
            else:
                cur = self.app.updater.current_commit()
                badge = f"@ {cur}"
            self._rows.append({
                "label": "Updates & changelog",
                "type": "button",
                "btn_label": ("REVIEW" if pending else "OPEN"),
                "value": badge,
                "action": self._open_updates_screen,
            })

        self._rows.extend([
            {"label": "Mouse Mode", "type": "toggle", "value": self.app.mouse_mode,
             "action": self._toggle_mouse_mode},
            {"label": "Auto-Record", "type": "toggle", "value": self.app.auto_record,
             "action": self._toggle_auto_record},
            {"label": "Threshold Level", "type": "adjust", "value": threshold,
             "action_dec": lambda: self._set_threshold(self._get_threshold() - 5),
             "action_inc": lambda: self._set_threshold(self._get_threshold() + 5)},
            {"label": "Splash Screen", "type": "toggle", "value": not splash_off,
             "action": self._toggle_splash},
            {"label": "Color Theme", "type": "button",
             "btn_label": theme.active_theme_name().upper(),
             "action": self._cycle_theme},
            {"label": "", "type": "section", "value": "CONNECTED DEVICES"},
        ])

        # Dynamic device rows
        connected = self.app.device_manager.connected
        focus_key = self.app.device_manager.focus_key
        for short_name, profile in connected.items():
            midi_conn = self.app._midi_connections.get(short_name)
            is_focused = (short_name == focus_key)
            midi_status = "MIDI OK" if (midi_conn and midi_conn.connected) else "No MIDI"
            audio_info = f"{profile.audio_in_channels}in/{profile.audio_out_channels}out"
            rates = "/".join(str(r // 1000) + "k" for r in profile.supported_sample_rates)
            status = f"{midi_status} | {audio_info} {rates}"

            if is_focused:
                self._rows.append({
                    "label": f"► {profile.name}", "type": "info",
                    "value": f"FOCUSED  {status}",
                })
            else:
                self._rows.append({
                    "label": f"  {profile.name}", "type": "button",
                    "btn_label": "FOCUS",
                    "action": lambda sn=short_name: self.app.switch_focus(sn),
                    "value": status,
                })
            # Color picker row for this device
            self._rows.append({
                "label": f"  Color", "type": "color_picker",
                "device": short_name,
            })

        if not connected:
            self._rows.append({"label": "  No devices", "type": "info", "value": "—"})

        # MIDI Controller Mapping
        self._rows.append({"label": "", "type": "section", "value": "MIDI CONTROLLER"})

        # New unified mapper — profile-based + MIDI Learn
        cm = getattr(self.app, "controller_mapper", None)
        if cm is not None:
            # Local var name — NOT `connected` — to avoid shadowing the
            # outer `connected` dict used by audio routing/clock relay.
            cm_bindings = cm.connected_controllers()
            if cm_bindings:
                names = ", ".join(b.profile.name for b in cm_bindings)[:50]
                status = f"{len(cm_bindings)} connected: {names}"
            else:
                status = f"{len(cm._profiles)} profiles · plug in to use"
            self._rows.append({
                "label": "  Controller mappings", "type": "button",
                "btn_label": "CONFIGURE",
                "action": lambda: self.app.switch_screen("controller"),
                "value": status,
            })

        # Legacy MidiMapper (kept for backward compat with existing Twister
        # auto-map flow; will be retired once ControllerMapper covers all
        # previous use cases)
        mapper = self.app.midi_mapper
        if mapper.is_running:
            self._rows.append({
                "label": f"  Legacy: {mapper.controller_name}", "type": "button",
                "btn_label": "STOP",
                "action": self._stop_mapper,
            })
            self._rows.append({
                "label": f"  {len(mapper.mappings)} mappings, {len(mapper.macros)} macros",
                "type": "info", "value": "",
            })
        else:
            controllers = mapper.detect_controllers()
            if controllers:
                for ctrl in controllers:
                    self._rows.append({
                        "label": f"  {ctrl['name'][:30]}", "type": "button",
                        "btn_label": "MAP",
                        "action": lambda c=ctrl: self._connect_controller(c),
                    })
            else:
                self._rows.append({
                    "label": "  No external controllers found", "type": "info",
                    "value": "Plug in a MIDI controller",
                })

        # Compa-to-Compa Network Link — points to Files → Network tab
        if hasattr(self.app, 'compa_browser'):
            self._rows.append({"label": "", "type": "section", "value": "COMPA NETWORK"})
            peers = self.app.compa_browser.peers
            if peers:
                self._rows.append({
                    "label": f"  {len(peers)} peer{'s' if len(peers) != 1 else ''} online",
                    "type": "button", "btn_label": "OPEN",
                    "action": self._open_network_transfer,
                    "value": ", ".join(p["name"] for p in peers)[:40],
                })
            else:
                self._rows.append({
                    "label": "  No peers found", "type": "info",
                    "value": "Both Compas must be on same WiFi",
                })
            # Upload notification toggle
            self._rows.append({
                "label": "  Upload notifications", "type": "toggle",
                "value": getattr(self.app, "notify_uploads", True),
                "action": self._toggle_upload_notifications,
            })

        # Twister Genius settings
        tw = self.app.twister
        if tw.connected:
            self._rows.append({"label": "", "type": "section", "value": "TWISTER GENIUS"})
            self._rows.append({
                "label": "  FX Mode", "type": "toggle",
                "value": tw.mode == "momentary",
                "action": self._toggle_twister_mode,
                "true_label": "MOMENTARY", "false_label": "TOGGLE",
            })
            self._rows.append({
                "label": f"  FX Page", "type": "info",
                "value": f"Page {tw.current_page + 1} of {tw.page_count}  ({len(tw.slots)} effects)",
            })

        # Compa Updater
        if hasattr(self.app, 'updater') and self.app.updater.is_git_repo:
            self._rows.append({"label": "", "type": "section", "value": "COMPA UPDATER"})
            current = self.app.updater.current_commit()
            branch = self.app.updater.current_branch()
            self._rows.append({
                "label": f"  Version", "type": "info",
                "value": f"{branch} @ {current}",
            })
            self._rows.append({
                "label": f"  Check for updates", "type": "button",
                "btn_label": "CHECK",
                "action": self._check_updates,
                "value": self._update_status,
            })
            self._rows.append({
                "label": f"  Apply update", "type": "button",
                "btn_label": "UPDATE",
                "action": self._apply_update,
            })

        # Audio routing (only when multiple devices connected)
        if len(connected) >= 2:
            self._rows.append({"label": "", "type": "section", "value": "AUDIO ROUTING"})

            route = self.app.audio_route
            if route and route.is_active:
                src = route.source_name
                dst = route.dest_name
                src_r = f"{route._src_rate // 1000}k"
                dst_r = f"{route._dst_rate // 1000}k"
                src_text = f"{src} → {dst}"
                if route._needs_src:
                    src_text += f" ({src_r}→{dst_r} SRC)"
                self._rows.append({
                    "label": f"  Route: {src_text}", "type": "button",
                    "btn_label": "STOP",
                    "action": self._stop_audio_route,
                })
            else:
                # Build route options from connected devices
                keys = list(connected.keys())
                for i, src_key in enumerate(keys):
                    for dst_key in keys:
                        if src_key == dst_key:
                            continue
                        label = f"  {src_key} → {dst_key}"
                        self._rows.append({
                            "label": label, "type": "button",
                            "btn_label": "START",
                            "action": lambda s=src_key, d=dst_key: self._start_audio_route(s, d),
                        })

        # MIDI clock relay (only when multiple devices)
        if len(connected) >= 2:
            self._rows.append({"label": "", "type": "section", "value": "MIDI CLOCK RELAY"})
            if self.app.clock_relay_active:
                src = getattr(self.app, "_clock_relay_source", "?")
                dst = getattr(self.app, "_clock_relay_dest", "?")
                self._rows.append({
                    "label": f"  Clock: {src} → {dst}", "type": "button",
                    "btn_label": "STOP",
                    "action": self._stop_clock_relay,
                })
            else:
                keys = list(connected.keys())
                for src_key in keys:
                    for dst_key in keys:
                        if src_key == dst_key:
                            continue
                        self._rows.append({
                            "label": f"  {src_key} → {dst_key}", "type": "button",
                            "btn_label": "SYNC",
                            "action": lambda s=src_key, d=dst_key: self._start_clock_relay(s, d),
                        })

        # IO & Connectivity (WiFi / Bluetooth / Network info)
        self._rows.append({"label": "", "type": "section",
                           "value": "IO & CONNECTIVITY"})
        try:
            from engine import network_manager as nm
            ssid = self.app.wifi.current_ssid() if getattr(self.app, "wifi", None) else ""
            ip = nm.get_ip_address()
            status_bits = []
            if ssid:
                status_bits.append(f"WiFi: {ssid}")
            elif ip and ip != "—":
                status_bits.append("Ethernet")
            status_bits.append(ip)
            io_status = " · ".join(status_bits)[:40]
        except Exception:
            io_status = ""
        self._rows.append({
            "label": "  Network & Bluetooth", "type": "button",
            "btn_label": "OPEN",
            "action": lambda: self.app.switch_screen("io"),
            "value": io_status,
        })

        # Video Recording
        rec = getattr(self.app, "video_recorder", None)
        demo = getattr(self.app, "demo_scheduler", None)
        if rec is not None:
            self._rows.append({"label": "", "type": "section",
                               "value": "VIDEO RECORDING"})
            # Record / stop toggle
            if rec.recording:
                elapsed = rec.duration_seconds
                frames = rec.frames_written
                if demo is not None:
                    total = demo.total_duration
                    status = f"Demo running · {elapsed:.0f}/{total:.0f}s · {frames} frames"
                else:
                    status = f"Recording · {elapsed:.0f}s · {frames} frames"
                self._rows.append({
                    "label": "  Record screen", "type": "button",
                    "btn_label": "STOP",
                    "action": self._stop_recording,
                    "value": status,
                })
            else:
                from ui.video_recorder import latest_video_path
                latest = latest_video_path()
                if latest:
                    size_mb = os.path.getsize(latest) / (1024 * 1024)
                    status = f"Last: {os.path.basename(latest)} ({size_mb:.1f} MB)"
                else:
                    status = "Saved to ~/compa/videos/"
                self._rows.append({
                    "label": "  Record screen", "type": "button",
                    "btn_label": "RECORD",
                    "action": self._start_recording,
                    "value": status,
                })
                # Auto-demo button only when idle
                self._rows.append({
                    "label": "  Auto-demo walkthrough", "type": "button",
                    "btn_label": "DEMO",
                    "action": self._start_demo,
                    "value": "~43s cycle through all screens",
                })

        # Screenshots — grouped with video so all capture controls
        # live in one place. Push 2 is instant (the device's surface
        # is whatever its current focused tab shows). Compa captures
        # use a 3-second countdown so the user can navigate to the
        # screen they want to capture.
        self._rows.append({"label": "", "type": "section",
                           "value": "SCREENSHOTS"})
        push2_present = getattr(self.app, "push2", None) is not None
        push2_status = ("Saves to ~/compa/screenshots/"
                        if push2_present else "Push 2 not connected")
        self._rows.append({
            "label": "  Push 2 screen", "type": "button",
            "btn_label": "CAPTURE",
            "action": self._capture_push2,
            "value": push2_status,
        })
        self._rows.append({
            "label": "  Compa screen (5s timer)", "type": "button",
            "btn_label": "CAPTURE",
            "action": self._capture_compa_timed,
            "value": "Navigate after pressing — saves on countdown end",
        })
        self._rows.append({
            "label": "  Both (5s timer)", "type": "button",
            "btn_label": "CAPTURE",
            "action": self._capture_both_timed,
            "value": "Compa + Push 2 fire on the same tick",
        })

        # Audio and display info
        self._rows.extend([
            {"label": "", "type": "section", "value": "AUDIO & DISPLAY"},
            {"label": "Audio Input", "type": "info",
             "value": self.app.recorder.device_name},
            {"label": "Display", "type": "info", "value": self._get_resolution()},
            {"label": "Touch Calibration", "type": "button", "btn_label": "CALIBRATE",
             "action": self._run_calibrate},
            {"label": "", "type": "section", "value": "ABOUT"},
            {"label": "Version", "type": "info",
             "value": "Compa v1.0 by RARE DATA / raredata.net"},
        ])
        self._content_height = len(self._rows) * self._row_height + 80

    # ── Event handling ──────────────────────────────────────────────

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.app.switch_screen("session")
                return
            elif event.key == pygame.K_UP:
                self._scroll_y = max(0, self._scroll_y - 40)
                return
            elif event.key == pygame.K_DOWN:
                self._scroll_y += 40
                return

        # ── Mouse-wheel scroll (still on MOUSEBUTTONDOWN — wheel
        # has no concept of drag) ──────────────────────────────────
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 4:
                self._scroll_y = max(0, self._scroll_y - 30)
                return
            if event.button == 5:
                self._scroll_y += 30
                return

        # ── Tap / drag tracking for the touchscreen ────────────────
        # Press: record where the press landed + the current scroll
        # offset. Don't fire any action yet — we'll wait until the
        # user releases (or starts dragging).
        if (event.type == pygame.MOUSEBUTTONDOWN
                and event.button == 1):
            self._press_pos = event.pos if hasattr(event, "pos") else (0, 0)
            self._press_scroll_y = self._scroll_y
            self._is_dragging = False
            return

        # Motion while pressed: if we've moved past the threshold,
        # latch into drag mode and start scrolling instead.
        if (event.type == pygame.MOUSEMOTION
                and self._press_pos is not None):
            dx = event.pos[0] - self._press_pos[0]
            dy = event.pos[1] - self._press_pos[1]
            if (not self._is_dragging
                    and (abs(dx) + abs(dy)) >= self.TAP_DRAG_THRESHOLD):
                self._is_dragging = True
            if self._is_dragging:
                # Scroll inversely to vertical drag — pulling down
                # reveals content above.
                self._scroll_y = max(0, self._press_scroll_y - dy)
            return

        # Release: if no drag started, this was a tap — fire the
        # action that lives at the press position.
        if (event.type == pygame.MOUSEBUTTONUP
                and event.button == 1
                and self._press_pos is not None):
            mx, my = self._press_pos
            was_dragging = self._is_dragging
            self._press_pos = None
            self._is_dragging = False
            if was_dragging:
                # Drag ended — scroll already updated, no action.
                return

            # ── Treat as tap. Fire the appropriate action. ──────────
            self._dispatch_tap(mx, my)
            return

    def _dispatch_tap(self, mx: int, my: int) -> None:
        """Tap-action dispatch shared by MOUSEBUTTONUP. Same logic
        the press-handler used to do, just gated on no-drag."""
        # HELP + UPDATES buttons (top-right). Geometry must match
        # the draw side below.
        help_rect = pygame.Rect(theme.SCREEN_WIDTH - 80, 6, 70, 28)
        update_rect = pygame.Rect(
            theme.SCREEN_WIDTH - 80 - 76, 6, 70, 28)
        if help_rect.collidepoint(mx, my):
            self.app.switch_screen("help")
            return
        if update_rect.collidepoint(mx, my):
            self.app.switch_screen("updates")
            return

        # Row interactions
        content_y = 46
        row_h = self._row_height
        self._build_rows()

        for i, row in enumerate(self._rows):
            ry = content_y + i * row_h - self._scroll_y
            if (ry < content_y - row_h
                    or ry > theme.SCREEN_HEIGHT - theme.NAV_HEIGHT):
                continue

            row_rect = pygame.Rect(0, ry, theme.SCREEN_WIDTH, row_h)
            if not row_rect.collidepoint(mx, my):
                continue

            rtype = row["type"]
            ctrl_x = theme.SCREEN_WIDTH - 160

            if rtype == "toggle":
                toggle_rect = pygame.Rect(
                    ctrl_x, ry + 4, 60, row_h - 8)
                if toggle_rect.collidepoint(mx, my):
                    row["action"]()
                return

            elif rtype == "adjust":
                dec_rect = pygame.Rect(ctrl_x, ry + 4, 32, row_h - 8)
                inc_rect = pygame.Rect(
                    ctrl_x + 80, ry + 4, 32, row_h - 8)
                if dec_rect.collidepoint(mx, my):
                    row["action_dec"]()
                elif inc_rect.collidepoint(mx, my):
                    row["action_inc"]()
                return

            elif rtype == "color_picker":
                swatches = list(theme.COLOR_SWATCHES.items())
                sw_size = min(
                    26,
                    (theme.SCREEN_WIDTH - 120) // len(swatches) - 2)
                sx = 100
                for j, (swatch_name, rgb) in enumerate(swatches):
                    sr = pygame.Rect(
                        sx + j * (sw_size + 2), ry + 4,
                        sw_size, row_h - 8)
                    if sr.collidepoint(mx, my):
                        dev = row["device"]
                        theme.set_device_color(dev, swatch_name)
                        theme.apply_theme_for_device(dev)
                        from ui.p6_app import save_config_key
                        save_config_key(
                            f"COLOR_{dev}", swatch_name)
                        print(
                            f"Device color: {dev} → {swatch_name}",
                            flush=True,
                        )
                        return
                return

            elif rtype == "button":
                # Whole-row clickable for accessibility — only
                # fires when no drag occurred (the caller already
                # gated on this).
                try:
                    row["action"]()
                    self._button_flash = {
                        "idx": i,
                        "until": pygame.time.get_ticks() + 150,
                    }
                except Exception as e:
                    print(
                        f"Settings button action error: {e}",
                        flush=True,
                    )
                return

    def update(self):
        pass

    # ── Drawing ─────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")

        # Header
        y_after = theme.draw_screen_header(surface, "SETTINGS", "")

        # HELP button (top-right)
        help_rect = pygame.Rect(theme.SCREEN_WIDTH - 80, 6, 70, 28)
        pygame.draw.rect(surface, theme.BUTTON_BG, help_rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, help_rect, 1, border_radius=6)
        surf = f_small.render("HELP", True, theme.TEXT)
        surface.blit(surf, surf.get_rect(center=help_rect.center))

        # UPDATE button (immediately to the left of HELP). Lights in
        # accent color when an update is pending so it reads as
        # "something to do" without being noisy. Tap → Updates
        # screen.
        update_rect = pygame.Rect(
            theme.SCREEN_WIDTH - 80 - 76, 6, 70, 28)
        try:
            update_pending = bool(
                getattr(self.app.updater, "update_available", False))
            commits_behind = int(
                getattr(self.app.updater, "commits_behind", 0))
        except Exception:
            update_pending = False
            commits_behind = 0
        if update_pending:
            pygame.draw.rect(
                surface, theme.ACCENT, update_rect, border_radius=6)
            label = (f"UPDATE ({commits_behind})"
                     if commits_behind <= 9 else "UPDATE")
            tc = theme.BG
        else:
            pygame.draw.rect(
                surface, theme.BUTTON_BG, update_rect, border_radius=6)
            label = "UPDATES"
            tc = theme.TEXT
        pygame.draw.rect(
            surface, theme.BORDER, update_rect, 1, border_radius=6)
        us = f_small.render(label, True, tc)
        surface.blit(us, us.get_rect(center=update_rect.center))

        # Build current row data
        self._build_rows()

        # Content area with clipping
        content_y = 46
        content_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - content_y
        content_rect = pygame.Rect(0, content_y, theme.SCREEN_WIDTH, content_h)
        clip = surface.get_clip()
        surface.set_clip(content_rect)

        row_h = self._row_height
        ctrl_x = theme.SCREEN_WIDTH - 160

        for i, row in enumerate(self._rows):
            ry = content_y + i * row_h - self._scroll_y
            if ry + row_h < content_y or ry > content_rect.bottom:
                continue

            # Alternating row backgrounds
            if i % 2 == 0:
                pygame.draw.rect(surface, theme.BG_PANEL,
                                 (8, ry, theme.SCREEN_WIDTH - 16, row_h),
                                 border_radius=4)
            else:
                pygame.draw.rect(surface, theme.BG,
                                 (8, ry, theme.SCREEN_WIDTH - 16, row_h))

            rtype = row["type"]

            # Section headers
            if rtype == "section":
                pygame.draw.line(surface, theme.BORDER, (20, ry + row_h // 2),
                                (theme.SCREEN_WIDTH - 20, ry + row_h // 2))
                sect_surf = f_tiny.render(row["value"], True, theme.ACCENT)
                bg_rect = sect_surf.get_rect(left=30, centery=ry + row_h // 2)
                bg_rect.inflate_ip(12, 4)
                pygame.draw.rect(surface, theme.BG, bg_rect)
                surface.blit(sect_surf, (30, ry + (row_h - sect_surf.get_height()) // 2))
                continue

            # Label
            label_surf = f_med.render(row["label"], True, theme.TEXT)
            surface.blit(label_surf, (20, ry + (row_h - label_surf.get_height()) // 2))

            if rtype == "toggle":
                # Toggle button (supports custom labels via true_label/false_label)
                is_on = row["value"]
                on_text = row.get("true_label", "ON")
                off_text = row.get("false_label", "OFF")
                tw = max(60, len(on_text if is_on else off_text) * 10 + 16)
                toggle_rect = pygame.Rect(ctrl_x, ry + 4, tw, row_h - 8)
                if is_on:
                    pygame.draw.rect(surface, theme.GREEN, toggle_rect, border_radius=6)
                    lbl = f_small.render(on_text, True, theme.BG)
                else:
                    pygame.draw.rect(surface, theme.BG_LIGHTER, toggle_rect, border_radius=6)
                    pygame.draw.rect(surface, theme.BORDER, toggle_rect, 1, border_radius=6)
                    lbl = f_small.render(off_text, True, theme.TEXT_DIM)
                surface.blit(lbl, lbl.get_rect(center=toggle_rect.center))

            elif rtype == "adjust":
                # [-] [value] [+]
                val = row["value"]
                dec_rect = pygame.Rect(ctrl_x, ry + 4, 32, row_h - 8)
                pygame.draw.rect(surface, theme.BUTTON_BG, dec_rect, border_radius=6)
                pygame.draw.rect(surface, theme.BORDER, dec_rect, 1, border_radius=6)
                lbl = f_med.render("-", True, theme.TEXT)
                surface.blit(lbl, lbl.get_rect(center=dec_rect.center))

                # Value display
                val_surf = f_med.render(str(val), True, theme.ACCENT)
                val_x = ctrl_x + 36 + (44 - val_surf.get_width()) // 2
                surface.blit(val_surf, (val_x, ry + (row_h - val_surf.get_height()) // 2))

                inc_rect = pygame.Rect(ctrl_x + 80, ry + 4, 32, row_h - 8)
                pygame.draw.rect(surface, theme.BUTTON_BG, inc_rect, border_radius=6)
                pygame.draw.rect(surface, theme.BORDER, inc_rect, 1, border_radius=6)
                lbl = f_med.render("+", True, theme.TEXT)
                surface.blit(lbl, lbl.get_rect(center=inc_rect.center))

            elif rtype == "color_picker":
                # Row of color swatches
                dev = row["device"]
                current_accent = theme.get_device_color(dev)
                swatches = list(theme.COLOR_SWATCHES.items())
                sw_size = min(26, (theme.SCREEN_WIDTH - 120) // len(swatches) - 2)
                sx = 100
                for j, (swatch_name, rgb) in enumerate(swatches):
                    sr = pygame.Rect(sx + j * (sw_size + 2), ry + 4, sw_size, row_h - 8)
                    pygame.draw.rect(surface, rgb, sr, border_radius=4)
                    # Highlight current selection with white border
                    if rgb == current_accent:
                        pygame.draw.rect(surface, theme.TEXT_BRIGHT, sr, 2, border_radius=4)

            elif rtype == "button":
                # Flash on recent press for visual feedback
                flashing = (self._button_flash and self._button_flash["idx"] == i
                            and pygame.time.get_ticks() < self._button_flash["until"])
                btn_rect = pygame.Rect(ctrl_x, ry + 4, 90, row_h - 8)
                btn_bg = theme.ACCENT if flashing else theme.ACCENT_DIM
                pygame.draw.rect(surface, btn_bg, btn_rect, border_radius=6)
                lbl = f_small.render(row["btn_label"], True, theme.TEXT_BRIGHT)
                surface.blit(lbl, lbl.get_rect(center=btn_rect.center))
                # Show value (status message) between label and button
                val = row.get("value")
                if val:
                    val_surf = f_tiny.render(str(val)[:40], True, theme.TEXT_DIM)
                    val_x = btn_rect.x - val_surf.get_width() - 8
                    surface.blit(val_surf, (val_x, ry + (row_h - val_surf.get_height()) // 2))

            elif rtype == "info":
                # Display value text on right side
                val_str = str(row["value"])
                val_surf = f_small.render(val_str, True, theme.TEXT_DIM)
                # Right-align within the control area
                vx = theme.SCREEN_WIDTH - 24 - val_surf.get_width()
                surface.blit(val_surf, (vx, ry + (row_h - val_surf.get_height()) // 2))

        surface.set_clip(clip)

        # Scrollbar
        total_h = len(self._rows) * row_h
        if total_h > content_h:
            bar_x = theme.SCREEN_WIDTH - 5
            thumb_h = max(20, int(content_h * content_h / total_h))
            thumb_y = content_y + int(
                (content_h - thumb_h) * self._scroll_y / max(1, total_h - content_h))
            thumb_y = max(content_y, min(thumb_y, content_y + content_h - thumb_h))
            pygame.draw.rect(surface, theme.BORDER,
                             (bar_x, content_y, 3, content_h), border_radius=1)
            pygame.draw.rect(surface, theme.ACCENT,
                             (bar_x, thumb_y, 3, thumb_h), border_radius=1)
