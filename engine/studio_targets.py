"""Studio target capability catalog.

Studio tracks can point at internal sound engines, external grooveboxes, or
network peers.  This module keeps the lightweight capability metadata in one
place so UI and planner code can make the same gating decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from session.track import Track, TrackTarget, default_target_for_track


@dataclass(frozen=True)
class TargetCapability:
    key: str
    label: str
    category: str
    device: str = ""
    pads: int = 0
    chromatic: bool = False
    audio_input: bool = False
    audio_output: bool = False
    fx_cc: bool = False
    internal_audio: bool = False
    min_pi_generation: Optional[int] = None
    notes: str = ""

    def feature_labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.pads:
            labels.append(f"{self.pads} pads")
        if self.chromatic:
            labels.append("chromatic")
        if self.audio_input:
            labels.append("audio in")
        if self.audio_output:
            labels.append("audio out")
        if self.fx_cc:
            labels.append("FX CC")
        if self.internal_audio:
            labels.append("internal audio")
        if not labels:
            labels.append("MIDI")
        return tuple(labels)


TARGET_CAPABILITIES: dict[str, TargetCapability] = {
    "internal.sample_drum_rack": TargetCapability(
        key="internal.sample_drum_rack",
        label="Sample Drum Rack",
        category="internal",
        pads=16,
        internal_audio=True,
        min_pi_generation=4,
        notes="Push-style sample pads hosted inside Compa Studio.",
    ),
    "internal.drum_synth": TargetCapability(
        key="internal.drum_synth",
        label="Drum Synth",
        category="internal",
        pads=16,
        internal_audio=True,
        min_pi_generation=4,
        notes="808/909-style internal drum voices.",
    ),
    "internal.mono_synth": TargetCapability(
        key="internal.mono_synth",
        label="Mono Synth",
        category="internal",
        chromatic=True,
        internal_audio=True,
        min_pi_generation=4,
        notes="Internal bass and lead lane for one-note-at-a-time parts.",
    ),
    "internal.poly_synth": TargetCapability(
        key="internal.poly_synth",
        label="Poly Synth",
        category="internal",
        chromatic=True,
        internal_audio=True,
        min_pi_generation=4,
        notes="Internal chord, pad, and poly lead lane.",
    ),
    "internal.audio_track": TargetCapability(
        key="internal.audio_track",
        label="Audio Track",
        category="internal",
        audio_input=True,
        audio_output=True,
        internal_audio=True,
        min_pi_generation=4,
        notes="Clip playback, capture, and routing hosted by Compa.",
    ),
    "internal.midi": TargetCapability(
        key="internal.midi",
        label="MIDI Track",
        category="internal",
        notes="Generic MIDI track without a dedicated sound engine.",
    ),
    "external.sp404.a1_a6_beat_bass": TargetCapability(
        key="external.sp404.a1_a6_beat_bass",
        label="SP-404 A1-A6 Beat+Bass",
        category="external",
        device="SP-404MKII",
        pads=6,
        chromatic=True,
        fx_cc=True,
        notes="Confirmed Bank A drum pads plus A6 chromatic bass workflow.",
    ),
    "external.sp404.pad_bank": TargetCapability(
        key="external.sp404.pad_bank",
        label="SP-404 Pad Bank",
        category="external",
        device="SP-404MKII",
        pads=16,
        fx_cc=True,
        notes="Pad triggering and SP-specific performance controls.",
    ),
    "external.p6.pads": TargetCapability(
        key="external.p6.pads",
        label="P-6 Pads",
        category="external",
        device="P-6",
        pads=6,
        chromatic=True,
        fx_cc=True,
        notes="P-6 pad and granular performance target.",
    ),
    "network.compa_peer": TargetCapability(
        key="network.compa_peer",
        label="Compa Peer",
        category="network",
        audio_input=True,
        audio_output=True,
        notes="Network MIDI/audio target for another Compa.",
    ),
}


def known_targets(category: str | None = None) -> tuple[TargetCapability, ...]:
    targets = tuple(TARGET_CAPABILITIES.values())
    if category is None:
        return targets
    return tuple(t for t in targets if t.category == category)


def target_for_track(track: Track) -> TrackTarget:
    return track.target or default_target_for_track(track.type, track.instrument)


def capability_for(target: TrackTarget | str) -> TargetCapability:
    if isinstance(target, TrackTarget):
        key = target.key
        label = target.label
    else:
        key = target
        label = ""
    capability = TARGET_CAPABILITIES.get(key)
    if capability is not None:
        return capability
    return TargetCapability(
        key=key,
        label=label or key,
        category="unknown",
        notes="No capability profile has been defined for this target yet.",
    )


def is_available(
    capability: TargetCapability,
    *,
    pi_generation: int | None = None,
    studio_audio_enabled: bool = True,
) -> bool:
    if capability.internal_audio and not studio_audio_enabled:
        return False
    if (capability.min_pi_generation is not None
            and pi_generation is not None
            and pi_generation < capability.min_pi_generation):
        return False
    return True


def availability_label(
    capability: TargetCapability,
    *,
    pi_generation: int | None = None,
    studio_audio_enabled: bool = True,
) -> str:
    if capability.internal_audio and not studio_audio_enabled:
        return "audio gated"
    if (capability.min_pi_generation is not None
            and pi_generation is not None
            and pi_generation < capability.min_pi_generation):
        return f"Pi {capability.min_pi_generation}+"
    return "ready"
