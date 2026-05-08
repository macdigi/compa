"""Rhythm Generator — Live 12.4-style Euclidean MIDI tool.

Outputs a deterministic Euclidean pattern as MidiClip-compatible
notes when asked. Used to populate clip slots with generated content
and as a live-running track instrument later.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EuclideanParams:
    length: int = 16        # 1–32 step cycle
    density: int = 4        # number of hits within the cycle
    variation: int = 0      # rotational offset of geometric distribution
    shift: int = 0          # additional rotational offset
    pitch: int = 36         # MIDI note to emit
    velocity: int = 100


def euclidean_pattern(length: int, density: int, variation: int = 0,
                      shift: int = 0) -> list[bool]:
    """Bjorklund algorithm — distribute `density` hits across `length` steps.

    `variation` adds a per-step pseudo-random rotation (deterministic)
    to break rigid Euclidean spacing; `shift` is a hard rotation.
    """
    if length <= 0:
        return []
    density = max(0, min(density, length))
    if density == 0:
        return [False] * length
    if density == length:
        return [True] * length

    # Bjorklund
    counts = [0] * (length - density)
    remainders = [1] * density
    rotations = [False] * length
    pattern: list[list[bool]] = [[True]] * density + [[False]] * (length - density)

    while True:
        # Find smallest count
        min_block = pattern[-1]
        merged = []
        i = 0
        # Pair from front and back, merging
        merged_count = 0
        while i < len(pattern) // 2:
            # combine pattern[i] + pattern[-(i+1)] into pattern[i]
            new_block = pattern[i] + pattern[-(i + 1)]
            merged.append(new_block)
            i += 1
            merged_count += 1
        # Remaining unpaired blocks (middle if odd)
        if len(pattern) % 2 == 1:
            merged.append(pattern[len(pattern) // 2])
        # Append remaining tail blocks (the ones we didn't pair)
        tail_start = merged_count
        tail_end = len(pattern) - merged_count
        merged.extend(pattern[tail_start:tail_end])
        if len(merged) == len(pattern):
            break
        pattern = merged
        if all(p == pattern[0] for p in pattern):
            break

    flat: list[bool] = []
    for block in pattern:
        flat.extend(block)
    flat = flat[:length]
    while len(flat) < length:
        flat.append(False)

    # Apply variation as deterministic small rotation per group of 4
    if variation:
        rot = (variation * 3) % length
        flat = flat[rot:] + flat[:rot]

    # Hard shift
    if shift:
        s = shift % length
        flat = flat[-s:] + flat[:-s]

    return flat


def generate_notes(params: EuclideanParams,
                   step_beats: float = 0.25) -> list[tuple[int, float, float, int]]:
    """Return a list of (pitch, start_beat, dur_beats, velocity) notes."""
    pat = euclidean_pattern(params.length, params.density,
                            params.variation, params.shift)
    notes = []
    for step, hit in enumerate(pat):
        if hit:
            notes.append((
                params.pitch,
                step * step_beats,
                step_beats * 0.9,
                params.velocity,
            ))
    return notes
