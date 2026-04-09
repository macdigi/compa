# Compa — Claude Code Context

## What Is This
MPC-style hardware sampler running on Raspberry Pi 3B with 7" DSI touchscreen, USB MIDI controller, and USB audio interface. Full instrument — not a prototype.

## Hardware
- **Pi 3B**: 1GB RAM, quad-core 1.2GHz ARM
- **Display**: Official 7" DSI touchscreen, 800x480, capacitive
- **Audio**: USB class-compliant audio interface via ALSA
- **MIDI**: USB class-compliant pad controller (MPD, LPD, etc.)
- **Network**: WiFi to Mac Mini for sample library access via SSHFS

## Architecture
- **No desktop environment** — pygame runs directly on framebuffer (fbcon)
- **No X11, no Wayland, no web server**
- Python 3.11+ with venv at `/home/pi/pi-sampler/venv`
- Audio: `sounddevice` (PortAudio) + `numpy` for real-time mixing
- MIDI: `python-rtmidi` with auto-detect and reconnect
- Samples: `soundfile` for loading, cached as float32 numpy arrays in RAM
- Kits: JSON files in `kits/` directory
- Network samples: SSHFS mount at `/mnt/samples`, copied to local cache on assign

## Key Constraints
- **1GB RAM budget**: ~600MB for samples, ~200MB OS, ~200MB app
- **Audio callback must be lock-free**: no allocations, no file I/O, numpy vectorized only
- **Buffer: 256 frames** at 44100Hz (~5.8ms latency)
- **32 max voices** globally, oldest-voice stealing
- **30fps UI** — don't waste CPU on higher
- Waveform previews pre-computed on load (downsampled to 800 points)

## File Structure
```
engine/          — Audio engine, MIDI, pad model, sample loader, kit manager
ui/              — Pygame app, theme, screens (main/browser/pad_edit/kit)
ui/components/   — Reusable widgets (pad_grid, waveform, file_list, knob, button, modal)
setup/           — Pi setup scripts (base, audio, network, autostart) + config.env
kits/            — Saved kit JSON files
samples/         — Local sample cache
```

## Running
- On Pi: starts via systemd `pi-sampler.service` on boot
- Manual: `sudo systemctl start pi-sampler`
- Logs: `journalctl -u pi-sampler -f`
- Dev (on Pi): `/home/pi/pi-sampler/venv/bin/python ui/app.py`

## Pad Engine
- 4 banks (A/B/C/D) x 16 pads = 64 slots
- Each pad: sample, volume, pan, tune, start/end, attack/decay, mode (one-shot/loop), choke/mute groups
- MIDI notes 36-51 = pads 1-16 (configurable)
- Velocity-sensitive triggering

## Common Tasks
- **Add a screen**: Create in `ui/screens/`, register in `ui/app.py` screens dict, add nav button
- **Add a component**: Create in `ui/components/`, follow Button/Knob pattern (draw + handle_event)
- **Change audio behavior**: Edit `engine/audio_engine.py` `_render_voice()` or `_audio_callback()`
- **Change MIDI mapping**: Edit `setup/config.env` MIDI_BASE_NOTE or `engine/midi_input.py`
- **Add pad parameter**: Update `Pad` dataclass in `pad_bank.py`, add to `to_dict`/`from_dict`, add UI control in `pad_edit_screen.py`
