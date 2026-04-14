# Next Session Roadmap

## Known Issues
- P-6 does not receive MIDI CC over USB (may need TRS MIDI adapter)
- Audio playback crashes on sample rate mismatch (recorded at different rate than output)
- P-6 Twister LED "yellow" still shows as light blue (value 22 too low on this wheel)
- P-6 audio recording is glitchy (may be sample rate, buffer size, or USB bandwidth)

## New Feature Requests
- **Compa-to-Compa network link**: sync files between Compa 1 (.188) and Compa 2 (.191), control link
- **Auto updater**: optional update checker that pulls latest from git and restarts

## Compa 2 Setup
- Compa 2 online at 192.168.4.191 (Pi 3B, 7" 1024x600)
- Same codebase as Compa 1 (192.168.4.188)
- Deploy to both: rsync to .188 AND .191

## Pending Features

### 1. P-6 MIDI CC via TRS
- Test CC control through 3.5mm TRS MIDI jack instead of USB
- Need USB-to-TRS MIDI adapter
- Firmware 1.02 confirmed to support CC receive on Auto Ch (15) and Granular Ch (4)

### 2. Spectra Improvements
- Color calibration (current palette is guesswork)
- Hold function doesn't cut audio (SP-404 one-shot limitation)
- Bank switching is cosmetic only (SP-404 doesn't support via MIDI)

### 3. Twister Polish
- LED color calibration (yellow shows as green, red shows as off on some values)
- Effect assignment persistence (save custom assignments to config)
- Visual feedback when cycling pages (flash LEDs)

### 4. Recording / Playback
- Fix sample rate mismatch crash when playing back recordings
- Add waveform preview for recorded files
- Playback through connected device (SP-404 or P-6)

### 5. Settings Expansion
- Twister effect assignment editor (tap slot to pick from effect list)
- Spectra button assignment editor
- Network info display (show IP address on settings screen)
- Startup delay configuration

### 6. SP-404 Protocol Research
- Captured handshake bytes: 12 60 e0 05 fe 67 00 6d 33 31 31 03
- Local cache at ~/SP404 User/ROLAND/SP-404MKII_LOCAL/
- Need to capture more traffic to understand full protocol
- PADCONF.BIN + RFWV audio format partially decoded
