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
