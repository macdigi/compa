#!/bin/bash
# 01-base-setup.sh — Base system setup for Compa
# Run as root (sudo) on a fresh Raspberry Pi OS Lite 64-bit install
set -e

echo "=== Compa: Base Setup ==="

# Update system
echo ">>> Updating packages..."
apt update && apt upgrade -y

# Install system dependencies
echo ">>> Installing system packages..."
apt install -y \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-numpy \
    python3-pygame \
    python3-rtmidi \
    python3-evdev \
    libsdl2-dev \
    libsdl2-mixer-dev \
    libsdl2-image-dev \
    libsdl2-ttf-dev \
    libportaudio2 \
    portaudio19-dev \
    libsndfile1 \
    libasound2-dev \
    libusb-1.0-0 \
    pkg-config \
    exfatprogs \
    sshfs \
    git \
    fonts-dejavu-core

# Create project directory
PROJECT_DIR="/home/pi/compa"
echo ">>> Setting up project directory: $PROJECT_DIR"
mkdir -p "$PROJECT_DIR"
chown pi:pi "$PROJECT_DIR"

# Create Python virtual environment
echo ">>> Creating Python venv..."
sudo -u pi python3 -m venv "$PROJECT_DIR/venv" --system-site-packages

# Install Python packages in venv from the repo's dependency list
echo ">>> Installing Python packages from requirements.txt..."
sudo -u pi "$PROJECT_DIR/venv/bin/pip" install --upgrade pip
sudo -u pi "$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# GPU memory split — give more RAM to CPU for samples
echo ">>> Configuring GPU memory split (64MB)..."
if ! grep -q "gpu_mem=64" /boot/firmware/config.txt; then
    echo "gpu_mem=64" >> /boot/firmware/config.txt
fi

# Enable SSH (should already be enabled from Imager)
systemctl enable ssh

# Disable screen blanking
echo ">>> Disabling screen blanking..."
if ! grep -q "consoleblank=0" /boot/firmware/cmdline.txt; then
    sed -i 's/$/ consoleblank=0/' /boot/firmware/cmdline.txt
fi

# DSI display configuration
echo ">>> Configuring DSI touchscreen..."
# The official 7" DSI display should work out of the box
# If display is upside down, uncomment the next line:
# echo "lcd_rotate=2" >> /boot/firmware/config.txt

# Create sample directories
mkdir -p "$PROJECT_DIR/samples"
mkdir -p "$PROJECT_DIR/kits"
chown -R pi:pi "$PROJECT_DIR"

echo ""
echo "=== Base setup complete! ==="
echo "Next: Run 02-audio-setup.sh"
echo "Hostname unchanged. Set one explicitly if you want, for example:"
echo "  sudo hostnamectl set-hostname compa-pi"
echo "A reboot is recommended after all setup scripts are done."
