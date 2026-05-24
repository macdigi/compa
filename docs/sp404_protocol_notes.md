# SP-404 MK2 Librarian Protocol — Research Notes

## USB Devices
The SP-404 MK2 presents TWO USB devices:
- **0582:02e7** — CDC ACM (virtual serial port) — Librarian protocol
- **0582:0281** — Audio + MIDI (standard USB audio class)

## CDC ACM Interface (02e7)
- Interface 0: CDC Control, EP 0x81 IN Interrupt (16 bytes)
- Interface 1: CDC Data, EP 0x82 IN Bulk (512b), EP 0x03 OUT Bulk (512b)
- Linux creates `/dev/ttyACM0`
- macOS creates `/dev/cu.usbmodemXXXXXXXX`

## What We Know
- The Roland SP-404MKII Librarian app (Mac/PC) uses this CDC serial port
- App binary contains: `SerialPortInputStream`, `SerialPortOutputStream`
- Protocol is called "BMC IPC" (Board Management Controller Inter-Process Communication)
- Key classes: `CPRMHServer`, `CBmcIpcIn`, `CBmcIpcOut`, `CBMC_IPC_ConvMsg`
- Packet structure: `UPAC` (likely a struct name)
- Commands: `SMPLParseCommand`, `smpl_command`, `smpl_call_import`, `smpl_call_view`
- Message framing: `CMsgContainer`, `CMsgCommand`

## What We Tried (All Failed)
- All 256 single-byte commands with 4-byte packets
- Common 2-byte command pairs
- SysEx identity request
- Roland DT1/RQ1 data requests
- Text/JSON/AT commands
- DTR/RTS toggling
- BREAK signal
- CDC SEND_ENCAPSULATED_COMMAND
- Vendor-specific USB control transfers (0x40/0xC0)
- Various baud rates (300 to 2000000)

## Device Behavior
- Responds to GET_LINE_CODING (echoes back baud settings)
- Sends CDC SERIAL_STATE notification on interrupt EP after DTR/RTS set
- Completely ignores all data written to bulk OUT endpoint
- Never sends any data on bulk IN endpoint

## Next Steps
1. Capture USB traffic from actual Librarian app using Wireshark/usbmon
2. The app likely sends a specific multi-byte handshake sequence
3. May require USB control transfers before bulk data flows
4. Could try MITM using a USB hardware analyzer
5. Serial number from USB descriptor: `SP-404MKII-G-423721E8Q2875`

## BREAKTHROUGH — dtrace capture (2026-04-11)

### Handshake bytes (first write to serial port)
```
12 60 e0 05 fe 67 00 6d 33 31 31 03
```
12 bytes sent on fd=13 (/dev/tty.usbmodem31131101) immediately after
opening the port and setting ioctl parameters.

### ioctl sequence
1. 0x2000740d — TIOCEXCL (exclusive access)
2. 0x40487413 — TIOCSETA (set terminal attributes)
3. 0x80487414 — TIOCGETA (get terminal attributes) x2
4. 0x80085402 — TIOCGWINSZ (get window size)

### Local cache discovery
The Roland Librarian app maintains a local cache at:
```
~/SP404 User/ROLAND/SP-404MKII_LOCAL/
  PROJECT_01/
    PADCONF.BIN          — Pad configuration (starts with "RFPD" magic)
    SMPL/
      BANK1-01.SMP       — Sample files (starts with "RFWV" magic)
      BANK2-06.SMP       — 48kHz audio data
      ...
```

### SMP file format
- Magic: `RFWV` (4 bytes)
- Data length: uint32 BE (offset 4)
- Sample rate: uint32 BE (offset 8) — 0x0000BB80 = 48000
- Channels: uint32 BE (offset 12) — 0x00000001 = mono
- Bit depth: uint32 BE (offset 16) — 0x00000010 = 16-bit

### PADCONF.BIN format
- Magic: `RFPD` (4 bytes)
- Contains pad assignments, names, parameters for all 160 pads
- Bank naming at offset 0x80+ (space-padded)

### Next steps
1. Try sending the handshake bytes from the Pi to see if SP-404 responds
2. Parse PADCONF.BIN to extract pad assignments
3. Decode RFWV audio format to extract/import samples
4. Build SP-404 VIEW mode using local cache when available

