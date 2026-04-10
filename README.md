# Compa

**Universal companion device for USB music gear on Raspberry Pi.**

Compa turns a Raspberry Pi + touchscreen into a powerful companion for your hardware samplers and grooveboxes. Auto-detects your device and adapts its features. Record, sample, slice, stream radio, control parameters, chain patterns, and manage backups — all from a touchscreen or mouse.

## Supported Devices

| Device | Audio | MIDI Control | Backup/Restore | Sample Transfer |
|--------|-------|-------------|----------------|-----------------|
| **Roland P-6** | 2in/2out 44.1kHz | Granular, Filter, Envelope, Mixer, FX (40 CCs) | Pattern + Sample backup | Slicer → P-6 |
| **Roland SP-404 MK2** | 2in/4out 48kHz | Bus FX, DJ Mode, Looper (25+ CCs) | SD card backup | Slicer → SP-404 |
| **Akai MPC / Force** | — | — | — | XPM drum program export |
| **Any USB audio device** | Record/playback | — | — | — |

## Features

### Recording
- **Auto-record on transport** — hit play and Compa starts recording automatically
- **60-second recall buffer** — forgot to press record? Recall the last 60 seconds
- **Threshold recording** — auto-start when signal detected, stop on silence
- **Take management** — star, rename, delete recordings with BPM/pattern metadata
- **Samba share** — recordings accessible on your Mac/PC via network

### Sample Editing
- **Visual waveform slicer** — load any WAV, see the waveform, place slice markers
- **Start/End trim** with snap-to-zero-crossing (no clicks)
- **Auto-slice** — divide into 2, 4, 8, or 16 equal parts
- **Normalize, Mono, Downsample** — prepare samples for your device's constraints
- **Zoom** — zoom into waveform for precise editing
- **Export + Transfer** — convert and send slices to your device via USB

### Format Conversion
- **P-6 format** — 44.1kHz 16-bit mono WAV
- **SP-404 MK2 format** — 48kHz 16-bit stereo WAV in IMPORT folder
- **Akai MPC/Force** — generates .xpm drum programs with properly formatted samples
- **Cross-device** — record on one device, convert, load on another

### Internet Radio
- **137 stations** across 25 genres (Jazz, Soul, Funk, Lo-fi, Hip Hop, Metal, Classical, Electronic, Vintage, Paranormal, and more)
- **Capture buffer** — save the last 60 seconds of any stream
- **Record** — manual or threshold-based recording from radio
- **Track metadata** — shows current artist/song from ICY metadata
- **Full-width visualizer** — real-time waveform display

### Performance
- **Pattern chain / song mode** — program pattern sequences with bar counts
- **Tap tempo / master clock** — Pi sends MIDI clock at exact BPM
- **Pi-side step sequencer** — 6-pad x 16-step grid
- **Granular presets** (P-6) — save and recall all 14 granular engine parameters
- **Device-specific controls** — adapts to connected device's CC map

### Utility
- **One-button backup/restore** — save device contents as named snapshots
- **Session notes** — jot down ideas, auto-saved
- **Searchable reference manual** — every CC, shortcut, effect, and menu item
- **Resample calculator** — bar durations vs sample rates at current BPM
- **Settings screen** — mouse mode, auto-record, threshold, touch calibration

## Hardware Requirements

- **Raspberry Pi 3B+** or newer (Pi 4/5 recommended)
- **Touchscreen** — 7" HDMI recommended (800x480+), 3.5" SPI supported with mouse
- **USB music device** — Roland P-6, SP-404 MK2, or any USB audio interface
- **USB cable** (device to Pi, must be data-capable)
- **Official Pi power supply** (2.5A+ required)
- **Optional**: USB mouse for 3.5" screens

## Screens

| Screen | What it does |
|--------|-------------|
| **SESSION** | Dashboard: transport, BPM, pattern, resample calc, notes, backup/restore |
| **CONTROL** | Parameter knobs — adapts to device (P-6: granular/filter/etc, SP-404: bus FX/DJ/looper) |
| **PATTERN** | Pattern grid, pattern chain editor, Pi-side step sequencer |
| **RECORD** | Record/recall/threshold, level meters, waveform, recording list |
| **SAMPLE** | File browser, visual waveform slicer with editing tools |
| **RADIO** | Internet radio with 137 stations, visualizer, capture |
| **SETTINGS** | Mouse mode, audio config, touch calibration |

## Quick Install

```bash
# Flash Raspberry Pi OS Lite (64-bit) to SD card using Raspberry Pi Imager
# Set username: pi, enable SSH, configure WiFi

# SSH in, then:
git clone https://github.com/macdigi/compa.git
cd compa
python3 -m venv venv
venv/bin/pip install pygame sounddevice soundfile numpy python-rtmidi evdev
sudo apt install ffmpeg libts-bin samba

# Set up the service
sudo cp setup/compa.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable compa
sudo systemctl start compa
```

## Display Compatibility

| Screen | Resolution | Connection | Touch | Experience |
|--------|-----------|------------|-------|-----------|
| 7" HDMI | 800x480+ | HDMI + USB | Capacitive | Best — full touch |
| 5" HDMI | 800x480 | HDMI + USB | Capacitive | Good |
| 3.5" SPI | 480x320 | GPIO SPI | Resistive | Functional — mouse recommended |
| Any HDMI monitor | Varies | HDMI | Mouse | Works great |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| F1-F6 | Switch screens |
| F7 | Help / reference manual |
| F8 | Settings |
| Space | Transport start/stop |
| R | Toggle recording |
| A | Toggle auto-record |
| M | Toggle mouse mode |

## License

MIT License. See [LICENSE](LICENSE) for details.

## Credits

Created by **RARE DATA** — [raredata.net](https://raredata.net)
