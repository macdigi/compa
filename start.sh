#!/bin/bash
# Compa startup script — launches the app with the right display driver.
# Waits briefly for USB audio devices to enumerate, then starts pygame.

cd "$(dirname "$0")"

# Wait for USB audio devices to enumerate before starting (avoids
# "No suitable audio input device found" on cold boot).
sleep 3

# Video driver — KMSDRM for HDMI/DSI displays
export SDL_VIDEODRIVER=kmsdrm
export SDL_MOUSE_RELATIVE=0
export SDL_INPUT_LINUX_KEEP_KBD=1
export PYTHONUNBUFFERED=1
export HOME=/home/pi

exec ./venv/bin/python ui/p6_app.py