## Detailed capture (2026-04-11, second session)

### Exact termios settings used by Librarian app:
- c_iflag: 0x0000
- c_oflag: 0x0000  
- c_cflag: 0x4B00 (CS8 | CREAD | HUPCL)
- c_lflag: 0x0000
- ispeed/ospeed: 9600 (but CDC ACM ignores this)
- Raw mode, no flow control

### Handshake byte analysis:
```
12 60 e0 05 fe 67 00 6d 33 31 31 03
                      ^  ^  ^  ^  ^
                      g  \0 m  3  1  1  ETX
                         "m311" = from usbmodem path?
```

### Replicated EXACTLY on Mac (same machine):
- Same ioctls (TIOCEXCL, TIOCSETA, TIOCGETA, TIOCGWINSZ)
- Same termios flags (0x4B00, raw, 9600)
- Same 12-byte handshake
- STILL NO RESPONSE

### Theory:
The SP-404 may require the app to send something via the AUDIO/MIDI
interface (0582:0281) FIRST before the CDC serial port (0582:02e7)
becomes responsive. The dtrace capture didn't show MIDI activity
but the app opens MIDI ports during startup.

## Live Pi status (2026-05-18)

Jordan confirmed the official SP-404MKII App can access pad/sample data
while the SP is connected normally, without entering a visible USB storage
mode. That means Compa's mass-storage-only implementation is incomplete for
the desired workflow.

Current implementation status:
- Compa can mount generic Roland USB storage volumes through the
  `compa-storage-mount` helper when a block device appears.
- The SP-404MKII normal connection exposes audio/MIDI and, when present, the
  separate Roland CDC/librarian device described above.
- No normal-mode SP pad filesystem is visible as a Linux block device, so
  Files -> Device -> SP-404 must eventually talk the Roland librarian protocol
  instead of only scanning `lsblk`.

Live Pi probe:
- `/dev/ttyACM0` maps to USB ID `0582:02e7`
  (`usb-Roland_Corporation_Roland_SP-404MKII-if00`).
- Opening `/dev/ttyACM0` raw at 9600, setting DTR/RTS, and sending the
  captured handshake bytes now returns data from Jordan's hardware:

    write:
    12 60 e0 05 fe 67 00 6d 33 31 31 03

    read:
    13 e0 b5 05 44 6e e0 82 20 c7 5a 83 13 00 00 00
    7e 04 00 01 00 06 00 00 00 de 0e 00 00 02 00 00
    00 0b 33

This overturns the earlier "CDC serial is unreachable" assumption. The SP
does not need to mount as USB storage for librarian access, but Compa still
needs the command layer after this handshake.

Next concrete probes:
1. Identify the response frame boundaries, command IDs, sequence numbers, and
   checksum/CRC.
2. Capture the official app requesting project/pad lists immediately after
   this handshake.
3. Reproduce the smallest read-only command in Compa before attempting any
   write/delete operation.

## Pi protocol lab (2026-05-18)

Added tools/sp404_protocol_lab.py for repeatable local probing:

    tools/sp404_protocol_lab.py probe --count 5
    tools/sp404_protocol_lab.py analyze sessions/sp404_protocol/<log>.jsonl

Repeated live handshakes returned stable 35-byte responses in the current SP
state. Example:

    13 e0 3f 05 44 6e e0 82 88 e8 5b 83 13 00 00 00
    7e 04 00 04 00 08 00 01 ff ff ff ff 00 02 00 00
    00 0b 33

The lab also has a guarded send-hex mode for replaying captured read-only
commands after the official app traffic is captured. Do not replay unknown
import/delete/write commands.

Mac capture workflow is documented in
docs/sp404_librarian_capture_workflow.md.

## Mac capture attempt (2026-05-19)

Jordan provided a Mac mini capture bundle from the official Roland SP-404MKII
App 4.05. It confirmed the expected normal-mode USB nodes:

- /dev/cu.usbmodem11101
- /dev/tty.usbmodem11101

The ioreg output also confirmed the two SP USB personalities:

- 0582:02e7 Roland CDC/librarian interface
- 0582:0281 audio/MIDI interface

The first DTrace capture was inconclusive. Earlier attempts were blocked by
SIP/probe compatibility, and one trace only contained the startup banner with no
OPEN/IOCTL/TX/RX events. A later adapted DTrace capture succeeded and logged:

