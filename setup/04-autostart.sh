#!/bin/bash
# 04-autostart.sh — Systemd service to launch Compa on boot
# Run as root (sudo)
set -e

echo "=== Compa: Autostart Setup ==="

PROJECT_DIR="/home/pi/compa"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"

# Create systemd service
echo ">>> Creating compa.service..."
cat > /etc/systemd/system/compa.service << EOF
[Unit]
Description=Compa — P-6 Companion
After=network-online.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_PYTHON $PROJECT_DIR/ui/p6_app.py
Restart=always
RestartSec=3

# Audio realtime priority
Nice=-10
LimitRTPRIO=95
LimitMEMLOCK=infinity

# Environment for KMSDRM display
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=SDL_FBDEV=/dev/fb0
Environment=SDL_MOUSE_RELATIVE=0
Environment=SDL_INPUT_LINUX_KEEP_KBD=1
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/home/pi

# Allow access to audio, video, and input devices
SupplementaryGroups=audio video input render

[Install]
WantedBy=multi-user.target
EOF

# Create udev rule and privileged helper for USB storage
echo ">>> Setting up Compa USB storage helper..."
install -D -o root -g root -m 0755 \
    "$PROJECT_DIR/setup/compa-storage-mount" \
    /usr/local/sbin/compa-storage-mount
mkdir -p /mnt/compa
chown root:root /mnt/compa
chmod 0755 /mnt/compa
cat > /etc/sudoers.d/020_compa_storage_mount << 'EOF'
pi ALL=(root) NOPASSWD: /usr/local/sbin/compa-storage-mount *
EOF
chmod 0440 /etc/sudoers.d/020_compa_storage_mount
visudo -cf /etc/sudoers.d/020_compa_storage_mount >/dev/null

echo ">>> Setting up Roland USB storage udev tags..."
cat > /etc/udev/rules.d/99-p6-automount.rules << 'EOF'
# Mark Roland USB storage for Compa. Mounting is handled by
# /usr/local/sbin/compa-storage-mount from the app process.
ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="sd[a-z]*", ATTRS{idVendor}=="0582", ENV{ID_COMPA_STORAGE}="1"
EOF

# Reload udev
udevadm control --reload-rules
udevadm trigger

# Enable service
echo ">>> Enabling compa.service..."
systemctl daemon-reload
systemctl enable compa.service

echo ""
echo "=== Compa autostart setup complete! ==="
echo ""
echo "Manual controls:"
echo "  Start:   sudo systemctl start compa"
echo "  Stop:    sudo systemctl stop compa"
echo "  Status:  sudo systemctl status compa"
echo "  Logs:    journalctl -u compa -f"
echo ""
echo ">>> Reboot now? (y/n)"
read -r REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    reboot
fi
