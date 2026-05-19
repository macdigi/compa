"""Compa Studio touchscreen mirror of Push 2's clip/session view.

When Push 2 control is active, this screen shows the same content the
Push 2 OLED is showing, plus the 8x8 clip grid with the same colors
as the pads. Tap-to-launch matches Push 2 behavior.
"""
from __future__ import annotations

import pygame

from session.clip import ClipState
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
        font = pygame.font.SysFont("Arial", 15)
        labels = {
            "overview": "STUDIO OVERVIEW",
            "instruments": "INSTRUMENTS",
            "performer": "PERFORMER",
            "settings": "STUDIO SETTINGS",
        }
        surface.blit(font_big.render(labels.get(tab, tab.upper()), True,
                                     (232, 234, 242)), (20, top + 8))
        if tab == "overview" and sess is not None:
            items = [
                f"Tracks: {len(sess.tracks)}",
                f"Scenes: {len(sess.scenes)}",
                f"Tempo: {sess.bpm:.1f} BPM",
                f"Audio: {'running' if self._clip_audio_running() else 'stopped'}",
            ]
        elif tab == "instruments":
            items = ["Sample Rack", "Drum Synth", "Mono Synth", "Poly Synth"]
        elif tab == "performer":
            items = ["Pattern Targets", "Mutations", "FX Lanes", "Takes"]
        else:
            items = ["Audio Gate", "Controller Map", "Targets", "Project"]
        y = top + 48
        for item in items:
            rect = pygame.Rect(20, y, surface.get_width() - 40, 34)
            pygame.draw.rect(surface, (24, 26, 36), rect, border_radius=4)
            pygame.draw.rect(surface, (52, 58, 78), rect, 1, border_radius=4)
            surface.blit(font.render(item, True, (210, 214, 226)),
                         (rect.x + 12, rect.y + 8))
            y += 42

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
