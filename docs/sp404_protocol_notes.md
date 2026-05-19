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

This confirms the SP normal-mode librarian protocol can read internal sample
files without USB mass-storage mode. Keep this as read-only lab functionality
until chunk continuation, pad config parsing, and safe UI threading are decoded.
