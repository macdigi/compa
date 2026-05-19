"""Controller action registry.

Defines every Compa action that can be mapped to an external MIDI
controller, with category, display label, and a handler function.

Handlers take (app, value) where `value` is the MIDI data value
(note velocity, CC value, note number for chromatic.note, etc.).

The registry is a dict keyed by action id (string like
"pad.trigger.5" or "transport.play"). The controller mapper looks
up the handler when a MIDI message arrives.
"""

import logging
from typing import Callable

log = logging.getLogger(__name__)


# ── Action handlers ───────────────────────────────────────────────


def _action_play(app, value: int):
    """Start the transport on the focused device (and master clock)."""
    if value <= 0:
        return  # release — no-op
    midi = _focused_midi(app)
    if midi and hasattr(midi, "send_start"):
        midi.send_start()
    _record_event(app, "transport", "controller",
                  {"action": "play", "value": int(value)})


def _action_stop(app, value: int):
    if value <= 0:
        return
    midi = _focused_midi(app)
    if midi and hasattr(midi, "send_stop"):
        midi.send_stop()
    _record_event(app, "transport", "controller",
                  {"action": "stop", "value": int(value)})


def _action_record(app, value: int):
    """Toggle auto-record on/off."""
    if value <= 0:
        return
    if hasattr(app, "auto_record"):
        app.auto_record = not app.auto_record
        try:
            from ui.p6_app import save_config_key
            save_config_key("P6_AUTO_RECORD",
                            "1" if app.auto_record else "0")
        except Exception:
            pass
        _record_event(app, "record.toggle", "controller",
                      {"auto_record": bool(app.auto_record)})


def _focused_midi(app):
    """Get the P6Midi instance for the currently focused device."""
    try:
        return app._midi_connections.get(app.device_manager.focus_key)
    except Exception:
        return None


def _record_event(app, event_type: str, source: str, payload: dict):
    try:
        device = app.device_manager.focus_key
        app.record_performance_event(event_type, source, device, payload)
    except Exception:
        pass


def _record_note(app, source: str, note: int, velocity: int,
                 channel: int, payload: dict):
    try:
        device = app.device_manager.focus_key
        app.record_performance_note(source, device, note, velocity, channel,
                                    payload=payload)
    except Exception:
        pass


# ── Pad triggers — respect current bank ──────────────────────────


def _compute_pad_note(device_key: str, bank_idx: int, pad_idx: int,
                      midi) -> tuple[int, int]:
    """Compute (note, channel) for a pad trigger on the given device.

    SP-404 uses top-left-first pad numbering but MIDI notes 36-39
    map to the BOTTOM row, so we invert the SP row. P-6 uses a
    sequential sampler-channel mapping.

    Returns (note, channel) or (-1, -1) on failure.
    """
    if device_key == "SP-404MKII":
        # SP-404: Ch 1-10 for banks A-J, notes 36-51 per bank
        # top-left pad 1 = note 48 (row 0, col 0 → midi_row 3)
        if not (0 <= pad_idx < 16):
            return (-1, -1)
        if not (0 <= bank_idx < 10):
            bank_idx = 0
        sp_row = pad_idx // 4
        col = pad_idx % 4
        midi_row = 3 - sp_row
        note = 36 + midi_row * 4 + col
        return (note, bank_idx)
    if device_key == "P-6":
        # P-6 has 6 pads × 8 banks, sequential on sampler channel
        if not (0 <= pad_idx < 6):
            return (-1, -1)
        if not (0 <= bank_idx < 8):
            bank_idx = 0
        channel = getattr(midi, "ch_sampler", 10) if midi else 10
        note = 48 + bank_idx * 6 + pad_idx
        return (note, channel)
    # Generic fallback
    channel = getattr(midi, "ch_sampler", 10) if midi else 10
    return (36 + pad_idx, channel)


def _make_pad_trigger(pad_idx: int) -> Callable:
    """Factory: return a handler that triggers pad `pad_idx` (0-indexed)
    on the focused device's current bank.

    value > 0 = note on, value == 0 = note off.
    """
    def _handler(app, value: int):
        midi = _focused_midi(app)
        if midi is None:
            return
        device_key = app.device_manager.focus_key
        bank_idx = app.current_bank.get(device_key, 0)
        note, channel = _compute_pad_note(device_key, bank_idx, pad_idx, midi)
        if note < 0:
            return
        if value > 0:
            midi.send_note_on(note, value, channel=channel)
        else:
            midi.send_note_off(note, channel=channel)
        _record_note(app, "controller", note, value, channel,
                     {"bank": bank_idx, "pad": pad_idx})
    return _handler


