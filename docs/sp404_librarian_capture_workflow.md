# SP-404MKII Librarian Capture Workflow

Goal: capture the official Roland app's normal-mode librarian traffic so Compa
can reproduce read-only project/pad listing before attempting writes.

The first Mac DTrace attempt confirmed the SP-404 USB devices and app version,
but it did not capture serial TX/RX. On this Mac, prefer the DYLD interposer
capture below; keep the DTrace script as a fallback only.

A later adapted DTrace script did capture useful traffic. When DTrace is used,
always parse the trace with the parse-dtrace command so the 512-byte tracemem
dumps are trimmed to each event's reported length.

## What to Capture

Capture one action at a time. Avoid import, delete, restore, or project-write
operations until read-only commands are decoded.

Recommended first capture:

1. Official app launch with the SP-404MKII connected normally.
2. App detects the SP.
3. App lists projects/pads.
4. User clicks/views exactly one known pad.
5. Stop capture.

Record the visible action sequence in a text note beside the trace.

## Mac OpenClaw Prompt

Paste this to the Mac mini OpenClaw agent:

~~~~text
We need a read-only protocol capture from the Roland SP-404MKII App.

Do not import, delete, restore, or write samples. Capture only app launch,
device detection, project/pad list, and one pad view.

Please run these steps on the Mac:

1. Close the Roland SP-404MKII App.
2. Keep the SP-404MKII connected normally by USB.
3. In the Compa repo checkout, run:
   git fetch origin pi-runtime-fixes
   git checkout pi-runtime-fixes
   bash tools/mac_sp404_capture_interpose.sh
4. The script launches the Roland app. Wait for it to detect the SP, list
   projects/pads, then click/view one known pad.
5. Quit the Roland app.
6. Save a short notes file describing exactly what you clicked and the app version.
7. Zip ~/Desktop/sp404-capture and report the path.

If the app refuses to launch, or the log only contains trace_start/trace_stop,
save the exact terminal output and also run:
   codesign -dv --verbose=4 /Applications/Roland/SP-404MKII.app

Still save:
- ls -l /dev/cu.usbmodem* /dev/tty.usbmodem*
- ioreg -p IOUSB -l | grep -i -A20 -B5 'SP-404'
- a zip of ~/SP404 User if it exists
~~~~

## Preferred Mac Capture: DTrace

Use this first when DTrace can see syscall probes. It captures the official app
while leaving the app launch path unchanged.

~~~~bash
sudo dtrace -q -s ~/Desktop/sp404-capture/sp404_serial_trace.d | tee ~/Desktop/sp404-capture/sp404_trace_$(date -u +%Y%m%dT%H%M%SZ).txt
~~~~

Expected useful lines look like:

~~~~text
OPEN pid=2289 exec=SP-404MKII path=/dev/tty.usbmodem11101
TX pid=2289 exec=SP-404MKII fd=4 len=12 captured=12
RX pid=2289 exec=SP-404MKII fd=4 len=35 captured=35
~~~~

Then parse on the Pi:

~~~~bash
tools/sp404_protocol_lab.py parse-dtrace sp404_trace_YYYYMMDDTHHMMSSZ.txt \
  --only-fd 4 \
  --out sessions/sp404_protocol/sp404_app_capture.jsonl
tools/sp404_protocol_lab.py capture-summary sessions/sp404_protocol/sp404_app_capture.jsonl
~~~~

## Alternate Mac Capture: DYLD Interposer

If DTrace is blocked or produces only the startup banner, try the interposer.
It launches the Roland app with a small local library that logs serial-port
open/read/write/ioctl calls as JSONL.

~~~~bash
git fetch origin pi-runtime-fixes
git checkout pi-runtime-fixes
bash tools/mac_sp404_capture_interpose.sh
~~~~

If the log only has trace_start/trace_stop, macOS hardened runtime or app
launch mechanics probably blocked DYLD_INSERT_LIBRARIES. Capture the
codesign -dv --verbose=4 output.

## DTrace Script

This version uses constant-size tracemem copyin calls because some macOS
DTrace builds reject dynamic tracemem sizes. The parser trims each dump back
to the reported captured length.

~~~~d
#pragma D option quiet

dtrace:::BEGIN
{
    printf("SP-404 serial trace started. Launch/use the Roland app now.\n");
}

syscall::*open*:entry
{
    self->path = copyinstr(arg0);
}

syscall::*open*:return
/self->path && (strstr(self->path, "usbmodem") != NULL ||
                strstr(self->path, "tty.usb") != NULL ||
                strstr(self->path, "cu.usb") != NULL)/
{
    watched[pid] = 1;
    printf("\nOPEN pid=%d exec=%s path=%s\n", pid, execname, self->path);
    self->path = 0;
}

syscall::*open*:return
/self->path/
{
    self->path = 0;
}

syscall::*ioctl*:entry
/watched[pid]/
{
    printf("\nIOCTL pid=%d exec=%s fd=%d req=0x%x\n", pid, execname, arg0, arg1);
}

syscall::*write*:entry
/watched[pid] && arg2 > 0/
{
    this->n = arg2 > 512 ? 512 : arg2;
    printf("\nTX pid=%d exec=%s fd=%d len=%d captured=%d\n",
        pid, execname, arg0, arg2, this->n);
    tracemem(copyin(arg1, 512), 512);
}

syscall::*read*:entry
/watched[pid]/
{
    self->read_fd = arg0;
    self->read_buf = arg1;
}

syscall::*read*:return
/watched[pid] && arg0 > 0 && self->read_buf/
{
    this->n = arg0 > 512 ? 512 : arg0;
    printf("\nRX pid=%d exec=%s fd=%d len=%d captured=%d\n",
        pid, execname, self->read_fd, arg0, this->n);
    tracemem(copyin(self->read_buf, 512), 512);
    self->read_fd = 0;
    self->read_buf = 0;
}
~~~~

## Pi-Side Comparison

On the Pi, use:

~~~~bash
tools/sp404_protocol_lab.py probe --count 5
tools/sp404_protocol_lab.py analyze sessions/sp404_protocol/<log>.jsonl
~~~~

Once we have Mac app TX/RX bytes, only replay captured read-only commands with:

~~~~bash
tools/sp404_protocol_lab.py send-hex "<captured hex>" \
  --label list-projects-readonly \
  --i-understand-this-can-write-to-the-sp
~~~~

Do not replay import/delete/write commands until the command layer and backup
path are understood.
