# SP-404MKII Librarian Capture Workflow

Goal: capture the official Roland app's normal-mode librarian traffic so Compa
can reproduce read-only project/pad listing before attempting writes.

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
3. Create ~/Desktop/sp404-capture.
4. Create the DTrace script below as ~/Desktop/sp404-capture/sp404_serial_trace.d.
5. Start the trace with:
   sudo dtrace -q -s ~/Desktop/sp404-capture/sp404_serial_trace.d | tee ~/Desktop/sp404-capture/sp404_trace_$(date -u +%Y%m%dT%H%M%SZ).txt
6. Launch the Roland SP-404MKII App.
7. Wait for it to detect the SP, list projects/pads, then click/view one known pad.
8. Stop the trace with Ctrl-C.
9. Save a short notes file describing exactly what you clicked and the app version.
10. Zip ~/Desktop/sp404-capture and report the path.

If DTrace is blocked by macOS security, report the exact error and still save:
- ls -l /dev/cu.usbmodem* /dev/tty.usbmodem*
- ioreg -p IOUSB -l | grep -i -A20 -B5 'SP-404'
- a zip of ~/SP404 User if it exists
~~~~

## DTrace Script

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
    tracemem(copyin(arg1, this->n), this->n);
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
    tracemem(copyin(self->read_buf, this->n), this->n);
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