# ── Bank actions ─────────────────────────────────────────────────


def _bank_count_for(device_key: str) -> int:
    return {"SP-404MKII": 10, "P-6": 8}.get(device_key, 4)


def _action_bank_up(app, value: int):
    if value <= 0:
        return
    key = app.device_manager.focus_key
    cur = app.current_bank.get(key, 0)
    new = (cur + 1) % _bank_count_for(key)
    app.current_bank[key] = new
    _notify_bank_change(app, key, new)


def _action_bank_down(app, value: int):
    if value <= 0:
        return
    key = app.device_manager.focus_key
    cur = app.current_bank.get(key, 0)
    new = (cur - 1) % _bank_count_for(key)
    app.current_bank[key] = new
    _notify_bank_change(app, key, new)


def _make_bank_set(idx: int) -> Callable:
    def _handler(app, value: int):
        if value <= 0:
            return
        key = app.device_manager.focus_key
        if idx >= _bank_count_for(key):
            return
        app.current_bank[key] = idx
        _notify_bank_change(app, key, idx)
    return _handler


def _notify_bank_change(app, device_key: str, bank_idx: int):
    """Sync KEYS tab pad selector and push a brief HUD note."""
    # Sync the KEYS tab state so the on-screen pad selector reflects the
    # new bank immediately.
    ws = None
    try:
        ws = app.screens.get("device_workspace")
    except Exception:
        pass
    if ws and hasattr(ws, "_keys_bank"):
        ws._keys_bank = bank_idx
    if hasattr(app, "push_hud"):
        letter = chr(ord("A") + bank_idx)
        app.push_hud(f"Bank {letter}", None)
    _record_event(app, "bank.select", "controller",
                  {"bank": bank_idx})


# ── Twister-style FX slot control ────────────────────────────────


def _make_twister_turn(slot: int) -> Callable:
    """Knob turn → delegate to Twister's _on_turn for that slot."""
    def _handler(app, value: int):
        tw = getattr(app, "twister", None)
        if tw is None:
            return
        # Twister's public API uses physical knob index 0-15
        try:
            tw._on_turn(slot, value)
        except Exception as e:
            log.debug("twister.turn.%d failed: %s", slot + 1, e)
    return _handler


def _make_twister_press(slot: int) -> Callable:
    def _handler(app, value: int):
        tw = getattr(app, "twister", None)
        if tw is None:
            return
        try:
            tw._on_press(slot, 127 if value > 0 else 0)
        except Exception as e:
            log.debug("twister.press.%d failed: %s", slot + 1, e)
    return _handler


# ── Chromatic pass-through ───────────────────────────────────────


def _action_chromatic_note(app, note: int, velocity: int = 100):
    """Play a chromatic note through the ChromaticKeyboard module.

    Unlike other handlers, this one takes a second implicit arg:
    the mapper dispatches (note, velocity) for note events.
    """
    kb = getattr(app, "chromatic_kb", None)
    if kb is None or not kb.enabled:
        return
    if velocity > 0:
        kb._forward_note_on(note, velocity)
        kb.active_notes[note] = velocity
        if kb.on_note_on:
            kb.on_note_on(note, velocity)
    else:
        kb._forward_note_off(note)
        kb.active_notes.pop(note, None)
        if kb.on_note_off:
            kb.on_note_off(note)
    _record_note(app, "controller.chromatic", note, velocity, 0, {})


# ── Keys tab actions ─────────────────────────────────────────────


def _action_octave_up(app, value: int):
    if value <= 0:
        return
    kb = getattr(app, "chromatic_kb", None)
    if kb:
        kb.octave_shift = min(3, kb.octave_shift + 1)


def _action_octave_down(app, value: int):
    if value <= 0:
        return
    kb = getattr(app, "chromatic_kb", None)
    if kb:
        kb.octave_shift = max(-3, kb.octave_shift - 1)


def _action_latch_toggle(app, value: int):
    if value <= 0:
        return
    ws = app.screens.get("device_workspace")
    if ws and hasattr(ws, "_keys_latch"):
        ws._keys_latch = not ws._keys_latch


def _action_keep_toggle(app, value: int):
    if value <= 0:
        return
    ws = app.screens.get("device_workspace")
    if ws and hasattr(ws, "_keys_persistent"):
        ws._keys_persistent = not ws._keys_persistent


