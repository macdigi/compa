# Compa Changelog

User-facing release notes. The Updates screen on Compa reads the
top-most `## ` section's bullet points and shows them when an
update is pending.

Style: producer-to-producer, no jargon. One bullet per shipped
feature or fix. Skip internal refactors / CI / docs that don't
affect what a user sees.

---

## Unreleased

(nothing yet — this section fills up as new commits land)

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
