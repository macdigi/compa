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
from engine.studio_modules import (
    known_modules,
    module_availability_label,
    module_for_key,
    module_for_tab,
)
from engine.studio_sampler import (
    SAMPLER_PAD_COUNT,
    assign_sample_to_pad,
    clear_sampler_pad,
    list_sampler_samples,
    load_starter_kit,
    pad_display_name,
    sampler_pad_specs,
    sampler_track_index,
    sample_label,
)
from engine.studio_drum_synth import (
    DRUM_SYNTH_KITS,
    DRUM_SYNTH_PAD_COUNT,
    adjust_voice_param,
    drum_synth_track_index,
    drum_synth_voice_specs,
    ensure_drum_synth_track,
    set_drum_synth_kit,
    voice_display_name,
)
from engine.studio_synth import (
    SYNTH_PRESETS,
    adjust_synth_param,
    cycle_synth_waveform,
    ensure_synth_track,
    note_name,
    set_synth_preset,
    synth_params,
    synth_track_indices,
    synth_track_role,
)
from engine.studio_router import (
    adjust_track_mix,
    clear_solos,
    route_track_to_target,
    session_route_summary,
    target_choices_for_track,
)
from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index, build_palette


class ClipScreen:
    name = "studio"
    TABS = (
        ("overview", "HOME"),
        ("clips", "CLIPS"),
        ("performer", "PERFORM"),
        ("sampler", "SAMPLER"),
        ("drum_synth", "DRUM"),
        ("synth", "SYNTH"),
        ("mixer", "MIX"),
        ("recorder", "REC"),
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
        self._sampler_pad_idx = 0
        self._sampler_sample_idx = 0
        self._sampler_message = ""
        self._sampler_library: list[str] | None = None
        self._drum_synth_pad_idx = 0
        self._drum_synth_kit_idx = 0
        self._drum_synth_message = ""
        self._synth_track_choice_idx = 0
        self._synth_base_note = 48
        self._synth_message = ""
        self._router_message = ""

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
        if self._tab == "performer":
            desired = "performer"
        elif self._tab == "sampler":
            desired = "sampler"
        elif self._tab == "drum_synth":
            desired = "drum_synth"
        elif self._tab == "synth":
            desired = "studio_synth"
        elif self._tab == "mixer":
            desired = "studio_router"
        elif self._tab == "clips":
            desired = "session"
        else:
            desired = "studio"
        if getattr(ctrl, "mode_name", "") != desired:
            ctrl.switch_mode(desired)

    def _set_tab(self, tab: str) -> None:
        if tab not in self._studio_tab_keys():
            tab = "overview"
        self._tab = tab
        self._sync_push2_mode()
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()

    def _studio_tab_keys(self) -> set[str]:
        return {key for key, _label in self.TABS}

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

    def _module_availability(self, module) -> str:
        return module_availability_label(
            module,
            pi_generation=self._pi_generation(),
            studio_audio_enabled=self._studio_audio_supported(),
        )

    def _select_studio_module(self, key: str) -> None:
        module = module_for_key(key)
        if module is None:
            return
        self._set_tab(module.tab)

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

    # ── Sampler helpers ──────────────────────────────────────────
    def _sampler_track_index(self, sess) -> int | None:
        return sampler_track_index(sess)

    def _sampler_pads(self, sess) -> list[dict]:
        track_idx = self._sampler_track_index(sess)
        return sampler_pad_specs(sess, track_idx)

    def _sampler_samples(self) -> list[str]:
        if self._sampler_library is None:
            self._sampler_library = list_sampler_samples()
        return self._sampler_library

    def _sampler_selected_sample(self) -> str:
        samples = self._sampler_samples()
        if not samples:
            return ""
        self._sampler_sample_idx %= len(samples)
        return samples[self._sampler_sample_idx]

    def _rebuild_sampler(self, sess) -> None:
        engine = getattr(self.app, "clip_engine", None)
        if engine is not None and hasattr(engine, "_instantiate_instruments"):
            engine._instantiate_instruments()
        self._persist_session(sess)
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()

    def _trigger_sampler_pad(self, sess, pad_idx: int, velocity: int = 112) -> None:
        track_idx = self._sampler_track_index(sess)
        if track_idx is None:
            self._sampler_message = "no sampler track"
            return
        self._sampler_pad_idx = max(0, min(SAMPLER_PAD_COUNT - 1, int(pad_idx)))
        if not self._ensure_audio_started():
            self._sampler_message = "sampler audio gated"
            return
        engine = getattr(self.app, "clip_engine", None)
        if engine is None:
            self._sampler_message = "clip engine unavailable"
            return
        engine.play_note_live(
            track_idx,
            36 + self._sampler_pad_idx,
            max(1, min(127, int(velocity))),
            link_beat=0.0,
        )
        pads = self._sampler_pads(sess)
        label = pad_display_name(pads[self._sampler_pad_idx],
                                 self._sampler_pad_idx)
        self._sampler_message = f"triggered Pad {self._sampler_pad_idx + 1}: {label}"

    def _stop_sampler(self) -> None:
        engine = getattr(self.app, "clip_engine", None)
        if engine is not None:
            engine.all_notes_off()
        self._sampler_message = "sampler stopped"

    def _cycle_sampler_sample(self, delta: int) -> None:
        samples = self._sampler_samples()
        if not samples:
            self._sampler_message = "no local samples found"
            return
        self._sampler_sample_idx = (
            self._sampler_sample_idx + int(delta)) % len(samples)
        self._sampler_message = (
            f"library: {sample_label(samples[self._sampler_sample_idx])}")

    def _assign_sampler_sample(self, sess) -> None:
        track_idx = self._sampler_track_index(sess)
        path = self._sampler_selected_sample()
        if track_idx is None:
            self._sampler_message = "no sampler track"
            return
        if not path:
            self._sampler_message = "no local samples found"
            return
        assign_sample_to_pad(sess, track_idx, self._sampler_pad_idx, path)
        self._rebuild_sampler(sess)
        self._sampler_message = (
            f"Pad {self._sampler_pad_idx + 1} assigned: {sample_label(path)}")

    def _clear_sampler_pad(self, sess) -> None:
        track_idx = self._sampler_track_index(sess)
        if track_idx is None:
            self._sampler_message = "no sampler track"
            return
        clear_sampler_pad(sess, track_idx, self._sampler_pad_idx)
        self._rebuild_sampler(sess)
        self._sampler_message = f"Pad {self._sampler_pad_idx + 1} cleared"

    def _load_sampler_starter(self, sess) -> None:
        track_idx = self._sampler_track_index(sess)
        if track_idx is None:
            self._sampler_message = "no sampler track"
            return
        count = load_starter_kit(sess, track_idx)
        self._rebuild_sampler(sess)
        self._sampler_message = f"starter kit loaded: {count} pads"

    # ── Drum Synth helpers ───────────────────────────────────────
    def _drum_synth_track_index(self, sess) -> int | None:
        return drum_synth_track_index(sess)

    def _drum_synth_specs(self, sess) -> list[dict]:
        idx = self._drum_synth_track_index(sess)
        return drum_synth_voice_specs(sess, idx)

    def _ensure_drum_synth_track(self, sess) -> int:
        idx = ensure_drum_synth_track(sess)
        self._rebuild_drum_synth(sess)
        self._drum_synth_message = f"using Drum Synth track {idx + 1}"
        return idx

    def _rebuild_drum_synth(self, sess) -> None:
        engine = getattr(self.app, "clip_engine", None)
        if engine is not None and hasattr(engine, "_instantiate_instruments"):
            engine._instantiate_instruments()
        self._persist_session(sess)
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()

    def _trigger_drum_synth_pad(self, sess, pad_idx: int, velocity: int = 112) -> None:
        track_idx = self._drum_synth_track_index(sess)
        if track_idx is None:
            track_idx = self._ensure_drum_synth_track(sess)
        self._drum_synth_pad_idx = max(
            0, min(DRUM_SYNTH_PAD_COUNT - 1, int(pad_idx)))
        if not self._ensure_audio_started():
            self._drum_synth_message = "drum synth audio gated"
            return
        engine = getattr(self.app, "clip_engine", None)
        if engine is None:
            self._drum_synth_message = "clip engine unavailable"
            return
        engine.play_note_live(
            track_idx,
            36 + self._drum_synth_pad_idx,
            max(1, min(127, int(velocity))),
            link_beat=0.0,
        )
        specs = self._drum_synth_specs(sess)
        label = voice_display_name(specs[self._drum_synth_pad_idx],
                                   self._drum_synth_pad_idx)
        self._drum_synth_message = (
            f"triggered Pad {self._drum_synth_pad_idx + 1}: {label}")

    def _stop_drum_synth(self) -> None:
        engine = getattr(self.app, "clip_engine", None)
        if engine is not None:
            engine.all_notes_off()
        self._drum_synth_message = "drum synth stopped"

    def _set_drum_synth_kit(self, sess, kit: str) -> None:
        track_idx = self._drum_synth_track_index(sess)
        if track_idx is None:
            track_idx = self._ensure_drum_synth_track(sess)
        set_drum_synth_kit(sess, track_idx, kit)
        self._drum_synth_kit_idx = DRUM_SYNTH_KITS.index(kit)
        self._rebuild_drum_synth(sess)
        self._drum_synth_message = f"{kit} kit loaded"

    def _cycle_drum_synth_kit(self, sess) -> None:
        self._drum_synth_kit_idx = (
            self._drum_synth_kit_idx + 1) % len(DRUM_SYNTH_KITS)
        self._set_drum_synth_kit(
            sess, DRUM_SYNTH_KITS[self._drum_synth_kit_idx])

    def _adjust_drum_synth_param(self, sess, field: str, delta: float) -> None:
        track_idx = self._drum_synth_track_index(sess)
        if track_idx is None:
            track_idx = self._ensure_drum_synth_track(sess)
        spec = adjust_voice_param(
            sess, track_idx, self._drum_synth_pad_idx, field, delta)
        self._rebuild_drum_synth(sess)
        value = spec.get(field)
        if isinstance(value, float):
            if field in ("tone", "snap"):
                value_text = f"{value * 100:.0f}"
            else:
                value_text = f"{value:.2f}"
        else:
            value_text = str(value)
        self._drum_synth_message = (
            f"Pad {self._drum_synth_pad_idx + 1} {field}: {value_text}")

    # ── Synth helpers ─────────────────────────────────────────────
    def _synth_track_indices(self, sess) -> list[int]:
        return synth_track_indices(sess)

    def _selected_synth_track_index(self, sess) -> int | None:
        indices = self._synth_track_indices(sess)
        if not indices:
            return None
        self._synth_track_choice_idx %= len(indices)
        return indices[self._synth_track_choice_idx]

    def _ensure_synth_track(self, sess) -> int:
        idx = ensure_synth_track(sess, "bass")
        indices = self._synth_track_indices(sess)
        if idx in indices:
            self._synth_track_choice_idx = indices.index(idx)
        self._rebuild_synth(sess)
        self._synth_message = f"using synth track {idx + 1}"
        return idx

    def _rebuild_synth(self, sess) -> None:
        engine = getattr(self.app, "clip_engine", None)
        if engine is not None and hasattr(engine, "_instantiate_instruments"):
            engine._instantiate_instruments()
        self._persist_session(sess)
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()

    def _select_synth_track_slot(self, sess, slot_idx: int) -> None:
        indices = self._synth_track_indices(sess)
        if not indices:
            self._ensure_synth_track(sess)
            indices = self._synth_track_indices(sess)
        if not indices:
            return
        self._synth_track_choice_idx = max(0, min(len(indices) - 1, int(slot_idx)))
        idx = indices[self._synth_track_choice_idx]
        self._synth_message = f"selected {sess.tracks[idx].name}"

    def _cycle_synth_track(self, sess, delta: int) -> None:
        indices = self._synth_track_indices(sess)
        if not indices:
            self._ensure_synth_track(sess)
            indices = self._synth_track_indices(sess)
        if not indices:
            return
        self._synth_track_choice_idx = (
            self._synth_track_choice_idx + int(delta)) % len(indices)
        idx = indices[self._synth_track_choice_idx]
        self._synth_message = f"selected {sess.tracks[idx].name}"

    def _synth_note_on(self, sess, pitch: int, velocity: int = 100) -> None:
        track_idx = self._selected_synth_track_index(sess)
        if track_idx is None:
            track_idx = self._ensure_synth_track(sess)
        if not self._ensure_audio_started():
            self._synth_message = "synth audio gated"
            return
        engine = getattr(self.app, "clip_engine", None)
        if engine is None:
            self._synth_message = "clip engine unavailable"
            return
        engine.play_note_live(
            track_idx,
            max(0, min(127, int(pitch))),
            max(1, min(127, int(velocity))),
            link_beat=0.0,
        )
        self._synth_message = f"{sess.tracks[track_idx].name}: {note_name(pitch)}"

    def _synth_note_off(self, sess, pitch: int) -> None:
        track_idx = self._selected_synth_track_index(sess)
        engine = getattr(self.app, "clip_engine", None)
        if track_idx is not None and engine is not None:
            engine.stop_note_live(track_idx, max(0, min(127, int(pitch))),
                                  link_beat=0.0)

    def _preview_synth_note(self, sess, pitch: int, velocity: int = 100) -> None:
        self._synth_note_on(sess, pitch, velocity)
        track_idx = self._selected_synth_track_index(sess)
        engine = getattr(self.app, "clip_engine", None)
        if track_idx is None or engine is None:
            return
        import threading
        timer = threading.Timer(
            0.32,
            lambda: engine.stop_note_live(
                track_idx, max(0, min(127, int(pitch))), link_beat=0.0),
        )
        timer.daemon = True
        timer.start()

    def _stop_synth_notes(self) -> None:
        engine = getattr(self.app, "clip_engine", None)
        if engine is not None:
            engine.all_notes_off()
        self._synth_message = "synth notes stopped"

    def _set_synth_preset(self, sess, preset: str) -> None:
        track_idx = self._selected_synth_track_index(sess)
        if track_idx is None:
            track_idx = self._ensure_synth_track(sess)
        set_synth_preset(sess, track_idx, preset)
        self._rebuild_synth(sess)
        self._synth_message = f"{sess.tracks[track_idx].name}: {preset}"

    def _cycle_synth_waveform(self, sess) -> None:
        track_idx = self._selected_synth_track_index(sess)
        if track_idx is None:
            track_idx = self._ensure_synth_track(sess)
        waveform = cycle_synth_waveform(sess, track_idx)
        self._rebuild_synth(sess)
        self._synth_message = f"waveform: {waveform}"

    def _adjust_synth_param(self, sess, field: str, delta: float) -> None:
        track_idx = self._selected_synth_track_index(sess)
        if track_idx is None:
            track_idx = self._ensure_synth_track(sess)
        params = adjust_synth_param(sess, track_idx, field, delta)
        self._rebuild_synth(sess)
        value = params.get(field)
        if field == "cutoff_hz":
            value_text = f"{float(value):.0f}Hz"
        elif isinstance(value, float):
            value_text = f"{value:.2f}"
        else:
            value_text = str(value)
        self._synth_message = f"{field.replace('_', ' ')}: {value_text}"

    # ── Mixer / Router helpers ───────────────────────────────────
    def _router_summaries(self, sess) -> list[dict]:
        return session_route_summary(
            sess,
            pi_generation=self._pi_generation(),
            studio_audio_enabled=self._studio_audio_supported(),
        )

    def _select_router_track(self, sess, track_idx: int) -> None:
        if not sess.tracks:
            return
        idx = max(0, min(len(sess.tracks) - 1, int(track_idx)))
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.selected_track = idx
            ctrl.request_redraw()
        self._router_message = f"selected {sess.tracks[idx].name}"

    def _route_selected_track(self, sess, target_key: str) -> None:
        track_idx = self._selected_track_index(sess)
        target = route_track_to_target(sess, track_idx, target_key)
        self._persist_session(sess)
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()
        self._router_message = (
            f"{sess.tracks[track_idx].name} -> {target.label or target.key}")

    def _adjust_router_mix(self, sess, field: str, delta: float = 0.0) -> None:
        track_idx = self._selected_track_index(sess)
        track = adjust_track_mix(sess, track_idx, field, delta)
        self._persist_session(sess)
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()
        if field == "volume":
            detail = f"{int(track.volume * 100)}%"
        elif field == "pan":
            detail = f"{track.pan:+.2f}"
        elif field == "mute":
            detail = "muted" if track.mute else "unmuted"
        elif field == "solo":
            detail = "solo" if track.solo else "solo off"
        elif field == "arm":
            detail = "armed" if track.arm else "disarmed"
        else:
            detail = field
        self._router_message = f"{track.name}: {detail}"

    def _clear_router_solos(self, sess) -> None:
        clear_solos(sess)
        self._persist_session(sess)
        ctrl = getattr(self.app, "push2_control", None)
        if ctrl is not None:
            ctrl.request_redraw()
        self._router_message = "solos cleared"

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

    def _apply_performer_gesture(self, sess, gesture: str) -> None:
        controls = self._performer_lane_controls()

        def set_mutes(*, kick: bool = False, snare: bool = False,
                      hats: bool = False, bass: bool = False) -> None:
            controls["kick"]["mute"] = kick
            controls["snare"]["mute"] = snare
            controls["hats"]["mute"] = hats
            controls["bass"]["mute"] = bass

        if gesture == "all":
            set_mutes()
            self._set_performer_lane_controls(controls)
            self._performer_message = "all lanes in next loop"
            return
        if gesture == "drums":
            set_mutes(bass=True)
            self._set_performer_lane_controls(controls)
            self._performer_message = "drums only next loop"
            return
        if gesture == "bass":
            set_mutes(kick=True, snare=True, hats=True)
            self._set_performer_lane_controls(controls)
            self._performer_message = "bass only next loop"
            return
        if gesture == "drop_drums":
            set_mutes(kick=True, snare=True, hats=True, bass=False)
            self._set_performer_lane_controls(controls)
            self._performer_message = "drum drop next loop"
            return
        if gesture == "reset":
            self._set_performer_lane_controls(normalized_lane_controls())
            self._performer_message = "lane controls reset"
            return
        if gesture == "fill":
            self._queue_fill_once(sess)

    def _queue_fill_once(self, sess) -> None:
        player = self._performer_player()
        if not player.status()["running"]:
            self._performer_message = "play loop before firing fill"
            return
        base_spec = self._current_performer_spec()
        self._performer_seed += 1
        controls = self._performer_generator_controls()
        controls.update({
            "density": max(controls["density"], 82.0),
            "complexity": max(controls["complexity"], 78.0),
            "fill": 100.0,
            "variation": max(controls["variation"], 70.0),
        })
        fill_spec = generate_sp404_beat_bass_variation(
            self._performer_seed,
            style=self._performer_style(),
            controls=controls,
        )
        if player.queue_spec(
                fill_spec,
                pattern_label="Fill",
                return_spec=base_spec,
                return_pattern_label="Return"):
            self._performer_message = "fill queued, returns after one loop"
        else:
            self._performer_message = "fill queue failed"

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

        surface.blit(f_big.render("STUDIO", True, (230, 230, 240)), (16, 12))
        subtitle = f"{sess.name}   {sess.bpm:.1f} BPM"
        surface.blit(f_sm.render(subtitle[:72], True, (156, 166, 184)),
                     (116, 20))
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

    def _draw_text_fit(self, surface: pygame.Surface, font, text: str,
                       color: tuple[int, int, int], pos: tuple[int, int],
                       max_width: int) -> None:
        label = str(text)
        while len(label) > 3 and font.size(label)[0] > max_width:
            label = label[:-4].rstrip() + "..."
        surface.blit(font.render(label, True, color), pos)

    def _draw_module_card(self, surface: pygame.Surface, module, rect: pygame.Rect,
                          *, active: bool = False) -> None:
        self._buttons[f"studio_module_{module.key}"] = rect
        font = pygame.font.SysFont("Arial", 15, bold=True)
        font_sm = pygame.font.SysFont("Arial", 12)
        status = self._module_availability(module)
        blocked = status.lower() in ("audio gated", f"pi {module.min_pi_generation}+")
        bg = (30, 42, 54) if active else (20, 23, 34)
        edge = (92, 210, 170) if active else (
            (158, 74, 82) if blocked else (52, 60, 82))
        pygame.draw.rect(surface, bg, rect, border_radius=6)
        pygame.draw.rect(surface, edge, rect, 1, border_radius=6)
        self._draw_text_fit(surface, font, module.label, (236, 240, 248),
                            (rect.x + 12, rect.y + 9), rect.width - 118)
        chip_w = 88
        chip = pygame.Rect(rect.right - chip_w - 10, rect.y + 8, chip_w, 22)
        chip_bg = (52, 92, 76) if not blocked else (86, 42, 48)
        pygame.draw.rect(surface, chip_bg, chip, border_radius=4)
        self._draw_text_fit(surface, font_sm, status.upper(), (236, 240, 248),
                            (chip.x + 8, chip.y + 4), chip.width - 12)
        self._draw_text_fit(surface, font_sm, module.summary,
                            (158, 170, 192), (rect.x + 12, rect.y + 32),
                            rect.width - 24)
        features = " / ".join(module.features[:3])
        self._draw_text_fit(surface, font_sm, features,
                            (118, 132, 160), (rect.x + 12, rect.y + 48),
                            rect.width - 24)

    def _draw_module_hub(self, surface: pygame.Surface, top: int, sess) -> None:
        font_big = pygame.font.SysFont("Arial", 24, bold=True)
        font = pygame.font.SysFont("Arial", 13)
        surface.blit(font_big.render("Studio Home", True, (232, 234, 242)),
                     (20, top + 8))
        pi = self._pi_generation()
        status = "Pi generation: " + (str(pi) if pi is not None else "unknown")
        if not self._studio_audio_supported():
            status += "  |  internal audio gated"
        surface.blit(font.render(status, True, (150, 162, 184)),
                     (172, top + 16))

        modules = known_modules()
        gap = 8
        x0 = 20
        y0 = top + 44
        col_w = (surface.get_width() - 40 - gap) // 2
        row_h = 62
        for idx, module in enumerate(modules):
            col = idx % 2
            row = idx // 2
            rect = pygame.Rect(
                x0 + col * (col_w + gap),
                y0 + row * (row_h + gap),
                col_w,
                row_h,
            )
            self._draw_module_card(
                surface, module, rect, active=module.tab == self._tab)

        track_y = y0 + ((len(modules) + 1) // 2) * (row_h + gap) + 2
        if track_y > surface.get_height() - 92:
            return
        self._draw_text_fit(
            surface, font, "Current targets", (174, 188, 222),
            (20, track_y), surface.get_width() - 40)
        font_sm = pygame.font.SysFont("Arial", 12)
        x = 20
        y = track_y + 22
        for track in sess.tracks[:4]:
            target = target_for_track(track)
            capability = capability_for(target)
            rect = pygame.Rect(x, y, 180, 34)
            pygame.draw.rect(surface, (18, 20, 30), rect, border_radius=4)
            pygame.draw.rect(surface, (42, 48, 66), rect, 1, border_radius=4)
            self._draw_text_fit(
                surface, font_sm,
                f"{track.name}: {target.label or capability.label}",
                (188, 198, 214), (rect.x + 8, rect.y + 10), rect.width - 16)
            x += 190
            if x + 180 > surface.get_width() - 20:
                break

    def _draw_sampler_tab(self, surface: pygame.Surface, top: int, sess) -> None:
        font_big = pygame.font.SysFont("Arial", 24, bold=True)
        font = pygame.font.SysFont("Arial", 14)
        font_sm = pygame.font.SysFont("Arial", 12)
        track_idx = self._sampler_track_index(sess)
        pads = self._sampler_pads(sess)
        sample_path = self._sampler_selected_sample()
        selected = self._sampler_pad_idx
        track_name = "No sampler track"
        if track_idx is not None and 0 <= track_idx < len(sess.tracks):
            track_name = sess.tracks[track_idx].name
        surface.blit(font_big.render("Compa Sampler", True, (232, 234, 242)),
                     (20, top + 8))
        surface.blit(font.render(track_name, True, (150, 162, 184)),
                     (220, top + 16))
        if self._sampler_message:
            self._draw_text_fit(surface, font_sm, self._sampler_message,
                                (174, 188, 222), (380, top + 19),
                                surface.get_width() - 400)

        margin = 20
        gap = 12
        y = top + 54
        content_w = surface.get_width() - margin * 2
        left_w = min(430, int(content_w * 0.58))
        right_w = content_w - left_w - gap
        left = pygame.Rect(margin, y, left_w, 290)
        right = pygame.Rect(margin + left_w + gap, y, right_w, 290)
        py = self._panel(surface, left, "Pad Rack")
        ry = self._panel(surface, right, "Selected Pad")

        pad_gap = 8
        pad_size = min(
            (left.width - 28 - pad_gap * 3) // 4,
            (left.height - 54 - pad_gap * 3) // 4,
        )
        grid_w = pad_size * 4 + pad_gap * 3
        grid_x = left.x + (left.width - grid_w) // 2
        grid_y = py
        for idx in range(SAMPLER_PAD_COUNT):
            row = idx // 4
            col = idx % 4
            rect = pygame.Rect(
                grid_x + col * (pad_size + pad_gap),
                grid_y + row * (pad_size + pad_gap),
                pad_size,
                pad_size,
            )
            spec = pads[idx] if idx < len(pads) else None
            assigned = bool(spec and spec.get("sample_path"))
            enabled = bool(spec and (spec.get("sample_path")
                           or spec.get("use_default", True)))
            active = idx == selected
            bg = (38, 82, 72) if active else (
                (34, 40, 56) if enabled else (18, 20, 28))
            edge = (94, 220, 176) if active else (
                (92, 112, 150) if assigned else (50, 56, 76))
            pygame.draw.rect(surface, bg, rect, border_radius=6)
            pygame.draw.rect(surface, edge, rect, 1, border_radius=6)
            self._buttons[f"sampler_pad_{idx}"] = rect
            self._draw_text_fit(surface, font_sm, f"{idx + 1}",
                                (232, 236, 244), (rect.x + 8, rect.y + 7),
                                rect.width - 16)
            label = pad_display_name(spec, idx)
            self._draw_text_fit(surface, font_sm, label, (180, 192, 210),
                                (rect.x + 8, rect.y + rect.height - 22),
                                rect.width - 16)

        selected_spec = pads[selected] if selected < len(pads) else None
        selected_name = pad_display_name(selected_spec, selected)
        source = "Starter/Internal"
        if selected_spec and selected_spec.get("sample_path"):
            source = os.path.basename(selected_spec["sample_path"])
        elif selected_spec and not selected_spec.get("use_default", True):
            source = "Empty"
        info_w = right.width - 28
        self._info_pair(surface, right.x + 14, ry, f"Pad {selected + 1}",
                        selected_name, info_w)
        self._info_pair(surface, right.x + 14, ry + 50, "Source",
                        source, info_w)
        library_label = sample_label(sample_path) if sample_path else "No samples found"
        self._info_pair(surface, right.x + 14, ry + 100, "Library",
                        library_label, info_w)

        bx = right.x + 14
        by = ry + 158
        button_gap = 8
        button_w = (info_w - button_gap) // 2
        self._button(surface, "sampler_sample_prev",
                     pygame.Rect(bx, by, button_w, 34), "Prev")
        self._button(surface, "sampler_sample_next",
                     pygame.Rect(bx + button_w + button_gap, by, button_w, 34),
                     "Next")
        self._button(surface, "sampler_assign",
                     pygame.Rect(bx, by + 44, button_w, 34), "Assign")
        self._button(surface, "sampler_clear",
                     pygame.Rect(bx + button_w + button_gap, by + 44,
                                 button_w, 34), "Clear", danger=True)
        self._button(surface, "sampler_load_starter",
                     pygame.Rect(bx, by + 88, button_w, 34), "Starter Kit")
        self._button(surface, "sampler_stop",
                     pygame.Rect(bx + button_w + button_gap, by + 88,
                                 button_w, 34), "Stop", danger=True)

    def _draw_drum_synth_tab(self, surface: pygame.Surface, top: int, sess) -> None:
        font_big = pygame.font.SysFont("Arial", 24, bold=True)
        font = pygame.font.SysFont("Arial", 14)
        font_sm = pygame.font.SysFont("Arial", 12)
        track_idx = self._drum_synth_track_index(sess)
        specs = self._drum_synth_specs(sess)
        selected = self._drum_synth_pad_idx
        track_name = "No drum synth track"
        kit = DRUM_SYNTH_KITS[self._drum_synth_kit_idx]
        if track_idx is not None and 0 <= track_idx < len(sess.tracks):
            track = sess.tracks[track_idx]
            track_name = track.name
            kit = str((track.instrument.params if track.instrument else {})
                      .get("kit") or kit)
        surface.blit(font_big.render("Drum Synth", True, (232, 234, 242)),
                     (20, top + 8))
        surface.blit(font.render(f"{track_name}  {kit}", True,
                                 (150, 162, 184)), (190, top + 16))
        if self._drum_synth_message:
            self._draw_text_fit(surface, font_sm, self._drum_synth_message,
                                (174, 188, 222), (380, top + 19),
                                surface.get_width() - 400)

        margin = 20
        gap = 12
        y = top + 54
        content_w = surface.get_width() - margin * 2
        left_w = min(430, int(content_w * 0.58))
        right_w = content_w - left_w - gap
        left = pygame.Rect(margin, y, left_w, 290)
        right = pygame.Rect(margin + left_w + gap, y, right_w, 290)
        py = self._panel(surface, left, "Synth Voices")
        ry = self._panel(surface, right, "Selected Voice")

        pad_gap = 8
        pad_size = min(
            (left.width - 28 - pad_gap * 3) // 4,
            (left.height - 54 - pad_gap * 3) // 4,
        )
        grid_w = pad_size * 4 + pad_gap * 3
        grid_x = left.x + (left.width - grid_w) // 2
        grid_y = py
        for idx in range(DRUM_SYNTH_PAD_COUNT):
            row = idx // 4
            col = idx % 4
            rect = pygame.Rect(
                grid_x + col * (pad_size + pad_gap),
                grid_y + row * (pad_size + pad_gap),
                pad_size,
                pad_size,
            )
            spec = specs[idx] if idx < len(specs) else None
            active = idx == selected
            bg = (44, 72, 62) if active else (28, 30, 42)
            edge = (112, 226, 166) if active else (62, 72, 96)
            pygame.draw.rect(surface, bg, rect, border_radius=6)
            pygame.draw.rect(surface, edge, rect, 1, border_radius=6)
            self._buttons[f"drum_synth_pad_{idx}"] = rect
            self._draw_text_fit(surface, font_sm, f"{idx + 1}",
                                (232, 236, 244), (rect.x + 8, rect.y + 7),
                                rect.width - 16)
            self._draw_text_fit(surface, font_sm, voice_display_name(spec, idx),
                                (180, 192, 210),
                                (rect.x + 8, rect.y + rect.height - 22),
                                rect.width - 16)

        selected_spec = specs[selected] if selected < len(specs) else None
        if not selected_spec:
            selected_spec = {}
        voice_name = voice_display_name(selected_spec, selected)
        voice_type = str(selected_spec.get("voice_type", "-")).replace("_", " ")
        info_w = right.width - 28
        self._info_pair(surface, right.x + 14, ry, f"Pad {selected + 1}",
                        voice_name, info_w)
        self._info_pair(surface, right.x + 14, ry + 50, "Type",
                        voice_type, info_w)
        control_w = (info_w - 16) // 3
        self._info_pair(surface, right.x + 14, ry + 100, "Tone",
                        f"{float(selected_spec.get('tone', 0.0)) * 100:.0f}",
                        control_w)
        self._info_pair(surface, right.x + 22 + control_w, ry + 100, "Decay",
                        f"{float(selected_spec.get('decay', 0.0)):.2f}s",
                        control_w)
        self._info_pair(surface, right.x + 30 + control_w * 2, ry + 100, "Snap",
                        f"{float(selected_spec.get('snap', 0.0)) * 100:.0f}",
                        control_w)

        bx = right.x + 14
        by = ry + 158
        button_gap = 8
        button_w = (info_w - button_gap * 2) // 3
        self._button(surface, "drum_synth_kit_808",
                     pygame.Rect(bx, by, button_w, 34), "808",
                     active=kit == "808")
        self._button(surface, "drum_synth_kit_909",
                     pygame.Rect(bx + button_w + button_gap, by, button_w, 34),
                     "909", active=kit == "909")
        self._button(surface, "drum_synth_stop",
                     pygame.Rect(bx + (button_w + button_gap) * 2, by,
                                 button_w, 34), "Stop", danger=True)
        small_w = (info_w - button_gap * 5) // 6
        controls = [
            ("drum_synth_tone_down", "Tone-"),
            ("drum_synth_tone_up", "Tone+"),
            ("drum_synth_decay_down", "Dec-"),
            ("drum_synth_decay_up", "Dec+"),
            ("drum_synth_snap_down", "Snap-"),
            ("drum_synth_snap_up", "Snap+"),
        ]
        for idx, (key, label) in enumerate(controls):
            self._button(surface, key,
                         pygame.Rect(bx + idx * (small_w + button_gap),
                                     by + 46, small_w, 30),
                         label)
        self._button(surface, "drum_synth_create",
                     pygame.Rect(bx, by + 88, info_w, 34),
                     "Create / Use Drum Synth Track",
                     active=track_idx is not None)

    def _draw_synth_tab(self, surface: pygame.Surface, top: int, sess) -> None:
        font_big = pygame.font.SysFont("Arial", 24, bold=True)
        font = pygame.font.SysFont("Arial", 14)
        font_sm = pygame.font.SysFont("Arial", 12)
        indices = self._synth_track_indices(sess)
        track_idx = self._selected_synth_track_index(sess)
        track_name = "No synth track"
        role = "-"
        params = synth_params(sess, track_idx)
        preset = "lead"
        if track_idx is not None and 0 <= track_idx < len(sess.tracks):
            track = sess.tracks[track_idx]
            track_name = track.name
            role = synth_track_role(track)
            preset = str((track.instrument.params if track.instrument else {})
                         .get("preset") or preset)
        surface.blit(font_big.render("Synths", True, (232, 234, 242)),
                     (20, top + 8))
        surface.blit(font.render(f"{track_name}  {role}  {preset}", True,
                                 (150, 162, 184)), (150, top + 16))
        if self._synth_message:
            self._draw_text_fit(surface, font_sm, self._synth_message,
                                (174, 188, 222), (380, top + 19),
                                surface.get_width() - 400)

        margin = 20
        gap = 12
        y = top + 54
        content_w = surface.get_width() - margin * 2
        left_w = min(310, int(content_w * 0.40))
        right_w = content_w - left_w - gap
        left = pygame.Rect(margin, y, left_w, 276)
        right = pygame.Rect(margin + left_w + gap, y, right_w, 276)
        ly = self._panel(surface, left, "Tracks")
        ry = self._panel(surface, right, "Sound")

        button_h = 34
        for slot, idx in enumerate(indices[:5]):
            track = sess.tracks[idx]
            rect = pygame.Rect(left.x + 14, ly + slot * (button_h + 8),
                               left.width - 28, button_h)
            label = f"{idx + 1}. {track.name}"
            self._button(surface, f"synth_track_{slot}", rect, label,
                         active=idx == track_idx)
        if not indices:
            self._button(surface, "synth_create",
                         pygame.Rect(left.x + 14, ly, left.width - 28, button_h),
                         "Create Synth Track")
        nav_y = left.bottom - 44
        nav_w = (left.width - 36) // 2
        self._button(surface, "synth_track_prev",
                     pygame.Rect(left.x + 14, nav_y, nav_w, 34), "Track-")
        self._button(surface, "synth_track_next",
                     pygame.Rect(left.x + 22 + nav_w, nav_y, nav_w, 34),
                     "Track+")

        info_w = right.width - 28
        col_w = max(72, (info_w - 24) // 4)
        col_x = [right.x + 14 + idx * (col_w + 8) for idx in range(4)]
        self._info_pair(surface, col_x[0], ry, "Waveform",
                        str(params.get("waveform", "-")), col_w)
        self._info_pair(surface, col_x[1], ry, "Cutoff",
                        f"{float(params.get('cutoff_hz', 0)):.0f}Hz", col_w)
        self._info_pair(surface, col_x[2], ry, "Env",
                        f"{float(params.get('cutoff_env', 0)):.2f}", col_w)
        self._info_pair(surface, col_x[3], ry, "Gain",
                        f"{float(params.get('gain', 0)):.2f}", col_w)
        self._info_pair(surface, col_x[0], ry + 56, "Attack",
                        f"{float(params.get('attack', 0)):.2f}s", col_w)
        self._info_pair(surface, col_x[1], ry + 56, "Decay",
                        f"{float(params.get('decay', 0)):.2f}s", col_w)
        self._info_pair(surface, col_x[2], ry + 56, "Sustain",
                        f"{float(params.get('sustain', 0)):.2f}", col_w)
        self._info_pair(surface, col_x[3], ry + 56, "Release",
                        f"{float(params.get('release', 0)):.2f}s", col_w)

        preset_w = (info_w - 24) // 4
        by = ry + 116
        for idx, preset_name in enumerate(SYNTH_PRESETS):
            self._button(
                surface,
                f"synth_preset_{preset_name}",
                pygame.Rect(right.x + 14 + idx * (preset_w + 8), by,
                            preset_w, 34),
                preset_name.title(),
                active=preset == preset_name,
            )
        self._button(surface, "synth_wave",
                     pygame.Rect(right.x + 14 + 3 * (preset_w + 8), by,
                                 preset_w, 34),
                     "Wave")

        controls = [
            ("synth_cutoff_down", "Cut-"),
            ("synth_cutoff_up", "Cut+"),
            ("synth_attack_down", "Atk-"),
            ("synth_attack_up", "Atk+"),
            ("synth_release_down", "Rel-"),
            ("synth_release_up", "Rel+"),
            ("synth_gain_down", "Gain-"),
            ("synth_gain_up", "Gain+"),
        ]
        small_w = (info_w - 7 * 6) // 8
        for idx, (key, label) in enumerate(controls):
            self._button(surface, key,
                         pygame.Rect(right.x + 14 + idx * (small_w + 6),
                                     by + 46, small_w, 30),
                         label)
        self._button(surface, "synth_stop",
                     pygame.Rect(right.x + 14, by + 88, info_w, 34),
                     "Stop Synth Notes", danger=True)

        key_top = y + 292
        key_w = max(36, (content_w - 15 * 6) // 16)
        for idx in range(16):
            pitch = self._synth_base_note + idx
            rect = pygame.Rect(margin + idx * (key_w + 6), key_top,
                               key_w, 58)
            black = pitch % 12 in (1, 3, 6, 8, 10)
            bg = (28, 30, 42) if black else (42, 48, 64)
            edge = (70, 84, 110)
            pygame.draw.rect(surface, bg, rect, border_radius=5)
            pygame.draw.rect(surface, edge, rect, 1, border_radius=5)
            self._buttons[f"synth_key_{pitch}"] = rect
            self._draw_text_fit(surface, font_sm, note_name(pitch),
                                (232, 236, 244), (rect.x + 6, rect.y + 21),
                                rect.width - 12)

    def _draw_mixer_tab(self, surface: pygame.Surface, top: int, sess) -> None:
        font_big = pygame.font.SysFont("Arial", 24, bold=True)
        font = pygame.font.SysFont("Arial", 14)
        font_sm = pygame.font.SysFont("Arial", 12)
        summaries = self._router_summaries(sess)
        selected_idx = self._selected_track_index(sess)
        selected = sess.tracks[selected_idx] if sess.tracks else None
        selected_summary = (
            summaries[selected_idx]
            if 0 <= selected_idx < len(summaries) else None
        )

        surface.blit(font_big.render("Mixer / Router", True, (232, 234, 242)),
                     (20, top + 8))
        header = "track targets, levels, mute/solo, record arm"
        surface.blit(font.render(header, True, (150, 162, 184)),
                     (208, top + 16))
        if self._router_message:
            self._draw_text_fit(surface, font_sm, self._router_message,
                                (174, 188, 222), (470, top + 19),
                                surface.get_width() - 490)

        margin = 20
        gap = 12
        y = top + 54
        content_w = surface.get_width() - margin * 2
        left_w = min(470, int(content_w * 0.62))
        right_w = content_w - left_w - gap
        left = pygame.Rect(margin, y, left_w, 286)
        right = pygame.Rect(margin + left_w + gap, y, right_w, 286)
        ly = self._panel(surface, left, "Track Routes")
        ry = self._panel(surface, right, "Selected Track")

        row_h = 28
        row_gap = 6
        for row, summary in enumerate(summaries[:8]):
            yy = ly + row * (row_h + row_gap)
            if yy + row_h > left.bottom - 10:
                break
            active = summary["index"] == selected_idx
            bg = (36, 68, 60) if active else (18, 20, 30)
            edge = (104, 218, 166) if active else (42, 48, 66)
            row_rect = pygame.Rect(left.x + 12, yy, left.width - 24, row_h)
            pygame.draw.rect(surface, bg, row_rect, border_radius=4)
            pygame.draw.rect(surface, edge, row_rect, 1, border_radius=4)
            select_rect = pygame.Rect(row_rect.x, row_rect.y,
                                      row_rect.width - 96, row_rect.height)
            self._buttons[f"router_track_{summary['index']}"] = select_rect
            name_w = max(70, int(select_rect.width * 0.35))
            self._draw_text_fit(
                surface, font_sm,
                f"{summary['index'] + 1}. {summary['name']}",
                (232, 236, 244), (row_rect.x + 8, row_rect.y + 8), name_w)
            self._draw_text_fit(
                surface, font_sm,
                summary["target_label"],
                (166, 178, 198),
                (row_rect.x + 18 + name_w, row_rect.y + 8),
                select_rect.width - name_w - 24)
            for idx, (field, label) in enumerate((
                ("mute", "M"),
                ("solo", "S"),
                ("arm", "A"),
            )):
                bx = row_rect.right - 90 + idx * 30
                brect = pygame.Rect(bx, row_rect.y + 4, 24, row_rect.height - 8)
                on = bool(summary[field])
                color = (
                    (160, 68, 74) if field == "mute" and on
                    else ((72, 176, 130) if on else (26, 30, 42))
                )
                pygame.draw.rect(surface, color, brect, border_radius=3)
                pygame.draw.rect(surface, (64, 72, 94), brect, 1,
                                 border_radius=3)
                self._buttons[f"router_{field}_{summary['index']}"] = brect
                text_surf = font_sm.render(label, True, (232, 236, 244))
                surface.blit(text_surf, text_surf.get_rect(center=brect.center))

        if selected is not None and selected_summary is not None:
            info_w = right.width - 28
            col_w = max(86, (info_w - 12) // 2)
            self._info_pair(surface, right.x + 14, ry, "Target",
                            selected_summary["target_label"], col_w)
            self._info_pair(surface, right.x + 26 + col_w, ry, "Status",
                            selected_summary["available"], col_w)
            self._info_pair(surface, right.x + 14, ry + 52, "Volume",
                            f"{int(selected.volume * 100)}%", col_w)
            self._info_pair(surface, right.x + 26 + col_w, ry + 52, "Pan",
                            f"{selected.pan:+.2f}", col_w)

            by = ry + 108
            small_gap = 6
            small_w = max(46, (info_w - small_gap * 7) // 8)
            controls = [
                ("router_vol_down", "Vol-"),
                ("router_vol_up", "Vol+"),
                ("router_pan_left", "PanL"),
                ("router_pan_right", "PanR"),
                ("router_mute_selected", "Mute"),
                ("router_solo_selected", "Solo"),
                ("router_arm_selected", "Arm"),
                ("router_clear_solos", "Clear"),
            ]
            for idx, (key, label) in enumerate(controls):
                self._button(
                    surface,
                    key,
                    pygame.Rect(right.x + 14 + idx * (small_w + small_gap),
                                by, small_w, 30),
                    label,
                    active=(
                        key == "router_mute_selected" and selected.mute
                        or key == "router_solo_selected" and selected.solo
                        or key == "router_arm_selected" and selected.arm
                    ),
                    danger=key == "router_mute_selected" and selected.mute,
                )

            target_y = by + 45
            surface.blit(font.render("Targets", True, (226, 230, 242)),
                         (right.x + 14, target_y))
            target_y += 24
            choices = target_choices_for_track(selected)
            target_w = (info_w - 8) // 2
            current_key = target_for_track(selected).key
            for idx, capability in enumerate(choices[:8]):
                col = idx % 2
                row = idx // 2
                rect = pygame.Rect(
                    right.x + 14 + col * (target_w + 8),
                    target_y + row * 34,
                    target_w,
                    28,
                )
                label = capability.label
                active = capability.key == current_key
                self._button(surface, f"router_target_{capability.key}",
                             rect, label, active=active)

        status_rect = pygame.Rect(margin, y + 300, content_w, 66)
        sy = self._panel(surface, status_rect, "Runtime")
        runtime = [
            ("Audio", "running" if self._clip_audio_running() else "stopped"),
            ("Studio audio", "available" if self._studio_audio_supported()
             else "gated"),
            ("Pi", str(self._pi_generation() or "unknown")),
            ("Tracks", str(len(sess.tracks))),
        ]
        box_w = (content_w - 28 - 18) // 4
        for idx, (label, value) in enumerate(runtime):
            self._info_pair(surface, status_rect.x + 14 + idx * (box_w + 6),
                            sy, label, value, box_w)

    def _draw_module_detail_tab(self, surface: pygame.Surface, tab: str,
                                top: int, sess) -> None:
        module = module_for_tab(tab)
        if module is None:
            self._draw_module_hub(surface, top, sess)
            return
        font_big = pygame.font.SysFont("Arial", 24, bold=True)
        font = pygame.font.SysFont("Arial", 14)
        font_sm = pygame.font.SysFont("Arial", 12)
        status = self._module_availability(module)
        surface.blit(font_big.render(module.label, True, (232, 234, 242)),
                     (20, top + 8))
        surface.blit(font.render(f"{status} - {module.stage_label()}",
                                 True, (150, 162, 184)), (20, top + 38))
        surface.blit(font_sm.render(module.summary[:92], True, (150, 162, 184)),
                     (230, top + 18))

        margin = 20
        gap = 12
        y = top + 56
        content_w = surface.get_width() - margin * 2
        left_w = int(content_w * 0.54)
        right_w = content_w - left_w - gap
        left = pygame.Rect(margin, y, left_w, 176)
        right = pygame.Rect(margin + left_w + gap, y, right_w, 176)
        ly = self._panel(surface, left, "Module Surface")
        ry = self._panel(surface, right, "Build Direction")

        self._info_pair(surface, left.x + 14, ly, "Status", status,
                        (left_w - 40) // 2)
        self._info_pair(surface, left.x + 24 + (left_w - 40) // 2, ly,
                        "Runtime", "internal audio" if module.internal_audio else "MIDI",
                        (left_w - 40) // 2)
        list_y = ly + 60
        surface.blit(font.render("Primary controls", True, (226, 230, 242)),
                     (left.x + 14, list_y))
        for idx, feature in enumerate(module.features[:4]):
            self._draw_text_fit(surface, font_sm, feature, (156, 166, 184),
                                (left.x + 24, list_y + 26 + idx * 22),
                                left.width - 48)

        surface.blit(font.render("Next implementation passes", True,
                                 (226, 230, 242)), (right.x + 14, ry))
        for idx, step in enumerate(module.next_steps):
            self._draw_text_fit(surface, font_sm, step, (156, 166, 184),
                                (right.x + 24, ry + 28 + idx * 24),
                                right.width - 48)

        cap_rect = pygame.Rect(margin, y + 190, content_w, 78)
        cy = self._panel(surface, cap_rect, "Capability Targets")
        x = cap_rect.x + 14
        for key in module.capability_keys:
            capability = capability_for(key)
            label = f"{capability.label}: {self._availability(capability)}"
            box = pygame.Rect(x, cy, 220, 34)
            pygame.draw.rect(surface, (24, 27, 38), box, border_radius=4)
            pygame.draw.rect(surface, (52, 58, 78), box, 1, border_radius=4)
            self._draw_text_fit(surface, font_sm, label, (188, 198, 214),
                                (box.x + 10, box.y + 10), box.width - 20)
            x += 230
            if x + 220 > cap_rect.right - 14:
                break

    def _draw_placeholder_tab(self, surface: pygame.Surface, tab: str,
                              top: int, sess) -> None:
        if tab == "performer":
            self._draw_performer_tab(surface, top, sess)
            return
        if tab == "sampler":
            self._draw_sampler_tab(surface, top, sess)
            return
        if tab == "drum_synth":
            self._draw_drum_synth_tab(surface, top, sess)
            return
        if tab == "synth":
            self._draw_synth_tab(surface, top, sess)
            return
        if tab == "mixer":
            self._draw_mixer_tab(surface, top, sess)
            return
        if tab == "overview":
            self._draw_module_hub(surface, top, sess)
            return
        self._draw_module_detail_tab(surface, tab, top, sess)

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
        gesture_gap = 5
        gesture_x = right_x + 16
        gesture_w = (right_w - 32 - gesture_gap * 5) // 6
        gestures = [
            ("performer_lane_mute", "Mute" if not lane_ctrl["mute"] else "On",
             bool(lane_ctrl["mute"])),
            ("performer_gesture_all", "All", False),
            ("performer_gesture_drums", "Drums", False),
            ("performer_gesture_bass", "Bass", False),
            ("performer_gesture_drop_drums", "Drop", False),
            ("performer_gesture_fill", "Fill", False),
        ]
        for idx, (key, label, danger) in enumerate(gestures):
            self._button(
                surface,
                key,
                pygame.Rect(
                    gesture_x + idx * (gesture_w + gesture_gap),
                    ly + 92,
                    gesture_w,
                    24,
                ),
                label,
                danger=danger,
                tone="queued" if key == "performer_gesture_fill" else "",
            )

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
            if key.startswith("studio_module_"):
                self._select_studio_module(key.replace("studio_module_", "", 1))
                return True
            if key.startswith("sampler_pad_"):
                self._trigger_sampler_pad(
                    sess, int(key.rsplit("_", 1)[1]))
                return True
            if key == "sampler_sample_prev":
                self._cycle_sampler_sample(-1)
                return True
            if key == "sampler_sample_next":
                self._cycle_sampler_sample(1)
                return True
            if key == "sampler_assign":
                self._assign_sampler_sample(sess)
                return True
            if key == "sampler_clear":
                self._clear_sampler_pad(sess)
                return True
            if key == "sampler_load_starter":
                self._load_sampler_starter(sess)
                return True
            if key == "sampler_stop":
                self._stop_sampler()
                return True
            if key.startswith("drum_synth_pad_"):
                self._trigger_drum_synth_pad(
                    sess, int(key.rsplit("_", 1)[1]))
                return True
            if key == "drum_synth_create":
                self._ensure_drum_synth_track(sess)
                return True
            if key == "drum_synth_kit_808":
                self._set_drum_synth_kit(sess, "808")
                return True
            if key == "drum_synth_kit_909":
                self._set_drum_synth_kit(sess, "909")
                return True
            if key == "drum_synth_stop":
                self._stop_drum_synth()
                return True
            if key == "drum_synth_tone_down":
                self._adjust_drum_synth_param(sess, "tone", -0.05)
                return True
            if key == "drum_synth_tone_up":
                self._adjust_drum_synth_param(sess, "tone", 0.05)
                return True
            if key == "drum_synth_decay_down":
                self._adjust_drum_synth_param(sess, "decay", -0.05)
                return True
            if key == "drum_synth_decay_up":
                self._adjust_drum_synth_param(sess, "decay", 0.05)
                return True
            if key == "drum_synth_snap_down":
                self._adjust_drum_synth_param(sess, "snap", -0.05)
                return True
            if key == "drum_synth_snap_up":
                self._adjust_drum_synth_param(sess, "snap", 0.05)
                return True
            if key.startswith("synth_track_"):
                suffix = key.replace("synth_track_", "", 1)
                if suffix == "prev":
                    self._cycle_synth_track(sess, -1)
                elif suffix == "next":
                    self._cycle_synth_track(sess, 1)
                else:
                    self._select_synth_track_slot(sess, int(suffix))
                return True
            if key == "synth_create":
                self._ensure_synth_track(sess)
                return True
            if key.startswith("synth_key_"):
                self._preview_synth_note(sess, int(key.rsplit("_", 1)[1]))
                return True
            if key == "synth_stop":
                self._stop_synth_notes()
                return True
            if key == "synth_wave":
                self._cycle_synth_waveform(sess)
                return True
            if key == "synth_preset_bass":
                self._set_synth_preset(sess, "bass")
                return True
            if key == "synth_preset_lead":
                self._set_synth_preset(sess, "lead")
                return True
            if key == "synth_preset_pad":
                self._set_synth_preset(sess, "pad")
                return True
            if key == "synth_cutoff_down":
                self._adjust_synth_param(sess, "cutoff_hz", -250.0)
                return True
            if key == "synth_cutoff_up":
                self._adjust_synth_param(sess, "cutoff_hz", 250.0)
                return True
            if key == "synth_attack_down":
                self._adjust_synth_param(sess, "attack", -0.025)
                return True
            if key == "synth_attack_up":
                self._adjust_synth_param(sess, "attack", 0.025)
                return True
            if key == "synth_release_down":
                self._adjust_synth_param(sess, "release", -0.05)
                return True
            if key == "synth_release_up":
                self._adjust_synth_param(sess, "release", 0.05)
                return True
            if key == "synth_gain_down":
                self._adjust_synth_param(sess, "gain", -0.05)
                return True
            if key == "synth_gain_up":
                self._adjust_synth_param(sess, "gain", 0.05)
                return True
            if key.startswith("router_track_"):
                self._select_router_track(sess, int(key.rsplit("_", 1)[1]))
                return True
            if key.startswith("router_target_"):
                self._route_selected_track(
                    sess, key.replace("router_target_", "", 1))
                return True
            if key == "router_vol_down":
                self._adjust_router_mix(sess, "volume", -0.05)
                return True
            if key == "router_vol_up":
                self._adjust_router_mix(sess, "volume", 0.05)
                return True
            if key == "router_pan_left":
                self._adjust_router_mix(sess, "pan", -0.1)
                return True
            if key == "router_pan_right":
                self._adjust_router_mix(sess, "pan", 0.1)
                return True
            if key == "router_mute_selected":
                self._adjust_router_mix(sess, "mute")
                return True
            if key == "router_solo_selected":
                self._adjust_router_mix(sess, "solo")
                return True
            if key == "router_arm_selected":
                self._adjust_router_mix(sess, "arm")
                return True
            if key == "router_clear_solos":
                self._clear_router_solos(sess)
                return True
            if key.startswith("router_mute_"):
                self._select_router_track(sess, int(key.rsplit("_", 1)[1]))
                self._adjust_router_mix(sess, "mute")
                return True
            if key.startswith("router_solo_"):
                self._select_router_track(sess, int(key.rsplit("_", 1)[1]))
                self._adjust_router_mix(sess, "solo")
                return True
            if key.startswith("router_arm_"):
                self._select_router_track(sess, int(key.rsplit("_", 1)[1]))
                self._adjust_router_mix(sess, "arm")
                return True
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
            gesture_buttons = {
                "performer_gesture_all": "all",
                "performer_gesture_drums": "drums",
                "performer_gesture_bass": "bass",
                "performer_gesture_drop_drums": "drop_drums",
                "performer_gesture_fill": "fill",
            }
            if key in gesture_buttons:
                self._apply_performer_gesture(sess, gesture_buttons[key])
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
