#!/bin/bash
# Take a screenshot of the Compa screen on the Pi and open it on your Mac
# Usage: ./screenshot.sh

PI="pi@192.168.4.188"
KEY="/Users/macdigi/.ssh/id_ed25519"
LOCAL="/tmp/compa_screenshot.png"

ssh -i "$KEY" "$PI" "sudo fbgrab /tmp/screen.png 2>/dev/null && cat /tmp/screen.png" > "$LOCAL" 2>/dev/null

if [ -s "$LOCAL" ]; then
    open "$LOCAL"
    echo "Screenshot saved to $LOCAL"
else
    echo "Failed — trying alternative method..."
    ssh -i "$KEY" "$PI" "sudo cat /dev/fb0" | convert -size 1024x600 -depth 16 rgb:- "$LOCAL" 2>/dev/null
    if [ -s "$LOCAL" ]; then
        open "$LOCAL"
    else
        echo "Could not capture screenshot"
    fi
fi
