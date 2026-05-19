"""Compa Studio touchscreen mirror of Push 2's clip/session view.

When Push 2 control is active, this screen shows the same content the
Push 2 OLED is showing, plus the 8x8 clip grid with the same colors
as the pads. Tap-to-launch matches Push 2 behavior.
"""
from __future__ import annotations

import pygame

from session.clip import ClipState
from session.track import TrackTarget
from engine.studio_performer import (
    PatternPerformer,
    SP404_BEAT_BASS_TARGET,
    confirmed_sp404_beat_bass_spec,
    generate_sp404_beat_bass_variation,
)
from engine.studio_targets import (
    availability_label,
    capability_for,
    known_targets,
    target_for_track,
)
from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index, build_palette


class ClipScreen:
    name = "studio"
    TABS = (
        ("overview", "OVERVIEW"),
        ("clips", "CLIPS"),
        ("instruments", "INSTRUMENTS"),
        ("performer", "PERFORMER"),
        ("settings", "SETTINGS"),
    )

    def __init__(self, app) -> None:
        self.app = app
        self._palette = build_palette()
        self._scene_offset = 0
        self._track_offset = 0
        self._tab = "overview"
        self._buttons: dict[str, pygame.Rect] = {}
        self._tab_rects: dict[str, pygame.Rect] = {}
        self._clip_grid_geometry: tuple[int, int, int, int] = (16, 160, 40, 40)
        self._scene_button_top = 0
        self._scene_button_w = 0
        self._performer_message = ""
        self._performer_seed = 0

    # ── Lifecycle ─────────────────────────────────────────────────
    def on_enter(self) -> None:
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()

    def on_exit(self) -> None:
        # Keep explicit user-started Studio audio running in the background.
        pass

    # ── Color helpers ─────────────────────────────────────────────
    def _palette_rgb(self, idx: int) -> tuple[int, int, int]:
        r, g, b, _ = self._palette[idx]
        return r, g, b

    def _clip_audio_running(self) -> bool:
        stream = getattr(self.app, "clip_stream", None)
        return bool(stream is not None and getattr(stream, "running", False))

    def _studio_audio_supported(self) -> bool:
        supported = getattr(self.app, "_studio_audio_supported", None)
        return bool(supported()) if callable(supported) else True

    def _pi_generation(self) -> int | None:
        generation = getattr(self.app, "_raspberry_pi_generation", None)
        return generation() if callable(generation) else None

    def _availability(self, capability) -> str:
        return availability_label(
            capability,
            pi_generation=self._pi_generation(),
            studio_audio_enabled=self._studio_audio_supported(),
        )

    def _ensure_audio_started(self) -> bool:
        if not self._studio_audio_supported():
            return False
        engine = getattr(self.app, "clip_engine", None)
        stream = getattr(self.app, "clip_stream", None)
        if stream is None:
            if engine is not None:
                engine.active = True
            return engine is not None
        if not getattr(stream, "running", False):
            if not stream.start():
                return False
        if engine is not None:
            engine.active = True
        return True

    def _stop_all(self) -> None:
        ctrl = getattr(self.app, "push2_control", None)
        engine = getattr(self.app, "clip_engine", None)
        self._performer_player().stop()
        if ctrl is not None:
            ctrl.stop_all_clips()
        elif engine is not None:
            engine.stop_all(0.0)
        if engine is not None:
            engine.all_notes_off()

    def _toggle_audio(self) -> None:
        stream = getattr(self.app, "clip_stream", None)
        engine = getattr(self.app, "clip_engine", None)
        if stream is not None and getattr(stream, "running", False):
            stream.stop()
            return
        if not self._ensure_audio_started() and engine is not None:
            engine.active = False

    def _performer_player(self) -> PatternPerformer:
        player = getattr(self.app, "studio_performer", None)
        if player is None:
            player = PatternPerformer()
            setattr(self.app, "studio_performer", player)
        return player

    def _selected_track_index(self, sess) -> int:
        ctrl = getattr(self.app, "push2_control", None)
        idx = getattr(ctrl, "selected_track", 0) if ctrl is not None else 0
        try:
            idx = int(idx or 0)
        except Exception:
            idx = 0
        if not sess.tracks:
            return 0
        return max(0, min(len(sess.tracks) - 1, idx))

    def _set_selected_track_target(self, sess, target: TrackTarget) -> None:
        track_idx = self._selected_track_index(sess)
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None and hasattr(ctrl, "set_track_target"):
            ctrl.set_track_target(track_idx, target)
        elif 0 <= track_idx < len(sess.tracks):
            sess.tracks[track_idx].target = target
        self._performer_message = f"target set: {target.label or target.key}"

    def _sp_beat_bass_target(self) -> TrackTarget:
        return TrackTarget(
            SP404_BEAT_BASS_TARGET,
            "SP-404 A1-A6 Beat+Bass",
            {
                "project": 3,
                "bank": "A",
                "drum_pads": "A1-A5",
                "chromatic_pad": "A6",
            },
        )

    def _cycle_selected_target(self, sess) -> None:
        choices = [
            capability for category in ("external", "network")
            for capability in known_targets(category)
        ]
        if not choices:
            return
        track = sess.tracks[self._selected_track_index(sess)]
        current = target_for_track(track).key
        keys = [capability.key for capability in choices]
        next_idx = (keys.index(current) + 1) % len(keys) if current in keys else 0
        capability = choices[next_idx]
        params = {}
        if capability.key == SP404_BEAT_BASS_TARGET:
            params = self._sp_beat_bass_target().params
        self._set_selected_track_target(
            sess, TrackTarget(capability.key, capability.label, params))

    def _midi_sender_for_target(self, target_key: str):
        connections = getattr(self.app, "_midi_connections", {}) or {}
        device_key = ""
        if target_key.startswith("external.sp404"):
            device_key = "SP-404MKII"
        elif target_key.startswith("external.p6"):
            device_key = "P-6"
        if not device_key:
            return None, ""
        conn = connections.get(device_key)
        out = getattr(conn, "_out", None)
        sender = getattr(out, "send_message", None)
        if callable(sender):
            return sender, device_key
        return None, device_key

    def _performer_bpm(self, sess) -> float:
        try:
            return float(sess.bpm)
        except Exception:
            return 94.0

    def _current_performer_spec(self):
        spec = getattr(self.app, "studio_performer_spec", None)
        if spec is None:
            spec = confirmed_sp404_beat_bass_spec()
            setattr(self.app, "studio_performer_spec", spec)
        return spec

    def _set_current_performer_spec(self, spec) -> None:
        setattr(self.app, "studio_performer_spec", spec)

    def _play_sp_beat_bass(self, sess) -> None:
        target = self._sp_beat_bass_target()
        self._set_selected_track_target(sess, target)
        sender, port_label = self._midi_sender_for_target(target.key)
        if sender is None:
            self._performer_message = f"{port_label or 'SP-404'} MIDI unavailable"
            return
        spec = self._current_performer_spec()
        try:
            self._performer_player().play(
                spec,
                send_message=sender,
                target_key=target.key,
                port_label=port_label,
                loops=0,
                bpm_provider=lambda: self._performer_bpm(sess),
            )
            self._performer_message = f"playing {spec.name}"
        except Exception as exc:
            self._performer_message = f"play failed: {exc}"

    def _generate_sp_variation(self, sess) -> None:
        self._performer_seed += 1
        spec = generate_sp404_beat_bass_variation(self._performer_seed)
        self._set_current_performer_spec(spec)
        status = self._performer_player().status()
        self._performer_message = f"generated {spec.name}"
        if status["running"]:
            self._play_sp_beat_bass(sess)

    def _stop_performer(self) -> None:
        self._performer_player().stop()
        self._performer_message = "performer stopped"

    def _toggle_performer_mute(self) -> None:
        muted = self._performer_player().toggle_mute()
        self._performer_message = "performer muted" if muted else "performer live"

    def _button(self, surface: pygame.Surface, key: str, rect: pygame.Rect,
                label: str, *, active: bool = False,
                danger: bool = False) -> None:
        self._buttons[key] = rect
        if danger:
            bg = (108, 36, 42)
            edge = (210, 92, 100)
        elif active:
            bg = (42, 88, 78)
            edge = (90, 210, 170)
        else:
            bg = (34, 36, 48)
            edge = (74, 80, 104)
        pygame.draw.rect(surface, bg, rect, border_radius=4)
        pygame.draw.rect(surface, edge, rect, 1, border_radius=4)
        font = pygame.font.SysFont("Arial", 13, bold=True)
        txt = font.render(label, True, (238, 238, 244))
        surface.blit(txt, txt.get_rect(center=rect.center))

    def _draw_tabs(self, surface: pygame.Surface, y: int,
                   width: int) -> int:
        self._tab_rects.clear()
        font = pygame.font.SysFont("Arial", 12, bold=True)
        x = 16
        gap = 4
        tab_w = max(76, min(120, (width - 32 - gap * (len(self.TABS) - 1)) // len(self.TABS)))
        for key, label in self.TABS:
            rect = pygame.Rect(x, y, tab_w, 28)
            self._tab_rects[key] = rect
            active = key == self._tab
            bg = (72, 86, 116) if active else (26, 28, 38)
            edge = (130, 160, 220) if active else (56, 60, 76)
            pygame.draw.rect(surface, bg, rect, border_radius=4)
            pygame.draw.rect(surface, edge, rect, 1, border_radius=4)
            txt = font.render(label, True, (235, 238, 245))
            surface.blit(txt, txt.get_rect(center=rect.center))
            x += tab_w + gap
        return y + 36

    def _draw_status_strip(self, surface: pygame.Surface, y: int) -> int:
        self._button(
            surface, "toggle_audio", pygame.Rect(16, y, 96, 30),
            "AUDIO ON" if self._clip_audio_running() else "AUDIO OFF",
            active=self._clip_audio_running(),
        )
        self._button(
            surface, "stop_all", pygame.Rect(120, y, 88, 30),
            "STOP ALL", danger=True,
        )
        ctrl = getattr(self.app, "push2_control", None)
        engine = getattr(self.app, "clip_engine", None)
        sess = ctrl.session if ctrl else (engine.session if engine else None)
        font = pygame.font.SysFont("Arial", 13)
        status = "Pi 3 gate" if not self._studio_audio_supported() else (
            "stream running" if self._clip_audio_running() else "stream stopped")
        if sess is not None:
            status = f"{len(sess.tracks)} tracks  {len(sess.scenes)} scenes  {sess.bpm:.1f} BPM  {status}"
        surface.blit(font.render(status, True, (176, 184, 198)), (220, y + 8))
        return y + 42

    # ── Drawing ───────────────────────────────────────────────────
    def draw(self, surface: pygame.Surface) -> None:
        from ui import theme

        ctrl = getattr(self.app, "push2_control", None)
        engine = getattr(self.app, "clip_engine", None)
        sess = ctrl.session if ctrl else (engine.session if engine else None)
        self._buttons.clear()

        surface.fill((10, 10, 16))

        if sess is None:
            font = pygame.font.SysFont("Arial", 28)
            surf = font.render("Clip engine not initialized", True, (220, 220, 220))
            surface.blit(surf, (40, 40))
            return

        # Top bar
        f_big = pygame.font.SysFont("Arial", 24, bold=True)
        f_med = pygame.font.SysFont("Arial", 18)
        f_sm = pygame.font.SysFont("Arial", 13)

        title = f"STUDIO · {sess.name}   {sess.bpm:.1f} BPM"
        surface.blit(f_big.render(title, True, (230, 230, 240)), (16, 12))
        if ctrl is not None:
            mode_str = f"Push 2 mode: {ctrl.mode_name}"
            surface.blit(f_med.render(mode_str, True, (160, 200, 255)),
                         (16, 44))
        content_top = self._draw_tabs(surface, 72, surface.get_width())
        content_top = self._draw_status_strip(surface, content_top)

        if self._tab != "clips":
            self._draw_placeholder_tab(surface, self._tab, content_top, sess)
            return

        # 8x8 clip grid
        grid_left = 16
        grid_top = content_top + 8
        grid_w = surface.get_width() - 32
        grid_h = surface.get_height() - grid_top - 58
        cell_w = grid_w // 8
        cell_h = grid_h // 8
        self._clip_grid_geometry = (grid_left, grid_top, cell_w, cell_h)

        sched = engine.scheduler if engine else None

        for c in range(8):
            ti = c + self._track_offset
            if ti >= len(sess.tracks):
                continue
            track = sess.tracks[ti]
            track_color = self._palette_rgb(track.color
                                            or track_color_index(ti))

            # Header
            hx = grid_left + c * cell_w
            pygame.draw.rect(surface, track_color,
                             (hx + 2, grid_top - 28, cell_w - 4, 14))
            surface.blit(f_sm.render(track.name[:12], True, (220, 220, 220)),
                         (hx + 4, grid_top - 14))

            for r in range(8):
                scene_idx = r + self._scene_offset
                if scene_idx >= len(sess.scenes):
                    continue
                cy = grid_top + r * cell_h
                clip = track.clips[scene_idx] if scene_idx < len(track.clips) else None
                rect = pygame.Rect(hx + 2, cy + 2, cell_w - 4, cell_h - 4)
                if (engine is not None
                        and engine.is_recording(ti, scene_idx)):
                    pygame.draw.rect(surface, (200, 40, 40), rect,
                                     border_radius=4)
                    surface.blit(f_sm.render("REC", True, (255, 255, 255)),
                                 (rect.x + 4, rect.y + 4))
                    continue
                if clip is None:
                    pygame.draw.rect(surface, (28, 28, 38), rect, border_radius=4)
                    pygame.draw.rect(surface, (50, 50, 60), rect, 1,
                                     border_radius=4)
                    continue
                color_idx = clip.color or track.color or track_color_index(ti)
                rgb = self._palette_rgb(color_idx)
                state = sched.get_state(ti, scene_idx) if sched else ClipState.STOPPED
                if state == ClipState.PLAYING:
                    pygame.draw.rect(surface, rgb, rect, border_radius=4)
                    # Brighten label
                    surface.blit(f_sm.render(clip.name or "▶", True, (0, 0, 0)),
                                 (rect.x + 4, rect.y + 4))
                elif state == ClipState.QUEUED:
                    pygame.draw.rect(surface, (180, 200, 255), rect,
                                     border_radius=4)
                    surface.blit(f_sm.render(clip.name or "...", True, (0, 0, 0)),
                                 (rect.x + 4, rect.y + 4))
                else:
                    dim = tuple(c // 2 for c in rgb)
                    pygame.draw.rect(surface, dim, rect, border_radius=4)
                    pygame.draw.rect(surface, rgb, rect, 1, border_radius=4)
                    surface.blit(f_sm.render(clip.name or "·", True, rgb),
                                 (rect.x + 4, rect.y + 4))

        # Bottom strip — scene-launch buttons (mirror Push 2 right column)
        scene_btn_top = grid_top + 8 * cell_h + 8
        self._scene_button_top = scene_btn_top
        self._scene_button_w = grid_w // 8
        for r in range(8):
            scene_idx = r + self._scene_offset
            if scene_idx >= len(sess.scenes):
                continue
            scene = sess.scenes[scene_idx]
            x = grid_left + r * (grid_w // 8)
            rect = pygame.Rect(x + 4, scene_btn_top, (grid_w // 8) - 8, 36)
            pygame.draw.rect(surface, (40, 40, 60), rect, border_radius=4)
            surface.blit(f_sm.render(f"Scene {scene_idx+1}", True, (220, 220, 220)),
                         (rect.x + 6, rect.y + 8))

    def _draw_placeholder_tab(self, surface: pygame.Surface, tab: str,
                              top: int, sess) -> None:
        font_big = pygame.font.SysFont("Arial", 24, bold=True)
        font = pygame.font.SysFont("Arial", 14)
        font_sm = pygame.font.SysFont("Arial", 12)
        labels = {
            "overview": "STUDIO OVERVIEW",
            "instruments": "INSTRUMENTS",
            "performer": "PERFORMER",
            "settings": "STUDIO SETTINGS",
        }
        surface.blit(font_big.render(labels.get(tab, tab.upper()), True,
                                     (232, 234, 242)), (20, top + 8))
        if tab == "overview" and sess is not None:
            items = []
            for track in sess.tracks[:8]:
                target = target_for_track(track)
                capability = capability_for(target)
                features = ", ".join(capability.feature_labels()[:3])
                items.append((
                    f"{track.name}: {target.label or capability.label}",
                    f"{self._availability(capability)} - {features}",
                ))
            columns = 2
        elif tab == "instruments":
            items = []
            for capability in known_targets("internal"):
                if capability.key == "internal.midi":
                    continue
                features = ", ".join(capability.feature_labels()[:3])
                items.append((
                    capability.label,
                    f"{self._availability(capability)} - {features}",
                ))
            columns = 2
        elif tab == "performer":
            self._draw_performer_tab(surface, top, sess)
            return
        else:
            pi = self._pi_generation()
            items = [
                ("Audio Gate", "ready" if self._studio_audio_supported()
                 else "Pi 3 internal audio gated"),
                ("Controller Map", "Push 2 and touch share Studio targets"),
                ("Targets", f"{len(known_targets())} capability profiles"),
                ("Project", f"Pi generation: {pi if pi is not None else 'unknown'}"),
            ]
            columns = 1
        y = top + 48
        gap = 8
        col_w = (surface.get_width() - 40 - gap * (columns - 1)) // columns
        row_h = 46
        for idx, item in enumerate(items):
            title, detail = item if isinstance(item, tuple) else (item, "")
            col = idx % columns
            row = idx // columns
            x = 20 + col * (col_w + gap)
            rect = pygame.Rect(x, y + row * (row_h + 6), col_w, row_h)
            pygame.draw.rect(surface, (24, 26, 36), rect, border_radius=4)
            pygame.draw.rect(surface, (52, 58, 78), rect, 1, border_radius=4)
            surface.blit(font.render(str(title)[:28], True, (224, 228, 238)),
                         (rect.x + 10, rect.y + 7))
            if detail:
                surface.blit(font_sm.render(str(detail)[:42], True,
                                            (156, 166, 184)),
                             (rect.x + 10, rect.y + 27))

    def _draw_performer_tab(self, surface: pygame.Surface, top: int, sess) -> None:
        font = pygame.font.SysFont("Arial", 14)
        font_sm = pygame.font.SysFont("Arial", 12)
        track_idx = self._selected_track_index(sess)
        track = sess.tracks[track_idx] if sess.tracks else None
        target = target_for_track(track) if track is not None else self._sp_beat_bass_target()
        capability = capability_for(target)
        status = self._performer_player().status()
        spec = self._current_performer_spec()
        sender, port_label = self._midi_sender_for_target(SP404_BEAT_BASS_TARGET)
        midi_status = "ready" if sender is not None else f"{port_label or 'SP-404'} missing"
        rows = [
            (f"Track {track_idx + 1}", track.name if track is not None else "none"),
            ("Target", target.label or capability.label),
            ("Pattern", spec.name),
            ("Tempo", f"follows Studio BPM: {self._performer_bpm(sess):.1f}"),
            ("MIDI", midi_status),
        ]
        if self._performer_message:
            rows.append(("Status", self._performer_message))
        elif status["last_error"]:
            rows.append(("Status", status["last_error"]))
        elif status["running"]:
            rows.append(("Status", "playing" if not status["muted"] else "muted"))

        y = top + 48
        row_h = 38
        for title, detail in rows:
            rect = pygame.Rect(20, y, surface.get_width() - 40, row_h)
            pygame.draw.rect(surface, (24, 26, 36), rect, border_radius=4)
            pygame.draw.rect(surface, (52, 58, 78), rect, 1, border_radius=4)
            surface.blit(font.render(str(title)[:18], True, (224, 228, 238)),
                         (rect.x + 10, rect.y + 6))
            surface.blit(font_sm.render(str(detail)[:62], True,
                                        (156, 166, 184)),
                         (rect.x + 136, rect.y + 8))
            y += row_h + 6

        button_top = y + 8
        x = 20
        self._button(surface, "performer_target_next",
                     pygame.Rect(x, button_top, 112, 34), "TARGET +")
        x += 120
        self._button(surface, "performer_assign_sp",
                     pygame.Rect(x, button_top, 140, 34), "SET SP A1-A6",
                     active=target.key == SP404_BEAT_BASS_TARGET)
        x += 148
        self._button(surface, "performer_generate",
                     pygame.Rect(x, button_top, 72, 34), "GEN")
        x += 80
        self._button(surface, "performer_play_v3",
                     pygame.Rect(x, button_top, 78, 34), "PLAY",
                     active=bool(status["running"]))
        x += 86
        self._button(surface, "performer_mute",
                     pygame.Rect(x, button_top, 92, 34),
                     "UNMUTE" if status["muted"] else "MUTE",
                     active=bool(status["muted"]))
        x += 100
        self._button(surface, "performer_stop",
                     pygame.Rect(x, button_top, 84, 34), "STOP",
                     danger=True)

    # ── Touch ────────────────────────────────────────────────────
    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type != pygame.MOUSEBUTTONDOWN:
            return False
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is None:
            return False
        sess = ctrl.session

        mx, my = event.pos
        for key, rect in self._tab_rects.items():
            if rect.collidepoint(mx, my):
                self._tab = key
                return True
        for key, rect in self._buttons.items():
            if not rect.collidepoint(mx, my):
                continue
            if key == "stop_all":
                self._stop_all()
                return True
            if key == "toggle_audio":
                self._toggle_audio()
                return True
            if key == "performer_target_next":
                self._cycle_selected_target(sess)
                return True
            if key == "performer_assign_sp":
                self._set_selected_track_target(sess, self._sp_beat_bass_target())
                return True
            if key == "performer_play_v3":
                self._play_sp_beat_bass(sess)
                return True
            if key == "performer_generate":
                self._generate_sp_variation(sess)
                return True
            if key == "performer_mute":
                self._toggle_performer_mute()
                return True
            if key == "performer_stop":
                self._stop_performer()
                return True

        if self._tab != "clips":
            return True

        grid_left, grid_top, cell_w, cell_h = self._clip_grid_geometry

        # Grid hits
        if (grid_left <= mx < grid_left + 8 * cell_w
                and grid_top <= my < grid_top + 8 * cell_h):
            col = (mx - grid_left) // cell_w
            row = (my - grid_top) // cell_h
            track_idx = col + self._track_offset
            scene_idx = row + self._scene_offset
            if (track_idx < len(sess.tracks)
                    and scene_idx < len(sess.scenes)):
                clip = sess.get_clip(track_idx, scene_idx)
                if clip is not None:
                    self._ensure_audio_started()
                    ctrl.launch_clip(track_idx, scene_idx)
                else:
                    ctrl.select_cell(track_idx, scene_idx)
            return True

        # Scene buttons
        scene_btn_top = self._scene_button_top
        if scene_btn_top <= my < scene_btn_top + 36:
            if self._scene_button_w <= 0:
                return True
            r = (mx - grid_left) // self._scene_button_w
            scene_idx = r + self._scene_offset
            if 0 <= scene_idx < len(sess.scenes):
                self._ensure_audio_started()
                ctrl.launch_scene(scene_idx)
            return True

        return False
