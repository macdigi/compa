"""Scene = one row of the clip grid + a follow action."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FollowActionType(Enum):
    NONE = "none"
    STOP = "stop"
    NEXT = "next"
    PREVIOUS = "previous"
    FIRST = "first"
    LAST = "last"
    ANY = "any"
    JUMP = "jump"


@dataclass
class FollowAction:
    type: FollowActionType = FollowActionType.NONE
    target_scene: int = -1   # for JUMP
    chance: float = 1.0      # 0–1

    def to_dict(self) -> dict:
        return {"type": self.type.value, "target_scene": self.target_scene,
                "chance": self.chance}

    @classmethod
    def from_dict(cls, d: dict) -> "FollowAction":
        return cls(type=FollowActionType(d.get("type", "none")),
                   target_scene=int(d.get("target_scene", -1)),
                   chance=float(d.get("chance", 1.0)))


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