- OPEN events: 1
- IOCTL events: 6
- TX events: 7128
- RX events: 6622

After trimming DTrace's fixed 512-byte memory dumps to reported packet lengths,
valid fd=4 traffic included:

- handshake write:
  12 60 e0 05 fe 67 00 74 68 2d 49 03
- 35-byte handshake/status replies:
  13 e0 3f 05 ... 7e 04 ... 0b 33
- project/list init writes:
  12 60 e0 05 9a 04 00 00 00 20 20 03
  12 60 e0 05 fd 04 00 00 07 00 00 03
- normal-mode remote file paths such as:
  /SP404REMOTE///ROLAND/SP-404MKII/PROJECT_05/SMPL/BANK1-01.SMP

The app is walking the SP's internal project/sample files through the CDC
serial protocol, not mounting a block filesystem. Read-only path requests
return RFWV sample data chunks, so Compa can eventually provide normal-mode
pad/file browsing without USB storage mode.

Important safety note: a broad ad-hoc live sweep of 9e entry indexes caused the
SP USB devices to reset/re-enumerate on the Pi. Keep broad scans inside the
protocol lab until framing/state requirements are better understood.

## Read-only path probe (2026-05-19)

The protocol lab can now reproduce the official app's first read-only remote
sample path request for one known pad:

    tools/sp404_protocol_lab.py read-path --project PROJECT_05 --bank 1 --pad 1
    tools/sp404_protocol_lab.py read-bank --project PROJECT_05 --bank 1

This command sends only the captured app init/list sequence and the captured
read path sequence for:

    /SP404REMOTE///ROLAND/SP-404MKII/PROJECT_05/SMPL/BANK1-01.SMP

Live result on Jordan's SP-404MKII:

- CDC port: /dev/ttyACM0
- project list response: 905 bytes
- file metadata response: 39 bytes
- sample header/data response: 543 bytes
- returned file magic: RFWV
- parsed sample metadata: 48,000 Hz, stereo, 16-bit
- reported payload size: 29,445,620 bytes
- full Bank A scan: 16/16 pads returned RFWV headers in one serial session

This confirms the SP normal-mode librarian protocol can read internal sample
files without USB mass-storage mode. Keep this as read-only lab functionality
until chunk continuation, pad config parsing, and safe UI threading are decoded.

## PADCONF read probe (2026-05-24)

The same normal-mode path reader can open project pad configuration files, but
unlike the first .SMP sample read it must skip the captured sample-file
preamble:

    tools/sp404_protocol_lab.py read-path \
      --path /SP404REMOTE///ROLAND/SP-404MKII/PROJECT_05/PADCONF.BIN \
      --no-preamble

Live result on Jordan's SP-404MKII:

- path-open returned a dynamic file key (01 55 in the observed run)
- the first data chunk contained RFPD
- header fields observed:
  - magic: RFPD
  - header size / pad-table offset: 0xa0 / 160 bytes
  - version/format marker: little-endian 3
  - pad-table bytes: 0x7a80 / 31360
  - known SP pad count: 160
  - implied pad record size: 196 bytes
  - project name appears inside the header block at offset 0x80

Added lab helpers:

    tools/sp404_protocol_lab.py padconf-dump --project PROJECT_05 --label baseline
    tools/sp404_protocol_lab.py padconf-diff before.bin after.bin --pad A01

First setting diff:

- Baseline: A01 Gate was ON.
- Change: Jordan turned A01 Gate OFF on the SP.
- Dump: sessions/sp404_protocol/padconf_PROJECT_05_gate-off-a01_20260524T163433Z.bin
- Diff: one byte changed in A01:
  - record offset 0x13, absolute offset 0x00b3: 01 -> 00
- Working interpretation: A01 Gate is represented by the 32-bit word at record
  offset 0x10, with 00000001 = Gate ON and 00000000 = Gate OFF.
- Confirmation: Jordan turned A01 Gate back ON, captured
  sessions/sp404_protocol/padconf_PROJECT_05_gate-on-confirm-a01_20260524T163714Z.bin.
  Diff from Gate OFF showed the same byte at record+0x13 / abs 0x00b3
  changed 00 -> 01, and diff against the original baseline showed no A01
  pad-record changes. Gate offset is confirmed.

Second setting diff:

