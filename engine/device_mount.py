"""Dynamic removable-drive mount detection.

Scans every mounted partition on the system and returns the mount points
of removable drives (anything that's NOT the Pi's own SD card boot). The
librarians use this to find the Roland P-6 and SP-404 MK2 USB mass
storage mount points without hardcoding a path like `/media/pi/P-6`.

On a Raspberry Pi the same device can land at different paths depending
on udev/autofs config:
    /media/pi/<LABEL>
    /media/<user>/<LABEL>
    /run/media/<user>/<LABEL>
    /mnt/<anything>

We don't care — if it's mounted and it has the signature we expect,
we use it.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class RemovableMount:
    """A single mounted removable partition."""
    device: str           # e.g. /dev/sda1
    mount_point: str      # e.g. /media/pi/P-6
    label: str            # volume label (may be empty)
    size_gb: float = 0.0


def list_removable_mounts() -> list[RemovableMount]:
    """Enumerate every mounted removable partition.

    Primary strategy: `lsblk -rno NAME,SIZE,TYPE,MOUNTPOINT,LABEL`.
    Fallback: scan /media/<user>/, /run/media/<user>/, /mnt/ for
    subdirectories (for distros that don't ship lsblk or populate it
    oddly). Always filters out the Pi's own SD card and system mounts.
    """
    mounts: list[RemovableMount] = []
    seen_paths: set[str] = set()

    # Primary: lsblk
    try:
        out = subprocess.run(
            ["lsblk", "-rno", "NAME,SIZE,TYPE,MOUNTPOINT,LABEL"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            name = parts[0]
            size = parts[1]
            ptype = parts[2]
            mountpoint = parts[3] if len(parts) >= 4 else ""
            label = parts[4] if len(parts) >= 5 else ""

            if ptype != "part":
                continue
            if not mountpoint:
                continue
            if mountpoint in ("/", "/boot", "/boot/firmware"):
                continue
            if mountpoint.startswith("/boot/"):
                continue
            if name.startswith("mmcblk0"):
                continue

            size_gb = _parse_size_gb(size)
            mounts.append(RemovableMount(
                device=f"/dev/{name}",
                mount_point=mountpoint,
                label=label.strip(),
                size_gb=size_gb,
            ))
            seen_paths.add(mountpoint)
    except Exception as e:
        log.debug("lsblk failed: %s", e)

    # Fallback: /media and /run/media subdirs
    fallback_roots = [
        "/media/pi",
        "/media/compa",
        "/run/media/pi",
        "/run/media/compa",
        "/mnt",
    ]
    # Also enumerate /media/* for any user
    try:
        if os.path.isdir("/media"):
            for user in os.listdir("/media"):
                p = os.path.join("/media", user)
                if os.path.isdir(p):
                    fallback_roots.append(p)
    except Exception:
        pass
    try:
        if os.path.isdir("/run/media"):
            for user in os.listdir("/run/media"):
                p = os.path.join("/run/media", user)
                if os.path.isdir(p):
                    fallback_roots.append(p)
    except Exception:
        pass

    for root in fallback_roots:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                candidate = os.path.join(root, entry)
                if candidate in seen_paths:
                    continue
                if not os.path.isdir(candidate):
                    continue
                # Must actually be a mount point (not just an empty dir)
                if not os.path.ismount(candidate):
                    # Still track it — some automounts put content in subdirs
                    # without marking the subdir as a mount point
                    try:
                        if not os.listdir(candidate):
                            continue
                    except Exception:
                        continue
                mounts.append(RemovableMount(
                    device="",
                    mount_point=candidate,
                    label=entry,
                    size_gb=0.0,
                ))
                seen_paths.add(candidate)
        except Exception as e:
            log.debug("Fallback scan of %s failed: %s", root, e)

    return mounts


def _parse_size_gb(size: str) -> float:
    """Parse an lsblk SIZE field like '32G' or '256M' into GB."""
    try:
        if not size:
            return 0.0
        if size.endswith("T"):
            return float(size[:-1]) * 1024
        if size.endswith("G"):
            return float(size[:-1])
        if size.endswith("M"):
            return float(size[:-1]) / 1024
        if size.endswith("K"):
            return float(size[:-1]) / (1024 * 1024)
        return float(size)
    except Exception:
        return 0.0


def find_device_mount(
    signature_check: Callable[[str, str], bool],
) -> Optional[RemovableMount]:
    """Return the first removable mount that matches `signature_check`.

    `signature_check(mount_point, label)` should return True when the
    filesystem at `mount_point` looks like the device we're looking for.
    """
    for mount in list_removable_mounts():
        try:
            if signature_check(mount.mount_point, mount.label):
                return mount
        except Exception as e:
            log.debug("signature check on %s failed: %s", mount.mount_point, e)
    return None


def list_mount_candidates_debug() -> list[str]:
    """Return human-readable strings describing every removable mount.

    Useful for diagnostic status lines in the UI — tells the user what
    we saw even when nothing matched.
    """
    lines = []
    for m in list_removable_mounts():
        label = m.label or "(no label)"
        lines.append(f"{m.device} → {m.mount_point} [{label}] {m.size_gb:.0f}G")
    return lines
