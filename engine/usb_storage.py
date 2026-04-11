"""USB storage auto-mount and file transfer for MPC/Force Computer Mode.

When an Akai MPC or Force is in Computer Mode, its internal storage
(SD card + SSD) appears as USB mass storage block devices. This module
auto-detects, mounts, and provides file operations for transferring
samples, kits, and projects between Compa and the device.

Works with stock Akai devices — no Machba mod or SSH needed.
"""

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Known Akai USB storage vendors/products
AKAI_STORAGE_IDS = {
    # Genesys Logic card reader (used in Force/MPC for SD card)
    (0x05e3, 0x0743): "sd_card",
    # JMicron SATA bridge (used in Force for internal SSD)
    (0x152d, 0x0578): "internal_ssd",
}

MOUNT_BASE = "/media/pi"


@dataclass
class MountedDrive:
    """Represents one mounted Akai device drive."""
    device: str          # e.g. "/dev/sda1"
    mount_point: str     # e.g. "/media/pi/Force_SD"
    drive_type: str      # "sd_card" or "internal_ssd"
    label: str           # "SD Card" or "Internal SSD"
    total_gb: float = 0.0
    free_gb: float = 0.0

    # Key directories found on the drive
    samples_dir: str = ""
    projects_dir: str = ""


class AkaiStorageManager:
    """Auto-detect, mount, and manage Akai device USB storage."""

    def __init__(self):
        self._drives: list[MountedDrive] = []
        self._last_scan = 0.0
        self._scan_interval = 3.0  # seconds between scans

    @property
    def drives(self) -> list[MountedDrive]:
        return list(self._drives)

    @property
    def is_connected(self) -> bool:
        return len(self._drives) > 0

    @property
    def samples_dir(self) -> str:
        """Best samples directory across all mounted drives."""
        for d in self._drives:
            if d.samples_dir:
                return d.samples_dir
        return ""

    @property
    def projects_dir(self) -> str:
        """Best projects directory across all mounted drives."""
        for d in self._drives:
            if d.projects_dir:
                return d.projects_dir
        return ""

    def scan_and_mount(self) -> list[MountedDrive]:
        """Scan for Akai storage devices — detect mounted + mount new ones.

        Called periodically from the app's update loop.
        Returns list of currently mounted drives.
        """
        now = time.monotonic()
        if now - self._last_scan < self._scan_interval:
            return self._drives
        self._last_scan = now

        known_devices = {d.device for d in self._drives}
        known_mounts = {d.mount_point for d in self._drives}

        # Strategy 1: Check already-mounted partitions for Akai content
        try:
            out = subprocess.run(
                ["lsblk", "-rno", "NAME,SIZE,TYPE,MOUNTPOINT"],
                capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.strip().split("\n"):
                fields = line.split()
                if len(fields) < 4 or fields[2] != "part":
                    continue
                dev_path = f"/dev/{fields[0]}"
                mount_point = fields[3]
                if dev_path in known_devices or not mount_point or mount_point == "/":
                    continue
                if mount_point in known_mounts:
                    continue  # Already tracked this mount point
                if "mmcblk0" in fields[0]:
                    continue  # Skip Pi's own SD card

                # Check if this mount has Akai content
                has_samples = os.path.isdir(os.path.join(mount_point, "Samples"))
                has_projects = os.path.isdir(os.path.join(mount_point, "Projects"))
                has_mockba = os.path.exists(os.path.join(mount_point, "MockbaMod"))
                has_synths = os.path.isdir(os.path.join(mount_point, "Synths"))
                has_addons = os.path.isdir(os.path.join(mount_point, "AddOns"))

                if has_samples or has_projects or has_mockba or has_synths or has_addons:
                    # Determine type by size and content
                    size_str = fields[1]
                    size_gb = 0.0
                    try:
                        if "G" in size_str:
                            size_gb = float(size_str.replace("G", ""))
                        elif "T" in size_str:
                            size_gb = float(size_str.replace("T", "")) * 1024
                    except ValueError:
                        pass

                    if size_gb > 100 or has_projects or has_synths:
                        drive_type = "internal_ssd"
                        label = "Internal SSD"
                    else:
                        drive_type = "sd_card"
                        label = "SD Card"

                    # Get disk space
                    total_gb = free_gb = 0.0
                    try:
                        st = os.statvfs(mount_point)
                        total_gb = round((st.f_blocks * st.f_frsize) / (1024**3), 1)
                        free_gb = round((st.f_bavail * st.f_frsize) / (1024**3), 1)
                    except Exception:
                        pass

                    drive = MountedDrive(
                        device=dev_path,
                        mount_point=mount_point,
                        drive_type=drive_type,
                        label=label,
                        total_gb=total_gb,
                        free_gb=free_gb,
                        samples_dir=os.path.join(mount_point, "Samples") if has_samples else "",
                        projects_dir=os.path.join(mount_point, "Projects") if has_projects else "",
                    )
                    self._drives.append(drive)
                    known_devices.add(dev_path)
                    known_mounts.add(mount_point)
                    log.info("Found Akai %s: %s at %s (%.1fGB free)",
                             label, dev_path, mount_point, free_gb)

        except Exception as e:
            log.debug("Drive scan error: %s", e)

        # Strategy 2: Try to mount unmounted partitions
        new_unmounted = self._detect_unmounted_drives()
        for dev_path, part_path, drive_type in new_unmounted:
            if part_path in known_devices:
                continue
            mount = self._mount_partition(part_path, drive_type)
            if mount:
                self._drives.append(mount)
                known_devices.add(part_path)
                log.info("Mounted Akai %s: %s → %s",
                         mount.label, part_path, mount.mount_point)

        # Remove drives that are no longer mounted or have gone stale
        valid = []
        for d in self._drives:
            if not os.path.ismount(d.mount_point):
                log.info("Drive removed (unmounted): %s", d.mount_point)
                continue
            # Check for I/O errors (stale USB connection)
            try:
                os.listdir(d.mount_point)
                valid.append(d)
            except OSError:
                log.warning("Drive stale (I/O error): %s — removing", d.mount_point)
                try:
                    subprocess.run(["sudo", "umount", "-l", d.mount_point],
                                  timeout=5, capture_output=True)
                except Exception:
                    pass
        self._drives = valid

        return self._drives

    def unmount_all(self):
        """Unmount all Akai storage drives."""
        for d in self._drives:
            try:
                subprocess.run(["sudo", "umount", d.mount_point],
                              timeout=10, capture_output=True)
                log.info("Unmounted: %s", d.mount_point)
            except Exception as e:
                log.error("Unmount failed %s: %s", d.mount_point, e)
        self._drives.clear()

    def list_samples(self, drive_index: int = -1) -> list[dict]:
        """List samples on a drive. Returns [{name, path, size, ext}, ...]."""
        sdir = ""
        if drive_index >= 0 and drive_index < len(self._drives):
            sdir = self._drives[drive_index].samples_dir
        else:
            sdir = self.samples_dir

        if not sdir or not os.path.isdir(sdir):
            return []

        results = []
        for root, dirs, files in os.walk(sdir):
            # Skip deep nesting (ProjectData folders)
            depth = root.replace(sdir, "").count(os.sep)
            if depth > 3:
                continue
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in (".wav", ".mp3", ".aif", ".aiff", ".xpm", ".xpj"):
                    path = os.path.join(root, f)
                    rel = os.path.relpath(path, sdir)
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        size = 0
                    results.append({
                        "name": f,
                        "path": path,
                        "rel_path": rel,
                        "size": size,
                        "ext": ext,
                    })
        return results

    def push_file(self, src_path: str, dest_subdir: str = "") -> Optional[str]:
        """Copy a file to the Akai device's Samples directory.

        Args:
            src_path: Local file to copy.
            dest_subdir: Optional subdirectory within Samples/.

        Returns destination path on success, None on failure.
        """
        sdir = self.samples_dir
        if not sdir:
            log.error("No Akai samples directory mounted")
            return None

        dest_dir = os.path.join(sdir, dest_subdir) if dest_subdir else sdir
        os.makedirs(dest_dir, exist_ok=True)

        dest_path = os.path.join(dest_dir, os.path.basename(src_path))
        try:
            import shutil
            shutil.copy2(src_path, dest_path)
            log.info("Pushed: %s → %s", src_path, dest_path)
            return dest_path
        except Exception as e:
            log.error("Push failed: %s", e)
            return None

    def push_kit(self, kit_dir: str) -> Optional[str]:
        """Copy a kit directory (XPM + samples) to the device.

        Args:
            kit_dir: Local directory containing .xpm and WAV files.

        Returns destination path on success, None on failure.
        """
        sdir = self.samples_dir
        if not sdir:
            log.error("No Akai samples directory mounted")
            return None

        kit_name = os.path.basename(kit_dir)
        dest_dir = os.path.join(sdir, "Compa Kits", kit_name)
        try:
            import shutil
            if os.path.exists(dest_dir):
                shutil.rmtree(dest_dir)
            shutil.copytree(kit_dir, dest_dir)
            log.info("Kit pushed: %s → %s", kit_dir, dest_dir)
            return dest_dir
        except Exception as e:
            log.error("Kit push failed: %s", e)
            return None

    def pull_file(self, src_path: str, dest_dir: str) -> Optional[str]:
        """Copy a file FROM the Akai device to local storage."""
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, os.path.basename(src_path))
        try:
            import shutil
            shutil.copy2(src_path, dest_path)
            log.info("Pulled: %s → %s", src_path, dest_path)
            return dest_path
        except Exception as e:
            log.error("Pull failed: %s", e)
            return None

    # ── Internal methods ─────────────────────────────────────────────

    def _detect_unmounted_drives(self) -> list[tuple[str, str, str]]:
        """Find Akai-related block devices not yet mounted.

        Returns [(device_path, partition_path, drive_type), ...]
        """
        results = []
        already_mounted = {d.device for d in self._drives}

        try:
            # Parse lsblk for unmounted partitions
            out = subprocess.run(
                ["lsblk", "-rno", "NAME,SIZE,TYPE,MOUNTPOINT"],
                capture_output=True, text=True, timeout=5,
            )
            parts = []
            for line in out.stdout.strip().split("\n"):
                fields = line.split()
                if len(fields) >= 3 and fields[2] == "part":
                    dev_path = f"/dev/{fields[0]}"
                    mountpoint = fields[3] if len(fields) > 3 else ""
                    if not mountpoint and dev_path not in already_mounted:
                        # Skip Pi's own SD card and tiny partitions (<1GB)
                        if "mmcblk0" in fields[0]:
                            continue
                        size_str = fields[1]
                        if "M" in size_str:  # Skip MB-sized partitions (boot, EFI)
                            continue
                        parts.append(dev_path)

            # Match partitions to Akai USB devices via udevadm
            for part in parts:
                drive_type = self._identify_akai_drive(part)
                if drive_type:
                    results.append((part, part, drive_type))

        except Exception as e:
            log.debug("Drive detection error: %s", e)

        return results

    def _identify_akai_drive(self, part_path: str) -> str:
        """Check if a partition belongs to an Akai device.

        Returns drive_type string or empty string.
        """
        # Get parent disk
        disk_name = part_path.replace("/dev/", "").rstrip("0123456789")
        if not disk_name:
            return ""

        # Try to read USB vendor/product from sysfs
        try:
            for usb_dir in os.listdir("/sys/bus/usb/devices"):
                vendor_path = f"/sys/bus/usb/devices/{usb_dir}/idVendor"
                product_path = f"/sys/bus/usb/devices/{usb_dir}/idProduct"
                if not os.path.exists(vendor_path):
                    continue
                with open(vendor_path) as f:
                    vendor = int(f.read().strip(), 16)
                with open(product_path) as f:
                    product = int(f.read().strip(), 16)
                key = (vendor, product)
                if key in AKAI_STORAGE_IDS:
                    return AKAI_STORAGE_IDS[key]
        except Exception:
            pass

        # Fallback: check by known sizes (Force SSD ~465GB, SD cards ~32GB)
        try:
            out = subprocess.run(
                ["lsblk", "-rno", "SIZE", part_path],
                capture_output=True, text=True, timeout=5)
            size_str = out.stdout.strip()
            if "G" in size_str:
                size = float(size_str.replace("G", ""))
                if size > 100:
                    return "internal_ssd"
                elif size > 1:
                    return "sd_card"
        except Exception:
            pass

        return ""

    def _mount_partition(self, part_path: str, drive_type: str) -> Optional[MountedDrive]:
        """Mount a partition and identify key directories."""
        # Determine size first to correct drive_type if needed
        try:
            out = subprocess.run(
                ["lsblk", "-rno", "SIZE", part_path],
                capture_output=True, text=True, timeout=5)
            size_str = out.stdout.strip()
            size_gb = 0.0
            if "G" in size_str:
                size_gb = float(size_str.replace("G", ""))
            elif "T" in size_str:
                size_gb = float(size_str.replace("T", "")) * 1024
            if size_gb > 100:
                drive_type = "internal_ssd"
            elif size_gb < 1:
                return None  # Skip tiny partitions
        except Exception:
            pass

        label = "Internal SSD" if drive_type == "internal_ssd" else "SD Card"
        safe_name = f"Force_{'SSD' if 'ssd' in drive_type else 'SD'}"
        mount_point = os.path.join(MOUNT_BASE, safe_name)

        # Already mounted?
        if os.path.ismount(mount_point):
            pass  # Use existing mount
        else:
            os.makedirs(mount_point, exist_ok=True)
            try:
                result = subprocess.run(
                    ["sudo", "mount", part_path, mount_point],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode != 0:
                    log.error("Mount failed %s: %s", part_path, result.stderr)
                    return None
            except Exception as e:
                log.error("Mount error %s: %s", part_path, e)
                return None

        # Identify key directories
        samples_dir = ""
        projects_dir = ""
        for d in ["Samples", "Projects"]:
            path = os.path.join(mount_point, d)
            if os.path.isdir(path):
                if d == "Samples":
                    samples_dir = path
                else:
                    projects_dir = path

        # Get disk space
        total_gb = free_gb = 0.0
        try:
            st = os.statvfs(mount_point)
            total_gb = (st.f_blocks * st.f_frsize) / (1024**3)
            free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
        except Exception:
            pass

        return MountedDrive(
            device=part_path,
            mount_point=mount_point,
            drive_type=drive_type,
            label=label,
            total_gb=round(total_gb, 1),
            free_gb=round(free_gb, 1),
            samples_dir=samples_dir,
            projects_dir=projects_dir,
        )