- Baseline: A01 Loop was OFF.
- Change: Jordan turned A01 Loop ON on the SP.
- Dump: sessions/sp404_protocol/padconf_PROJECT_05_loop-on-a01_20260524T170921Z.bin
- Diff: four bytes changed in A01:
  - record offset 0x14, absolute offset 0x00b4: 00 -> 7f
  - record offset 0x15, absolute offset 0x00b5: 00 -> ff
  - record offset 0x16, absolute offset 0x00b6: 00 -> ff
  - record offset 0x17, absolute offset 0x00b7: 00 -> ff
- Working interpretation: A01 Loop is represented by the 32-bit word at record
  offset 0x14, with 00000000 = Loop OFF and 7fffffff = Loop ON.
- Confirmation: Jordan turned A01 Loop back OFF, captured
  sessions/sp404_protocol/padconf_PROJECT_05_loop-off-confirm-a01_20260524T172010Z.bin.
  Diff from Loop ON showed record+0x14..0x17 / abs 0x00b4..0x00b7
  changed 7fffffff -> 00000000, and diff against Gate-confirm showed no
  A01 pad-record changes. Loop offset is confirmed.

Third setting diff:

- Baseline: A01 Reverse was OFF.
- Change: Jordan turned A01 Reverse ON on the SP.
- Dump: sessions/sp404_protocol/padconf_PROJECT_05_reverse-on-a01_20260524T172142Z.bin
- Diff: four bytes changed in A01:
  - record offsets 0x2d..0x2f, absolute offsets 0x00cd..0x00cf:
    00 02 00 -> 0e 28 b0, making the 32-bit word at 0x2c change
    00000200 -> 000e28b0
  - record offset 0x3f, absolute offset 0x00df: 00 -> 01, making the
    32-bit word at 0x3c change 00000000 -> 00000001
- Working interpretation: record word 0x3c is likely the Reverse ON/OFF flag.
  The word at 0x2c also changes when reverse is enabled and may be a play
  boundary/cursor value that moves to the sample end.
- Confirmation: Jordan turned A01 Reverse back OFF, captured
  sessions/sp404_protocol/padconf_PROJECT_05_reverse-off-confirm-a01_20260524T172304Z.bin.
  Diff from Reverse ON showed record+0x2d..0x2f / abs 0x00cd..0x00cf
  changed 0e 28 b0 -> 00 02 00 and record+0x3f / abs 0x00df changed
  01 -> 00. Diff against Loop OFF showed no A01 pad-record changes. Treat
  record word 0x3c as the Reverse flag: 00000000 = Reverse OFF and
  00000001 = Reverse ON. Keep record word 0x2c as related playback-boundary
  state unless later captures prove it is independently meaningful.

Fourth setting diff:

- Baseline: A01 Gate ON, Loop OFF, Reverse OFF.
- Change: Jordan turned A01 BPM Sync ON on the SP.
- Dump: sessions/sp404_protocol/padconf_PROJECT_05_bpm-sync-on-a01_20260524T172514Z.bin
- Diff: no A01 pad-record byte changes against Reverse OFF / Loop OFF.
- Follow-up change: Jordan turned A01 BPM Sync back OFF on the SP.
- Dump: sessions/sp404_protocol/padconf_PROJECT_05_bpm-sync-off-confirm-a01_20260524T172645Z.bin
- Diff from BPM Sync ON and from Reverse OFF / Loop OFF:
  - record offset 0x23, absolute offset 0x00c3: 00 -> 01
- Working interpretation: A01 BPM Sync was already ON during the first capture.
  Record byte 0x23 is likely the BPM Sync flag, with 00 = BPM Sync ON and
  01 = BPM Sync OFF.
- Confirmation: Jordan turned A01 BPM Sync back ON, captured
  sessions/sp404_protocol/padconf_PROJECT_05_bpm-sync-on-confirm-a01_20260524T172859Z.bin.
  Diff from BPM Sync OFF showed record+0x23 / abs 0x00c3 changed 01 -> 00.
  BPM Sync offset is confirmed.

Fifth setting diff:

- Baseline for clean comparison: A01 BPM Sync OFF, Bus 1.
- Change: Jordan changed A01 from Bus 1 to Bus 2.
- Dump: sessions/sp404_protocol/padconf_PROJECT_05_bus-2-a01_20260524T173031Z.bin
- Diff against BPM Sync OFF / Bus 1:
  - record offset 0x53, absolute offset 0x00f3: 01 -> 02
