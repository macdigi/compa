# Flashing the SD Card

## Requirements
- MicroSD card (16GB+ recommended, Class 10 or better)
- Raspberry Pi Imager (https://www.raspberrypi.com/software/)

## Steps

1. Download and install **Raspberry Pi Imager** on your Mac/PC.

2. Open Raspberry Pi Imager:
   - **Choose OS:** Raspberry Pi OS (other) -> **Raspberry Pi OS Lite (64-bit)** (Bookworm)
   - **Choose Storage:** Select your SD card

3. Click the **gear icon** (Advanced Options) before writing:
   - Set hostname: `pi-sampler`
   - Enable SSH (use password authentication initially)
   - Set username: `pi`
   - Set password: (your choice)
   - Configure WiFi: enter your network SSID and password
   - Set locale: your timezone

4. Click **Write** and wait for it to finish.

5. Insert SD card into Pi 3B and boot.

6. SSH in: `ssh pi@pi-sampler.local`

7. Clone or copy the pi-sampler project to `/home/pi/pi-sampler/`

8. Run the setup scripts in order:
   ```bash
   cd /home/pi/pi-sampler/setup
   chmod +x *.sh
   sudo ./01-base-setup.sh
   sudo ./02-audio-setup.sh
   ./03-network-mounts.sh
   sudo ./04-autostart.sh
   ```

9. Reboot: `sudo reboot`

The sampler should start automatically on boot.
