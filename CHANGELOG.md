# Compa Changelog

User-facing release notes. The Updates screen on Compa reads the
top-most `## ` section's bullet points and shows them when an
update is pending.

Style: producer-to-producer, no jargon. One bullet per shipped
feature or fix. Skip internal refactors / CI / docs that don't
affect what a user sees.

---

## v0.3.2 — 2026-06-28

- Settings → Updates now finds new versions on a freshly flashed card.
  The clean image always said "Up to date" even when there was new
  stuff — fixed. Check Now pulls the latest, Update Now installs + reboots.
- A failed update check now says so, instead of pretending you're current.
- No touchscreen? The first setup screen tells you to press M for
  mouse + keyboard mode — and M turns the cursor on right there.
- RARE DATA RADIO sits at the top of the Radio tab on a fresh card.
- The User Manual is now a download link (not an email attachment),
  with the Rare Data logo on the cover.

## RARE DATA RADIO restored — 2026-06-26

- **RARE DATA RADIO is back at the top of the Radio tab** (electronic),
  streaming from radio.raredata.net.

## v0.3.1 — 2026-05-15

**First-boot wizard — actually works on a fresh flash now**

- **Compa now boots into the wizard.** The v0.3.0 image was missing
  the EGL graphics libraries (`libegl1`, `libgles2`, `libegl-mesa0`),
  so pygame crashed on every boot trying to initialize the display
  and the Pi sat at a terminal `compa login:` prompt instead. The
  image build now installs them. Existing v0.3.0 Pis: a one-time
  `sudo apt install libegl1 libgles2 libegl-mesa0` fixes it without
  re-flashing.

- **Touchscreen calibration in the wizard no longer freezes.** The
  "Calibrate Now" button was calling the legacy `ts_calibrate` tool,
  which only handles resistive ADS7846 panels — on a modern HID
  touchscreen it would hang for 60 s, take over the framebuffer, and
  crash the wizard. The wizard now uses the same in-app calibration
  flow Settings has had since v0.1.1 (four corners + center, affine
  least-squares fit, saved to `~/.config/compa/touch_calibration.json`).

- **Welcome card now says "SP-404 MK2 + P-6 Companion"** instead of
  just "P-6 Companion" — Compa supports both samplers (plus any
  USB-class-compliant device), and the wizard's intro card should
  say so.

- **`pylinkaudio` and `aalink` are now in the image's venv.** Same
  gap as v0.1.1 — the install script wasn't pip-installing them
  even though they're in `requirements.txt`. Without them, Compa
  printed "pylinkaudio not installed — Link Audio broadcast disabled"
  at startup. Now baked in so Link Audio works on a fresh flash.

## v0.3.0 — 2026-05-14

**Network MIDI bypass — dedicate a controller to your computer**

- New per-controller toggle: **Settings → Network MIDI → "Bypass
  local · send to network only"** (also on each controller's
  CONFIGURE screen). Flip it on and that controller's MIDI goes
  straight to your Mac/PC over the network — it stops triggering the
  focused SP-404 / P-6. Flip it off and it's back to driving Compa
  locally. So your MIDI Fighter, keyboard, or pad controller can
  play Ableton over WiFi while the rest of your gear keeps working
  as normal.

- **Network MIDI works out of the box on a fresh flash** — the
  rtpmidid daemon that powers Network MIDI is now baked into the
  Compa image (pinned to v26.01). No more "toggle does nothing,
  journal fills up with start failed" on a brand-new Pi. Existing
  Pis: re-run `setup/install.sh` to pick it up. If rtpmidid is still
  missing for any reason, Settings → Network MIDI now shows
  "rtpmidid not installed" instead of offering a dead toggle.

**Stability — no more freezes during recording**

- **Compa no longer locks up under load.** Every recorder action —
  switching the focused card, hitting Recall or +REC, starting or
  stopping a recording, monitoring — now runs off the UI thread.
  Hammering card switches or the recall buffer during a screen
  recording used to freeze the whole screen; it doesn't anymore.

- **Recording and the oscilloscope reliably follow the focused
  card.** Rapid switching between the SP-404 and P-6 could leave
  Compa capturing silence from the wrong device — the audio input
  now hands off cleanly every time.

**Other**

