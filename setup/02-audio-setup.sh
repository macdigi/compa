#!/bin/bash
# 02-audio-setup.sh — Audio configuration for Pi Sampler
# Run as root (sudo)
set -e

echo "=== Pi Sampler: Audio Setup ==="

# Load config
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/config.env"

# Set USB audio as default ALSA device
echo ">>> Configuring ALSA for USB audio..."
cat > /etc/asound.conf << 'EOF'
# Pi Sampler ALSA config — prioritize USB audio interface
pcm.!default {
    type hw
    card 1
    device 0
}

ctl.!default {
    type hw
    card 1
}

# Fallback to onboard if USB not present
pcm.onboard {
    type hw
    card 0
    device 0
}
EOF

# Note: USB audio is typically card 1 on Pi.
# If your interface shows up as a different card number, adjust above.
# Check with: aplay -l

# Set realtime scheduling priority for audio
echo ">>> Configuring realtime audio priority..."
cat > /etc/security/limits.d/audio.conf << 'EOF'
@audio   -  rtprio     95
@audio   -  memlock    unlimited
@audio   -  nice       -19
EOF

# Add pi user to audio group
echo ">>> Adding pi to audio group..."
usermod -a -G audio pi

# Disable onboard audio (saves resources, avoids conflicts)
echo ">>> Disabling onboard audio..."
if ! grep -q "dtparam=audio=off" /boot/firmware/config.txt; then
    sed -i 's/dtparam=audio=on/dtparam=audio=off/' /boot/firmware/config.txt
fi

# Test audio (will fail if no USB interface connected — that's OK)
echo ""
echo ">>> Audio device list:"
aplay -l 2>/dev/null || echo "(No playback devices found — connect USB audio interface)"

echo ""
echo "=== Audio setup complete! ==="
echo "Connect your USB audio interface and run: aplay -l"
echo "Verify it appears as card 1. If not, edit /etc/asound.conf."
echo "Next: Run 03-network-mounts.sh"
