"""P-6 librarian — on-device sample management.

Reads, writes, clears, and backs up samples on a Roland AIRA Compact P-6
when it's mounted as USB mass storage (SAMPLING + power-on).

Storage layout on the P-6:
    <mount>/
        info.txt                 — "A-1:\tsample_name\n" for each pad
        IMPORT/
            BANK_A/PAD_1/*.wav
            BANK_A/PAD_2/*.wav
            ...
            BANK_H/PAD_6/*.wav
        SAMPLE/                  — fallback flat sample directory

The mount point is auto-detected by scanning every mounted removable
drive for a P-6 signature (info.txt or IMPORT/BANK_A presence, or a
volume labelled P-6). We do NOT hardcode `/media/pi/P-6` because the
actual path depends on the mount helper and the volume label.

Backup/restore is delegated to engine.p6_image.P6ImageManager — it's
already device-agnostic (despite the name) and runs in a background
thread with progress tracking.
"""

import logging
import os
import shutil
import subprocess
import threading
from typing import Callable, Optional

from engine.p6_image import P6ImageManager
from engine.device_mount import find_or_mount_device, diagnostic_info

log = logging.getLogger(__name__)


def _p6_signature(mount_point: str, label: str) -> bool:
    """Return True if `mount_point` looks like a P-6 mass-storage volume."""
    try:
        if not os.path.isdir(mount_point):
            return False
        # Direct signatures — files/folders the P-6 places at its root
        if os.path.isfile(os.path.join(mount_point, "info.txt")):
            return True
        if os.path.isdir(os.path.join(mount_point, "IMPORT")):
            return True
        if os.path.isdir(os.path.join(mount_point, "SAMPLE")):
            return True
        # Label-based match for a fresh device with nothing loaded
        if label:
            lab = label.upper().replace("-", "").replace("_", "")
            if lab in ("P6", "AIRAP6", "ROLANDP6"):
                return True
    except Exception:
        pass
    return False


