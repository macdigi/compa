"""IO Settings — WiFi, Bluetooth, and basic network configuration.

End-user connectivity screen. Lets the user:
- See current network status (SSID, IP, connection type)
- Scan + connect to WiFi networks with on-screen password entry
- Enable Bluetooth and pair keyboards/mice/other peripherals

Operations that shell out to `nmcli`/`bluetoothctl` run in background
threads via `engine/network_manager.py`. The UI polls for status updates
on each redraw.
"""

import pygame
from .. import theme
from ..components.button import Button


# Tab identifiers
TAB_STATUS = "status"
TAB_WIFI = "wifi"
TAB_BT = "bt"


class IOSettingsScreen:
    """Tabbed settings panel for WiFi/Bluetooth/Network info."""

    def __init__(self, app):
        self.app = app
        self._tab = TAB_STATUS

        self._scroll_y = 0
        self._row_h = 40
        self._rows: list[dict] = []

        # Auto-refresh timers
        self._last_status_refresh = 0.0
        self._status_cache: dict = {}

        # Flash feedback on button presses
        self._flash_key = None
        self._flash_until = 0

        # Touch-drag scroll
        self._drag_start_y = 0
        self._drag_start_scroll = 0
        self._drag_active = False

        # Most recent action message
        self._action_msg = ""
        self._action_msg_until = 0

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_enter(self):
        self._scroll_y = 0
        self._action_msg = ""
        # Prime status and kick off a scan for whichever tab we're on
        self._refresh_status()
        if self._tab == TAB_WIFI:
            self._start_wifi_scan()
        elif self._tab == TAB_BT:
            self._refresh_bt_devices()

    def on_exit(self):
        pass

    # ── Helpers ──────────────────────────────────────────────────────

    def _flash(self, key: str):
        self._flash_key = key
        self._flash_until = pygame.time.get_ticks() + 150

    def _set_msg(self, msg: str):
        self._action_msg = msg
        self._action_msg_until = pygame.time.get_ticks() + 3000
        print(f"IO: {msg}", flush=True)

    def _refresh_status(self):
        """Cache network info; called on demand (cheap, but not every frame)."""
        from engine import network_manager as nm
        try:
            self._status_cache = {
                "hostname": nm.get_hostname(),
                "ip": nm.get_ip_address(),
                "active": nm.get_active_connection(),
                "wifi_ssid": self.app.wifi.current_ssid() if self.app.wifi else "",
                "wifi_on": self.app.wifi.radio_on() if self.app.wifi else False,
                "bt_on": self.app.bluetooth.powered() if self.app.bluetooth else False,
            }
        except Exception as e:
            print(f"IO status refresh error: {e}", flush=True)
            self._status_cache = {}

    def _start_wifi_scan(self):
        wifi = self.app.wifi
        if wifi is None:
            return
        wifi.scan_async(on_done=lambda nets: self._set_msg(f"Found {len(nets)} networks"))

    def _refresh_bt_devices(self):
        bt = self.app.bluetooth
        if bt is None:
            return
        import threading
        threading.Thread(target=bt.refresh_devices, daemon=True).start()

    # ── WiFi actions ─────────────────────────────────────────────────

    def _prompt_wifi_password(self, ssid: str, security: str):
        """Open on-screen keyboard to get a password for ssid."""
        if not self.app.keyboard:
            self._set_msg("Keyboard unavailable")
            return

        if security in ("--", "", "None"):
            # Open network — connect directly
            self._connect_wifi(ssid, None)
            return

        def _on_submit(pw: str):
            self._connect_wifi(ssid, pw)

        self.app.keyboard.show(
            title=f"Password for {ssid}",
            default="",
            password=True,
            on_submit=_on_submit,
            on_cancel=lambda: self._set_msg("Cancelled"),
        )

    def _connect_wifi(self, ssid: str, password: str | None):
        wifi = self.app.wifi
        if wifi is None:
            return
        self._set_msg(f"Connecting to {ssid}...")

        def _on_done(ok: bool, msg: str):
            self._set_msg(f"{ssid}: {msg}")
            self._refresh_status()
            # Re-scan to update the active marker
            wifi.scan_async()

        wifi.connect_async(ssid, password, on_done=_on_done)

    def _toggle_wifi_radio(self):
        wifi = self.app.wifi
        if wifi is None:
            return
        on = not wifi.radio_on()
        wifi.set_radio(on)
        self._set_msg(f"WiFi {'ON' if on else 'OFF'}")
        # Allow radio to come up before refreshing
        import threading

        def _delayed():
            import time
            time.sleep(1.0)
            self._refresh_status()
            if on:
                self._start_wifi_scan()

        threading.Thread(target=_delayed, daemon=True).start()

    def _disconnect_wifi(self):
        wifi = self.app.wifi
        if wifi is None:
            return
        wifi.disconnect_async(on_done=lambda ok: self._set_msg("Disconnected"))

    # ── Bluetooth actions ────────────────────────────────────────────

    def _toggle_bt_power(self):
        bt = self.app.bluetooth
        if bt is None:
            return
        on = not bt.powered()
        bt.set_power(on)
        self._set_msg(f"Bluetooth {'ON' if on else 'OFF'}")
        import threading

        def _delayed():
            import time
            time.sleep(0.5)
            self._refresh_status()

        threading.Thread(target=_delayed, daemon=True).start()

    def _bt_scan(self):
        bt = self.app.bluetooth
        if bt is None:
            return
        if bt.is_scanning:
            self._set_msg("Already scanning")
            return
        self._set_msg("Scanning Bluetooth...")
        bt.scan_async(
            duration=15.0,
            on_done=lambda devs: self._set_msg(f"Found {len(devs)} devices"),
        )

    def _bt_pair(self, mac: str, name: str):
        bt = self.app.bluetooth
        if bt is None:
            return
        self._set_msg(f"Pairing {name[:20]}...")

        def _on_done(ok: bool, msg: str):
            self._set_msg(f"{name[:20]}: {msg}")

        bt.pair_async(mac, on_done=_on_done)

    def _bt_connect(self, mac: str, name: str):
        bt = self.app.bluetooth
        if bt is None:
            return
        self._set_msg(f"Connecting {name[:20]}...")

        def _on_done(ok: bool, msg: str):
            self._set_msg(f"{name[:20]}: {msg}")

        bt.connect_async(mac, on_done=_on_done)

    def _bt_disconnect(self, mac: str, name: str):
        bt = self.app.bluetooth
        if bt is None:
            return
        bt.disconnect_async(mac)
        self._set_msg(f"Disconnected {name[:20]}")

    def _bt_remove(self, mac: str, name: str):
        bt = self.app.bluetooth
        if bt is None:
            return
        bt.remove_async(mac)
        self._set_msg(f"Removed {name[:20]}")

    # ── Row building ─────────────────────────────────────────────────

    def _build_status_rows(self):
        s = self._status_cache
        self._rows = [
            {"label": "", "type": "section", "value": "DEVICE"},
            {"label": "Hostname", "type": "info",
             "value": s.get("hostname", "—")},
            {"label": "IP Address", "type": "info", "value": s.get("ip", "—")},
            {"label": "", "type": "section", "value": "CONNECTION"},
        ]

        active = s.get("active", {})
        if active and active.get("type"):
            conn_type = active["type"].upper()
            self._rows.append({
                "label": f"{conn_type}", "type": "info",
                "value": active.get("name", "") or active.get("device", ""),
            })
        else:
            self._rows.append({
                "label": "Connection", "type": "info",
                "value": "Not connected",
            })

        # WiFi summary
        self._rows.append({"label": "", "type": "section", "value": "WIFI"})
        self._rows.append({
            "label": "WiFi Radio", "type": "toggle",
            "value": s.get("wifi_on", False),
            "action": self._toggle_wifi_radio,
        })
        self._rows.append({
            "label": "Current SSID", "type": "info",
            "value": s.get("wifi_ssid", "") or "—",
        })
        self._rows.append({
            "label": "Manage networks", "type": "button",
            "btn_label": "WIFI",
            "action": lambda: self._set_tab(TAB_WIFI),
        })

        # Bluetooth summary
        self._rows.append({"label": "", "type": "section", "value": "BLUETOOTH"})
        self._rows.append({
            "label": "Bluetooth", "type": "toggle",
            "value": s.get("bt_on", False),
            "action": self._toggle_bt_power,
        })
        self._rows.append({
            "label": "Manage devices", "type": "button",
            "btn_label": "BT",
            "action": lambda: self._set_tab(TAB_BT),
        })

    def _build_wifi_rows(self):
        wifi = self.app.wifi
        self._rows = []

        # Header controls
        if wifi is None:
            self._rows.append({
                "label": "NetworkManager unavailable", "type": "info",
                "value": "Install nmcli",
            })
            return

        radio_on = wifi.radio_on()
        self._rows.append({
            "label": "WiFi Radio", "type": "toggle",
            "value": radio_on,
            "action": self._toggle_wifi_radio,
        })
        self._rows.append({
            "label": f"Current: {wifi.current_ssid() or '—'}",
            "type": "button",
            "btn_label": "RESCAN",
            "action": self._start_wifi_scan,
            "value": wifi.status if wifi.status else "",
        })

        if wifi.current_ssid():
            self._rows.append({
                "label": "Disconnect", "type": "button",
                "btn_label": "DROP",
                "action": self._disconnect_wifi,
            })

        self._rows.append({"label": "", "type": "section",
                           "value": f"NETWORKS ({len(wifi.networks)})"})

        if wifi.is_scanning and not wifi.networks:
            self._rows.append({
                "label": "  Scanning...", "type": "info", "value": "",
            })
            return

        if not wifi.networks:
            self._rows.append({
                "label": "  No networks found", "type": "info",
                "value": "Tap RESCAN",
            })
            return

        # One row per SSID — entire row clickable to connect
        for net in wifi.networks:
            ssid = net["ssid"]
            sig = net["signal"]
            sec = net["security"] or "Open"
            marker = "●" if net["active"] else "○"
            bars = self._signal_bars(sig)

            self._rows.append({
                "label": f"  {marker} {bars}  {ssid}",
                "type": "wifi_entry",
                "ssid": ssid,
                "security": sec,
                "value": f"{sig}%  {sec}",
                "is_active": net["active"],
            })

    def _build_bt_rows(self):
        bt = self.app.bluetooth
        self._rows = []

        if bt is None:
            self._rows.append({
                "label": "bluetoothctl unavailable", "type": "info",
                "value": "Install bluez",
            })
            return

        powered = bt.powered()
        self._rows.append({
            "label": "Bluetooth", "type": "toggle",
            "value": powered,
            "action": self._toggle_bt_power,
        })

        if not powered:
            self._rows.append({
                "label": "Turn on Bluetooth to scan", "type": "info", "value": "",
            })
            return

        self._rows.append({
            "label": "Scan for devices", "type": "button",
            "btn_label": "SCAN",
            "action": self._bt_scan,
            "value": bt.status if bt.status else "",
        })

        # Paired
        paired = [d for d in bt.devices if d["paired"]]
        if paired:
            self._rows.append({"label": "", "type": "section",
                               "value": f"PAIRED ({len(paired)})"})
            for d in paired:
                icon = self._bt_icon(d.get("icon", ""))
                marker = "●" if d["connected"] else "○"
                self._rows.append({
                    "label": f"  {marker} {icon} {d['name']}",
                    "type": "bt_entry",
                    "mac": d["mac"],
                    "name": d["name"],
                    "paired": True,
                    "connected": d["connected"],
                    "value": d["mac"][-8:],
                })

        # Available (not paired)
        available = [d for d in bt.devices if not d["paired"]]
        if available:
            self._rows.append({"label": "", "type": "section",
                               "value": f"AVAILABLE ({len(available)})"})
            for d in available:
                icon = self._bt_icon(d.get("icon", ""))
                self._rows.append({
                    "label": f"  {icon} {d['name']}",
                    "type": "bt_entry",
                    "mac": d["mac"],
                    "name": d["name"],
                    "paired": False,
                    "connected": False,
                    "value": d["mac"][-8:],
                })

        if not bt.devices:
            hint = "Scanning..." if bt.is_scanning else "Tap SCAN"
            self._rows.append({
                "label": "  No devices", "type": "info", "value": hint,
            })

    @staticmethod
    def _signal_bars(signal: int) -> str:
        if signal >= 75:
            return "████"
        if signal >= 50:
            return "███-"
        if signal >= 25:
            return "██--"
        return "█---"

    @staticmethod
    def _bt_icon(icon: str) -> str:
        mapping = {
            "input-keyboard": "KB",
            "input-mouse":    "MS",
            "input-gaming":   "GP",
            "audio-headset":  "AU",
            "audio-headphones": "AU",
            "audio-card":     "AU",
            "phone":          "PH",
            "computer":       "PC",
        }
        return mapping.get(icon, "??")

    # ── Tab switching ────────────────────────────────────────────────

    def _set_tab(self, tab: str):
        if tab == self._tab:
            return
        self._tab = tab
        self._scroll_y = 0
        if tab == TAB_STATUS:
            self._refresh_status()
        elif tab == TAB_WIFI:
            self._refresh_status()
            self._start_wifi_scan()
        elif tab == TAB_BT:
            self._refresh_status()
            self._refresh_bt_devices()

    # ── Event handling ───────────────────────────────────────────────

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.app.switch_screen("settings")
                return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Back button (top-left in header)
            back_rect = pygame.Rect(8, 6, 70, 28)
            if back_rect.collidepoint(mx, my):
                self.app.switch_screen("settings")
                return

            # Tab bar
            tab_y = 44
            tab_h = 34
            tabs = [
                ("STATUS", TAB_STATUS),
                ("WIFI",   TAB_WIFI),
                ("BLUETOOTH", TAB_BT),
            ]
            tab_w = theme.SCREEN_WIDTH // len(tabs)
            for i, (_, key) in enumerate(tabs):
                tr = pygame.Rect(i * tab_w, tab_y, tab_w, tab_h)
                if tr.collidepoint(mx, my):
                    self._set_tab(key)
                    return

            # Start drag-scroll tracking on content area
            content_top = tab_y + tab_h + 4
            if my > content_top and my < theme.SCREEN_HEIGHT - theme.NAV_HEIGHT:
                self._drag_start_y = my
                self._drag_start_scroll = self._scroll_y
                self._drag_active = True
                self._drag_moved = False

            # Row interactions — handled on MOUSEBUTTONUP to allow drag vs tap
            # But record the press target for later
            self._pending_press_y = my

        if event.type == pygame.MOUSEMOTION and self._drag_active:
            dy = event.pos[1] - self._drag_start_y
            if abs(dy) > 6:
                self._drag_moved = True
                self._scroll_y = max(0, self._drag_start_scroll - dy)

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._drag_active and not self._drag_moved:
                # Treat as a tap — dispatch to rows
                self._handle_row_click(event.pos)
            self._drag_active = False

        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
            self._scroll_y += -30 if event.button == 4 else 30
            self._scroll_y = max(0, self._scroll_y)

    def _handle_row_click(self, pos):
        mx, my = pos
        content_y = 82  # Below header + tabs
        row_h = self._row_h
        ctrl_x = theme.SCREEN_WIDTH - 140

        for i, row in enumerate(self._rows):
            ry = content_y + i * row_h - self._scroll_y
            if ry < content_y - row_h or ry > theme.SCREEN_HEIGHT - theme.NAV_HEIGHT:
                continue
            row_rect = pygame.Rect(0, ry, theme.SCREEN_WIDTH, row_h)
            if not row_rect.collidepoint(mx, my):
                continue

            rtype = row["type"]

            if rtype == "toggle":
                # Toggle area on right
                toggle_rect = pygame.Rect(ctrl_x, ry + 4, 70, row_h - 8)
                if toggle_rect.collidepoint(mx, my):
                    row["action"]()
                    self._flash(f"row_{i}")
                return

            if rtype == "button":
                try:
                    row["action"]()
                    self._flash(f"row_{i}")
                except Exception as e:
                    print(f"IO button error: {e}", flush=True)
                return

            if rtype == "wifi_entry":
                self._flash(f"row_{i}")
                self._prompt_wifi_password(row["ssid"], row["security"])
                return

            if rtype == "bt_entry":
                # Whole row click — pair if not paired, connect/disconnect if paired
                self._flash(f"row_{i}")
                if not row["paired"]:
                    self._bt_pair(row["mac"], row["name"])
                elif row["connected"]:
                    # Check which part of the row they tapped
                    # Right-side "REMOVE" button first
                    rm_rect = pygame.Rect(theme.SCREEN_WIDTH - 80, ry + 4, 68, row_h - 8)
                    if rm_rect.collidepoint(mx, my):
                        self._bt_remove(row["mac"], row["name"])
                    else:
                        self._bt_disconnect(row["mac"], row["name"])
                else:
                    rm_rect = pygame.Rect(theme.SCREEN_WIDTH - 80, ry + 4, 68, row_h - 8)
                    if rm_rect.collidepoint(mx, my):
                        self._bt_remove(row["mac"], row["name"])
                    else:
                        self._bt_connect(row["mac"], row["name"])
                return

    def update(self):
        # Periodically refresh bluetooth device list while scanning
        if self._tab == TAB_BT and self.app.bluetooth and self.app.bluetooth.is_scanning:
            now = pygame.time.get_ticks()
            if now - getattr(self, "_last_bt_poll", 0) > 1500:
                self._last_bt_poll = now
                # List already being refreshed by the scanner thread — just re-render

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_med = theme.font("medium")
        f_small = theme.font("small")
        f_tiny = theme.font("tiny")
        f_title = theme.font("title")

        # Custom header: back button | title
        header_rect = pygame.Rect(0, 0, theme.SCREEN_WIDTH, 38)
        pygame.draw.rect(surface, theme.BG_PANEL, header_rect)
        pygame.draw.line(surface, theme.BORDER, (0, 38), (theme.SCREEN_WIDTH, 38))

        # Back button
        back_rect = pygame.Rect(8, 6, 70, 28)
        pygame.draw.rect(surface, theme.BUTTON_BG, back_rect, border_radius=6)
        pygame.draw.rect(surface, theme.BORDER, back_rect, 1, border_radius=6)
        back_surf = f_small.render("< BACK", True, theme.TEXT)
        surface.blit(back_surf, back_surf.get_rect(center=back_rect.center))

        # Title — pushed past the back button
        title_surf = f_title.render("IO & CONNECTIVITY", True, theme.ACCENT)
        surface.blit(title_surf, (back_rect.right + 14, 5))

        # Tab bar
        tab_y = 44
        tab_h = 34
        tabs = [
            ("STATUS",   TAB_STATUS),
            ("WIFI",     TAB_WIFI),
            ("BLUETOOTH", TAB_BT),
        ]
        tab_w = theme.SCREEN_WIDTH // len(tabs)
        for i, (label, key) in enumerate(tabs):
            tr = pygame.Rect(i * tab_w, tab_y, tab_w, tab_h)
            active = (key == self._tab)
            bg = theme.ACCENT if active else theme.BG_PANEL
            pygame.draw.rect(surface, bg, tr)
            pygame.draw.line(surface, theme.BORDER, tr.bottomleft, tr.bottomright)
            if i > 0:
                pygame.draw.line(surface, theme.BORDER, tr.topleft, tr.bottomleft)
            lbl_color = theme.BG if active else theme.TEXT
            lbl = f_med.render(label, True, lbl_color)
            surface.blit(lbl, lbl.get_rect(center=tr.center))

        # Build rows for current tab
        if self._tab == TAB_STATUS:
            self._build_status_rows()
        elif self._tab == TAB_WIFI:
            self._build_wifi_rows()
        else:
            self._build_bt_rows()

        # Content with clipping
        content_y = tab_y + tab_h + 4
        content_h = theme.SCREEN_HEIGHT - theme.NAV_HEIGHT - content_y
        content_rect = pygame.Rect(0, content_y, theme.SCREEN_WIDTH, content_h)
        clip = surface.get_clip()
        surface.set_clip(content_rect)

        row_h = self._row_h
        ctrl_x = theme.SCREEN_WIDTH - 140

        for i, row in enumerate(self._rows):
            ry = content_y + i * row_h - self._scroll_y
            if ry + row_h < content_y or ry > content_rect.bottom:
                continue

            # Alternating backgrounds
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

            # Active row highlight
            if row.get("is_active"):
                pygame.draw.rect(surface, theme.ACCENT_DIM,
                                 (8, ry + 2, theme.SCREEN_WIDTH - 16, row_h - 4),
                                 2, border_radius=4)

            # Label
            label_surf = f_med.render(row["label"], True, theme.TEXT)
            surface.blit(label_surf, (20, ry + (row_h - label_surf.get_height()) // 2))

            if rtype == "toggle":
                is_on = row["value"]
                tw = 70
                toggle_rect = pygame.Rect(ctrl_x, ry + 4, tw, row_h - 8)
                if is_on:
                    pygame.draw.rect(surface, theme.GREEN, toggle_rect, border_radius=6)
                    lbl = f_small.render("ON", True, theme.BG)
                else:
                    pygame.draw.rect(surface, theme.BG_LIGHTER, toggle_rect, border_radius=6)
                    pygame.draw.rect(surface, theme.BORDER, toggle_rect, 1, border_radius=6)
                    lbl = f_small.render("OFF", True, theme.TEXT_DIM)
                surface.blit(lbl, lbl.get_rect(center=toggle_rect.center))

            elif rtype == "button":
                flashing = (self._flash_key == f"row_{i}"
                            and pygame.time.get_ticks() < self._flash_until)
                btn_rect = pygame.Rect(ctrl_x, ry + 4, 90, row_h - 8)
                btn_bg = theme.ACCENT if flashing else theme.ACCENT_DIM
                pygame.draw.rect(surface, btn_bg, btn_rect, border_radius=6)
                lbl = f_small.render(row["btn_label"], True, theme.TEXT_BRIGHT)
                surface.blit(lbl, lbl.get_rect(center=btn_rect.center))
                val = row.get("value")
                if val:
                    val_surf = f_tiny.render(str(val)[:30], True, theme.TEXT_DIM)
                    val_x = btn_rect.x - val_surf.get_width() - 8
                    surface.blit(val_surf, (val_x, ry + (row_h - val_surf.get_height()) // 2))

            elif rtype == "wifi_entry":
                val_str = str(row["value"])
                val_surf = f_small.render(val_str, True, theme.TEXT_DIM)
                vx = theme.SCREEN_WIDTH - 20 - val_surf.get_width()
                surface.blit(val_surf, (vx, ry + (row_h - val_surf.get_height()) // 2))

            elif rtype == "bt_entry":
                # Connection status tag + REMOVE button on right
                tag = "CONNECTED" if row["connected"] else ("PAIRED" if row["paired"] else "AVAILABLE")
                tag_color = theme.GREEN if row["connected"] else (
                    theme.ACCENT if row["paired"] else theme.TEXT_DIM)
                tag_surf = f_tiny.render(tag, True, tag_color)
                # REMOVE button for paired entries
                if row["paired"]:
                    rm_rect = pygame.Rect(theme.SCREEN_WIDTH - 80, ry + 4, 68, row_h - 8)
                    pygame.draw.rect(surface, theme.BUTTON_BG, rm_rect, border_radius=6)
                    pygame.draw.rect(surface, theme.BORDER, rm_rect, 1, border_radius=6)
                    rm_lbl = f_tiny.render("FORGET", True, theme.TEXT_DIM)
                    surface.blit(rm_lbl, rm_lbl.get_rect(center=rm_rect.center))
                    tag_x = rm_rect.x - tag_surf.get_width() - 12
                else:
                    tag_x = theme.SCREEN_WIDTH - 20 - tag_surf.get_width()
                surface.blit(tag_surf, (tag_x, ry + (row_h - tag_surf.get_height()) // 2))

            elif rtype == "info":
                val_str = str(row["value"])
                val_surf = f_small.render(val_str, True, theme.TEXT_DIM)
                vx = theme.SCREEN_WIDTH - 20 - val_surf.get_width()
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

        # Action message toast (top-right, under header)
        if pygame.time.get_ticks() < self._action_msg_until and self._action_msg:
            toast = f_small.render(self._action_msg[:60], True, theme.TEXT_BRIGHT)
            tw = toast.get_width() + 16
            th = toast.get_height() + 8
            tx = theme.SCREEN_WIDTH - tw - 12
            ty = 4
            bg = pygame.Surface((tw, th), pygame.SRCALPHA)
            bg.fill((*theme.ACCENT_DIM, 220))
            surface.blit(bg, (tx, ty))
            pygame.draw.rect(surface, theme.ACCENT, (tx, ty, tw, th), 1,
                             border_radius=4)
            surface.blit(toast, (tx + 8, ty + 4))
