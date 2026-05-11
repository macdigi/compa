"""Scene = one row of the clip grid + a follow action.

FollowAction lives in clip.py (used by both clips and scenes).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .clip import FollowAction, FollowActionType  # noqa: F401 — re-export


@dataclass
class Scene:
    name: str = ""
    color: int = 0
    follow_action: FollowAction = field(default_factory=FollowAction)

    def to_dict(self) -> dict:
        return {"name": self.name, "color": self.color,
                "follow_action": self.follow_action.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        return cls(
            name=d.get("name", ""),
            color=int(d.get("color", 0)),
            follow_action=FollowAction.from_dict(d.get("follow_action", {})),
        )
