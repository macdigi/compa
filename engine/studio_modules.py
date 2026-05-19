"""Compa Studio module catalog.

Studio is a module hub, not one oversized page.  This catalog keeps the
high-level Studio destinations and their hardware/runtime gates in one place so
the touch UI, Push 2 mode, tests, and docs can agree on the shape of the app.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StudioModule:
    key: str
    label: str
    short_label: str
    tab: str
    category: str
    stage: str
    summary: str
    features: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    capability_keys: tuple[str, ...] = ()
    internal_audio: bool = False
    min_pi_generation: Optional[int] = None

    def stage_label(self) -> str:
        return self.stage.replace("_", " ").title()


STUDIO_MODULES: tuple[StudioModule, ...] = (
    StudioModule(
        key="performer",
        label="AI Performer",
        short_label="PERFORM",
        tab="performer",
        category="external",
        stage="usable",
        summary="Generate and play target-aware parts on external grooveboxes.",
        features=("SP A1-A6 beat+bass", "takes", "loop gestures"),
        next_steps=("make patterns editable", "quantized pattern switching"),
        capability_keys=("external.sp404.a1_a6_beat_bass",),
    ),
    StudioModule(
        key="clips",
        label="Clip Launcher",
        short_label="CLIPS",
        tab="clips",
        category="studio",
        stage="foundation",
        summary="Launch scenes and clips from Compa or Push 2.",
        features=("8x8 clip grid", "scene launch", "audio clips"),
        next_steps=("clip stop controls", "sample browser", "capture flow"),
        capability_keys=("internal.audio_track",),
        internal_audio=True,
        min_pi_generation=4,
    ),
    StudioModule(
        key="sampler",
        label="Compa Sampler",
        short_label="SAMPLER",
        tab="sampler",
        category="instrument",
        stage="planned",
        summary="Internal sample pad rack for Push 2 and touch performance.",
        features=("16 pads", "one-shot/gate", "sample import"),
        next_steps=("pad sample assignment", "voice playback", "choke groups"),
        capability_keys=("internal.sample_drum_rack",),
        internal_audio=True,
        min_pi_generation=4,
    ),
    StudioModule(
        key="drum_synth",
        label="Drum Synth",
        short_label="DRUM",
        tab="drum_synth",
        category="instrument",
        stage="planned",
        summary="808/909-style drum voices hosted inside Compa.",
        features=("kick", "snare", "hats", "toms"),
        next_steps=("voice model", "kit presets", "Push macro mapping"),
        capability_keys=("internal.drum_synth",),
        internal_audio=True,
        min_pi_generation=4,
    ),
    StudioModule(
        key="synth",
        label="Synths",
        short_label="SYNTH",
        tab="synth",
        category="instrument",
        stage="planned",
        summary="Internal mono bass and poly synth lanes.",
        features=("mono bass", "poly chords", "controller macros"),
        next_steps=("mono synth MVP", "poly voice mode", "preset storage"),
        capability_keys=("internal.mono_synth", "internal.poly_synth"),
        internal_audio=True,
        min_pi_generation=4,
    ),
    StudioModule(
        key="mixer",
        label="Mixer / Router",
        short_label="MIX",
        tab="mixer",
        category="routing",
        stage="foundation",
        summary="Make track targets, audio routes, and device bridges explicit.",
        features=("track targets", "audio routes", "network peers"),
        next_steps=("route matrix", "track target editor", "meter bridge"),
        capability_keys=("internal.audio_track", "network.compa_peer"),
        internal_audio=True,
        min_pi_generation=4,
    ),
    StudioModule(
        key="recorder",
        label="Recorder",
        short_label="REC",
        tab="recorder",
        category="capture",
        stage="planned",
        summary="Capture loops, resample Compa, and assist SP pattern recording.",
        features=("audio capture", "resampling", "SP record pass"),
        next_steps=("record arm surface", "take browser", "SP pattern sync"),
        capability_keys=("internal.audio_track", "external.sp404.a1_a6_beat_bass"),
        internal_audio=True,
        min_pi_generation=4,
    ),
)


def known_modules(category: str | None = None) -> tuple[StudioModule, ...]:
    if category is None:
        return STUDIO_MODULES
    return tuple(module for module in STUDIO_MODULES
                 if module.category == category)


def module_for_key(key: str) -> StudioModule | None:
    for module in STUDIO_MODULES:
        if module.key == key:
            return module
    return None


def module_for_tab(tab: str) -> StudioModule | None:
    for module in STUDIO_MODULES:
        if module.tab == tab:
            return module
    return None


def is_module_available(
    module: StudioModule,
    *,
    pi_generation: int | None = None,
    studio_audio_enabled: bool = True,
) -> bool:
    if (module.min_pi_generation is not None
            and pi_generation is not None
            and pi_generation < module.min_pi_generation):
        return False
    if module.internal_audio and not studio_audio_enabled:
        return False
    return True


def module_availability_label(
    module: StudioModule,
    *,
    pi_generation: int | None = None,
    studio_audio_enabled: bool = True,
) -> str:
    if (module.min_pi_generation is not None
            and pi_generation is not None
            and pi_generation < module.min_pi_generation):
        return f"Pi {module.min_pi_generation}+"
    if module.internal_audio and not studio_audio_enabled:
        return "audio gated"
    if module.stage == "usable":
        return "ready"
    return module.stage_label()
