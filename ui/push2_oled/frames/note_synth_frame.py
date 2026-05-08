"""OLED frame for Note (synth) mode."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F",
              "F#", "G", "G#", "A", "A#", "B"]


def pitch_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def draw_note_synth_frame(w: int, h: int, control, mode) -> Image.Image:
    img = Image.new("RGB", (w, h), color=(8, 8, 14))
    d = ImageDraw.Draw(img)

    f_big = _font(24)
    f_med = _font(16)
    f_sm = _font(13)

    sess = control.session
    track = sess.tracks[mode.track_idx] if mode.track_idx < len(sess.tracks) else None
    title = track.name if track else "SYNTH"

    d.text((10, 6), f"NOTE · {title}", fill=(220, 220, 230), font=f_big)

    info = (f"{mode.scale}   root {pitch_name(mode.root_note)}"
            f"   layout 4ths   {'In Key' if mode.in_key else 'Chromatic'}")
    d.text((10, 40), info, fill=(180, 200, 255), font=f_med)

    # Held notes
    held = sorted(mode._held_notes.values()) if mode._held_notes else []
    if held:
        held_str = "  ".join(pitch_name(p) for p in held)
        d.text((10, 74), held_str, fill=(255, 255, 255), font=f_med)
    else:
        d.text((10, 74), "(no notes held)", fill=(120, 120, 140), font=f_med)

    # Octave indicator bar
    oct_str = f"oct {mode.root_note // 12 - 1}"
    d.text((w - 100, 40), oct_str, fill=(220, 220, 230), font=f_med)

    # Footer hints
    d.text((10, h - 22),
           "Octave ↑↓ shift octaves   Scale cycles scale   Shift+Octave = step",
           fill=(120, 120, 140), font=f_sm)
    return img