- Working interpretation: record byte 0x53 is likely pad bus assignment, with
  01 = Bus 1 and 02 = Bus 2.
- Confirmation: Jordan changed A01 back from Bus 2 to Bus 1, captured
  sessions/sp404_protocol/padconf_PROJECT_05_bus-1-confirm-a01_20260524T173229Z.bin.
  Diff from Bus 2 showed record+0x53 / abs 0x00f3 changed 02 -> 01, and
  diff against the earlier BPM Sync OFF / Bus 1 capture showed no A01
  pad-record changes. Bus assignment offset is confirmed for Bus 1 and Bus 2.
- Note: diff against the immediately previous BPM Sync ON capture also showed
  record+0x23 changed 00 -> 01, meaning BPM Sync was OFF by the time Bus 2 was
  captured. This appears unrelated to the bus byte.
- Jordan could not find a direct SP UI path to assign a pad to Bus 3, Bus 4, or
  Input, so the confirmed per-pad assignment range may be Bus 1/Bus 2 only.

Hold check:

- Change: Jordan held A01, engaged Hold, and released the pad so the SP latched
  playback live.
- Dump: sessions/sp404_protocol/padconf_PROJECT_05_hold-on-a01_20260524T174137Z.bin
- Diff against Bus 1 confirm: no A01 pad-record byte changes.
- Working interpretation: Hold is a live latch state, not a saved A01 PADCONF
  setting. Compa should model Push 2 momentary-vs-latched behavior through the
  saved Gate setting, and treat a future Hold button as a separate live control.

This is enough to start a read-only PADCONF parser. It is not enough yet to
write PADCONF safely or claim exact meanings for Gate/Loop/Reverse/BPM Sync/
Bus/Hold bytes. Next decoding step: capture or read full PADCONF chunks, then
compare before/after files where only one SP pad setting changes.

Implemented read-only parsing in Compa for the confirmed fields present in the
currently available PADCONF chunk:

- Gate: record word 0x10, 00000000 = OFF, 00000001 = ON
- Loop: record word 0x14, 00000000 = OFF, 7fffffff = ON
- BPM Sync: record byte 0x23, 00 = ON, 01 = OFF
- Reverse: record word 0x3c, 00000000 = OFF, 00000001 = ON
- Pad bus: record byte 0x53, 01 = Bus 1, 02 = Bus 2

Push 2 status uses this cache to show the selected SP pad settings when the
record is available. As of this note the normal-mode read yields A01 only;
other pads must stay unknown until PADCONF chunk continuation is decoded.
Push 2 pad release behavior now defaults to auto mode: when a decoded pad has
Gate ON, release sends Note Off and clears the white pad light; when Gate is OFF
or the pad setting is unknown, Compa keeps the one-shot play-through behavior.

Deferred follow-up:

- Jordan confirmed we should pass on grabbing PADCONF for all 160 pads/banks
  right now. Keep the limitation visible: full-pad support requires decoding
  PADCONF chunk continuation or another way to read the complete 31360-byte pad
  table.
- If Push 2 Bus 2 appears to affect Bus 1 pads, first check the SP's own bus
  routing. Jordan found Bus 1 was routed into Bus 2; after changing the SP bus
  routing, the Push 2 bus selector behaved correctly.

Compa integration now exposes this as read-only manual scanning in
Files -> Device -> SP-404 while the SP is connected normally:

- project names come from the normal-mode CDC protocol
- SCAN BANK reads the currently selected bank in a background thread
- scanned pads show RFWV sample metadata in the SP grid
- import/delete/move/backup/restore remain disabled unless a real mass-storage
  mount is present

## Write/import capture (2026-05-19)

Jordan provided a clean Mac mini capture from the official Roland SP-404MKII
App 4.05 importing:

    compa-sp404-write-probe-1s-48k-mono.wav

into:

    PROJECT_02 / Bank A / Pad 2
    /SP404REMOTE///ROLAND/SP-404MKII/PROJECT_02/SMPL/BANK1-02.SMP

