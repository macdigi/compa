"""Generate a Push 2 hardware mockup PNG for video overlay compositing.

Drop the captured push2_*.mp4 (960x160 LCD content) into the LCD
region of this background. Result: a video that looks like the
real Push 2 hardware running its actual rendered content.

Output: tools/push2_mockup.png  (1500x900, black body, 960x160 LCD
area at known coordinates so the ffmpeg overlay filter can place
the video stream there).

Replace this PNG with a real Push 2 product photo later — keep the
LCD coordinates the same and the overlay still works.
"""

from PIL import Image, ImageDraw

W, H = 1500, 900
LCD_W, LCD_H = 960, 160
LCD_X = (W - LCD_W) // 2          # centered horizontally
LCD_Y = 60                         # near the top, leaves room for "logo" lip
PAD_GRID_X = (W - 800) // 2
PAD_GRID_Y = 280
PAD_SIZE = 90
PAD_GAP = 10
ENCODER_Y = LCD_Y + LCD_H + 30
ENCODER_RADIUS = 14

img = Image.new("RGB", (W, H), (8, 8, 12))
d = ImageDraw.Draw(img)

# ── Body — rounded charcoal rectangle ───────────────────────────
body = (40, 40, W - 40, H - 40)
d.rounded_rectangle(body, radius=24, fill=(28, 28, 34), outline=(60, 60, 68), width=2)

# ── LCD region — black rectangle, this is where the video lands ─
# The LCD bezel is slightly larger than the LCD itself
bezel = (LCD_X - 14, LCD_Y - 14, LCD_X + LCD_W + 14, LCD_Y + LCD_H + 14)
d.rounded_rectangle(bezel, radius=8, fill=(8, 8, 10), outline=(50, 50, 56), width=2)
# Inner LCD area
d.rectangle((LCD_X, LCD_Y, LCD_X + LCD_W, LCD_Y + LCD_H), fill=(0, 0, 0))

# ── 16 encoders above the pad grid (just above pads) ────────────
encoder_y = ENCODER_Y
for i in range(16):
    cx = PAD_GRID_X + (i % 8) * (PAD_SIZE + PAD_GAP) + PAD_SIZE // 2
    cy = encoder_y + (i // 8) * 40
    d.ellipse((cx - ENCODER_RADIUS, cy - ENCODER_RADIUS,
               cx + ENCODER_RADIUS, cy + ENCODER_RADIUS),
              fill=(60, 60, 68), outline=(90, 90, 98), width=2)

# ── 8x8 pad grid ───────────────────────────────────────────────
for r in range(8):
    for c in range(8):
        x = PAD_GRID_X + c * (PAD_SIZE + PAD_GAP)
        y = PAD_GRID_Y + r * (PAD_SIZE + PAD_GAP)
        d.rounded_rectangle((x, y, x + PAD_SIZE, y + PAD_SIZE),
                            radius=8, fill=(50, 50, 56),
                            outline=(85, 85, 93), width=2)

# ── Touch strip on the left side ────────────────────────────────
ts_x = PAD_GRID_X - 50
d.rounded_rectangle((ts_x - 16, PAD_GRID_Y, ts_x + 8, PAD_GRID_Y + 8 * (PAD_SIZE + PAD_GAP) - PAD_GAP),
                    radius=6, fill=(40, 40, 46), outline=(70, 70, 78), width=2)

# ── Brand pill bottom-right ─────────────────────────────────────
brand_x = W - 200
brand_y = H - 100
d.text((brand_x, brand_y), "PUSH 2", fill=(140, 140, 150))

img.save("/Users/macdigi/Movies/compa/tools/push2_mockup.png")
print(f"saved push2_mockup.png ({W}x{H}, LCD at {LCD_X},{LCD_Y} {LCD_W}x{LCD_H})")
