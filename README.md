# Compa

**Touchscreen companion app for the Roland AIRA Compact P-6 on Raspberry Pi.**

Compa turns a Raspberry Pi + touchscreen into a powerful control surface, recorder, sample editor, and performance tool for the P-6 pocket sampler. It addresses the P-6's biggest limitations: tiny screen, no song mode, no tap tempo, tedious backup process, and limited sample management.

## Features

### Recording
- **Auto-record on transport** — hit play on the P-6 and Compa starts recording automatically
- **60-second recall buffer** — forgot to press record? Recall the last 60 seconds of audio
- **Take management** — star, rename, delete recordings with BPM/pattern metadata
- **Normalized playback** — recordings play back at proper volume despite P-6's quiet USB audio
- **Samba share** — recordings instantly accessible on your Mac via network

### Sample Editing
- **Visual waveform slicer** — load any WAV, see the full waveform, place slice markers
- **Start/End trim** — set S and E points, trim with snap-to-zero-crossing (no clicks)
- **Auto-slice** — divide into 2, 4, 8, or 16 equal parts
- **Normalize, Mono, Downsample** — prepare samples for P-6's memory constraints
- **Zoom** — zoom into waveform for precise marker placement
- **Export to P-6** — transfer slices directly when P-6 is in USB storage mode
- **Undo** — revert destructive edits (up to 5 levels)

### Performance
- **Pattern chain / song mode** — program pattern sequences with bar counts (the P-6 doesn't have this!)
- **Tap tempo / master clock** — Pi sends MIDI clock to P-6 at exact BPM
- **Pi-side step sequencer** — 6-pad x 16-step grid that triggers P-6 pads via MIDI
- **Granular presets** — save and recall all 14 granular engine parameters

### Utility
- **One-button P-6 backup/restore** — save entire P-6 contents as named snapshots
- **Session notes** — jot down ideas with the keyboard, auto-saved
- **Searchable P-6 reference manual** — every CC, shortcut, effect, and menu item
- **Resample calculator** — shows bar durations vs sample rates at current BPM

### Connectivity
- **Touchscreen support** — full multitouch via USB HID
- **Mouse support** — works with any USB mouse
- **ATOM SQ integration** — optional MIDI controller for navigation and pad triggering
- **BPM sync** — reads P-6 MIDI clock for accurate tempo display

## Hardware Requirements

- **Raspberry Pi 3B+** or newer (Pi 4 recommended)
- **5" USB touchscreen** (800x480 or 800x600, HDMI + USB)
- **Roland AIRA Compact P-6**
- **USB cable** (P-6 to Pi, must be data-capable)
- **Official Pi power supply** (2.5A+ required — undervoltage causes USB dropouts)
- **Optional:** PreSonus ATOM SQ MIDI controller

## Quick Install

```bash
# Flash Raspberry Pi OS Lite (64-bit) to SD card
# Connect via SSH, then:

git clone https://github.com/macdigi/compa.git
cd compa/setup
chmod +x *.sh
./01-base-setup.sh
./02-audio-setup.sh
./04-autostart.sh

sudo reboot
```

## P-6 Setup

For best results, configure these P-6 MIDI settings:
- **SYnC = Auto** (or USB when using tap tempo)
- **rxPC = On** (receive program changes)
- **A.CH = 15** (auto MIDI channel, default)
- **G.CH = 4** (granular MIDI channel, default)

## Screens

| Screen | What it does |
|--------|-------------|
| **SESSION** | Dashboard: transport, BPM, pattern, resample calc, notes, P-6 backup |
| **CONTROL** | Parameter knobs (granular/filter/envelope/mixer/FX), granular presets, tap tempo clock |
| **PATTERN** | 64-pattern grid, pattern chain editor, Pi-side step sequencer |
| **RECORD** | Record/recall/stop, level meters, waveform, recording list with metadata |
| **SAMPLE** | File browser, visual waveform slicer with editing tools |
| **? (HELP)** | Searchable P-6 reference manual |

## P-6 USB Backup Modes

The P-6 requires specific button combos to enter USB storage mode:

| Mode | Button combo | What it backs up |
|------|-------------|-----------------|
| Pattern backup | Hold **STOP** + power on | .PRM pattern files |
| Sample export A-D | Hold **bank button** + power on | .WAV + .PRM sample files |
| Sample export E-H | Hold **bank + SAMPLING** + power on | .WAV + .PRM sample files |
| Sample import | Hold **SAMPLING** + power on | Load WAV files to pads |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| F1-F5 | Switch screens (Session/Control/Pattern/Record/Sample) |
| F6 | Help / reference manual |
| Space | P-6 transport start/stop |
| R | Toggle recording |
| A | Toggle auto-record |
| ESC | Exit help screen / quit app |

## Coming Soon

- Roland SP-404 MK2 support
- Multi-device simultaneous connection
- Device abstraction for other samplers
- Performance FX automation

## License

MIT License. See [LICENSE](LICENSE) for details.

## Credits

Created by **macdigi** with Claude Code.

P-6 research informed by the community: sunwarper, SPVIDZ, imLowKey, Ricky Tinez, BoBeats, minutiae, and Nonjuror.
