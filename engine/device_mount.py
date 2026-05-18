"""Dynamic removable-drive mount detection + active mounting.

On a headless Raspberry Pi without a desktop environment, USB mass
storage devices don't automatically get mounted when plugged in. This
module handles that case:

1. list_removable_mounts() — already-mounted removable partitions
2. list_unmounted_partitions() — plugged-in partitions that need mounting
3. active_mount_partition() — mount a partition to a chosen path
4. find_or_mount_device() — high-level "get me a mount point matching
   this signature, creating one if needed"

This is what AkaiStorageManager does internally for MPC/Force. We
extract the same pattern so the P-6 and SP-404 librarians can piggyback
on it without depending on distro-specific auto-mount helpers.

The librarians use this to find the Roland P-6 and SP-404 MK2 USB mass
storage mount points without hardcoding a path like `/media/pi/P-6`.
"""

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

_LSBLK_PAIR_RE = re.compile(r'(\w+)="([^"]*)"')
_LAST_MOUNT_ERRORS: dict[str, str] = {}
_LAST_MOUNT_ATTEMPTS: dict[str, float] = {}
_MOUNT_RETRY_AFTER = 8.0


@dataclass
class RemovableMount:
    """A single mounted removable partition."""
    device: str           # e.g. /dev/sda1
    mount_point: str      # e.g. /media/pi/P-6
    label: str            # volume label (may be empty)
    size_gb: float = 0.0


