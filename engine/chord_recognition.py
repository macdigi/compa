"""Chord-name recognition from a set of MIDI notes.

Given a set of held MIDI notes, identify whether they form a known
chord and return a short readable label (``"Cmaj7"``, ``"F#m"``,
``"G7/B"`` for slash chords, etc.). The renderers (touchscreen
perform view, Push 2 LCD header) use this to surface what the
producer is actually playing in music-theory terms.

The match is **strict** — we only return a label when the held
pitch-class set is an exact match for a known chord pattern. That
avoids false positives like labeling two random notes as a triad.
Single notes and intervals (2 unique pcs) return ``None`` so the
caller can fall back to plain note-name rendering.

Patterns are stored as sorted interval tuples in semitones from
the root, already mod 12. To recognize a chord we try each
pitch-class in the set as the candidate root, compute the
intervals, and match against ``CHORD_PATTERNS``. If multiple
candidates match we prefer the one whose root is the actual bass
note (root position), then earliest in the sorted pc set. If the
root differs from the bass note we append ``/<bass-name>`` for
slash-chord notation.
"""

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F",
              "F#", "G", "G#", "A", "A#", "B")

# Interval tuple → quality suffix. Suffixes are short on purpose so
# they fit on the small Push 2 LCD header. Empty string means a
# plain major triad ("C" rather than "Cmaj").
CHORD_PATTERNS: dict[tuple[int, ...], str] = {
    # ── Triads ───────────────────────────────────────────────────
    (0, 4, 7):    "",       # major
    (0, 3, 7):    "m",      # minor
    (0, 3, 6):    "dim",    # diminished
    (0, 4, 8):    "aug",    # augmented
    (0, 2, 7):    "sus2",   # suspended 2nd
    (0, 5, 7):    "sus4",   # suspended 4th

    # ── 7th chords ───────────────────────────────────────────────
    (0, 4, 7, 11): "maj7",
    (0, 3, 7, 10): "m7",
    (0, 4, 7, 10): "7",
    (0, 3, 6, 10): "m7b5",
    (0, 3, 6, 9):  "dim7",
    (0, 3, 7, 11): "mMaj7",
    (0, 4, 8, 10): "aug7",
    (0, 4, 8, 11): "augMaj7",

    # ── 6th chords ───────────────────────────────────────────────
    (0, 4, 7, 9):  "6",
    (0, 3, 7, 9):  "m6",

    # ── Add chords ───────────────────────────────────────────────
    (0, 2, 4, 7):  "add9",
    (0, 2, 3, 7):  "madd9",
    (0, 4, 7, 14 % 12): "add9",   # same as above (14 % 12 = 2)

    # ── 9 chords ─────────────────────────────────────────────────
    (0, 2, 4, 7, 10): "9",
    (0, 2, 4, 7, 11): "maj9",
    (0, 2, 3, 7, 10): "m9",
    (0, 2, 4, 7, 9):  "6/9",

    # ── 11 chords ────────────────────────────────────────────────
    (0, 2, 4, 5, 7, 10): "11",
    (0, 2, 4, 5, 7, 11): "maj11",
    (0, 2, 3, 5, 7, 10): "m11",
}


def recognize_chord(notes) -> str | None:
    """Return a chord label for ``notes`` or ``None``.

    ``notes`` is any iterable of MIDI note numbers (0..127).
    Duplicate notes / octave duplicates are folded down to the
    pitch-class set before matching. The lowest MIDI note (the
    actual bass) is used to decide whether the chord is in root
    position or an inversion.
    """
    note_list = [n for n in notes if 0 <= n <= 127]
    if len(note_list) < 3:
        return None

    pcs_set = {n % 12 for n in note_list}
    if len(pcs_set) < 3:
        # Two different pitch classes spread across octaves — that's
        # an interval, not a chord. Caller renders note names.
        return None
    pcs = sorted(pcs_set)
    bass_pc = min(note_list) % 12

    # Try each pc as the candidate root. Score so root-position
    # matches win over inversions, and earlier roots win over later
    # ones to keep the result deterministic.
    candidates: list[tuple[tuple[int, int], str]] = []
    for root in pcs:
        intervals = tuple(sorted((pc - root) % 12 for pc in pcs))
        suffix = CHORD_PATTERNS.get(intervals)
        if suffix is None:
            continue
        label = f"{NOTE_NAMES[root]}{suffix}"
        if bass_pc != root:
            label += f"/{NOTE_NAMES[bass_pc]}"
        # Lower score wins. Prefer root-position (bass==root), then
        # the earliest root in the pc-sorted list.
        score = (0 if bass_pc == root else 1, pcs.index(root))
        candidates.append((score, label))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]