# ── Navigation actions ───────────────────────────────────────────


def _make_screen_nav(screen_name: str) -> Callable:
    def _handler(app, value: int):
        if value <= 0:
            return
        if hasattr(app, "switch_screen"):
            app.switch_screen(screen_name)
    return _handler


def _action_focus_next(app, value: int):
    if value <= 0:
        return
    if hasattr(app, "cycle_focus"):
        app.cycle_focus()


# ── Volume actions (placeholders — wired when volume control lands) ──


def _action_volume_master(app, value: int):
    """Master volume — placeholder. Future: map CC 0-127 to master level."""
    # TODO: wire to recorder/audio engine when master volume exists
    pass


# ── Action registry ──────────────────────────────────────────────
#
# Entry: action_id → (display_label, category, handler)
#

CATEGORIES = ["transport", "pad", "bank", "twister",
              "navigation", "keys", "volume"]


def _build_registry() -> dict[str, tuple[str, str, Callable]]:
    reg: dict[str, tuple[str, str, Callable]] = {}

    # Transport
    reg["transport.play"]   = ("Play",   "transport", _action_play)
    reg["transport.stop"]   = ("Stop",   "transport", _action_stop)
    reg["transport.record"] = ("Record", "transport", _action_record)

    # Pad triggers 1-16
    for i in range(16):
        reg[f"pad.trigger.{i + 1}"] = (
            f"Pad {i + 1}", "pad", _make_pad_trigger(i))

    # Bank nav
    reg["bank.up"]   = ("Bank +", "bank", _action_bank_up)
    reg["bank.down"] = ("Bank −", "bank", _action_bank_down)
    for i in range(10):
        letter = chr(ord("A") + i)
        reg[f"bank.set.{letter}"] = (
            f"Bank {letter}", "bank", _make_bank_set(i))

    # Twister slots 1-16 turn + press
    for i in range(16):
        reg[f"twister.turn.{i + 1}"] = (
            f"FX Slot {i + 1} turn", "twister", _make_twister_turn(i))
        reg[f"twister.press.{i + 1}"] = (
            f"FX Slot {i + 1} press", "twister", _make_twister_press(i))

    # Keys tab
    reg["chromatic.note"]     = ("Chromatic note", "keys",
                                  _action_chromatic_note)
    reg["keys.octave_up"]     = ("Octave +", "keys", _action_octave_up)
    reg["keys.octave_down"]   = ("Octave −", "keys", _action_octave_down)
    reg["keys.latch_toggle"]  = ("Latch toggle", "keys",
                                  _action_latch_toggle)
    reg["keys.keep_toggle"]   = ("Keep toggle", "keys",
                                  _action_keep_toggle)

    # Navigation
    for screen in ("session", "record", "sample", "radio", "files",
                   "settings", "io", "kit", "controller"):
        reg[f"navigation.screen.{screen}"] = (
            f"Go to {screen.upper()}", "navigation",
            _make_screen_nav(screen))
    reg["navigation.focus_next"] = (
        "Next device", "navigation", _action_focus_next)

    # Volume (placeholders)
    reg["volume.master"] = ("Master vol", "volume", _action_volume_master)

    return reg


_REGISTRY = _build_registry()


def all_actions() -> dict[str, tuple[str, str, Callable]]:
    return _REGISTRY


def action_label(action_id: str) -> str:
    entry = _REGISTRY.get(action_id)
    return entry[0] if entry else action_id


def action_category(action_id: str) -> str:
    entry = _REGISTRY.get(action_id)
    return entry[1] if entry else "unknown"


def actions_by_category(category: str) -> list[str]:
    return [aid for aid, (_, cat, _) in _REGISTRY.items() if cat == category]


def dispatch(action_id: str, value, app):
    """Dispatch an action by id. `value` is the MIDI data value.

    For `chromatic.note`, `value` can be a tuple (note, velocity) or
    a single int (note only, uses velocity 100). The mapper handles
    this encoding.
    """
    entry = _REGISTRY.get(action_id)
    if entry is None:
        log.debug("unknown action: %s", action_id)
        return
    _label, _cat, handler = entry
    try:
        if action_id == "chromatic.note" and isinstance(value, tuple):
            handler(app, value[0], value[1])
        else:
            handler(app, value)
    except Exception as e:
        log.warning("action %s failed: %s", action_id, e)
