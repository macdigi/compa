#!/bin/bash
# 03-network-mounts.sh — SSHFS mount to Mac Mini sample library
# Run as regular user (pi), NOT root
set -e

echo "=== Compa: Network Mount Setup ==="

# Load config
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/config.env"

MOUNT_POINT="/mnt/samples"

# Generate SSH key pair if not exists
if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
    echo ">>> Generating SSH key pair..."
    mkdir -p "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$HOME/.ssh/id_ed25519" -N "" -C "compa"
    echo ""
fi

echo "========================================="
echo "Add this public key to your Mac Mini:"
echo ""
echo "  On your Mac, run:"
echo "  echo '$(cat "$HOME/.ssh/id_ed25519.pub")' >> ~/.ssh/authorized_keys"
echo ""
echo "  Or copy-paste the key above into your Mac's"
echo "  System Settings > General > Sharing > Remote Login > authorized keys"
echo ""
echo "  Mac Mini IP: $MAC_MINI_IP"
echo "  Mac Mini User: $MAC_MINI_USER"
echo "  Remote Dir: $REMOTE_SAMPLE_DIR"
echo "========================================="
echo ""
read -p "Press Enter after adding the key to your Mac... "

# Create mount point
echo ">>> Creating mount point: $MOUNT_POINT"
sudo mkdir -p "$MOUNT_POINT"
sudo chown pi:pi "$MOUNT_POINT"

# Test SSH connection
echo ">>> Testing SSH connection to $MAC_MINI_USER@$MAC_MINI_IP..."
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$MAC_MINI_USER@$MAC_MINI_IP" "echo OK" 2>/dev/null; then
    echo "    SSH connection successful!"
else
    echo "    WARNING: SSH connection failed. Check your key and Mac Mini settings."
    echo "    Continuing with systemd mount setup anyway..."
fi

# Create systemd mount unit for SSHFS
echo ">>> Creating systemd automount..."
ESCAPED_MOUNT=$(systemd-escape --path "$MOUNT_POINT")

sudo tee "/etc/systemd/system/${ESCAPED_MOUNT}.mount" > /dev/null << EOF
[Unit]
Description=SSHFS mount to Mac Mini samples
After=network-online.target
Wants=network-online.target

[Mount]
What=${MAC_MINI_USER}@${MAC_MINI_IP}:${REMOTE_SAMPLE_DIR}
Where=${MOUNT_POINT}
Type=fuse.sshfs
Options=_netdev,allow_other,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,IdentityFile=/home/pi/.ssh/id_ed25519,uid=1000,gid=1000

[Install]
WantedBy=multi-user.target
EOF

# Create automount unit for lazy mounting
sudo tee "/etc/systemd/system/${ESCAPED_MOUNT}.automount" > /dev/null << EOF
[Unit]
Description=Automount SSHFS to Mac Mini samples

[Automount]
Where=${MOUNT_POINT}
TimeoutIdleSec=0

[Install]
WantedBy=multi-user.target
EOF

# Enable allow_other for FUSE
if ! grep -q "^user_allow_other" /etc/fuse.conf; then
    echo "user_allow_other" | sudo tee -a /etc/fuse.conf
fi

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable "${ESCAPED_MOUNT}.automount"
sudo systemctl start "${ESCAPED_MOUNT}.automount"

# Test mount
echo ">>> Testing mount..."
if ls "$MOUNT_POINT" > /dev/null 2>&1; then
    echo "    Mount successful! Sample library available at $MOUNT_POINT"
    SAMPLE_COUNT=$(find "$MOUNT_POINT" -maxdepth 3 -name "*.wav" 2>/dev/null | head -20 | wc -l)
    echo "    Found $SAMPLE_COUNT .wav files (checked 3 levels deep)"
else
    echo "    Mount not accessible yet. It will auto-mount when accessed."
    echo "    Check with: ls $MOUNT_POINT"
fi

echo ""
echo "=== Network mount setup complete! ==="
echo "Next: Run sudo ./04-autostart.sh"
