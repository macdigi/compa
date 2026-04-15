"""P-6 librarian — on-device sample management.

Reads, writes, clears, and backs up samples on a Roland AIRA Compact P-6
when it's mounted as USB mass storage (SAMPLING + power-on).

Storage layout on the P-6:
    /media/pi/P-6/
        info.txt                 — "A-1:\tsample_name\n" for each pad
        IMPORT/
            BANK_A/PAD_1/*.wav
            BANK_A/PAD_2/*.wav
            ...
            BANK_H/PAD_6/*.wav
        SAMPLE/                  — fallback flat sample directory

The P-6 reads the IMPORT/ folders on the next "IMPORT" action (or on
reconnect, depending on mode). Dropping a WAV into IMPORT/BANK_X/PAD_N/
assigns it to that pad.

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

log = logging.getLogger(__name__)


class P6Librarian:
    """P-6 on-device librarian."""

    MOUNT_PATH = "/media/pi/P-6"
    BANKS = "ABCDEFGH"    # 8 banks
    PADS_PER_BANK = 6
    NUM_PADS = 48          # 8 * 6

    def __init__(self, images_dir: str, mount_path: str = ""):
        self._mount_path = mount_path or self.MOUNT_PATH
        os.makedirs(images_dir, exist_ok=True)
        self._img = P6ImageManager(images_dir, mount_path=self._mount_path)

    # ── Mount state ──────────────────────────────────────────────────

    @property
    def mount_path(self) -> str:
        return self._mount_path

    def is_mounted(self) -> bool:
        """True if the P-6 is currently mounted at mount_path."""
        p = self._mount_path
        if not os.path.isdir(p):
            return False
        # Must have an IMPORT dir or info.txt to count as a P-6 mount
        return (os.path.isdir(os.path.join(p, "IMPORT"))
                or os.path.isfile(os.path.join(p, "info.txt")))

    def import_dir(self) -> str:
        return os.path.join(self._mount_path, "IMPORT")

    # ── Reading assignments ─────────────────────────────────────────

    def read_assignments(self) -> list[Optional[dict]]:
        """Return 48 pad slots — each None or a dict with sample info.

        Merges two data sources:
          - info.txt (tracks what's currently loaded on the P-6)
          - IMPORT/BANK_X/PAD_N/*.wav (tracks pending imports)
        """
        pads: list[Optional[dict]] = [None] * self.NUM_PADS

        if not self.is_mounted():
            return pads

        # Parse info.txt for current device assignments
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

        # Scan IMPORT/BANK_X/PAD_N for pending WAVs
        import_dir = self.import_dir()

        for bi, bank in enumerate(self.BANKS):
            for pi in range(self.PADS_PER_BANK):
                pad_idx = bi * self.PADS_PER_BANK + pi
                pad_id = f"{bank}-{pi + 1}"
                pad_dir = os.path.join(import_dir, f"BANK_{bank}", f"PAD_{pi + 1}")

                wav_path = None
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
        the new one.
        """
        if not self.is_mounted():
            log.warning("P-6 not mounted")
            return None
        if not os.path.isfile(src_wav):
            log.warning("Source WAV not found: %s", src_wav)
            return None
        if not 0 <= bank_idx < len(self.BANKS):
            return None
        if not 0 <= pad_idx < self.PADS_PER_BANK:
            return None

        pad_dir = self._pad_dir(bank_idx, pad_idx)
        try:
            os.makedirs(pad_dir, exist_ok=True)
        except Exception as e:
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
        """Clear every pad in the IMPORT folder (destructive)."""
        if not self.is_mounted():
            return False
        import_dir = self.import_dir()
        if not os.path.isdir(import_dir):
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
        except Exception as e:
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
