# Next Session Roadmap

## Device Workspace Enhancements

### 1. Oscilloscope in Workspace
- Full-width oscilloscope at top of expanded card (like session card but bigger)
- Shows live audio from that device
- BPM + transport overlay on the oscilloscope
- Fills the empty space above the control knobs

### 2. Midi Fighter Twister Deep Integration
- **Knob press = load effect**: Each knob press (CC on ch2) loads a specific effect on the active bus
  - Knob 1 press → Downer
  - Knob 2 press → Lo-fi
  - Knob 3 press → Isolator
  - Knob 4 press → 303 VinylSim
  - etc. (user-configurable)
- **Knob turn = main parameter**: While pressed, the knob controls Ctrl 1 of that effect
- **Release = keep effect or bypass**: Configurable behavior
- **LED colors**: Each knob lights up with the effect's color when active
- **Auto-map genius mode**: Detects Twister, maps all 16 knobs intelligently across 2 buses

### 3. P-6 Control Buildout
- Granular engine knobs matching P-6 layout
- Filter, Envelope, Mixer sections
- Sample select with pad preview
- Pattern grid (6 pads visible)

### 4. Empty Space → Useful Content
- Oscilloscope takes top third
- Control knobs take middle
- Bus selector + signal flow at bottom
- No dead black space

### 5. Control Tab Scoping
- When in workspace, Control tab is locked to that device
- No need to switch focus — workspace IS the focus
- Remove the old Control screen from nav bar? Or keep as fallback

### 6. Single Device Auto-Expand
- When only one device connected, skip card view
- Go straight to expanded workspace on boot

## SP-404 Protocol (Ongoing)
- Captured handshake bytes: 12 60 e0 05 fe 67 00 6d 33 31 31 03
- Local cache at ~/SP404 User/ROLAND/SP-404MKII_LOCAL/
- Need to capture more traffic to understand full protocol
- PADCONF.BIN + RFWV audio format partially decoded
