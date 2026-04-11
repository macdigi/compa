<p align="center">
  <strong>C O M P A</strong><br>
  Universal Touchscreen Companion for USB Music Hardware
</p>

<p align="center">
  <a href="#quick-start"><img src="https://img.shields.io/badge/platform-Raspberry%20Pi-C51A4A?style=flat-square&logo=raspberrypi&logoColor=white" alt="Raspberry Pi"></a>
  <a href="#supported-hardware"><img src="https://img.shields.io/badge/devices-Roland%20%7C%20Akai%20%7C%20USB%20Audio-1a1a2e?style=flat-square" alt="Devices"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="https://raredata.net"><img src="https://img.shields.io/badge/by-RARE%20DATA-000000?style=flat-square" alt="RARE DATA"></a>
</p>

---

Compa turns a Raspberry Pi and touchscreen into a multi-device control surface, recorder, sampler, and transfer hub for your hardware. Plug in up to three USB devices, and Compa auto-detects each one, adapts its interface, and connects everything through a single unified workflow: **Record, Slice, Build Kit, Push to Device.**

No desktop environment. No web browser. Just a direct pygame UI on KMSDRM, built for live use with your fingers.

---

## Table of Contents

- [Features](#features)
- [Supported Hardware](#supported-hardware)
- [Multi-Device Hub](#multi-device-hub)
- [Screens](#screens)
- [Cross-Device Workflow](#cross-device-workflow)
- [Screenshots](#screenshots)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Display Compatibility](#display-compatibility)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)
- [Credits](#credits)

---

## Features

### Multi-Device Hub
- Connect up to **3 USB devices simultaneously** with independent MIDI connections
- **Auto-detection** via USB vendor/product IDs and Linux sysfs scanning
- **Hot-plug** support -- connect and disconnect devices without restarting
- **Audio routing** between devices (e.g. SP-404 output into P-6 input)
- **MIDI clock relay** from Compa to all connected devices
- Tap a device name in the nav bar to switch focus

### Recording & Capture
- Record from any connected USB audio device
- **Input source selector** -- choose which device to record from
- **60-second recall buffer** -- forgot to press record? Capture the last minute
- **Threshold recording** -- auto-start on signal, auto-stop on silence
- Take management with star, rename, delete, and BPM/pattern metadata
- Samba network share for Mac/PC access to all recordings

### Sample Editing
- **Visual waveform slicer** -- see the full waveform, place slice markers
- Start/end trim with **snap-to-zero-crossing** (no clicks or pops)
- Auto-slice into 2, 4, 8, or 16 equal parts
- Normalize, convert to mono, downsample
- Zoom for precision editing
- Export slices directly to any connected device

### Format Conversion
| Target | Format |
|--------|--------|
| **Roland P-6** | 44.1 kHz, 16-bit, mono WAV |
| **Roland SP-404 MK2** | 48 kHz, 16-bit, stereo WAV |
| **Akai MPC / Force** | `.Drum.xpm` program (template-based from Force) |
| **Ableton Live** | `.adg` Drum Rack (template-based, gzipped XML) |

Record on one device, convert, load on another. Cross-device sample sharing in seconds.

### Kit Builder
- **4x4 pad grid** with **8 banks** (128 pads total)
- Drag samples onto pads from the file browser
- **Smart import** -- auto-detect drum types (kick, snare, hat, clap, etc.) from folder and file names using pattern matching against common sample library conventions
- Waveform preview per pad
- Export as Akai `.xpm` drum program or Ableton `.adg` Drum Rack

### Internet Radio
- **137 stations** across 25+ genres (Jazz, Soul, Funk, Lo-fi, Hip Hop, Metal, Classical, Electronic, Vintage, Paranormal, and more)
- ICY metadata -- current artist and track displayed live
- Full-width real-time waveform visualizer
- **Capture buffer** -- save the last 60 seconds of any stream as WAV
- Manual or threshold-based recording from radio

### Pattern Sequencing
- Pattern grid view (4x4 or 8x8 depending on device)
- **Chain / Song mode** -- program pattern sequences with bar counts and FX snapshots
- Pi-side step sequencer: 6-pad x 16-step grid (expandable to 64 steps)
- Special row types for SP-404 MK2: **Chromatic**, **Ghost Kick** (sidechain trigger), **EXT SOURCE** (gate live audio input)
- Step probability for generative patterns

### LFO Automation
- Sine, triangle, saw (up/down), square, random, and sample-and-hold waveforms
- Modulate any MIDI CC parameter at configurable rates (0.01 Hz to 30 Hz)
- Multiple simultaneous LFO targets
- 30 Hz update rate -- smooth enough for filter sweeps, light on CPU

### Performance & Control
- **Tap tempo / master MIDI clock** with sub-millisecond timing (hybrid sleep/spin-wait)
- Device-adapted CC knob screens -- different layout per device
- Granular engine presets (P-6): save and recall all 14 parameters
- Transport control: Play / Record / Stop per device
- Real-time oscilloscope and level meters per device card

### Utility
- One-button **backup / restore** for device contents
- Session notes (auto-saved)
- **3 searchable reference manuals** (Compa, P-6, SP-404 MK2)
- Resample calculator -- bar durations at current BPM vs. sample rates
- Touch calibration
- Hardware-inspired color themes (P-6 yellow, SP-404 teal, Force red)

---

## Supported Hardware

| Device | Audio | MIDI Control | Patterns | Backup | Sample Transfer |
|--------|-------|-------------|----------|--------|-----------------|
| **Roland P-6** | 2-in / 2-out, 44.1 kHz | Granular, Filter, Envelope, Mixer, FX (40+ CCs) | 64 patterns | Full backup/restore | Slicer and format converter |
| **Roland SP-404 MK2** | 2-in / 4-out, 48 kHz | 5-bus FX with named effects, DJ mode, Looper (25+ CCs) | 16 patterns | SD card backup | Slicer and format converter |
| **Akai Force / MPC** | -- | -- | -- | -- | USB file transfer (Computer Mode), XPM drum program export |
| **Any USB audio device** | Record / playback | -- | -- | -- | -- |

### SP-404 MK2 Effects Coverage

Compa includes the complete SP-404 MK2 effects list with named presets per bus:

- **Bus 1 and 2** -- 42 effects including Scatter, Ha-Dou, Ko-Da-Ma, Tape Echo, JUNO Chorus, Cloud Delay, and more
- **Bus 3 and 4** -- 40 effects with a different ordering, no Direct FX
- **Input FX** -- 18 effects focused on vocal and amp processing

All effect selection is via CC with human-readable names displayed on screen.

---

## Multi-Device Hub

```
   +-----------+     USB     +-----------+     USB     +-----------+
   | Roland P-6 |<---------->|           |<---------->| SP-404 MK2 |
   +-----------+    Audio    |   COMPA   |    Audio    +-----------+
                    + MIDI   |           |    + MIDI
   +-----------+     USB     |  Pi + 7"  |
   | Akai Force |<---------->| Touchscr. |
   +-----------+   Storage   +-----------+
```

- Each device gets its own MIDI connection on the correct channels
- Session screen shows a **playing card per device** with oscilloscope, meters, and transport buttons
- Device color themes adapt automatically (yellow for P-6, teal for SP-404, red for Force)
- Audio can be routed between devices via lock-free ring buffers with sample rate conversion
- MIDI clock is relayed from Compa's master clock to all connected devices simultaneously

---

## Screens

| # | Screen | Description |
|---|--------|-------------|
| 1 | **SESSION** | Device cards showing BPM, transport state, oscilloscope, Play/Rec/Stop per card |
| 2 | **CONTROL** | CC parameter knobs adapted per device (P-6 granular engine, SP-404 5-bus FX + looper + DJ mode) |
| 3 | **PATTERN** | Grid view (4x4 or 8x8), chain/song mode with FX snapshots, step sequencer with chromatic/ghost/EXT SOURCE rows |
| 4 | **RECORD** | Record from any device, input source selector, 60s recall buffer, threshold recording, level meters |
| 5 | **SAMPLE** | Folder browser, visual waveform slicer, format converter (P-6 / SP-404 / MPC / Ableton) |
| 6 | **RADIO** | 137 internet radio stations, real-time visualizer, capture buffer, threshold recording |
| 7 | **KIT BUILDER** | 4x4 pad grid, 8 banks (128 pads), smart drum import, waveform preview, export XPM + ADG |
| 8 | **XFER** | Push/pull files to MPC/Force via USB Computer Mode, SD card and SSD drive selector |
| 9 | **SETTINGS** | Device config, audio routing, MIDI clock relay, themes, touch calibration |
| 10 | **HELP** | 3 searchable reference manuals (Compa, P-6, SP-404 MK2) |

---

## Cross-Device Workflow

Compa is designed around a single continuous flow that works across any combination of connected devices:

```
 RECORD          SAMPLE           KIT BUILDER        XFER
 ------          ------           -----------        ----
 Capture audio   Load WAV         Drop slices        Push kit to
 from any     -> Slice it up   -> onto 128-pad    -> MPC/Force via
 device or       Trim, normalize  grid. Auto-detect   USB, or export
 radio stream    Export slices     drum types.         for SP-404/P-6
```

**Record** from your SP-404. **Slice** the recording. **Build a kit** from the slices (auto-detecting kick, snare, hat). **Push** the finished kit to your MPC Force as an XPM drum program, or export as an Ableton Drum Rack. All without leaving Compa.

---

## Screenshots

> Screenshots coming soon. Compa runs at 800x480 on a 7" touchscreen with a dark theme and hardware-inspired accent colors.

---

## Quick Start

### Requirements

- **Raspberry Pi 3B+**, 4, or 5
- **7" HDMI or DSI touchscreen** (800x480 or higher)
- **USB music device** (Roland P-6, SP-404 MK2, Akai Force/MPC, or any USB audio interface)
- **USB data cable** (must be data-capable, not charge-only)
- **Official Pi power supply** (2.5A minimum)
- **SD card** (16 GB+, Class 10 or faster)

### Install

```bash
# 1. Flash Raspberry Pi OS Lite (64-bit) to SD card
#    Use Raspberry Pi Imager. Set username: pi, enable SSH, configure WiFi.

# 2. SSH in
ssh pi@compa.local

# 3. Clone the repo
git clone https://github.com/macdigi/compa.git
cd compa

# 4. Create virtual environment (system-site-packages for pygame/numpy)
python3 -m venv venv --system-site-packages
source venv/bin/activate

# 5. Install Python dependencies
pip install pygame sounddevice soundfile numpy python-rtmidi evdev

# 6. Install system packages
sudo apt update
sudo apt install -y ffmpeg libts-bin samba

# 7. Install fonts (bundled in docs/fonts/)
sudo mkdir -p /usr/local/share/fonts
sudo cp docs/fonts/*.ttf /usr/local/share/fonts/
sudo fc-cache -f

# 8. Set up the systemd service
sudo cp setup/compa.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable compa
sudo systemctl start compa
```

### Samba Share (optional)

To access recordings from your Mac or PC over the network, add this to `/etc/samba/smb.conf`:

```ini
[compa]
   path = /home/pi/compa
   browseable = yes
   read only = no
   guest ok = yes
```

Then restart Samba:

```bash
sudo systemctl restart smbd
```

On macOS: Finder > Go > Connect to Server > `smb://compa.local/compa`

---

## Configuration

Edit `setup/config.env` to customize your setup:

```bash
# Network sample library (optional -- mount from Mac/PC via SSHFS)
MAC_MINI_IP=192.168.1.XXX
MAC_MINI_USER=charlie
REMOTE_SAMPLE_DIR=/Users/charlie/Music/Samples

# Audio
AUDIO_DEVICE=default
BUFFER_SIZE=256          # 256 frames = ~5.8ms latency at 44.1kHz
SAMPLE_RATE=44100

# MIDI
MIDI_BASE_NOTE=36        # Bottom pad = MIDI note 36 (C2)
```

### Setup Scripts

The `setup/` directory includes four numbered scripts that configure a fresh Pi:

| Script | Purpose |
|--------|---------|
| `01-base-setup.sh` | System packages, Python, venv |
| `02-audio-setup.sh` | ALSA config, USB audio permissions |
| `03-network-mounts.sh` | SSHFS mount, Samba share |
| `04-autostart.sh` | systemd service, boot-to-Compa |

---

## Display Compatibility

| Screen | Resolution | Connection | Touch | Experience |
|--------|-----------|------------|-------|-----------|
| 7" HDMI | 800x480+ | HDMI + USB | Capacitive | Best -- full touch, designed for this |
| 7" DSI (official) | 800x480 | DSI ribbon | Capacitive | Excellent -- no extra USB needed |
| 5" HDMI | 800x480 | HDMI + USB | Capacitive | Good |
| 3.5" SPI | 480x320 | GPIO SPI | Resistive | Functional -- mouse recommended |
| Any HDMI monitor | Varies | HDMI | Mouse | Works fine |

---

## Keyboard Shortcuts

For development or when using Compa with a keyboard attached:

| Key | Action |
|-----|--------|
| `F1` -- `F6` | Switch screens (Session, Control, Pattern, Record, Sample, Radio) |
| `F7` | Help / reference manual |
| `F8` | Settings |
| `Space` | Transport start / stop |
| `R` | Toggle recording |
| `A` | Toggle auto-record |
| `M` | Toggle mouse mode |

---

## Project Structure

```
compa/
  engine/                 Audio engine, MIDI, device detection, format conversion
    audio_engine.py         Real-time audio mixer (lock-free, numpy vectorized)
    audio_router.py         Route audio between USB devices with SRC
    device_detect.py        USB sysfs scanning and auto-detection
    device_profiles.py      DeviceProfile dataclass + built-in profiles
    drum_detector.py        Auto-classify samples as kick/snare/hat/etc.
    drum_mapper.py          Map detected drums to MPC pad layout
    format_converter.py     WAV conversion + XPM/ADG generation
    midi_clock.py           Master MIDI clock with tap tempo
    midi_lfo.py             LFO automation (sine/tri/saw/sq/random/S&H)
    midi_router.py          MIDI routing hub between controller and devices
    p6_midi.py              Roland P-6 MIDI implementation
    p6_sequencer.py         Step sequencer with chromatic/ghost/EXT SOURCE rows
    p6_chain.py             Pattern chain / song mode
    p6_presets.py           Granular engine preset save/recall
    radio_stream.py         Internet radio via ffmpeg with 60s capture buffer
    recorder.py             Multi-device recorder with threshold and recall
    sample_slicer.py        Waveform slicing engine
    sp404_effects.py        Complete SP-404 MK2 effect lists (all 5 buses)
    usb_storage.py          MPC/Force USB mass storage auto-mount and transfer
    ...
  ui/                     Pygame application and screens
    app.py                  Main app loop, screen manager, nav bar
    screens/                One module per screen (session, control, pattern, etc.)
    components/             Reusable widgets (pad_grid, waveform, knob, button, modal, etc.)
  docs/                   Reference data and templates
    fonts/                  Inter + JetBrains Mono (bundled)
    radio_stations.json     137 internet radio stations
    akai_drum_template.xpm  Golden Akai Force/MPC drum program template
    ableton_drumrack_template.adg  Ableton Live Drum Rack template
    compa_reference.txt     Compa reference manual
    p6_reference.txt        Roland P-6 reference manual
    sp404_reference.txt     SP-404 MK2 reference manual
  kits/                   Saved kit JSON files
  samples/                Local sample cache
  setup/                  Pi setup scripts + config
  requirements.txt        Python dependencies
```

---

## Contributing

Compa is currently in private development. The repo will open up once the core feature set stabilizes. In the meantime:

1. **Report issues** -- open a GitHub issue with your Pi model, device, and steps to reproduce.
2. **Device profiles** -- if you have a USB music device not listed above, a device profile contribution is the easiest way to expand Compa's hardware support. See `engine/device_profiles.py` for the pattern.
3. **Radio stations** -- submit station URLs via PR to `docs/radio_stations.json`.
4. **Code style** -- Python 3.11+, type hints, dataclasses. No external frameworks beyond pygame/numpy/sounddevice/rtmidi.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Credits

Created by **[RARE DATA](https://raredata.net)**

Fonts: [Inter](https://rsms.me/inter/) by Rasmus Andersson, [JetBrains Mono](https://www.jetbrains.com/lp/mono/) by JetBrains.

Roland P-6, SP-404 MK2, Akai Force, and Akai MPC are trademarks of their respective owners. Compa is an independent project and is not affiliated with or endorsed by Roland or Akai.
