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
