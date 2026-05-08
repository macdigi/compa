"""OLED frame for Mix mode."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver.palette import track_color_index


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _palette_to_rgb(palette: list, idx: int) -> tuple[int, int, int]:
    r, g, b, _ = palette[idx]
    return r, g, b


def draw_mix_frame(w: int, h: int, control, mode) -> Image.Image:
    img = Image.new("RGB", (w, h), color=(8, 8, 14))
    d = ImageDraw.Draw(img)
    f_big = _font(22)
    f_med = _font(15)
    f_sm = _font(12)

    sess = control.session
    palette = control.surface.palette if control.surface else None

    is_pan = "shift" in control.modifiers
    title = "MIX · PAN" if is_pan else "MIX"
    d.text((10, 6), title, fill=(220, 220, 230), font=f_big)

    cell_w = (w - 20) // 8
    for i in range(8):
        ti = i + mode.track_offset
        if ti >= len(sess.tracks):
            continue
        t = sess.tracks[ti]
        x = 10 + i * cell_w
        cidx = t.color or track_color_index(ti)
        rgb = _palette_to_rgb(palette, cidx) if palette else (180, 180, 180)
        # Track name
        d.text((x + 4, 36), (t.name or f"T{ti+1}")[:8],
               fill=(220, 220, 220), font=f_sm)
        # Fader column
        col_top = 52
        col_bot = h - 24
        col_h = col_bot - col_top
        d.rectangle((x + 4, col_top, x + cell_w - 6, col_bot),
                    fill=(28, 28, 38))
        if is_pan:
            # Pan dot at vertical center, horizontal position by pan
            cy = col_top + col_h // 2
            cx = x + 4 + int((t.pan + 1.0) * 0.5 * (cell_w - 10))
            d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=rgb)
        else:
            level_h = int(col_h * t.volume)
            if level_h > 0:
                d.rectangle((x + 4, col_bot - level_h,
                             x + cell_w - 6, col_bot), fill=rgb)
        # Mute/Solo dots
        mute_color = (255, 60, 60) if t.mute else (40, 40, 50)
        solo_color = (60, 220, 60) if t.solo else (40, 40, 50)
        d.ellipse((x + 4, col_bot + 4, x + 12, col_bot + 12), fill=mute_color)
        d.ellipse((x + 16, col_bot + 4, x + 24, col_bot + 12), fill=solo_color)
        # Numeric
        val = (f"{int(t.pan*100):+d}" if is_pan
               else f"{int(t.volume*100)}%")
        d.text((x + 4, h - 14), val, fill=(180, 180, 200), font=f_sm)

    return img
