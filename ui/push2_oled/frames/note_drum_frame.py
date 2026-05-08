"""OLED frame for Note (drum) mode."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from session.clip import MidiClip


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


def draw_note_drum_frame(w: int, h: int, control, mode) -> Image.Image:
    img = Image.new("RGB", (w, h), color=(8, 8, 14))
    d = ImageDraw.Draw(img)
    f_big = _font(22)
    f_med = _font(15)
    f_sm = _font(12)

    sess = control.session
    track = sess.tracks[mode.track_idx] if mode.track_idx < len(sess.tracks) else None
    title = track.name if track else "DRUMS"
    d.text((10, 6), f"NOTE · {title}", fill=(220, 220, 230), font=f_big)
    d.text((400, 12),
           f"step {(mode.page * 16) + 1}–{(mode.page * 16) + 16}",
           fill=(180, 200, 255), font=f_med)

    # Drum names row
    inst = control.engine._instruments[mode.track_idx] if mode.track_idx < len(control.engine._instruments) else None
    cell_w = (w - 20) // 16
    for i in range(16):
        x = 10 + i * cell_w
        if i == mode.selected_pad:
            d.rectangle((x, 38, x + cell_w - 2, 90),
                        fill=(60, 60, 90), outline=(255, 255, 255))
        else:
            d.rectangle((x, 38, x + cell_w - 2, 90),
                        outline=(60, 60, 80))
        if inst is not None and hasattr(inst, "pads") and i < len(inst.pads):
            name = inst.pads[i].name or f"P{i+1}"
        else:
            name = f"P{i+1}"
        d.text((x + 4, 44), name[:6], fill=(220, 220, 220), font=f_sm)

    # 16-step preview row
    scene = control.selected_scene or 0
    clip = sess.get_clip(mode.track_idx, scene)
    pitch = mode.selected_drum_pitch
    playhead_step = control.playhead_step_for_clip(
        mode.track_idx, scene, mode.step_resolution_beats)
    for step_in_page in range(16):
        step = mode.page * 16 + step_in_page
        x = 10 + step_in_page * cell_w
        active = False
        if isinstance(clip, MidiClip):
            beat = step * mode.step_resolution_beats
            for n in clip.notes:
                if (abs(n.start_beat - beat) < 1e-3 and n.pitch == pitch):
                    active = True
                    break
        bar_y = 110
        if step == playhead_step:
            d.rectangle((x, bar_y, x + cell_w - 2, bar_y + 30),
                        fill=(255, 255, 255))
        elif active:
            d.rectangle((x, bar_y, x + cell_w - 2, bar_y + 30),
                        fill=(120, 200, 255))
        else:
            d.rectangle((x, bar_y, x + cell_w - 2, bar_y + 30),
                        outline=(50, 50, 70))

    return img
