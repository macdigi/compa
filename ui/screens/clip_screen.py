"""Compa Studio touchscreen mirror of Push 2's clip/session view.

When Push 2 control is active, this screen shows the same content the
Push 2 OLED is showing, plus the 8x8 clip grid with the same colors
as the pads. Tap-to-launch matches Push 2 behavior.
"""
from __future__ import annotations

import os

import pygame

from engine.ai_pattern import install_step_grid
from engine.compa_step_persistence import load as load_step_grids
from engine.compa_step_persistence import save as save_step_grids
from session.clip import ClipState
from session.track import TrackTarget
from engine.studio_performer import (
    MAX_PERFORMER_TAKES,
    PERFORMER_LANE_LABELS,
    PERFORMER_LANES,
    PatternPerformer,
    SP404_BEAT_BASS_TARGET,
    SP404_VARIATION_STYLES,
    confirmed_sp404_beat_bass_spec,
    feel_from_performer_take,
    generate_sp404_beat_bass_variation,
    lane_controls_from_performer_take,
    normalized_lane_controls,
    normalized_generator_controls,
    normalized_performer_feel,
    performer_take_from_spec,
    spec_from_performer_take,
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
        self._performer_style_idx = 0
        self._performer_take_idx = 0
        self._performer_swing = 56.0
        self._performer_humanize = 0.0
        self._performer_gate = 1.0
        self._performer_density = 60.0
        self._performer_complexity = 45.0
        self._performer_fill = 35.0
        self._performer_bass_activity = 60.0
        self._performer_variation = 50.0
        self._performer_lane_idx = 0
        self._performer_lane_controls_state = normalized_lane_controls()

    # ── Lifecycle ─────────────────────────────────────────────────
    def on_enter(self) -> None:
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()
        self._sync_push2_mode()

    def on_exit(self) -> None:
        # Keep explicit user-started Studio audio running in the background.
        pass

    def _sync_push2_mode(self) -> None:
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is None:
            return
        desired = "performer" if self._tab == "performer" else "session"
        if getattr(ctrl, "mode_name", "") != desired:
            ctrl.switch_mode(desired)

    def _set_tab(self, tab: str) -> None:
        self._tab = tab
        self._sync_push2_mode()

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

    def _performer_style(self) -> str:
        if not SP404_VARIATION_STYLES:
            return "busy_boom_bap"
        return SP404_VARIATION_STYLES[
            self._performer_style_idx % len(SP404_VARIATION_STYLES)]

    @staticmethod
    def _style_label(style: str) -> str:
        return style.replace("_", " ").title()

    def _performer_feel(self) -> dict:
        return normalized_performer_feel({
            "swing": self._performer_swing,
            "humanize": self._performer_humanize,
            "gate": self._performer_gate,
        }, spec=self._current_performer_spec())

    def _set_performer_feel(self, feel: dict) -> None:
        normalized = normalized_performer_feel(
            feel, spec=self._current_performer_spec())
        self._performer_swing = normalized["swing"]
        self._performer_humanize = normalized["humanize"]
        self._performer_gate = normalized["gate"]

    def _feel_label(self) -> str:
        feel = self._performer_feel()
        return (
            f"Sw {feel['swing']:.0f}  Hu {feel['humanize']:.0f}  "
            f"Gate {feel['gate'] * 100:.0f}%")

    def _adjust_performer_feel(self, field: str, delta: float) -> None:
        feel = self._performer_feel()
        if field == "swing":
            feel["swing"] += delta
        elif field == "humanize":
            feel["humanize"] += delta
        elif field == "gate":
            feel["gate"] += delta
        self._set_performer_feel(feel)
        suffix = " next loop" if self._performer_player().status()["running"] else ""
        self._performer_message = f"feel {self._feel_label()}{suffix}"

    def _performer_generator_controls(self) -> dict:
        return normalized_generator_controls({
            "density": self._performer_density,
            "complexity": self._performer_complexity,
            "fill": self._performer_fill,
            "bass_activity": self._performer_bass_activity,
            "variation": self._performer_variation,
        })

    def _generator_label(self) -> str:
        controls = self._performer_generator_controls()
        return (
            f"Dn {controls['density']:.0f}  Cx {controls['complexity']:.0f}  "
            f"Fill {controls['fill']:.0f}  Bass {controls['bass_activity']:.0f}  "
            f"Var {controls['variation']:.0f}")

    def _adjust_performer_generator(self, field: str, delta: float) -> None:
        controls = self._performer_generator_controls()
        if field not in controls:
            return
        controls[field] += delta
        controls = normalized_generator_controls(controls)
        self._performer_density = controls["density"]
        self._performer_complexity = controls["complexity"]
        self._performer_fill = controls["fill"]
        self._performer_bass_activity = controls["bass_activity"]
        self._performer_variation = controls["variation"]
        self._performer_message = f"generator {self._generator_label()}"

    def _performer_lane_controls(self) -> dict:
        return normalized_lane_controls(self._performer_lane_controls_state)

    def _set_performer_lane_controls(self, controls: dict) -> None:
        self._performer_lane_controls_state = normalized_lane_controls(controls)

    def _performer_lane(self) -> str:
        return PERFORMER_LANES[
            self._performer_lane_idx % len(PERFORMER_LANES)]

    def _lane_label(self, lane: str | None = None) -> str:
        lane = lane or self._performer_lane()
        return PERFORMER_LANE_LABELS.get(lane, str(lane).title())

    def _cycle_performer_lane(self, direction: int = 1) -> None:
        self._performer_lane_idx = (
            self._performer_lane_idx + int(direction)) % len(PERFORMER_LANES)
        self._performer_message = f"lane: {self._lane_label()}"

    def _select_performer_lane(self, idx: int) -> None:
        self._performer_lane_idx = max(0, min(len(PERFORMER_LANES) - 1, int(idx)))
        self._performer_message = f"lane: {self._lane_label()}"

    def _adjust_performer_lane(self, field: str, delta: float = 0.0) -> None:
        controls = self._performer_lane_controls()
        lane = self._performer_lane()
        lane_ctrl = dict(controls[lane])
        if field == "gate":
            lane_ctrl["gate"] += delta
        elif field == "level":
            lane_ctrl["level"] += delta
        elif field == "mute":
            lane_ctrl["mute"] = not lane_ctrl["mute"]
        else:
            return
        controls[lane] = lane_ctrl
        self._set_performer_lane_controls(controls)
        lane_ctrl = self._performer_lane_controls()[lane]
        state = "muted" if lane_ctrl["mute"] else (
            f"gate {lane_ctrl['gate'] * 100:.0f}% level {lane_ctrl['level'] * 100:.0f}%")
        suffix = " next loop" if self._performer_player().status()["running"] else ""
        self._performer_message = f"{self._lane_label(lane)} {state}{suffix}"

    def _performer_takes(self, sess) -> list:
        takes = getattr(sess, "studio_performer_takes", None)
        if not isinstance(takes, list):
            takes = []
        takes = takes[:MAX_PERFORMER_TAKES]
        while len(takes) < MAX_PERFORMER_TAKES:
            takes.append(None)
        sess.studio_performer_takes = takes
        return takes

    def _current_take(self, sess) -> dict | None:
        takes = self._performer_takes(sess)
        return takes[self._performer_take_idx % MAX_PERFORMER_TAKES]

    def _take_label(self, sess) -> str:
        take = self._current_take(sess)
        idx = self._performer_take_idx + 1
        if not take:
            return f"Take {idx}: empty"
        return f"Take {idx}: {str(take.get('name', 'saved'))[:42]}"

    @staticmethod
    def _slot_label(slot, fallback: str = "") -> str:
        if slot is None:
            return fallback or "-"
        try:
            return f"Take {int(slot) + 1}"
        except Exception:
            return fallback or "-"

    @staticmethod
    def _chain_label(status: dict) -> str:
        count = int(status.get("sequence_count") or 0)
        pos = int(status.get("sequence_position") or 0)
        if count <= 0 or pos <= 0:
            return "Off"
        return f"{pos}/{count}"

    def _persist_session(self, sess) -> None:
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None and hasattr(ctrl, "_persist"):
            ctrl._persist()
            return
        try:
            from session.persistence import save_session
            save_session(sess, "default")
        except Exception:
            pass

    def _cycle_performer_take(self, sess, direction: int = 1) -> None:
        self._performer_take_idx = (
            self._performer_take_idx + int(direction)) % MAX_PERFORMER_TAKES
        self._performer_message = f"selected {self._take_label(sess)}"

    def _select_performer_take(self, sess, idx: int) -> None:
        self._performer_take_idx = max(0, min(MAX_PERFORMER_TAKES - 1, int(idx)))
        self._performer_message = f"selected {self._take_label(sess)}"

    def _save_performer_take(self, sess) -> None:
        spec = self._current_performer_spec()
        takes = self._performer_takes(sess)
        takes[self._performer_take_idx] = performer_take_from_spec(
            spec, slot=self._performer_take_idx,
            target_key=SP404_BEAT_BASS_TARGET,
            feel=self._performer_feel(),
            lane_controls=self._performer_lane_controls())
        self._persist_session(sess)
        self._performer_message = (
            f"saved Take {self._performer_take_idx + 1}: {spec.name}")

    def _load_performer_take(self, sess) -> None:
        take = self._current_take(sess)
        spec = spec_from_performer_take(take)
        take_idx = self._performer_take_idx
        if spec is None:
            self._performer_message = (
                f"Take {take_idx + 1} is empty")
            return
        self._set_performer_feel(feel_from_performer_take(take))
        self._set_performer_lane_controls(
            lane_controls_from_performer_take(take))
        self._set_current_performer_spec(spec)
        player = self._performer_player()
        label = f"Take {take_idx + 1}"
        if player.status()["running"] and player.queue_spec(
                spec, pattern_label=label, take_slot=take_idx):
            self._performer_message = (
                f"queued {label}: {spec.name}")
        else:
            self._play_sp_beat_bass(
                sess, pattern_label=label, take_slot=take_idx)
            self._performer_message = (
                f"playing {label}: {spec.name}")

    def _saved_take_chain_from_selection(self, sess) -> tuple[list, list[str], list[int]]:
        takes = self._performer_takes(sess)
        ordered = (
            list(range(self._performer_take_idx, MAX_PERFORMER_TAKES))
            + list(range(0, self._performer_take_idx))
        )
        specs = []
        labels = []
        slots = []
        for idx in ordered:
            spec = spec_from_performer_take(takes[idx])
            if spec is not None:
                specs.append(spec)
                labels.append(f"Take {idx + 1}")
                slots.append(idx)
        return specs, labels, slots

    def _toggle_take_chain(self, sess) -> None:
        player = self._performer_player()
        status = player.status()
        if status.get("sequence_enabled"):
            player.clear_sequence()
            self._performer_message = "take chain off"
            return
        specs, labels, slots = self._saved_take_chain_from_selection(sess)
        if not specs:
            self._performer_message = "no saved takes to chain"
            return
        self._set_performer_feel(feel_from_performer_take(self._current_take(sess)))
        self._set_current_performer_spec(specs[0])
        if status["running"]:
            player.queue_spec(
                specs[0], pattern_label=labels[0], take_slot=slots[0])
        else:
            self._play_sp_beat_bass(
                sess, pattern_label=labels[0], take_slot=slots[0])
        player.set_sequence(
            specs, labels=labels, take_slots=slots, start_index=0)
        self._performer_message = f"take chain on: {len(specs)} takes"

    def _step_grids_path(self) -> str:
        resolver = getattr(self.app, "_step_grids_path", None)
        if callable(resolver):
            return resolver()
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            "sessions",
            "compa_step_grids.json",
        )

    def _export_performer_take_to_step_grid(self, sess) -> None:
        spec = self._current_performer_spec()
        pattern_idx = self._performer_take_idx
        path = self._step_grids_path()
        grids = getattr(self.app, "_compa_step_grids", None)
        if not isinstance(grids, dict):
            grids = load_step_grids(path)
            setattr(self.app, "_compa_step_grids", grids)
        install_step_grid(grids, spec, pattern_idx)
        if save_step_grids(grids, path):
            loader = getattr(self.app, "_load_step_grid", None)
            focus = getattr(getattr(self.app, "device_manager", None),
                            "focus_key", "")
            if callable(loader) and focus == spec.device:
                try:
                    loader(spec.device, pattern_idx)
                except Exception:
                    pass
            self._performer_message = (
                f"sent drums to Push pattern {pattern_idx + 1}")
        else:
            self._performer_message = "step export failed"

    def _play_sp_beat_bass(self, sess, *, loops: int = 0,
                           message_prefix: str = "playing",
                           pattern_label: str = "",
                           take_slot: int | None = None) -> None:
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
                loops=loops,
                bpm_provider=lambda: self._performer_bpm(sess),
                feel_provider=lambda: self._performer_feel(),
                lane_controls_provider=lambda: self._performer_lane_controls(),
                pattern_label=pattern_label or spec.name,
                take_slot=take_slot,
            )
            self._performer_message = f"{message_prefix} {spec.name}"
        except Exception as exc:
            self._performer_message = f"play failed: {exc}"

    def _capture_sp_pattern_once(self, sess) -> None:
        spec = self._current_performer_spec()
        self._play_sp_beat_bass(sess, loops=1, message_prefix="record pass")
        if self._performer_message.startswith("record pass"):
            self._performer_message = f"record pass armed: {spec.name}"

    def _generate_sp_variation(self, sess) -> None:
        self._performer_seed += 1
        style = self._performer_style()
        spec = generate_sp404_beat_bass_variation(
            self._performer_seed, style=style,
            controls=self._performer_generator_controls())
        self._set_current_performer_spec(spec)
        status = self._performer_player().status()
        self._performer_message = (
            f"generated {self._style_label(style)}  {self._generator_label()}")
        if status["running"]:
            self._performer_player().queue_spec(
                spec, pattern_label=self._style_label(style))
            self._performer_message = (
                f"queued {self._style_label(style)} variation")

    def _cycle_performer_genre(self) -> None:
        self._performer_style_idx = (
            self._performer_style_idx + 1) % len(SP404_VARIATION_STYLES)
        self._performer_message = (
            f"genre: {self._style_label(self._performer_style())}")

    def _stop_performer(self) -> None:
        self._performer_player().stop()
        self._performer_message = "performer stopped"

    def _toggle_performer_mute(self) -> None:
        muted = self._performer_player().toggle_mute()
        self._performer_message = "performer muted" if muted else "performer live"

    def _button(self, surface: pygame.Surface, key: str, rect: pygame.Rect,
                label: str, *, active: bool = False,
                danger: bool = False, tone: str = "") -> None:
        self._buttons[key] = rect
        if danger:
            bg = (108, 36, 42)
            edge = (210, 92, 100)
        elif tone == "queued":
            bg = (40, 52, 94)
            edge = (100, 142, 238)
        elif tone == "chain":
            bg = (74, 62, 34)
            edge = (214, 166, 78)
        elif tone == "playing":
            bg = (36, 92, 66)
            edge = (96, 224, 156)
        elif active:
            bg = (42, 88, 78)
            edge = (90, 210, 170)
        else:
            bg = (34, 36, 48)
            edge = (74, 80, 104)
        pygame.draw.rect(surface, bg, rect, border_radius=4)
        pygame.draw.rect(surface, edge, rect, 1, border_radius=4)
        font = pygame.font.SysFont("Arial", 13, bold=True)
        label = str(label)
        while len(label) > 3 and font.size(label)[0] > rect.width - 10:
            label = label[:-4].rstrip() + "..."
        txt = font.render(label, True, (238, 238, 244))
        surface.blit(txt, txt.get_rect(center=rect.center))

    def _panel(self, surface: pygame.Surface, rect: pygame.Rect,
               title: str) -> int:
        pygame.draw.rect(surface, (18, 20, 30), rect, border_radius=6)
        pygame.draw.rect(surface, (46, 52, 72), rect, 1, border_radius=6)
        font = pygame.font.SysFont("Arial", 13, bold=True)
        surface.blit(font.render(title.upper(), True, (174, 188, 222)),
                     (rect.x + 14, rect.y + 10))
        return rect.y + 34

    def _info_pair(self, surface: pygame.Surface, x: int, y: int,
                   title: str, value: str, width: int) -> None:
        font = pygame.font.SysFont("Arial", 12, bold=True)
        font_sm = pygame.font.SysFont("Arial", 12)
        pygame.draw.rect(surface, (24, 27, 38), (x, y, width, 42),
                         border_radius=4)
        surface.blit(font.render(title.upper()[:16], True, (226, 230, 242)),
                     (x + 10, y + 6))
        surface.blit(font_sm.render(value[:32], True, (150, 162, 184)),
                     (x + 10, y + 24))

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
        self._sync_push2_mode()

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
        font_big = pygame.font.SysFont("Arial", 22, bold=True)
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
        w = surface.get_width()
        margin = 20
        gap = 12
        content_w = w - margin * 2
        left_w = int(content_w * 0.62)
        right_w = content_w - left_w - gap
        y = top + 66
        left_x = margin
        right_x = margin + left_w + gap

        state = "Muted" if status["muted"] else (
            "Playing" if status["running"] else "Stopped")
        if status.get("sequence_enabled"):
            state = f"Chain {self._chain_label(status)}"
        if status.get("queued_pattern_name"):
            state = "Queued next loop"
        elif status["last_error"]:
            state = status["last_error"][:42]
        playing_slot = (
            status.get("pattern_slot") if status.get("running") else None)
        queued_slot = status.get("queued_slot")
        chain_slots = [
            slot for slot in status.get("sequence_slots", [])
            if slot is not None
        ]
        next_slot = queued_slot
        next_label = self._slot_label(
            queued_slot, status.get("queued_pattern_label") or "")
        if queued_slot is None and status.get("sequence_enabled"):
            next_slot = status.get("sequence_next_slot")
            next_label = self._slot_label(
                next_slot, status.get("sequence_next_label") or "")
        if not status.get("running") and queued_slot is None:
            next_label = "-"
        playing_label = self._slot_label(
            playing_slot, status.get("pattern_label") or spec.name)
        if not status.get("running"):
            playing_label = "-"

        surface.blit(font_big.render("Performer", True, (236, 240, 248)),
                     (left_x, top + 6))
        surface.blit(font.render(
            f"Pattern: {spec.name[:62]}",
            True, (150, 162, 184)), (left_x, top + 36))
        bpm_text = f"{self._performer_bpm(sess):.1f} BPM"
        bpm_surf = font.render(bpm_text, True, (150, 162, 184))
        surface.blit(bpm_surf, (left_x + left_w - bpm_surf.get_width(),
                                top + 12))

        current_rect = pygame.Rect(left_x, y, left_w, 146)
        cy = self._panel(surface, current_rect, "Current Pattern")
        pair_w = (left_w - 58) // 4
        self._info_pair(surface, left_x + 14, cy, "Target",
                        target.label or capability.label, pair_w)
        self._info_pair(surface, left_x + 24 + pair_w, cy, "State",
                        state, pair_w)
        self._info_pair(surface, left_x + 34 + pair_w * 2, cy, "Playing",
                        playing_label, pair_w)
        self._info_pair(surface, left_x + 44 + pair_w * 3, cy, "Next",
                        next_label, pair_w)
        bar_y = cy + 56
        bar_rect = pygame.Rect(left_x + 14, bar_y, left_w - 28, 12)
        pygame.draw.rect(surface, (28, 31, 44), bar_rect, border_radius=4)
        progress = max(0.0, min(1.0, float(status.get("loop_progress") or 0.0)))
        fill_w = int(bar_rect.width * progress)
        if fill_w > 0:
            pygame.draw.rect(
                surface, (88, 190, 150),
                pygame.Rect(bar_rect.x, bar_rect.y, fill_w, bar_rect.height),
                border_radius=4)
        pygame.draw.rect(surface, (62, 70, 94), bar_rect, 1, border_radius=4)
        loop_text = "Loop stopped"
        if status.get("running"):
            loop_text = (
                f"Loop {status.get('loop_count', 0)}  "
                f"{float(status.get('loop_remaining') or 0.0):.1f}s to next loop")
        surface.blit(font_sm.render(loop_text, True, (150, 162, 184)),
                     (left_x + 14, bar_y + 18))
        if self._performer_message:
            surface.blit(font_sm.render(self._performer_message[:70], True,
                                        (174, 188, 222)),
                         (left_x + 220, bar_y + 18))
        cy += 84
        row_x = left_x + 14
        row_w = left_w - 28
        row_gap = 8
        midi_w = max(86, min(126, row_w // 4))
        action_w = (row_w - midi_w - row_gap * 3) // 3
        self._info_pair(surface, row_x, cy, "MIDI", midi_status, midi_w)
        self._button(surface, "performer_assign_sp",
                     pygame.Rect(row_x + midi_w + row_gap, cy, action_w, 34),
                     "Use SP A1-A6",
                     active=target.key == SP404_BEAT_BASS_TARGET)
        self._button(surface, "performer_genre",
                     pygame.Rect(row_x + midi_w + row_gap * 2 + action_w,
                                 cy, action_w, 34),
                     f"Genre: {self._style_label(self._performer_style())}")
        self._button(surface, "performer_generate",
                     pygame.Rect(row_x + midi_w + row_gap * 3 + action_w * 2,
                                 cy, action_w, 34), "Generate")

        transport_rect = pygame.Rect(left_x, y + 158, left_w, 86)
        ty = self._panel(surface, transport_rect, "Transport")
        row_x = left_x + 14
        row_w = left_w - 28
        transport_gap = 8
        transport_w = (row_w - transport_gap * 3) // 4
        self._button(surface, "performer_play_v3",
                     pygame.Rect(row_x, ty, transport_w, 38), "Play Loop",
                     active=bool(status["running"]))
        self._button(surface, "performer_stop",
                     pygame.Rect(row_x + transport_w + transport_gap, ty,
                                 transport_w, 38), "Stop",
                     danger=True)
        self._button(surface, "performer_mute",
                     pygame.Rect(row_x + (transport_w + transport_gap) * 2,
                                 ty, transport_w, 38),
                     "Unmute" if status["muted"] else "Mute",
                     active=bool(status["muted"]))
        self._button(surface, "performer_record_once",
                     pygame.Rect(row_x + (transport_w + transport_gap) * 3,
                                 ty, transport_w, 38), "Record Once")

        takes_rect = pygame.Rect(left_x, y + 256, left_w, 226)
        ky = self._panel(surface, takes_rect, "Take Bank")
        takes = self._performer_takes(sess)
        slot_gap = 8
        slot_w = (left_w - 28 - slot_gap * 3) // 4
        for idx in range(MAX_PERFORMER_TAKES):
            row = idx // 4
            col = idx % 4
            sx = left_x + 14 + col * (slot_w + slot_gap)
            sy = ky + row * 44
            take = takes[idx]
            label = f"Take {idx + 1}"
            tone = ""
            if take:
                label += " Saved"
            if idx == playing_slot:
                label = f"Take {idx + 1} Playing"
                tone = "playing"
            elif idx == queued_slot:
                label = f"Take {idx + 1} Queued"
                tone = "queued"
            elif idx in chain_slots:
                label = f"Take {idx + 1} Chain"
                tone = "chain"
            self._button(
                surface,
                f"performer_take_select_{idx}",
                pygame.Rect(sx, sy, slot_w, 36),
                label,
                active=idx == self._performer_take_idx,
                tone=tone,
            )
        ay = ky + 94
        recall_label = "Queue Take" if status["running"] else "Play Take"
        action_x = left_x + 14
        action_w = (left_w - 28 - slot_gap * 3) // 4
        self._button(surface, "performer_take_save",
                     pygame.Rect(action_x, ay, action_w, 36), "Save Take")
        self._button(surface, "performer_take_load",
                     pygame.Rect(action_x + action_w + slot_gap, ay,
                                 action_w, 36), recall_label,
                     active=bool(self._current_take(sess)))
        self._button(surface, "performer_take_chain",
                     pygame.Rect(action_x + (action_w + slot_gap) * 2, ay,
                                 action_w, 36), "Chain Takes",
                     active=bool(status.get("sequence_enabled")))
        self._button(surface, "performer_step_export",
                     pygame.Rect(action_x + (action_w + slot_gap) * 3, ay,
                                 action_w, 36), "Send To Steps")

        feel_rect = pygame.Rect(right_x, y, right_w, 158)
        fy = self._panel(surface, feel_rect, "Feel Controls")
        feel = self._performer_feel()

        def param_row(row_y: int, title: str, value: str,
                      down_key: str, up_key: str) -> None:
            surface.blit(font.render(title, True, (226, 230, 242)),
                         (right_x + 16, row_y + 7))
            surface.blit(font_big.render(value, True, (236, 240, 248)),
                         (right_x + 128, row_y + 1))
            self._button(surface, down_key,
                         pygame.Rect(right_x + right_w - 104, row_y, 42, 34),
                         "-")
            self._button(surface, up_key,
                         pygame.Rect(right_x + right_w - 54, row_y, 42, 34),
                         "+")

        def compact_param_row(row_y: int, title: str, value: str,
                              down_key: str, up_key: str) -> None:
            surface.blit(font_sm.render(title, True, (226, 230, 242)),
                         (right_x + 16, row_y + 5))
            surface.blit(font.render(value, True, (236, 240, 248)),
                         (right_x + 126, row_y + 4))
            self._button(surface, down_key,
                         pygame.Rect(right_x + right_w - 92, row_y, 36, 24),
                         "-")
            self._button(surface, up_key,
                         pygame.Rect(right_x + right_w - 48, row_y, 36, 24),
                         "+")

        param_row(fy, "Swing", f"{feel['swing']:.0f}",
                  "performer_swing_down", "performer_swing_up")
        param_row(fy + 38, "Humanize", f"{feel['humanize']:.0f}",
                  "performer_human_down", "performer_human_up")
        param_row(fy + 76, "Gate Length", f"{feel['gate'] * 100:.0f}%",
                  "performer_gate_down", "performer_gate_up")
        surface.blit(font_sm.render(
            "Running changes land on the next loop.",
            True, (126, 138, 162)), (right_x + 16, fy + 116))

        gen_rect = pygame.Rect(right_x, y + 170, right_w, 184)
        gy = self._panel(surface, gen_rect, "Generator")
        gen = self._performer_generator_controls()

        def gen_row(row_y: int, title: str, field: str) -> None:
            compact_param_row(
                row_y, title, f"{gen[field]:.0f}",
                f"performer_{field}_down", f"performer_{field}_up")

        gen_row(gy, "Density", "density")
        gen_row(gy + 28, "Complexity", "complexity")
        gen_row(gy + 56, "Fill", "fill")
        gen_row(gy + 84, "Bass Activity", "bass_activity")
        gen_row(gy + 112, "Variation", "variation")
        surface.blit(font_sm.render(
            "Encoder pages: Feel / Gen / Lanes / Takes",
            True, (126, 138, 162)), (right_x + 16, gy + 146))

        lanes_rect = pygame.Rect(right_x, y + 366, right_w, 154)
        ly = self._panel(surface, lanes_rect, "Lanes")
        lane_controls = self._performer_lane_controls()
        lane_gap = 6
        lane_w = (right_w - 32 - lane_gap * 3) // 4
        for idx, lane in enumerate(PERFORMER_LANES):
            lx = right_x + 16 + idx * (lane_w + lane_gap)
            label = self._lane_label(lane)
            if lane_controls[lane]["mute"]:
                label += " M"
            self._button(
                surface,
                f"performer_lane_select_{idx}",
                pygame.Rect(lx, ly, lane_w, 26),
                label,
                active=idx == self._performer_lane_idx,
                danger=bool(lane_controls[lane]["mute"]),
            )
        lane = self._performer_lane()
        lane_ctrl = lane_controls[lane]
        compact_param_row(ly + 34, "Lane Gate",
                          f"{lane_ctrl['gate'] * 100:.0f}%",
                          "performer_lane_gate_down",
                          "performer_lane_gate_up")
        compact_param_row(ly + 62, "Lane Level",
                          f"{lane_ctrl['level'] * 100:.0f}%",
                          "performer_lane_level_down",
                          "performer_lane_level_up")
        self._button(surface, "performer_lane_mute",
                     pygame.Rect(right_x + 16, ly + 92, right_w - 32, 24),
                     "Mute" if not lane_ctrl["mute"] else "On",
                     danger=bool(lane_ctrl["mute"]))

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
                self._set_tab(key)
                return True
        for key, rect in self._buttons.items():
            if not rect.collidepoint(mx, my):
                continue
            if key.startswith("performer_take_select_"):
                self._select_performer_take(
                    sess, int(key.rsplit("_", 1)[1]))
                return True
            if key.startswith("performer_lane_select_"):
                self._select_performer_lane(
                    int(key.rsplit("_", 1)[1]))
                return True
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
            if key == "performer_genre":
                self._cycle_performer_genre()
                return True
            if key == "performer_take_prev":
                self._cycle_performer_take(sess, -1)
                return True
            if key == "performer_take_next":
                self._cycle_performer_take(sess, 1)
                return True
            if key == "performer_take_save":
                self._save_performer_take(sess)
                return True
            if key == "performer_take_load":
                self._load_performer_take(sess)
                return True
            if key == "performer_take_chain":
                self._toggle_take_chain(sess)
                return True
            if key == "performer_step_export":
                self._export_performer_take_to_step_grid(sess)
                return True
            if key == "performer_record_once":
                self._capture_sp_pattern_once(sess)
                return True
            if key == "performer_swing_down":
                self._adjust_performer_feel("swing", -5.0)
                return True
            if key == "performer_swing_up":
                self._adjust_performer_feel("swing", 5.0)
                return True
            if key == "performer_human_down":
                self._adjust_performer_feel("humanize", -10.0)
                return True
            if key == "performer_human_up":
                self._adjust_performer_feel("humanize", 10.0)
                return True
            if key == "performer_gate_down":
                self._adjust_performer_feel("gate", -0.2)
                return True
            if key == "performer_gate_up":
                self._adjust_performer_feel("gate", 0.2)
                return True
            if key == "performer_lane_gate_down":
                self._adjust_performer_lane("gate", -0.1)
                return True
            if key == "performer_lane_gate_up":
                self._adjust_performer_lane("gate", 0.1)
                return True
            if key == "performer_lane_level_down":
                self._adjust_performer_lane("level", -0.1)
                return True
            if key == "performer_lane_level_up":
                self._adjust_performer_lane("level", 0.1)
                return True
            if key == "performer_lane_mute":
                self._adjust_performer_lane("mute")
                return True
            generator_buttons = {
                "performer_density_down": ("density", -10.0),
                "performer_density_up": ("density", 10.0),
                "performer_complexity_down": ("complexity", -10.0),
                "performer_complexity_up": ("complexity", 10.0),
                "performer_fill_down": ("fill", -10.0),
                "performer_fill_up": ("fill", 10.0),
                "performer_bass_activity_down": ("bass_activity", -10.0),
                "performer_bass_activity_up": ("bass_activity", 10.0),
                "performer_variation_down": ("variation", -10.0),
                "performer_variation_up": ("variation", 10.0),
            }
            if key in generator_buttons:
                field, delta = generator_buttons[key]
                self._adjust_performer_generator(field, delta)
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