@dataclass
class Partition:
    """A block-device partition on the system (may or may not be mounted)."""
    device: str           # /dev/sda1
    name: str             # sda1
    size: str             # "32G"
    size_gb: float
    mountpoint: str       # "" if unmounted
    label: str
    fs_type: str          # vfat, exfat, etc


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
            ["lsblk", "-Pno", "NAME,SIZE,TYPE,MOUNTPOINT,LABEL"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            fields = _parse_lsblk_pairs(line)
            if not fields:
                continue
            name = fields.get("NAME", "")
            size = fields.get("SIZE", "")
            ptype = fields.get("TYPE", "")
            mountpoint = fields.get("MOUNTPOINT", "")
            label = fields.get("LABEL", "")

            if ptype not in ("part", "disk"):
                continue
            if not mountpoint:
                continue
            if mountpoint in ("/", "/boot", "/boot/firmware"):
                continue
            if mountpoint.startswith("/boot/"):
                continue
            if name.startswith("mmcblk0"):
                continue
            if name.startswith("zram") or name.startswith("loop"):
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


# ── Partition enumeration (mounted + unmounted) ────────────────────


def list_all_partitions() -> list[Partition]:
    """Enumerate every partition OR partitionless disk via lsblk.

    Includes both TYPE=part and TYPE=disk entries. The P-6 exposes its
    internal storage as a raw disk without a partition table, so a
    partition-only filter misses it entirely.

    Skips the Pi's own SD card (mmcblk0*), the root partition, /boot/*
    entries, zram/loop devices, and tiny unlabeled stubs. Tiny labeled
    volumes are kept because some Roland devices expose small FAT disks.
    """
    results: list[Partition] = []
    try:
        out = subprocess.run(
            ["lsblk", "-Pno", "NAME,SIZE,TYPE,MOUNTPOINT,LABEL,FSTYPE"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        log.debug("lsblk failed: %s", e)
        return results

    # Track which disks have partitions — if a disk has at least one
    # partition child, we prefer the partitions and skip the disk itself.
    disks_with_parts: set[str] = set()
    raw_entries: list[tuple[str, str, str, str, str, str]] = []

    for line in out.stdout.splitlines():
        fields = _parse_lsblk_pairs(line)
        if not fields:
            continue
        name = fields.get("NAME", "")
        size = fields.get("SIZE", "")
        ptype = fields.get("TYPE", "")
        mountpoint = fields.get("MOUNTPOINT", "")
        label = fields.get("LABEL", "")
        fs_type = fields.get("FSTYPE", "")
        raw_entries.append((name, size, ptype, mountpoint, label, fs_type))

        if ptype == "part":
            # Derive parent disk name: sda1 → sda, nvme0n1p1 → nvme0n1
            parent = _parent_disk(name)
            if parent:
                disks_with_parts.add(parent)

    for name, size, ptype, mountpoint, label, fs_type in raw_entries:
        if ptype not in ("part", "disk"):
            continue
        if name.startswith("mmcblk0"):
            continue  # Pi's own SD
        if name.startswith("zram") or name.startswith("loop"):
            continue
        if mountpoint in ("/", "/boot", "/boot/firmware"):
            continue
        if mountpoint.startswith("/boot/"):
            continue
        # Skip disks that have partition children — those children are
        # listed separately above.
        if ptype == "disk" and name in disks_with_parts:
            continue

        size_gb = _parse_size_gb(size)
        # Skip tiny unlabeled stubs, but keep explicitly-labelled removable
        # media. Some Roland devices expose very small FAT volumes in storage
        # mode, so a hard 256MB cutoff hides the real target device.
        if size_gb < 0.25 and not label.strip():
            continue

        results.append(Partition(
            device=f"/dev/{name}",
            name=name,
            size=size,
            size_gb=size_gb,
            mountpoint=mountpoint,
            label=label.strip(),
            fs_type=fs_type,
        ))

    return results


def _parent_disk(part_name: str) -> str:
    """Return the parent disk for a partition name ('sda1' → 'sda')."""
    # Simple heuristic: strip trailing digits (sda1 → sda)
    stripped = part_name.rstrip("0123456789")
    # nvme0n1p1 → nvme0n1 (rstrip leaves 'nvme0n1p', trim trailing 'p')
    if stripped.endswith("p"):
        stripped = stripped[:-1]
    return stripped or part_name


def _parse_lsblk_pairs(line: str) -> dict[str, str]:
    """Parse one lsblk -P line into a field dict."""
    return {k: v for k, v in _LSBLK_PAIR_RE.findall(line)}


def list_unmounted_partitions() -> list[Partition]:
    """Return only unmounted partitions (good candidates for active mount)."""
    return [p for p in list_all_partitions() if not p.mountpoint]


# ── Active mounting ────────────────────────────────────────────────

# Where we create our own mount points (owned by Compa)
COMPA_MOUNT_BASE = "/mnt/compa"
COMPA_MOUNT_HELPER = "/usr/local/sbin/compa-storage-mount"


def active_mount_partition(
    part: Partition,
    mount_name: str = "",
    force: bool = False,
) -> Optional[str]:
    """Mount an unmounted partition to /mnt/compa/<name>.

    Returns the mount point on success, None on failure.
    Uses sudo — Compa runs as `pi` user but the unit file has the
    correct passwordless sudo for mount/umount.
    """
    if part.mountpoint:
        return part.mountpoint  # already mounted

    now = time.monotonic()
    last_attempt = _LAST_MOUNT_ATTEMPTS.get(part.device, 0.0)
    if not force and now - last_attempt < _MOUNT_RETRY_AFTER:
        return None
    _LAST_MOUNT_ATTEMPTS[part.device] = now

    _clear_mount_error(part.device)

    # Pick a target path
    if not mount_name:
        mount_name = part.label or part.name
    # Sanitize
    safe = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in mount_name) or part.name
    target = os.path.join(COMPA_MOUNT_BASE, safe)
    helper_missing = not os.path.exists(COMPA_MOUNT_HELPER)

    # Prefer the dedicated root-owned helper installed by setup/install.sh.
    # It validates the target block device before mounting, and the sudoers
    # rule can safely grant only this helper instead of broad mount access.
    helper_result = _mount_with_helper(part, safe)
    if helper_result:
        return helper_result

    # Desktop/session installs may allow udisksctl via Polkit. The systemd
    # appliance service usually cannot use this because it has no auth agent,
    # but it is useful when running Compa manually from a login session.
    udisks_result = _mount_with_udisks(part)
    if udisks_result:
        return udisks_result

    # Legacy fallback: direct sudo mount. Use -n so the service never blocks
    # waiting for an interactive password prompt.
    # Make the parent dir (we own /mnt/compa/ after first mount)
    try:
        subprocess.run(
            ["sudo", "-n", "mkdir", "-p", target],
            capture_output=True, text=True, timeout=5,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or str(e)).strip()
        if helper_missing and "password" in msg.lower():
            _set_mount_error(
                part.device,
                "Compa storage mount helper is not installed — run sudo bash setup/install-storage-helper.sh",
            )
        else:
            _set_mount_error(part.device, f"mkdir failed: {msg}")
        log.error("mkdir %s: %s", target, msg)
        return None
    except Exception as e:
        _set_mount_error(part.device, f"mkdir exception: {e}")
        log.error("mkdir %s: %s", target, e)
        return None

    # Attempt the mount. We force uid/gid 1000 (pi) so we can write
    # without sudo on every operation.
    mount_opts = "uid=1000,gid=1000,fmask=0000,dmask=0000"
    try:
        result = subprocess.run(
            ["sudo", "-n", "mount", "-o", mount_opts, part.device, target],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            # Some filesystems reject uid/gid options (e.g. ext4)
            log.info("Mount with options failed (%s), retrying plain",
                     result.stderr.strip())
            result = subprocess.run(
                ["sudo", "-n", "mount", part.device, target],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                msg = result.stderr.strip() or result.stdout.strip()
                if helper_missing and "password" in msg.lower():
                    _set_mount_error(
                        part.device,
                        "Compa storage mount helper is not installed — run sudo bash setup/install-storage-helper.sh",
                    )
                else:
                    _set_mount_error(part.device, f"mount failed: {msg}")
                log.error("Plain mount also failed: %s", msg)
                return None
    except Exception as e:
        _set_mount_error(part.device, f"mount exception: {e}")
        log.error("Mount exception %s: %s", part.device, e)
        return None

    _clear_mount_error(part.device)
    log.info("Mounted %s → %s", part.device, target)
    return target


def _mount_with_helper(part: Partition, mount_name: str) -> Optional[str]:
    """Mount through the narrow Compa sudo helper when installed."""
    if not os.path.exists(COMPA_MOUNT_HELPER):
        return None
    try:
        result = subprocess.run(
            ["sudo", "-n", COMPA_MOUNT_HELPER, "mount", part.device, mount_name],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as e:
        _set_mount_error(part.device, f"mount helper exception: {e}")
        log.error("Mount helper exception %s: %s", part.device, e)
        return None

    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        if "password" in msg.lower():
            _set_mount_error(
                part.device,
                "Compa storage mount helper is installed but sudoers is not active — run sudo bash setup/install-storage-helper.sh",
            )
        else:
            _set_mount_error(part.device, f"mount helper failed: {msg}")
        log.error("Mount helper failed for %s: %s", part.device, msg)
        return None

    mount_point = result.stdout.strip().splitlines()[-1].strip()
    if mount_point and os.path.isdir(mount_point):
        _clear_mount_error(part.device)
        log.info("Mounted %s via helper → %s", part.device, mount_point)
        return mount_point

    mount_point = _mountpoint_for_device(part.device)
    if mount_point:
        _clear_mount_error(part.device)
        return mount_point

    _set_mount_error(part.device, "mount helper returned no mount point")
    return None


def _mount_with_udisks(part: Partition) -> Optional[str]:
    """Try user-session mounting via udisksctl."""
    try:
        result = subprocess.run(
            ["udisksctl", "mount", "-b", part.device],
            capture_output=True, text=True, timeout=20,
        )
    except FileNotFoundError:
        return None
    except Exception as e:
        _set_mount_error(part.device, f"udisksctl exception: {e}")
        log.debug("udisksctl exception %s: %s", part.device, e)
        return None

    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        _set_mount_error(part.device, f"udisksctl failed: {msg}")
        log.debug("udisksctl failed for %s: %s", part.device, msg)
        return None

    # Typical output: "Mounted /dev/sda at /media/pi/P-6."
    match = re.search(r" at (.+?)\.?$", result.stdout.strip())
    if match:
        mount_point = match.group(1).rstrip(".")
        if os.path.isdir(mount_point):
            _clear_mount_error(part.device)
            log.info("Mounted %s via udisksctl → %s", part.device, mount_point)
            return mount_point

    mount_point = _mountpoint_for_device(part.device)
    if mount_point:
        _clear_mount_error(part.device)
        return mount_point

    _set_mount_error(part.device, "udisksctl returned no mount point")
    return None


def _mountpoint_for_device(device: str) -> str:
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", "-S", device],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return ""


def _set_mount_error(device: str, message: str):
    _LAST_MOUNT_ERRORS[device] = " ".join(message.split())[:220]


def _clear_mount_error(device: str):
    _LAST_MOUNT_ERRORS.pop(device, None)


def get_mount_errors() -> dict[str, str]:
    return dict(_LAST_MOUNT_ERRORS)


def unmount_partition(mount_point: str) -> bool:
    """Unmount a path (must be under /mnt/compa or another owned path)."""
    if mount_point.startswith(COMPA_MOUNT_BASE + "/") and os.path.exists(COMPA_MOUNT_HELPER):
        try:
            result = subprocess.run(
                ["sudo", "-n", COMPA_MOUNT_HELPER, "umount", mount_point],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return True
            log.error("helper umount %s failed: %s",
                      mount_point, result.stderr.strip())
        except Exception as e:
            log.error("helper umount %s: %s", mount_point, e)

    try:
        result = subprocess.run(
            ["sudo", "-n", "umount", mount_point],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception as e:
        log.error("umount %s: %s", mount_point, e)
        return False


def find_or_mount_device(
    signature_check: Callable[[str, str], bool],
    mount_name: str = "compa",
    fallback_check: Optional[Callable[[str, str], bool]] = None,
) -> Optional[RemovableMount]:
    """Find a mounted match OR actively mount an unmounted candidate.

    Workflow:
      1. Scan already-mounted removable drives. Return the first that
         satisfies ``signature_check``.
      2. If none match, try ``fallback_check`` on those same mounted
         drives (useful for "any drive that isn't an MPC/Force").
      3. Try mounting each unmounted partition and re-check both the
         primary and fallback signatures.
      4. If nothing matches, return None.
    """
    already_mounted = list_removable_mounts()

    # Step 1: primary signature on mounted drives
    for mount in already_mounted:
        try:
            if signature_check(mount.mount_point, mount.label):
                return mount
        except Exception as e:
            log.debug("sig check %s: %s", mount.mount_point, e)

    # Step 2: fallback signature on mounted drives
    if fallback_check is not None:
        for mount in already_mounted:
            try:
                if fallback_check(mount.mount_point, mount.label):
                    log.info("Fallback match on mounted drive: %s",
                             mount.mount_point)
                    return mount
            except Exception as e:
                log.debug("fallback check %s: %s", mount.mount_point, e)

    # Step 3: actively mount unmounted candidates
    for part in list_unmounted_partitions():
        log.info("Trying to mount %s (%s) to probe for device",
                 part.device, part.label or "no label")
        mp = active_mount_partition(part, mount_name=part.label or mount_name)
        if not mp:
            continue
        try:
            if signature_check(mp, part.label):
                log.info("Match found: %s → %s", part.device, mp)
                return RemovableMount(
                    device=part.device,
                    mount_point=mp,
                    label=part.label,
                    size_gb=part.size_gb,
                )
            if fallback_check is not None and fallback_check(mp, part.label):
                log.info("Fallback match after mount: %s → %s",
                         part.device, mp)
                return RemovableMount(
                    device=part.device,
                    mount_point=mp,
                    label=part.label,
                    size_gb=part.size_gb,
                )
            # Didn't match — unmount so we don't leave garbage behind
            unmount_partition(mp)
        except Exception as e:
            log.debug("sig check (active) %s: %s", mp, e)

    return None


def diagnostic_info() -> dict:
    """Return everything we know about the current block-device state.

    Used by the librarian UI's DEBUG/SCAN button to show the user what
    Compa sees. Returns:
      {
        "mounted": [RemovableMount, ...],
        "unmounted": [Partition, ...],
        "all_partitions": [Partition, ...],
        "mount_errors": {"/dev/sdX": "last failure"},
        "lsblk_available": bool,
        "lsblk_raw": str,           # raw lsblk output
        "lsusb_raw": str,           # raw lsusb output (Roland filter)
        "dev_sd_list": list[str],   # `ls /dev/sd*`
      }
    """
    try:
        subprocess.run(["lsblk", "--version"],
                       capture_output=True, timeout=3, check=True)
        lsblk_ok = True
    except Exception:
        lsblk_ok = False

    lsblk_raw = ""
    try:
        out = subprocess.run(
            ["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,LABEL,FSTYPE"],
            capture_output=True, text=True, timeout=5,
        )
        lsblk_raw = out.stdout
    except Exception as e:
        lsblk_raw = f"(lsblk error: {e})"

    lsusb_raw = ""
    try:
        out = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=5,
        )
        lsusb_raw = out.stdout
    except Exception as e:
        lsusb_raw = f"(lsusb error: {e})"

    dev_sd = []
    try:
        for entry in sorted(os.listdir("/dev")):
            if entry.startswith("sd") or entry.startswith("mmcblk1"):
                dev_sd.append(f"/dev/{entry}")
    except Exception as e:
        dev_sd = [f"(/dev scan error: {e})"]

    return {
        "mounted": list_removable_mounts(),
        "unmounted": list_unmounted_partitions(),
        "all_partitions": list_all_partitions(),
        "mount_errors": get_mount_errors(),
        "lsblk_available": lsblk_ok,
        "lsblk_raw": lsblk_raw,
        "lsusb_raw": lsusb_raw,
        "dev_sd_list": dev_sd,
    }
