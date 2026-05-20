"""Push 2 control layer — owns mode state, dispatches surface events.

Bridges the stateless engine/push2/surface.Push2Surface to the
stateful Mode classes in ui/push2_modes/. All Push-2-related session
mutations flow through this object.
"""
from __future__ import annotations

import threading
from typing import Optional

from engine.push2driver import constants as C
from engine.push2driver.surface import (Push2Surface, PadEvent, ButtonEvent,
                                   EncoderTurnEvent, EncoderTouchEvent,
                                   TouchStripEvent, PadAftertouchEvent)
from modifiers.modifier_state import ModifierState, MODIFIER_NAMES

from session.session import Session
from session.clip import (MidiClip, AudioClip, ClipState,
                          LaunchQuantize, LaunchMode)
from session.track import TrackType


class Push2Control:
    """Holds Push 2 state + the active Mode."""

    def __init__(self, surface: Push2Surface, engine, session: Session,
                 link_provider) -> None:
        """
        link_provider: callable returning current Link beat (float).
        engine: ClipEngine instance.
        """
        self.surface = surface
        self.engine = engine
        self.session = session
        self.link_provider = link_provider

        self.modifiers = ModifierState()
        self.selected_track: Optional[int] = 0
        self.selected_scene: Optional[int] = 0
        self._dirty = True
        self._dirty_lock = threading.Lock()
        # Gating: only consume Push 2 events when active. The host
        # toggles this on entering / leaving Studio so the
        # existing Compa Push 2 modes (control / keys / pattern) keep
        # working when we're not in Studio.
        self.is_active = False

        # Modes — instantiate lazily so we don't depend on PIL at import time
        self._modes: dict[str, object] = {}
        self.mode_name = "session"
        self._active_mode = None

        if self.surface is not None and self.surface.available:
            self.surface.set_event_handler(self._on_surface_event)

    def _build_modes(self) -> None:
        if self._modes:
            return
        from ui.push2_modes.session import SessionMode
        from ui.push2_modes.note_drum import NoteDrumMode
        from ui.push2_modes.note_synth import NoteSynthMode
        from ui.push2_modes.mix import MixMode
        from ui.push2_modes.performer import PerformerMode
        from ui.push2_modes.sampler import SamplerMode
        from ui.push2_modes.drum_synth import DrumSynthMode
        from ui.push2_modes.studio_synth import StudioSynthMode
        from ui.push2_modes.studio_router import StudioRouterMode
        from ui.push2_modes.studio_recorder import StudioRecorderMode
        from ui.push2_modes.studio import StudioMode
        from ui.push2_modes.stub_modes import (
            DeviceMode, BrowseMode, ClipEditorMode, MasterMode,
            SetupMode, UserMode, OverviewMode,
        )
        self._modes = {
            "session": SessionMode(self),
            "studio": StudioMode(self),
            "note_drum": NoteDrumMode(self),
            "note_synth": NoteSynthMode(self),
            "mix": MixMode(self),
            "performer": PerformerMode(self),
            "sampler": SamplerMode(self),
            "drum_synth": DrumSynthMode(self),
            "studio_synth": StudioSynthMode(self),
            "studio_router": StudioRouterMode(self),
            "studio_recorder": StudioRecorderMode(self),
            "device": DeviceMode(self),
            "browse": BrowseMode(self),
            "clip_editor": ClipEditorMode(self),
            "master": MasterMode(self),
            "setup": SetupMode(self),
            "user": UserMode(self),
            "overview": OverviewMode(self),
        }
        self._active_mode = self._modes["session"]

    @property
    def active_mode(self):
        if self._active_mode is None:
            self._build_modes()
        return self._active_mode

    # ── Public API ────────────────────────────────────────────────
    def request_redraw(self) -> None:
        with self._dirty_lock:
            self._dirty = True

    def consume_dirty(self) -> bool:
        with self._dirty_lock:
            d = self._dirty
            self._dirty = False
        return d

    def switch_mode(self, name: str) -> None:
        if not self._modes:
            self._build_modes()
        if name not in self._modes:
            return
        if self._active_mode is not None:
            try:
                self._active_mode.exit()
            except Exception:
                pass
        self._active_mode = self._modes[name]
        self.mode_name = name
        try:
            self._active_mode.enter()
        except Exception:
            pass
        self.request_redraw()

    def select_cell(self, track: int, scene: int) -> None:
        self.selected_track = track
        self.selected_scene = scene
        self.request_redraw()

    def launch_clip(self, track: int, scene: int,
                    immediate: bool = False) -> None:
        beat = self._beat()
        if immediate:
            # Override quantize for this launch — pretend the clip's
            # quantize is NONE so it fires on the next callback.
            clip = self.session.get_clip(track, scene)
            if clip is not None:
                from session.clip import LaunchQuantize
                saved = clip.launch_quantize
                clip.launch_quantize = LaunchQuantize.NONE
                self.engine.launch_clip(track, scene, beat)
                clip.launch_quantize = saved
            else:
                self.engine.launch_clip(track, scene, beat)
        else:
            self.engine.launch_clip(track, scene, beat)
        self.selected_track = track
        self.selected_scene = scene
        self.request_redraw()

    def stop_clip(self, track: int) -> None:
        self.engine.stop_clip(track, self._beat())
        self.request_redraw()

    def set_track_target(self, track: int, target) -> None:
        if not (0 <= track < len(self.session.tracks)):
            return
        self.session.tracks[track].target = target
        self._persist()
        self.request_redraw()

    def stop_all_clips(self) -> None:
        self.engine.stop_all(self._beat())
        self.request_redraw()

    def launch_scene(self, scene: int) -> None:
        self.engine.launch_scene(scene, self._beat())
        self.selected_scene = scene
        self.request_redraw()

    def arm_recording(self, track: int, scene: int,
                      length_bars: int = 4) -> None:
        """Record from the input recorder into an audio clip slot."""
        ok = self.engine.arm_recording(
            track=track, scene=scene,
            link_beat=self._beat(),
            bpm=self.session.bpm,
            length_bars=length_bars,
            time_sig_num=self.session.time_signature_num,
        )
        if not ok:
            print("arm_recording: recorder unavailable", flush=True)
        else:
            print(f"arm_recording: track {track+1} scene {scene+1}, "
                  f"{length_bars} bars", flush=True)
        self.selected_track = track
        self.selected_scene = scene
        self.request_redraw()

    def add_track(self, drum: bool = False) -> None:
        """Append a new track at the end of the session.

        drum=True  → MIDI track with a Drum Rack instrument.
        drum=False → MIDI track with a SynthVoice (lead preset).
        """
        from session.track import Track, TrackType, InstrumentRef
        from engine.push2driver.palette import track_color_index
        sess = self.session
        idx = len(sess.tracks)
        if drum:
            name = f"Drums {idx+1}"
            instr = InstrumentRef(kind="drum_rack",
                                   name="Drum Rack")
        else:
            name = f"Synth {idx+1}"
            instr = InstrumentRef(kind="synth_voice", name="Synth",
                                   params={"preset": "lead"})
        track = Track(
            id=idx, name=name, type=TrackType.MIDI,
            color=track_color_index(idx), instrument=instr,
            clips=[None] * len(sess.scenes),
        )
        sess.tracks.append(track)
        # Rebuild engine instruments so the new track has audio
        try:
            self.engine._instantiate_instruments()
        except Exception as e:
            print(f"add_track: instrument build failed: {e}", flush=True)
        self.selected_track = idx
        self._persist()
        self.request_redraw()

    def add_scene(self) -> None:
        """Append a new empty scene at the end."""
        from session.scene import Scene
        sess = self.session
        new_idx = len(sess.scenes)
        sess.scenes.append(Scene(name=f"Scene {new_idx+1}"))
        # Each track grows its clip-slot list to match
        for t in sess.tracks:
            while len(t.clips) < len(sess.scenes):
                t.clips.append(None)
        self._persist()
        self.request_redraw()

    def remove_track(self, track_idx: int) -> None:
        """Delete a track. Stops its clips first."""
        sess = self.session
        if not (0 <= track_idx < len(sess.tracks)):
            return
        try:
            self.engine.stop_clip(track_idx, self._beat())
        except Exception:
            pass
        del sess.tracks[track_idx]
        # Re-id remaining tracks so persistence stays sane
        for i, t in enumerate(sess.tracks):
            t.id = i
        try:
            self.engine._instantiate_instruments()
        except Exception:
            pass
        if self.selected_track is not None and self.selected_track >= len(sess.tracks):
            self.selected_track = max(0, len(sess.tracks) - 1)
        self._persist()
        self.request_redraw()

    def _persist(self) -> None:
        try:
            from session.persistence import save_session
            save_session(self.session, "default")
        except Exception:
            pass

    def capture_midi_to_clip(self) -> None:
        """Live 12.4 'Capture MIDI' — drop the recently played MIDI
        on the selected track into the next empty clip slot."""
        sess = self.session
        track = self.selected_track or 0
        if track >= len(sess.tracks):
            return
        clip = self.engine.capture.capture_clip(
            ending_beat=self._beat(),
            bpm=sess.bpm,
            track_filter=track,
            time_sig_num=sess.time_signature_num,
        )
        if clip is None:
            return
        # Find next empty slot on this track
        for s in range(len(sess.tracks[track].clips)):
            if sess.tracks[track].clips[s] is None:
                sess.tracks[track].clips[s] = clip
                self.selected_scene = s
                self._persist()
                self.request_redraw()
                return
        # No empty slot: append a new scene at the end + put it there
        self.add_scene()
        new_idx = len(sess.scenes) - 1
        sess.tracks[track].clips[new_idx] = clip
        self.selected_scene = new_idx
        self._persist()
        self.request_redraw()

    def duplicate_clip(self, track: int, scene: int) -> None:
        sess = self.session
        clip = sess.get_clip(track, scene)
        if clip is None:
            return
        # Find next empty slot
        for s in range(scene + 1, len(sess.tracks[track].clips)):
            if sess.get_clip(track, s) is None:
                # Deep-ish copy via JSON round-trip
                d = clip.to_dict()
                from session.clip import clip_from_dict
                new = clip_from_dict(d)
                sess.set_clip(track, s, new)
                self.request_redraw()
                return

    def playhead_step_for_clip(self, track: int, scene: int,
                                step_resolution_beats: float) -> int:
        from session.clip import ClipState
        sched = self.engine.scheduler
        st = sched.state_for(track, scene)
        if st.state != ClipState.PLAYING:
            return -1
        clip = self.session.get_clip(track, scene)
        if clip is None or step_resolution_beats <= 0:
            return -1
        beat = self._beat()
        local = (beat - st.actual_start_beat) % clip.length_beats
        return int(local / step_resolution_beats)

    # ── Surface event ingestion ───────────────────────────────────
    def _on_surface_event(self, event) -> None:
        if not self.is_active:
            return
        try:
            self._dispatch(event)
            self.request_redraw()
        except Exception as e:
            print(f"Push 2 control: dispatch failed: {e}", flush=True)

    def _dispatch(self, ev) -> None:
        mode = self.active_mode
        if isinstance(ev, ButtonEvent):
            self._handle_button(ev, mode)
        elif isinstance(ev, PadEvent):
            mode.on_pad(ev.col, ev.row, ev.velocity, ev.is_press)
        elif isinstance(ev, EncoderTurnEvent):
            self._handle_encoder_turn(ev, mode)
        elif isinstance(ev, EncoderTouchEvent):
            mode.on_encoder_touch(ev.note, ev.is_touched)
        elif isinstance(ev, TouchStripEvent):
            mode.on_touch_strip(ev.value, ev.is_touch)
        elif isinstance(ev, PadAftertouchEvent):
            pass  # not wired in v1

    def _handle_button(self, ev: ButtonEvent, mode) -> None:
        # Modifier press/release management
        if ev.name in MODIFIER_NAMES:
            if ev.is_press:
                self.modifiers.press(ev.name)
            else:
                self.modifiers.release(ev.name)

        if (ev.is_press and getattr(mode, "name", "") == "performer"
                and ev.name in ("play", "stop_clip", "record")):
            if mode.on_button(ev.name, ev.is_press):
                return

        # Top-level mode switches (only on press)
        if ev.is_press:
            mode_map = {
                "session": "session",
                "mix": "mix",
                "device": "device",
                "browse": "browse",
                "clip": "clip_editor",
                "master": "master",
                "setup": "setup",
                "user": "user",
            }
            if ev.name in mode_map:
                self.switch_mode(mode_map[ev.name])
                return
            if ev.name == "note":
                # Note mode dispatches based on selected track type
                self._enter_note_mode_for_selected()
                return
            if ev.name == "play":
                # Play resumes Link transport (via host side; engine just runs)
                self.engine.active = not self.engine.active
                return
            if ev.name == "stop_clip":
                if "shift" in self.modifiers:
                    self.stop_all_clips()
                else:
                    if self.selected_track is not None:
                        self.stop_clip(self.selected_track)
                return

        # Hand off to the active mode
        mode.on_button(ev.name, ev.is_press)

    def _handle_encoder_turn(self, ev: EncoderTurnEvent, mode) -> None:
        # Tempo encoder = master BPM (writes to session — Link master)
        if ev.name == "tempo":
            step = 0.1 if "shift" in self.modifiers else 1.0
            self.session.bpm = max(20.0, min(300.0,
                                                 self.session.bpm + ev.delta * step))
            self.request_redraw()
            return
        if ev.name == "swing":
            self.session.swing = max(0.0, min(0.66,
                                                  self.session.swing + ev.delta * 0.005))
            self.request_redraw()
            return
        mode.on_encoder_turn(ev.name, ev.delta)

    def _enter_note_mode_for_selected(self) -> None:
        t = self.selected_track or 0
        if t < len(self.session.tracks):
            track = self.session.tracks[t]
            if (track.type == TrackType.MIDI
                    and track.instrument
                    and track.instrument.kind == "drum_rack"):
                self.switch_mode("note_drum")
                return
        self.switch_mode("note_synth")

    # ── Helper ────────────────────────────────────────────────────
    def _beat(self) -> float:
        try:
            return float(self.link_provider())
        except Exception:
            return 0.0
