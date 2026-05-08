"""OLED frame for Session mode."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from engine.push2driver import constants as C
from engine.push2driver.palette import track_color_index


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _palette_to_rgb(palette: list, idx: int) -> tuple[int, int, int]:
    r, g, b, _ = palette[idx]
    return r, g, b


def draw_session_frame(w: int, h: int, control, mode) -> Image.Image:
    img = Image.new("RGB", (w, h), color=(8, 8, 14))
    d = ImageDraw.Draw(img)

    sess = control.session
    palette = control.surface.palette if control.surface else None

    # Top bar — BPM, transport, scene number, peer count
    f_big = _font(22)
    f_med = _font(16)
    f_sm = _font(12)
    d.text((10, 6), "SESSION", fill=(220, 220, 230), font=f_big)
    bpm_str = f"{sess.bpm:.1f} BPM"
    d.text((180, 8), bpm_str, fill=(255, 255, 255), font=f_med)
    sel_t = control.selected_track if control.selected_track is not None else 0
    sel_s = control.selected_scene if control.selected_scene is not None else 0
    d.text((350, 8), f"T{sel_t+1}/S{sel_s+1}", fill=(180, 200, 255), font=f_med)

    # 8 track headers, evenly spaced
    track_w = (w - 40) // 8
    for i in range(8):
        ti = i + mode.track_offset
        if ti >= len(sess.tracks):
            continue
        track = sess.tracks[ti]
        x = 20 + i * track_w
        # Color swatch
        color_idx = track.color or track_color_index(ti)
        if palette is not None:
            color = _palette_to_rgb(palette, color_idx)
        else:
            color = (180, 180, 180)
        d.rectangle((x + 4, 38, x + track_w - 4, 50), fill=color)
        # Name
        name = track.name or f"T{ti+1}"
        d.text((x + 4, 54), name[:10], fill=(220, 220, 220), font=f_sm)

        # Volume bar
        vol = float(track.volume)
        bar_h = int(80 * vol)
        bar_y = 70
        d.rectangle((x + 4, bar_y, x + 12, bar_y + 80), fill=(40, 40, 50))
        if bar_h > 0:
            d.rectangle((x + 4, bar_y + 80 - bar_h, x + 12, bar_y + 80),
                        fill=color)

        # Mini-clip column (8 cells)
        sched = control.engine.scheduler
        cell_h = 8
        for s in range(8):
            sy = 70 + s * (cell_h + 2)
            if sy + cell_h > h - 6:
                break
            scene_idx = s + mode.scene_offset
            if scene_idx >= len(sess.scenes):
                continue
            from session.clip import ClipState
            state = sched.get_state(ti, scene_idx)
            clip = sess.get_clip(ti, scene_idx)
            cell_x1 = x + 18
            cell_x2 = x + track_w - 6
            if clip is None:
                d.rectangle((cell_x1, sy, cell_x2, sy + cell_h),
                            outline=(40, 40, 50))
            else:
                cc = (clip.color if clip.color
                      else track.color or track_color_index(ti))
                if palette is not None:
                    rgb = _palette_to_rgb(palette, cc)
                else:
                    rgb = (200, 200, 200)
                if state == ClipState.PLAYING:
                    fill = rgb
                elif state == ClipState.QUEUED:
                    fill = (180, 200, 255)
                else:
                    fill = tuple(c // 3 for c in rgb)
                d.rectangle((cell_x1, sy, cell_x2, sy + cell_h), fill=fill)

    return img