- **Clips tab hidden by default** — the Compa 2 clip launcher is
  still incomplete and shouldn't be tappable yet. The nav button,
  F10 shortcut, and Push 2 Clip-button routing are gated by a new
  `CLIPS_TAB_ENABLED` config flag (default off). Flip to `1` to
  preview the in-progress feature; re-enabled by default once it's
  ready to ship.

## v0.2.0 — 2026-05-10

**Recall buffer enhancements — three ways to never miss a take**

- **+REC button** on every device card. Dumps the entire current
  recall buffer to a new WAV file *and* keeps recording into the
  same file. One seamless take from "the moment you forgot to hit
  record" all the way to your stop press.
- **Pre-roll on every REC press** — new Settings row. Set it to any
  length up to your buffer size and every normal REC press silently
  prepends that much audio from the recall buffer. The "I forgot to
  record" failure mode becomes structurally impossible.
- **Configurable recall buffer length** — Settings row, smart steps
  (15s / 30s / 60s / 2min / 5min / 10min / 30min ceiling). Live
  resize with brief monitoring stop during the swap. RAM cost is
  ~23 MB per minute at 48 kHz stereo.
- File metadata sidecar tags pre-roll length and "started_via:
  recall_continue" so DAWs and your future self can see what the
  file actually is.

**SP-404 MK2 modes — Compa-side UI rewrite**

- **DJ mode** — split-deck console. Each deck has its own volume
  fader, transport row (PLAY/PAUSE/CUE/SYNC/BEND±), and full FX
  rack with the 38-effect SP list (prev/next cycler showing the
  effect name, ON/OFF toggle, six control knobs). Deck A drives
  Bus 1 / Ch1, Deck B drives Bus 2 / Ch2 — the SP's own internal
  routing. Crossfader spans both decks with a thumb that color-
  blends between the two deck accents based on position.
- **Looper** — performance-grade. Status badge that infers state
  (READY / RECORDING / PLAYING / OVERDUBBING) from the CCs Compa
  sent. Marquee REC button that smart-toggles to STOP RECORDING
  while a take is in progress. OVERDUB / STOP / DELETE / RESET
  TEMPO / UNDO / REDO laid out for fast finger reach.
- **Pattern** — added MIDI Start/Stop transport buttons to the
  pattern grid (PLAY / STOP send to the SP) and a "? RECORD"
  helper overlay that walks you through real-time recording,
  overdubbing, and step-edit on the SP itself.

**MON (monitor routing) fixes**

- MON between two USB-audio devices no longer creates a feedback
  chamber when SP-404's External Source is on. Two distinct bugs
  shipped: (1) MON setup was leaving the previous output stream
  open during the swap window — fixed with explicit close-first
  ordering. (2) Tapping a different card mid-MON would silently
  collapse the route via screen-transition tear-down — fixed by
  preserving the recorder when MON is active.

**Stability — kernel resource leaks**

- ChromaticKB scan no longer hammers `/dev/snd/seq` when the kernel
  ALSA seq table is starved. Exponential backoff (2s → 120s ceiling)
  + log rate-limit (1 line per minute). Each retry was previously
  leaking kernel-side seq client slots, eventually starving any
  audio open — *that* was the actual cause behind perceived "MON
  crashes the system" reports.
- Recorder hot-plug retry got the same backoff treatment (5s → 60s
  ceiling, log rate-limited, resets to 0 on USB topology change so
  a freshly plugged device is detected on the very next 5-second
  tick).

**Power supply — Pi 5 USB stability note**

- Pi 5 users: official 27 W USB-C PD power supply (5 V / 5 A) is
  required to drive multiple bus-powered USB devices. Generic
  "27 W" chargers that negotiate at 9 V / 3 A leave the Pi in
  restricted USB-current mode (600 mA cap across all ports
  combined), which trips over-current the moment a touchscreen or
  similar device is plugged in. Adding `usb_max_current_enable=1`
  to `/boot/firmware/config.txt` lifts the cap to 1.6 A *if* the
  PSU can deliver — both pieces are required.

## v0.1.1 — 2026-05-02

**Ableton Link tempo sync over WiFi**

- Joins any Link session on the local network and stays tempo-locked
  with iPad apps (AUM, Koala, Drambo, Loopy Pro, GarageBand),
  Ableton Live, Push 3, and other Link-aware peers.
