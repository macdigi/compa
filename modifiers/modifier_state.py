"""Modifier state — tracks which modifier buttons are held + locked.

Push 2's modifier model: a button can be tapped (latches a state) or
held (momentary). Some have lock-via-Shift (Mute, Solo, Stop Clip).
"""
from __future__ import annotations

from dataclasses import dataclass, field


MODIFIER_NAMES = frozenset({
    "shift", "select", "delete", "duplicate", "quantize",
    "double_loop", "fixed_length", "mute", "solo", "stop_clip",
    "repeat", "accent", "new",
})


@dataclass
class ModifierState:
    held: set[str] = field(default_factory=set)
    locked: set[str] = field(default_factory=set)

    def press(self, name: str) -> None:
        if name in MODIFIER_NAMES:
            self.held.add(name)

    def release(self, name: str) -> None:
        if name in MODIFIER_NAMES:
            self.held.discard(name)

    def lock(self, name: str) -> None:
        if name in MODIFIER_NAMES:
            self.locked.add(name)

    def unlock(self, name: str) -> None:
        self.locked.discard(name)

    def toggle_lock(self, name: str) -> None:
        if name in self.locked:
            self.locked.discard(name)
        else:
            self.locked.add(name)

    def is_active(self, name: str) -> bool:
        return name in self.held or name in self.locked

    def __contains__(self, name: str) -> bool:
        return self.is_active(name)