class P6Librarian:
    """P-6 on-device librarian."""

    BANKS = "ABCDEFGH"    # 8 banks
    PADS_PER_BANK = 6
    NUM_PADS = 48          # 8 * 6

    def __init__(self, images_dir: str, mount_path: str = ""):
        """Create a librarian.

        `mount_path` is only used as an initial hint or a fixed override
        (mainly for unit tests). At runtime we re-scan every call to
        `is_mounted()` so plug/unplug is tracked automatically.
        """
        self._fixed_mount = mount_path   # override for tests
        self._manual_mount = ""          # user-selected mount (DEBUG panel)
        self._mount_path = mount_path
        self._last_error = ""
        os.makedirs(images_dir, exist_ok=True)
        # Updated on each `is_mounted()` call as the real mount changes
        self._img = P6ImageManager(images_dir, mount_path=mount_path)

    # ── Mount state ──────────────────────────────────────────────────

    @property
    def mount_path(self) -> str:
        return self._mount_path

    @property
    def last_error(self) -> str:
        return self._last_error

    def is_mounted(self) -> bool:
        """Return True if a P-6 is currently mounted. Auto-rescans.

        If no mounted drive matches, tries to actively mount any
        unmounted removable partition and re-check. This handles
        headless Pi setups where auto-mount isn't running.
        """
        # Manual override from the DEBUG panel takes precedence
        if self._manual_mount and os.path.isdir(self._manual_mount):
            self._mount_path = self._manual_mount
            self._img._mount_path = self._manual_mount
            self._last_error = ""
            return True

        # Fixed override (tests) — just check it has P-6 content
        if self._fixed_mount:
            if os.path.isdir(self._fixed_mount) and _p6_signature(
                    self._fixed_mount, ""):
                self._mount_path = self._fixed_mount
                self._img._mount_path = self._fixed_mount
                return True
            return False

        # Live scan + active mount
        found = find_or_mount_device(_p6_signature, mount_name="p6")
        if found is None:
            self._mount_path = ""
            self._img._mount_path = ""
            self._last_error = "No P-6 mount found"
            return False
        self._mount_path = found.mount_point
        self._img._mount_path = found.mount_point
        self._last_error = ""
        return True

    def set_manual_mount(self, mount_point: str):
        """Override auto-detection with a user-selected mount point.

        Called from the DEBUG panel when the user picks a drive from
        the diagnostic list. Pass an empty string to clear the override
        and go back to auto-detection.
        """
        self._manual_mount = mount_point or ""
        if mount_point:
            self._mount_path = mount_point
            self._img._mount_path = mount_point
            log.info("P-6: manual mount override → %s", mount_point)

    def diagnostic(self) -> str:
        """One-liner diagnostic for the UI status line."""
        if self.is_mounted():
            return f"P-6: {self._mount_path}"
        info = diagnostic_info()
        if not info["lsblk_available"]:
            return "P-6: lsblk unavailable — install util-linux?"
        nm = len(info["mounted"])
        nu = len(info["unmounted"])
        if nm == 0 and nu == 0:
            return "P-6: no USB storage detected — hold SAMPLING + power on"
        return f"P-6: seen {nm} mounted + {nu} unmounted — none match signature"

    def diagnostic_lines(self) -> list[str]:
        """Full diagnostic report for the debug modal.

        Lists every partition we saw, whether mounted or not, with
        its label, size, and filesystem. Helps identify what the
        P-6 actually shows up as on the user's Pi.
        """
        lines: list[str] = []

        # Show current mount state first, regardless of lsblk
        if self.is_mounted():
            lines.append(f"CURRENT MOUNT: {self._mount_path}")
            try:
                entries = sorted(os.listdir(self._mount_path))[:12]
                if entries:
                    lines.append(f"  contents: {', '.join(entries)}")
            except Exception:
                pass
            lines.append("")
        else:
            lines.append("P-6 IS NOT DETECTED")
            if self._last_error:
                lines.append(f"  last error: {self._last_error}")
            lines.append("")

        info = diagnostic_info()

        if not info["lsblk_available"]:
            lines.append("ERROR: lsblk not available on this system")
            return lines

        mounted = info["mounted"]
        if mounted:
            lines.append(f"MOUNTED REMOVABLE DRIVES ({len(mounted)}):")
            for m in mounted:
                label = m.label or "(no label)"
                lines.append(f"  {m.device} → {m.mount_point}")
                lines.append(f"    [{label}] {m.size_gb:.0f}G")
                # Show what's in the root
                try:
                    entries = sorted(os.listdir(m.mount_point))[:8]
                    if entries:
                        lines.append(f"    contents: {', '.join(entries)}")
                except Exception:
                    pass
        else:
            lines.append("NO MOUNTED REMOVABLE DRIVES")

        unmounted = info["unmounted"]
        if unmounted:
            lines.append("")
            lines.append(f"UNMOUNTED PARTITIONS ({len(unmounted)}):")
            for p in unmounted:
                label = p.label or "(no label)"
                fs = p.fs_type or "?"
                lines.append(f"  {p.device} [{label}] {p.size} {fs}")

        return lines

    def import_dir(self) -> str:
        if not self._mount_path:
            return ""
        return os.path.join(self._mount_path, "IMPORT")

    # ── Reading assignments ─────────────────────────────────────────

    def read_assignments(self) -> list[Optional[dict]]:
        """Return 48 pad slots — each None or a dict with sample info.

        Merges two data sources:
          - info.txt (tracks what's currently loaded on the P-6)
          - IMPORT/BANK_X/PAD_N/*.wav (tracks pending imports)

        Graceful: if IMPORT/ or info.txt is absent we just return empty
        slots instead of failing — a fresh P-6 may not have either.
        """
        pads: list[Optional[dict]] = [None] * self.NUM_PADS

        if not self.is_mounted():
            return pads

        # Parse info.txt for current device assignments (optional)
        pad_names: dict[str, str] = {}  # "A-1" → sample name
        info_path = os.path.join(self._mount_path, "info.txt")
        if os.path.isfile(info_path):
            try:
                with open(info_path, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if ":" in line and "\t" in line:
                            parts = line.strip().split("\t")
                            if len(parts) >= 2:
                                pad_id = parts[0].strip().rstrip(":")
                                name = parts[1].strip()
                                pad_names[pad_id] = name
            except Exception as e:
                log.warning("Failed to parse info.txt: %s", e)

        # Scan IMPORT/BANK_X/PAD_N for pending WAVs (optional)
        import_dir = self.import_dir()
        has_import = os.path.isdir(import_dir)

        for bi, bank in enumerate(self.BANKS):
            for pi in range(self.PADS_PER_BANK):
                pad_idx = bi * self.PADS_PER_BANK + pi
                pad_id = f"{bank}-{pi + 1}"

                wav_path = None
                if has_import:
                    pad_dir = os.path.join(import_dir, f"BANK_{bank}",
                                           f"PAD_{pi + 1}")
                    if os.path.isdir(pad_dir):
                        for fn in sorted(os.listdir(pad_dir)):
                            if fn.lower().endswith(".wav"):
                                wav_path = os.path.join(pad_dir, fn)
                                break

                current_name = pad_names.get(pad_id, "")

                if wav_path:
                    dur = 0.0
                    size = 0
                    try:
                        import soundfile as sf
                        info = sf.info(wav_path)
                        dur = float(info.duration)
                    except Exception:
                        pass
                    try:
                        size = os.path.getsize(wav_path)
                    except Exception:
                        pass
                    pads[pad_idx] = {
                        "bank": bi,
                        "pad": pi,
                        "bank_letter": bank,
                        "pad_id": pad_id,
                        "filename": os.path.basename(wav_path),
                        "path": wav_path,
                        "duration": dur,
                        "size": size,
                        "on_device": False,
                        "in_import": True,
                    }
                elif current_name:
                    pads[pad_idx] = {
                        "bank": bi,
                        "pad": pi,
                        "bank_letter": bank,
                        "pad_id": pad_id,
                        "filename": current_name,
                        "path": "",
                        "duration": 0.0,
                        "size": 0,
                        "on_device": True,
                        "in_import": False,
                    }

        return pads

    # ── Writing / clearing ──────────────────────────────────────────

    def _pad_dir(self, bank_idx: int, pad_idx: int) -> str:
        """Absolute IMPORT/BANK_X/PAD_N directory for (bank_idx, pad_idx)."""
        bank = self.BANKS[bank_idx]
        return os.path.join(self.import_dir(), f"BANK_{bank}", f"PAD_{pad_idx + 1}")

    def write_pad(self, bank_idx: int, pad_idx: int, src_wav: str) -> Optional[str]:
        """Copy a WAV into the IMPORT folder for the given pad.

        Returns the destination path on success, None on failure. Clears
        any existing WAV in that pad's folder first so the P-6 picks up
        the new one. Creates the IMPORT/BANK_X/PAD_N hierarchy if the
        P-6 hasn't done so yet (e.g. a fresh device).
        """
        if not self.is_mounted():
            self._last_error = "P-6 not mounted"
            log.warning("P-6 not mounted")
            return None
        if not os.path.isfile(src_wav):
            self._last_error = f"Source WAV not found: {os.path.basename(src_wav)}"
            log.warning("Source WAV not found: %s", src_wav)
            return None
        if not 0 <= bank_idx < len(self.BANKS):
            return None
        if not 0 <= pad_idx < self.PADS_PER_BANK:
            return None

        pad_dir = self._pad_dir(bank_idx, pad_idx)
        try:
            # Auto-create the IMPORT root + bank + pad hierarchy if missing
            os.makedirs(pad_dir, exist_ok=True)
        except PermissionError as e:
            self._last_error = f"Permission denied writing to P-6 mount: {e}"
            log.error("Permission denied creating %s: %s", pad_dir, e)
            return None
        except Exception as e:
            self._last_error = f"Can't create pad dir: {e}"
            log.error("Can't create pad dir %s: %s", pad_dir, e)
            return None

        # Clear existing WAV(s) so the new file wins
        try:
            for fn in os.listdir(pad_dir):
                if fn.lower().endswith(".wav"):
                    os.remove(os.path.join(pad_dir, fn))
        except Exception as e:
            log.warning("Couldn't clear old pad files: %s", e)

        # Copy (use basename of source; no rename)
        name = os.path.basename(src_wav)
        dest = os.path.join(pad_dir, name)
        try:
            shutil.copy2(src_wav, dest)
        except Exception as e:
            log.error("Copy failed %s → %s: %s", src_wav, dest, e)
            return None

        self.sync()
        log.info("P-6: wrote %s → bank %s pad %d",
                 name, self.BANKS[bank_idx], pad_idx + 1)
        return dest

    def clear_pad(self, bank_idx: int, pad_idx: int) -> bool:
        """Delete any WAV in the IMPORT folder for this pad."""
        if not self.is_mounted():
            return False
        if not 0 <= bank_idx < len(self.BANKS):
            return False
        if not 0 <= pad_idx < self.PADS_PER_BANK:
            return False

        pad_dir = self._pad_dir(bank_idx, pad_idx)
        if not os.path.isdir(pad_dir):
            return True  # already clear

        try:
            for fn in os.listdir(pad_dir):
                os.remove(os.path.join(pad_dir, fn))
            self.sync()
            return True
        except Exception as e:
            log.error("clear_pad failed: %s", e)
            return False

    def clear_bank(self, bank_idx: int) -> bool:
        """Clear all pads in a bank."""
        ok = True
        for pi in range(self.PADS_PER_BANK):
            if not self.clear_pad(bank_idx, pi):
                ok = False
        return ok

    def clear_all(self) -> bool:
        """Clear every pad in the IMPORT folder (destructive).

        Returns True even if IMPORT/ doesn't exist yet — there's nothing
        to clear on a fresh device, which is success, not failure.
        """
        if not self.is_mounted():
            self._last_error = "P-6 not mounted"
            return False
        import_dir = self.import_dir()
        if not os.path.isdir(import_dir):
            # Nothing to clear — a fresh P-6 has no IMPORT folder yet.
            return True

        try:
            for bank in self.BANKS:
                bank_dir = os.path.join(import_dir, f"BANK_{bank}")
                if not os.path.isdir(bank_dir):
                    continue
                for pi in range(1, self.PADS_PER_BANK + 1):
                    pad_dir = os.path.join(bank_dir, f"PAD_{pi}")
                    if not os.path.isdir(pad_dir):
                        continue
                    for fn in os.listdir(pad_dir):
                        try:
                            os.remove(os.path.join(pad_dir, fn))
                        except Exception as e:
                            log.warning("Couldn't remove %s: %s", fn, e)
            self.sync()
            return True
        except PermissionError as e:
            self._last_error = f"Permission denied — is the P-6 mounted read-only? {e}"
            log.error("clear_all permission denied: %s", e)
            return False
        except Exception as e:
            self._last_error = f"clear_all failed: {e}"
            log.error("clear_all failed: %s", e)
            return False

    def sync(self):
        """Flush FAT writes so pulling the plug can't corrupt the card."""
        try:
            subprocess.run(["sync"], timeout=10)
        except Exception:
            pass

    # ── Backup / restore (delegated to P6ImageManager) ──────────────

    def backup(self, name: str, description: str = "",
               on_complete: Optional[Callable[[bool, str], None]] = None):
        self._img.backup(name, description=description, on_complete=on_complete)

    def restore(self, image_path: str,
                on_complete: Optional[Callable[[bool, str], None]] = None):
        self._img.restore(image_path, on_complete=on_complete)

    def list_images(self) -> list[dict]:
        return self._img.list_images()

    def delete_image(self, image_path: str) -> bool:
        return self._img.delete_image(image_path)

    @property
    def busy(self) -> bool:
        return self._img.busy

    @property
    def progress(self) -> float:
        return self._img.progress

    @property
    def status(self) -> str:
        return self._img.status
