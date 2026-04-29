# Compa Changelog

User-facing release notes. The Updates screen on Compa reads the
top-most `## ` section's bullet points and shows them when an
update is pending.

Style: producer-to-producer, no jargon. One bullet per shipped
feature or fix. Skip internal refactors / CI / docs that don't
affect what a user sees.

---

## Unreleased

- Auto-updater notifies you in the nav bar when new builds are out;
  one tap shows what changed and pulls the update with a restart.
- Push 2 keys mode: full-screen LCD layout with a piano keyboard,
  rolling note roll, and chord recognition (Cmaj7, F#m7b5, slash
  chords, etc.) on both the Push 2 LCD and the touchscreen.
- Push 2 chord layout: every pad plays a full chord. 8 columns are
  the diatonic chord positions (I-vii°+I'); rows are variations
  (root, +7, 1st inv, 2nd inv, then +1 octave). Tap LAYOUT button
  on Push 2 to cycle chromatic → in-key → chord.
- Arpeggiator on chord mode + chromatic + in-key. Knobs control
  rate, octaves, stab, swing, density, inversion, humanize, accent.
  Top buttons are pattern shortcuts (UP / DOWN / UP-DN / DN-UP /
  RANDOM / OFF) plus RESTART and HOLD. Tempo follows the P-6's BPM
  live.
- Top scale buttons (above LCD) and root buttons (below LCD) on
  Push 2 are direct shortcuts — Major, Minor, Pent, Blues, Dorian,
  Mixolydian for scales; C-D-E-F-G-A-B for roots (Shift+root for
  the sharp variant).
- SP-404 chromatic mode: Push 2 grid auto-aligns to whichever pad
  you're playing chromatically — bottom-left of the grid is now
  the SP's bend-window low end. Out-of-range pads stay dimly lit
  so the layout is always visually complete.

## v0.1.0 - first public image

- Compa OS image: complete Raspberry Pi image with Compa
  pre-installed and configured to launch on first boot. Flash with
  Raspberry Pi Imager → Use custom → boot. ~720 MB compressed.
- One-command installer at raredata.net/compa for users who
  already have Pi OS Lite running.
- Touchscreen UI for SP-404 MK2 and Roland AIRA Compact P-6 over
  USB. Live FX control, chromatic keyboard, kit builder, MPC /
  Force / Ableton export.
