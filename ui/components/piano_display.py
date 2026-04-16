"""Visual piano keyboard with active-note highlighting.

Renders a 2-octave (configurable) piano with:
  - White/black keys at correct relative sizes
  - Velocity-colored active key glow with per-frame decay
  - Note name labels (always on C notes, on any active key)
  - Touch-to-play support (tap a key, get back the MIDI note)
  - Octave shift (scroll the visible range up/down)

Layout follows the PadGrid pattern: __init__(rect), draw(surface),
handle_event(event), set_active(note, vel), decay_active().
"""

import pygame
from .. import theme

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Black-key semitones within an octave (C#, D#, F#, G#, A#)
_BLACK_SEMITONES = {1, 3, 6, 8, 10}


def note_name(midi_note: int) -> str:
    """Return 'C4', 'F#2', etc. MIDI note 60 = C4."""
    octave = (midi_note // 12) - 1
    name = NOTE_NAMES[midi_note % 12]
    return f"{name}{octave}"


def is_black_key(midi_note: int) -> bool:
    return (midi_note % 12) in _BLACK_SEMITONES


class PianoDisplay:
    """Touchable visual piano for the KEYS tab."""

    # Colors
    WHITE_KEY_OFF = (210, 210, 218)
    BLACK_KEY_OFF = (20, 20, 28)
    WHITE_KEY_BORDER = (120, 120, 135)

    def __init__(self, rect: pygame.Rect, octaves: int = 2,
                 start_octave: int = 3):
        self.rect = rect
        self._octaves = max(1, min(5, octaves))
        self._start_octave = start_octave

        # State
        self._active_notes: dict[int, int] = {}  # note → velocity (0-127)
        self._key_rects: dict[int, pygame.Rect] = {}
        self._white_keys: list[int] = []
        self._black_keys: list[int] = []

        self._recalculate()

    # ── Layout ───────────────────────────────────────────────────────

    def set_rect(self, rect: pygame.Rect):
        self.rect = rect
        self._recalculate()

    def set_range(self, start_octave: int, octaves: int):
        self._start_octave = start_octave
        self._octaves = max(1, min(5, octaves))
        self._recalculate()

    def shift_octave(self, delta: int):
        new = self._start_octave + delta
        if 0 <= new <= 7:
            self._start_octave = new
            self._recalculate()

    def _recalculate(self):
        """Rebuild key rects from current range."""
        self._key_rects.clear()
        self._white_keys.clear()
        self._black_keys.clear()

        # MIDI note range
        lo = (self._start_octave + 1) * 12  # C of start_octave
        hi = lo + self._octaves * 12 - 1

        # Separate white and black
        for n in range(lo, hi + 1):
            if is_black_key(n):
                self._black_keys.append(n)
            else:
                self._white_keys.append(n)

        n_white = len(self._white_keys)
        if n_white == 0:
            return

        # White keys fill the full rect width
        white_w = max(20, self.rect.width // n_white)
        white_h = self.rect.height
        black_w = max(14, int(white_w * 0.58))
        black_h = max(40, int(white_h * 0.60))

        # Build white key rects
        for i, note in enumerate(self._white_keys):
            x = self.rect.x + i * white_w
            self._key_rects[note] = pygame.Rect(x, self.rect.y,
                                                 white_w - 1, white_h)

        # Build black key rects (overlap between white keys)
        for note in self._black_keys:
            # Black key sits to the right of its preceding white key
            prev_white = note - 1
            while prev_white >= lo and is_black_key(prev_white):
                prev_white -= 1
            if prev_white in self._key_rects:
                wr = self._key_rects[prev_white]
                x = wr.right - black_w // 2
                self._key_rects[note] = pygame.Rect(x, self.rect.y,
                                                     black_w, black_h)

    # ── Active note management ───────────────────────────────────────

    def set_active(self, note: int, velocity: int):
        self._active_notes[note] = velocity

    def clear_active(self, note: int):
        self._active_notes.pop(note, None)

    def clear_all(self):
        self._active_notes.clear()

    def decay_active(self):
        """Fade out active notes. Call each frame (30fps)."""
        to_remove = []
        for note in list(self._active_notes):
            self._active_notes[note] = int(self._active_notes[note] * 0.90)
            if self._active_notes[note] < 4:
                to_remove.append(note)
        for note in to_remove:
            del self._active_notes[note]

    # ── Drawing ──────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface):
        f_tiny = theme.font("tiny")

        # White keys first (bottom layer)
        for note in self._white_keys:
            rect = self._key_rects.get(note)
            if rect is None:
                continue
            if note in self._active_notes:
                vel = self._active_notes[note] / 127.0
                color = theme.velocity_color(vel)
            else:
                color = self.WHITE_KEY_OFF

            pygame.draw.rect(surface, color, rect, border_radius=3)
            pygame.draw.rect(surface, self.WHITE_KEY_BORDER, rect, 1,
                             border_radius=3)

            # Label: always on C notes, also on any active key
            show_label = (note in self._active_notes) or (note % 12 == 0)
            if show_label:
                name = note_name(note)
                tc = theme.BG if note in self._active_notes else theme.TEXT_DIM
                lbl = f_tiny.render(name, True, tc)
                surface.blit(lbl, lbl.get_rect(
                    centerx=rect.centerx,
                    bottom=rect.bottom - 4))

        # Black keys on top (drawn second so they overlap)
        for note in self._black_keys:
            rect = self._key_rects.get(note)
            if rect is None:
                continue
            if note in self._active_notes:
                vel = self._active_notes[note] / 127.0
                color = theme.velocity_color(vel)
            else:
                color = self.BLACK_KEY_OFF

            pygame.draw.rect(surface, color, rect, border_radius=3)
            pygame.draw.rect(surface, theme.BORDER, rect, 1,
                             border_radius=3)

            # Label on active black keys
            if note in self._active_notes:
                name = note_name(note)
                lbl = f_tiny.render(name, True, theme.TEXT_BRIGHT)
                surface.blit(lbl, lbl.get_rect(
                    centerx=rect.centerx,
                    centery=rect.centery))

    # ── Touch interaction ────────────────────────────────────────────

    def handle_event_at(self, mx: int, my: int) -> int:
        """Check if (mx, my) hits a key. Returns MIDI note or -1.

        Checks black keys first since they visually overlap white keys.
        """
        # Black keys first (they're on top)
        for note in self._black_keys:
            rect = self._key_rects.get(note)
            if rect and rect.collidepoint(mx, my):
                return note
        # Then white keys
        for note in self._white_keys:
            rect = self._key_rects.get(note)
            if rect and rect.collidepoint(mx, my):
                return note
        return -1
