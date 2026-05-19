# Compa Studio Architecture

Compa has two complementary jobs:

- Be a companion and translator for external grooveboxes.
- Be a standalone groovebox/studio when paired with Push 2, MIDI controllers,
  and an audio interface.

The UI should keep those jobs distinct.

## Top-Level Model

### Session

The Session screen is the launch point. It shows hardware and system status:
connected grooveboxes, audio/MIDI health, storage/protocol status, network
state, and quick actions.

External hardware appears here as cards:

- SP-404MKII
- P-6
- future class-compliant devices such as Move or newer MPCs
- audio interfaces
- network Compa peers

Push 2 is a control surface, not a groovebox endpoint, so it should not become
one of these cards.

### Studio

Studio is Compa's internal groovebox/workstation. It owns clip launching,
tracks, scenes, internal instruments, generated patterns, capture/recording,
and Push 2 performance control.

Studio should work with only:

- a Raspberry Pi running Compa
- Push 2 or another MIDI controller
- an audio output/interface

Studio tracks can target internal engines or external devices.

Examples:

- Internal sample drum rack
- Internal 808/909-style drum synth
- Internal mono bass synth
- Internal poly/wavetable synth
- SP-404 pad bank plus chromatic lane
- P-6 pads/granular engine
- future Move/MPC profiles
- network MIDI/audio peer

Each track stores a TrackTarget separately from its InstrumentRef.
InstrumentRef describes the internal sound generator to instantiate when the
track is rendered inside Compa. TrackTarget describes the musical endpoint:
an internal engine, an external groovebox profile, or a network peer. Older
sessions without a target infer one from the track type and instrument kind.

Target capabilities live in engine/studio_targets.py. The catalog records
things Studio needs to know before exposing a workflow: pad count, chromatic
support, audio in/out, FX CC support, whether it requires internal audio, and
the minimum Pi generation for heavy internal engines.

The first runtime performer path lives in engine/studio_performer.py. It plays
PatternSpec data to an already-open MIDI connection from Studio without
starting Compa's internal audio stream. The initial confirmed target is
SP-404 A1-A6 Beat+Bass: Bank A pads A1-A5 for drums plus A6 as the selected
chromatic source on SP channel 16. Playback timing follows the live Studio BPM
at loop boundaries, clips note-offs to the pattern length for cleaner loops,
and can generate simple A1-A6 beat+bass variations for auditioning.

### Device Workspaces

Device workspaces stay focused on device-specific behavior:

- files and librarian actions
- pad/sample management
- protocol/mount status
- FX and transport controls
- capture/import/export
- device-specific performance pages

AI performer controls should not live only inside an SP-404 or P-6 card.
They belong in Studio, with a selected target.

## Current Implementation Notes

The original hidden `clips` screen is now the Studio surface. The internal
screen key is `studio`; `clips` remains as a legacy alias so older shortcuts
and config paths keep working.

`STUDIO_TAB_ENABLED=1` controls whether the Studio nav tab and Push 2 Clip
button routing are exposed. `CLIPS_TAB_ENABLED` is retained as a compatibility
alias.

## Near-Term Path

1. Stabilize Studio as the visible clip/session surface.
2. Add a track target/capabilities model so clips can target internal
   instruments or external device profiles.
3. Represent proven SP performer patterns as Studio clips or pattern slots.
4. Bring Push 2 Studio modes forward: session, note drum, note synth, mix,
   device/edit.
5. Build internal instruments in this order:
   sample drum rack, 808/909-style drum synth, mono bass synth, then poly or
   wavetable synth.
6. Keep device cards focused on hardware-specific control and translation.
