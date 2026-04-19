"""Generic MIDI controller mapper.

Claims USB MIDI ports matching ship-default JSON profiles (in
setup/midi_profiles/) and dispatches incoming MIDI events to Compa
actions defined in engine.controller_actions.

User overrides live at sessions/controller_overrides/<port_slug>.json
and supersede shipped profile mappings.

Coexists with:
 - ChromaticKeyboard: claimed ports are added to its exclusion set
 - TwisterGenius / SpectraMapper / AtomSQ: their ports never match a
   generic profile (they have distinctive port names already filtered).

Architecture
------------
  ControllerMapper          — top-level: load profiles, scan for ports,
                              spin up ActiveBinding instances, expose
                              MIDI Learn API.
  ActiveBinding             — one claimed port, owns a rtmidi.MidiIn,
                              runs a poll thread, dispatches messages
                              through its Profile's mapping table.
  Profile                   — parsed JSON: name, port_match list,
                              dict of Source → action_id.
  Source (dataclass)        — a MIDI event descriptor (kind, channel,
                              index). kinds: note, cc, note_range,
                              pitch_bend, program_change.
"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

try:
    import rtmidi
except ImportError:
    rtmidi = None

from engine import controller_actions as actions

log = logging.getLogger(__name__)


# ── Source representation ────────────────────────────────────────


@dataclass(frozen=True)
class Source:
    kind: str            # "note", "cc", "note_range", "pitch_bend", "program_change"
    channel: int = 0     # 0-indexed MIDI channel
    index: int = 0       # note number or CC number
    low: int = 0         # low end of note_range
    high: int = 127      # high end of note_range

    def key(self) -> tuple:
        """Hashable key for exact-match sources (not note_range)."""
        return (self.kind, self.channel, self.index)

    def describe(self) -> str:
        """Human-readable label for the UI."""
        ch = self.channel + 1
        if self.kind == "note":
            return f"Note {self.index} Ch{ch}"
        if self.kind == "cc":
            return f"CC {self.index} Ch{ch}"
        if self.kind == "note_range":
            return f"Notes {self.low}-{self.high} Ch{ch}"
        if self.kind == "pitch_bend":
            return f"Pitch Bend Ch{ch}"
        if self.kind == "program_change":
            return f"PC {self.index} Ch{ch}"
        return f"{self.kind} {self.index} Ch{ch}"

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "channel": self.channel}
        if self.kind == "note_range":
            d["low"] = self.low
            d["high"] = self.high
        else:
            d["index"] = self.index
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Source":
        kind = d.get("kind", "note")
        if kind == "note_range":
            return cls(kind="note_range",
                       channel=d.get("channel", 0),
                       low=d.get("low", 0),
                       high=d.get("high", 127))
        return cls(kind=kind,
                   channel=d.get("channel", 0),
                   index=d.get("index", 0))


def parse_midi_message(data: list[int]) -> Optional[tuple[Source, int]]:
    """Parse a raw MIDI byte list into (Source, value).

    Returns None for messages we don't handle (sysex, clock, etc).
    For notes: value = velocity (0-127), 0 on note off.
    For CCs: value = CC value (0-127).
    For pitch bend: value = combined 14-bit value mapped to 0-127.
    For program change: value = program number.
    """
    if not data:
        return None
    status = data[0] & 0xF0
    channel = data[0] & 0x0F

    if status == 0x90 and len(data) >= 3:
        # Note On (velocity 0 = note off per MIDI spec)
        velocity = data[2]
        return Source("note", channel, data[1]), velocity
    if status == 0x80 and len(data) >= 3:
        # Note Off
        return Source("note", channel, data[1]), 0
    if status == 0xB0 and len(data) >= 3:
        return Source("cc", channel, data[1]), data[2]
    if status == 0xE0 and len(data) >= 3:
        lsb = data[1]
        msb = data[2]
        raw = (msb << 7) | lsb
        # Map 0-16383 → 0-127 for compatibility with button/knob handlers
        mapped = min(127, raw >> 7)
        return Source("pitch_bend", channel, 0), mapped
    if status == 0xC0 and len(data) >= 2:
        return Source("program_change", channel, data[1]), data[1]
    return None


# ── Profile ──────────────────────────────────────────────────────


@dataclass
class Profile:
    name: str
    port_match: list[str]
    description: str = ""
    # (Source.key() or ("note_range", channel, low, high)) → action_id
    mappings: dict = field(default_factory=dict)
    # Store original Source objects for UI display
    sources_by_action: dict[str, Source] = field(default_factory=dict)
    source_path: str = ""  # file this profile was loaded from (for debug)
    # user_override flag: True if this mapping came from an override file
    overridden_actions: set = field(default_factory=set)

    def match_port(self, port_name: str) -> bool:
        lower = port_name.lower()
        for m in self.port_match:
            if m.lower() in lower:
                return True
        return False

    def mapping_for_msg(self, src: Source, raw_data: list[int]) -> Optional[str]:
        """Look up the action id for a given MIDI source. Also handles
        note_range sources by scanning for any matching range entry.
        """
        # Exact match first
        key = src.key()
        action = self.mappings.get(key)
        if action:
            return action
        # Check note_range entries (only for note kind)
        if src.kind == "note":
            for stored_key, aid in self.mappings.items():
                if len(stored_key) >= 4 and stored_key[0] == "note_range":
                    _nk, ch, low, high = stored_key
                    if ch == src.channel and low <= src.index <= high:
                        return aid
        return None

    def get_source_for_action(self, action_id: str) -> Optional[Source]:
        return self.sources_by_action.get(action_id)


def _source_key(src: Source) -> tuple:
    """Key tuple used as the internal mapping-dict key. note_range
    entries use a 4-tuple so we can distinguish them from exact note
    sources during lookup.
    """
    if src.kind == "note_range":
        return ("note_range", src.channel, src.low, src.high)
    return src.key()


def _load_profile_file(path: str) -> Optional[Profile]:
    try:
        with open(path) as f:
            data = json.load(f)
        prof = Profile(
            name=data.get("name", os.path.basename(path)),
            port_match=list(data.get("port_match", [])),
            description=data.get("description", ""),
            source_path=path,
        )
        for entry in data.get("mappings", []):
            src_dict = entry.get("source", {})
            action_id = entry.get("action")
            if not action_id:
                continue
            src = Source.from_dict(src_dict)
            prof.mappings[_source_key(src)] = action_id
            prof.sources_by_action[action_id] = src
        return prof
    except Exception as e:
        log.warning("failed to load profile %s: %s", path, e)
        return None


def _apply_override_to_profile(prof: Profile, override_path: str):
    """Overlay an override JSON onto an existing profile in place."""
    try:
        with open(override_path) as f:
            data = json.load(f)
    except Exception as e:
        log.debug("override %s not loaded: %s", override_path, e)
        return
    for entry in data.get("mappings", []):
        action_id = entry.get("action")
        if not action_id:
            continue
        src_dict = entry.get("source")
        cleared = entry.get("clear", False)
        if cleared:
            # Remove from mappings and sources_by_action
            src = prof.sources_by_action.pop(action_id, None)
            if src is not None:
                prof.mappings.pop(_source_key(src), None)
            prof.overridden_actions.add(action_id)
            continue
        if src_dict is None:
            continue
        # Remove any existing source for this action, then set new
        old_src = prof.sources_by_action.pop(action_id, None)
        if old_src is not None:
            prof.mappings.pop(_source_key(old_src), None)
        new_src = Source.from_dict(src_dict)
        prof.mappings[_source_key(new_src)] = action_id
        prof.sources_by_action[action_id] = new_src
        prof.overridden_actions.add(action_id)


# ── Active Binding (one claimed port) ────────────────────────────


class ActiveBinding:
    def __init__(self, mapper: "ControllerMapper", profile: Profile,
                 port_index: int, port_name: str):
        self.mapper = mapper
        self.profile = profile
        self.port_name = port_name
        self._midi_in = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        try:
            midi_in = rtmidi.MidiIn()
            midi_in.open_port(port_index)
            midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
            self._midi_in = midi_in
        except Exception as e:
            log.warning("failed to open %s: %s", port_name, e)

    def start(self):
        if self._midi_in is None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._midi_in is not None:
            try:
                self._midi_in.close_port()
            except Exception:
                pass
            self._midi_in = None

    def _poll(self):
        while self._running:
            if self._midi_in is None:
                return
            try:
                msg = self._midi_in.get_message()
            except Exception:
                log.info("controller disconnected: %s", self.port_name)
                self.mapper._on_binding_lost(self)
                return
            if msg is None:
                time.sleep(0.001)
                continue
            data, _delta = msg
            self._handle_message(data)

    def _handle_message(self, data: list[int]):
        parsed = parse_midi_message(data)
        if parsed is None:
            return
        src, value = parsed

        # MIDI Learn — capture and return without dispatching
        if self.mapper._learn_target is not None:
            # Ignore all-zero note-off at the start (treat as release)
            if src.kind == "note" and value == 0:
                return
            self.mapper._capture_learn(self, src)
            return

        # Dispatch — look up the action for this source on this profile
        action_id = self.profile.mapping_for_msg(src, data)
        if action_id is None:
            return

        # Special value encoding for chromatic notes: pass (note, velocity)
        if action_id == "chromatic.note" and src.kind == "note":
            actions.dispatch("chromatic.note",
                              (src.index, value), self.mapper.app)
            return

        actions.dispatch(action_id, value, self.mapper.app)


# ── ControllerMapper ─────────────────────────────────────────────


class ControllerMapper:
    def __init__(self, app, profiles_dir: str, overrides_dir: str):
        self.app = app
        self._profiles_dir = profiles_dir
        self._overrides_dir = overrides_dir
        self._profiles: list[Profile] = []          # ship defaults + overrides applied
        self._bindings: dict[str, ActiveBinding] = {}  # port_name → binding
        self._running = False
        self._scan_thread: Optional[threading.Thread] = None

        # MIDI Learn state
        self._learn_target: Optional[str] = None
        self._learn_callback: Optional[Callable] = None
        self._learn_lock = threading.Lock()

    # ── Profile loading ──────────────────────────────────────────

    def load_profiles(self):
        """Load ship profiles from profiles_dir, apply per-port overrides."""
        self._profiles.clear()
        if not os.path.isdir(self._profiles_dir):
            log.info("no profiles dir at %s", self._profiles_dir)
            return
        os.makedirs(self._overrides_dir, exist_ok=True)

        for fn in sorted(os.listdir(self._profiles_dir)):
            if not fn.endswith(".json"):
                continue
            prof = _load_profile_file(
                os.path.join(self._profiles_dir, fn))
            if prof:
                self._profiles.append(prof)

        log.info("loaded %d MIDI profiles", len(self._profiles))

        # Note: overrides are applied per-port when a binding starts,
        # since they live under sessions/controller_overrides/<slug>.json

    def _slug_for_port(self, port_name: str) -> str:
        """Turn a port name into a safe filename slug."""
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", port_name)
        return slug[:80] or "controller"

    def _override_path(self, port_name: str) -> str:
        return os.path.join(self._overrides_dir,
                             f"{self._slug_for_port(port_name)}.json")

    # ── Scan thread ──────────────────────────────────────────────

    def start(self):
        if rtmidi is None:
            log.info("rtmidi not installed — ControllerMapper disabled")
            return
        if self._running:
            return
        self._running = True
        self._scan_thread = threading.Thread(
            target=self._scan_loop, daemon=True)
        self._scan_thread.start()

    def stop(self):
        self._running = False
        for b in list(self._bindings.values()):
            b.stop()
        self._bindings.clear()

    def _scan_loop(self):
        while self._running:
            try:
                self._scan_once()
            except Exception as e:
                log.debug("scan error: %s", e)
            time.sleep(2.0)

    def _scan_once(self):
        try:
            midi_in = rtmidi.MidiIn()
            ports = midi_in.get_ports()
            midi_in.delete()
        except Exception:
            return

        active_port_names = set()
        for i, name in enumerate(ports):
            if name in self._bindings:
                active_port_names.add(name)
                continue
            # Look for a matching profile
            for prof in self._profiles:
                if prof.match_port(name):
                    # Make a per-binding copy so overrides don't leak
                    # across multiple instances of the same profile.
                    inst = Profile(
                        name=prof.name,
                        port_match=list(prof.port_match),
                        description=prof.description,
                        mappings=dict(prof.mappings),
                        sources_by_action=dict(prof.sources_by_action),
                        source_path=prof.source_path,
                    )
                    # Apply overrides for this specific port
                    override_path = self._override_path(name)
                    if os.path.isfile(override_path):
                        _apply_override_to_profile(inst, override_path)

                    binding = ActiveBinding(self, inst, i, name)
                    binding.start()
                    self._bindings[name] = binding
                    active_port_names.add(name)
                    log.info("claimed port: %s → profile %s", name, inst.name)
                    print(f"ControllerMapper: {name} → {inst.name}",
                          flush=True)
                    break

        # Drop bindings whose ports are gone
        for name in list(self._bindings.keys()):
            if name not in active_port_names:
                log.info("port gone: %s", name)
                self._bindings[name].stop()
                del self._bindings[name]

    def _on_binding_lost(self, binding: ActiveBinding):
        """Called from binding's poll thread when its port disappears."""
        self._bindings.pop(binding.port_name, None)

    # ── Public API ───────────────────────────────────────────────

    @property
    def claimed_port_names(self) -> set[str]:
        return set(self._bindings.keys())

    def claimed_port_hints(self) -> set[str]:
        """Port names currently claimed — used by ChromaticKeyboard's
        exclusion logic."""
        return set(self._bindings.keys())

    def port_matches_any_profile(self, port_name: str) -> bool:
        """True if this port would be claimed by one of the loaded
        profiles — even if not yet claimed. Prevents ChromaticKeyboard
        from grabbing a port that's about to be claimed on the next
        mapper scan cycle.
        """
        for prof in self._profiles:
            if prof.match_port(port_name):
                return True
        return False

    def connected_controllers(self) -> list[ActiveBinding]:
        return list(self._bindings.values())

    # ── MIDI Learn ───────────────────────────────────────────────

    def set_learn_target(self, action_id: str, callback: Callable):
        """Capture the next incoming MIDI message and call callback(source)."""
        with self._learn_lock:
            self._learn_target = action_id
            self._learn_callback = callback

    def cancel_learn(self):
        with self._learn_lock:
            self._learn_target = None
            self._learn_callback = None

    @property
    def learn_target(self) -> Optional[str]:
        return self._learn_target

    def _capture_learn(self, binding: ActiveBinding, src: Source):
        with self._learn_lock:
            if self._learn_target is None:
                return
            action_id = self._learn_target
            cb = self._learn_callback
            self._learn_target = None
            self._learn_callback = None

        # Persist the override on this binding's port
        self._apply_learn(binding, action_id, src)
        if cb:
            try:
                cb(src, binding, action_id)
            except Exception as e:
                log.warning("learn callback error: %s", e)

    def _apply_learn(self, binding: ActiveBinding, action_id: str,
                      src: Source):
        """Write the new mapping to the binding's profile and save override."""
        prof = binding.profile
        # Remove any previous source for this action
        old_src = prof.sources_by_action.pop(action_id, None)
        if old_src is not None:
            prof.mappings.pop(_source_key(old_src), None)
        # Remove any other action that was previously bound to this source
        existing = prof.mappings.pop(_source_key(src), None)
        if existing is not None:
            prof.sources_by_action.pop(existing, None)
        # Install new
        prof.mappings[_source_key(src)] = action_id
        prof.sources_by_action[action_id] = src
        prof.overridden_actions.add(action_id)
        self._save_override(binding.port_name, prof)

    def clear_mapping(self, binding: ActiveBinding, action_id: str):
        """Remove a mapping and persist as a 'clear' override."""
        prof = binding.profile
        old_src = prof.sources_by_action.pop(action_id, None)
        if old_src is not None:
            prof.mappings.pop(_source_key(old_src), None)
        prof.overridden_actions.add(action_id)
        self._save_override(binding.port_name, prof)

    def reset_overrides(self, binding: ActiveBinding):
        """Delete the override file for this port and reload from ship profile."""
        path = self._override_path(binding.port_name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception as e:
            log.warning("failed to delete override %s: %s", path, e)
        # Reload profile from disk (find the original)
        for ship in self._profiles:
            if ship.name == binding.profile.name:
                binding.profile.mappings = dict(ship.mappings)
                binding.profile.sources_by_action = dict(
                    ship.sources_by_action)
                binding.profile.overridden_actions.clear()
                break

    def _save_override(self, port_name: str, profile: Profile):
        """Write the current overrides to disk. Only include actions that
        differ from the ship profile."""
        # Find the original ship profile
        ship = next((p for p in self._profiles if p.name == profile.name),
                    None)
        override_entries = []
        if ship is None:
            # Unusual case — just dump all mappings
            for action_id, src in profile.sources_by_action.items():
                override_entries.append({
                    "action": action_id,
                    "source": src.to_dict(),
                })
        else:
            # Walk the union of ship + current sources_by_action
            all_actions = (set(profile.sources_by_action.keys())
                            | set(ship.sources_by_action.keys()))
            for aid in sorted(all_actions):
                cur = profile.sources_by_action.get(aid)
                orig = ship.sources_by_action.get(aid)
                if cur is None and orig is not None:
                    # Cleared: write a "clear" entry
                    override_entries.append({"action": aid, "clear": True})
                elif cur is not None and (orig is None or cur != orig):
                    override_entries.append({
                        "action": aid,
                        "source": cur.to_dict(),
                    })

        path = self._override_path(port_name)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {
                "port_name": port_name,
                "profile": profile.name,
                "mappings": override_entries,
            }
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            log.warning("failed to save override %s: %s", path, e)

    def on_focus_changed(self, device_key: str):
        """Hook for retargeting — no-op for the generic mapper since
        all actions look up the focused device dynamically."""
        pass
