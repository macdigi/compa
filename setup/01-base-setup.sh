#!/bin/bash
# 01-base-setup.sh — Base system setup for Pi Sampler
# Run as root (sudo) on a fresh Raspberry Pi OS Lite 64-bit install
set -e

echo "=== Pi Sampler: Base Setup ==="

# Update system
echo ">>> Updating packages..."
apt update && apt upgrade -y

# Install system dependencies
echo ">>> Installing system packages..."
apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-numpy \
    python3-pygame \
    libsdl2-dev \
    libsdl2-mixer-dev \
    libsdl2-image-dev \
    libsdl2-ttf-dev \
    libportaudio2 \
    portaudio19-dev \
    libsndfile1 \
    sshfs \
    git \
    fonts-dejavu-core

# Create project directory
PROJECT_DIR="/home/pi/pi-sampler"
echo ">>> Setting up project directory: $PROJECT_DIR"
mkdir -p "$PROJECT_DIR"
chown pi:pi "$PROJECT_DIR"

# Create Python virtual environment
echo ">>> Creating Python venv..."
sudo -u pi python3 -m venv "$PROJECT_DIR/venv" --system-site-packages

# Install Python packages in venv
echo ">>> Installing Python packages..."
sudo -u pi "$PROJECT_DIR/venv/bin/pip" install --upgrade pip
sudo -u pi "$PROJECT_DIR/venv/bin/pip" install \
    sounddevice \
    soundfile \
    python-rtmidi \
    numpy \
    Pillow

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

# Set hostname
echo ">>> Setting hostname to pi-sampler..."
hostnamectl set-hostname pi-sampler

echo ""
echo "=== Base setup complete! ==="
echo "Next: Run 02-audio-setup.sh"
echo "A reboot is recommended after all setup scripts are done."