The capture can be decoded with:

    tools/sp404_protocol_lab.py parse-dtrace \
      sp404_write_trace_secondpass_20260519T023043Z.txt \
      --only-fd 4 --out secondpass.parsed.jsonl

    tools/sp404_protocol_lab.py write-summary \
      secondpass.parsed.jsonl \
      --wav compa-sp404-write-probe-1s-48k-mono.wav \
      --sample-name compa-sp404-write-probe

Key findings:

- The app targets both the final .SMP path and a temporary .TMP path:

      /SP404REMOTE///ROLAND/SP-404MKII/PROJECT_02/SMPL/BANK1-02.SMP
      /SP404REMOTE///ROLAND/SP-404MKII/PROJECT_02/SMPL/BANK1-02.TMP

- The observed path setup sequence includes op 0x0a checks, one op 0x17
  packet containing both .SMP and .TMP, and op 0x0c/0x0d directory
  open/close-style packets for the SMPL directory.
- Comparing the first-pass A1 import and the second-pass A2 import shows the
  op 0x17 packet is two fixed 200-byte NUL-padded path slots:

      slot0 = final .SMP path
      slot1 = temporary .TMP path

- The source WAV is streamed over the CDC link as byte-swapped 16-bit PCM:
  WAV little-endian samples become big-endian samples on the wire.
- For this 48 kHz mono 1-second WAV, the upload was exactly 96,000 bytes and
  matched the source audio after byte-swapping.
- The audio upload uses op 0x06 data frames with declared lengths that are
  sixteen bytes larger than the raw payload:

      0x5010 -> 20,480 bytes of audio payload
      0x5010 -> 20,480 bytes of audio payload
      0x5010 -> 20,480 bytes of audio payload
      0x5010 -> 20,480 bytes of audio payload
      0x3710 -> 14,080 bytes of audio payload

- After the raw audio upload, the app sends another op 0x06 frame containing
  an RFWV header block:

      RFWV size=96504 sr=48000 ch=1 bits=16

  The size field appears to mean full .SMP file size minus 8 bytes. For this
  import: 96,504 + 8 = 96,512 total bytes, which is the 96,000-byte audio
  payload plus one 512-byte non-audio/header block.
- The sample name compa-sp404-write-probe appears in a later metadata packet
  and in the device response. Comparing the A1/A2 imports shows byte 17 in the
  outgoing sample-name metadata packet changed from 0 to 1, so it is very
  likely the zero-based pad index.
- The op 0x06 audio/header frames carry a command byte that matched the handle
  returned by the preceding target-path op 0x00 response: A1 used handle 0x01,
  A2 used handle 0x05.
- The app then reads the new .SMP back with the same read-style op 0x00,
  0x13, 0x07, 0x04, and 0x03 sequence used by the read-only probe.
- The follow-up read commands must copy the two-byte key returned by the
  path-open response. In responses shaped like:

      f0 41 7a 7a aa bb cc dd ee ...

  the app copies bytes \`dd ee\` into the later 0x13/0x07/0x04/0x03 commands.
  Hardcoded keys from one capture can make an otherwise valid pad read look
  empty or unreadable.

Safety status:

- We have enough information to understand the broad write flow.
- A synced replay of the A2 import flow against PROJECT_05 / Bank A / Pad 2
  successfully persisted the 1-second 48 kHz mono probe sample. Dynamic
  readback reports:

      RFWV size=96504 sr=48000 ch=1 bits=16 duration=1.01s

- Added a gated lab-only `write-pad-template` command. It is still a template
  writer, not a general SP import implementation: it rewrites the verified
  capture for PROJECT_XX / Bank A / a selected pad, patches live dynamic
  handles returned by op 0x00, and only accepts 48 kHz mono 16-bit 1-second
  WAVs. A literal PROJECT_03 test created readable A1-A5 RFWV files, but Jordan
  confirmed they were not visible on the hardware as Project 3 / Bank A.
  Follow-up project-list probing showed PROJECT_03 was not a visible project
  slot at the time. Treat this as an orphan-path lab write until the displayed
  SP project slot is mapped to its librarian PROJECT_XX path.

- The current implementation still keeps normal Compa write/import behavior
  behind lab tooling. Before exposing this as a regular UI action, replace the
  capture-template writer with a first-class writer that generates every
  packet from audio input, supports variable length/stereo where safe, validates
  P-6/SP constraints, and limits writes to explicit user-selected pads.