- Compa broadcasts MIDI clock (0xF8 at 24 PPQN) to every connected
  device's MIDI out — so SP-404, P-6, and other class-compliant
  grooveboxes follow Link tempo without a single USB cable to the
  iPad. Set the device's sync source to External / Auto.
- Multi-Compa mesh: two Compas on the same network see each other
  as peers and stay locked together.
- LINK indicator on the session screen — green dot pulses on each
  tempo / peers update.
- Settings → ABLETON LINK section: live status, tempo source
  ("from this Compa" / "from a Link peer"), recipient device list,
  Enable Link toggle, Send MIDI Clock toggle.

**Touchscreen calibration**

- New in-app calibration screen for USB capacitive HID touchscreens.
  4-corner + center taps compute an affine transform; persists at
  ~/.config/compa/touch_calibration.json.
- Settings → Calibrate now opens the in-app screen instead of
  launching ts_calibrate (which only worked for old resistive
  panels).

**Update flow fix**

- Settings → Updates now actually works on flashed images. The
  previous image build was stripping .git from the source so the
  updater couldn't fetch from GitHub. Both the workflow and
  install.sh have been patched so .git survives the image build,
  with a bootstrap fallback for any deployment that ships source
  without .git.

## v0.1.0 — 2026-04-29 — first public release

The first stamped Compa OS image. Touchscreen companion for
SP-404 MK2 and Roland AIRA Compact P-6, with deep Push 2 control,
chord and arpeggiator modes, and one-tap auto-updates so future
features land without re-flashing the SD card.

**Distribution**

- Compa OS image: complete Raspberry Pi image with Compa
  pre-installed and configured to launch on first boot. Flash with
  Raspberry Pi Imager → Use custom → boot. ~720 MB compressed.
- One-command installer at raredata.net/compa for users who
  already have Pi OS Lite running.

**Touchscreen UI for the samplers**

- Touchscreen workspace per device — SP-404 MK2 and P-6 — with
  live FX control, kit builder, chromatic keyboard, MPC / Force /
  Ableton export, and a session view tying everything together.

**Push 2 control deck**

- Keys mode: full-screen LCD layout with a piano keyboard, rolling
  note roll, and chord recognition (Cmaj7, F#m7b5, slash chords,
  etc.) on both the Push 2 LCD and the touchscreen.
- Chord layout: every pad plays a full chord. 8 columns are the
  diatonic chord positions (I–vii°+I'); rows are variations (root,
  +7, 1st inv, 2nd inv, then +1 octave). Tap LAYOUT to cycle
  chromatic → in-key → chord.
- Arpeggiator across all keys layouts. Encoders control rate,
  octaves, stab, swing, density, inversion, humanize, accent. Top
  buttons are pattern shortcuts (UP / DOWN / UP-DN / DN-UP /
  RANDOM / OFF) plus RESTART and HOLD. Tempo follows the P-6's
  BPM live.
- Top scale buttons (above LCD) and root buttons (below LCD) are
  direct shortcuts — Major, Minor, Pent, Blues, Dorian, Mixolydian
  for scales; C–B for roots (Shift+root for the sharp variant).
- SP-404 chromatic mode: Push 2 grid auto-aligns to whichever pad
  you're playing chromatically — bottom-left of the grid is now
  the SP's bend-window low end. Out-of-range pads stay dimly lit
  so the layout is always visually complete.

**Setup + updates**

- First-boot wizard with mouse auto-detect (move the mouse to
  pick MOUSE, tap the screen to pick TOUCHSCREEN), touchscreen
  calibration, and WiFi setup (scans nearby networks, lets you
  pick + enter a password via the on-screen keyboard, connects
  with `nmcli`). Skip is always available for Ethernet users.
- Auto-updater: Compa polls the repo every 30 min in the
  background. When a new build lands, the Settings menu's
  UPDATES button lights up in the accent color with a "(N)" badge.
  Tap → Updates screen with the changelog in plain English →
  Update now pulls and restarts.
- Updates & changelog screen: a single place to read everything
  that's shipped over time, organized by release.
- Tap-vs-drag detection across the Settings menu — drag anywhere
  on a button row to scroll without triggering the button.
