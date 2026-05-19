"""Session = the project: 8 tracks × 8 scenes + global parameters."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .track import Track, TrackType, InstrumentRef
from .scene import Scene
from .clip import Clip, MidiClip, AudioClip, LaunchQuantize


NUM_TRACKS = 8
NUM_SCENES = 8


@dataclass
class Session:
    name: str = "untitled"
    bpm: float = 120.0
    swing: float = 0.0
    time_signature_num: int = 4
    time_signature_den: int = 4
    global_quantize: LaunchQuantize = LaunchQuantize.ONE_BAR
    record_quantize: LaunchQuantize = LaunchQuantize.NONE
    tracks: list[Track] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    master_volume: float = 0.85
    studio_performer_takes: list[dict | None] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "name": self.name,
            "bpm": self.bpm,
            "swing": self.swing,
            "time_signature_num": self.time_signature_num,
            "time_signature_den": self.time_signature_den,
            "global_quantize": self.global_quantize.value,
            "record_quantize": self.record_quantize.value,
            "tracks": [t.to_dict() for t in self.tracks],
            "scenes": [s.to_dict() for s in self.scenes],
            "master_volume": self.master_volume,
            "studio_performer_takes": self.studio_performer_takes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            name=d.get("name", "untitled"),
            bpm=float(d.get("bpm", 120.0)),
            swing=float(d.get("swing", 0.0)),
            time_signature_num=int(d.get("time_signature_num", 4)),
            time_signature_den=int(d.get("time_signature_den", 4)),
            global_quantize=LaunchQuantize(d.get("global_quantize", "1bar")),
            record_quantize=LaunchQuantize(d.get("record_quantize", "none")),
            tracks=[Track.from_dict(t) for t in d.get("tracks", [])],
            scenes=[Scene.from_dict(s) for s in d.get("scenes", [])],
            master_volume=float(d.get("master_volume", 0.85)),
            studio_performer_takes=[
                take if isinstance(take, dict) else None
                for take in d.get("studio_performer_takes", [])
            ],
        )

    def get_clip(self, track_idx: int, scene_idx: int) -> Optional[Clip]:
        if not (0 <= track_idx < len(self.tracks)):
            return None
        track = self.tracks[track_idx]
        if not (0 <= scene_idx < len(track.clips)):
            return None
        return track.clips[scene_idx]

    def set_clip(self, track_idx: int, scene_idx: int,
                 clip: Optional[Clip]) -> None:
        if not (0 <= track_idx < len(self.tracks)):
            return
        track = self.tracks[track_idx]
        while len(track.clips) <= scene_idx:
            track.clips.append(None)
        track.clips[scene_idx] = clip

    @classmethod
    def empty(cls, num_tracks: int = NUM_TRACKS,
              num_scenes: int = NUM_SCENES) -> "Session":
        s = cls()
        s.tracks = []
        s.scenes = [Scene(name=f"Scene {i+1}") for i in range(num_scenes)]
        for i in range(num_tracks):
            s.tracks.append(Track(
                id=i,
                name=f"Track {i+1}",
                type=TrackType.MIDI if i < 4 else TrackType.AUDIO,
                clips=[None] * num_scenes,
            ))
        return s
