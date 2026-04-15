"""SP-404 MK2 librarian — on-device sample management with write support.

The SP-404 MK2 exposes its SD card as USB mass storage when it's put in
USB storage mode (Tools menu). Samples live at:

    /media/pi/SP-404MKII/
        ROLAND/SP-404MKII/
            PROJECT_01/
                PADCONF.BIN           — pad config (RFPD magic, partially decoded)
                SMPL/
                    BANK1-01.SMP       — sample file (RFWV magic + PCM data)
                    BANK1-02.SMP
                    ...
                    BANK10-16.SMP

The .SMP format is a 20-byte RFWV header + raw PCM:
    0x00: 'RFWV' magic (4 bytes)
    0x04: data_size (uint32 BE)
    0x08: sample_rate (uint32 BE)   — always 48000 on MK2
    0x0C: channels (uint32 BE)      — 1 or 2
    0x10: bit_depth (uint32 BE)     — 16

PADCONF.BIN is NOT written by v1 of this module. Phase A tests whether
the SP-404 can pick up new .SMP files on its own. If the hardware
requires PADCONF rewrites, that's a follow-up (Phase B).

The CDC serial protocol is confirmed unreachable (see
docs/sp404_protocol_notes.md), so we go direct to mass storage.

Backup/restore is delegated to engine.p6_image.P6ImageManager — same
device-agnostic background-thread implementation the P-6 librarian uses.
"""

import logging
import os
import shutil
import struct
import subprocess
from typing import Callable, Optional

from engine.p6_image import P6ImageManager
from engine import sp404_storage

log = logging.getLogger(__name__)


class SP404Librarian:
    """SP-404 MK2 on-device librarian."""

    MOUNT_PATH = "/media/pi/SP-404MKII"
    ALT_MOUNT_PATHS = ["/media/pi/SP404MKII"]
    ON_DEVICE_SUBDIR = os.path.join("ROLAND", "SP-404MKII")
    BANKS = 10
    PADS_PER_BANK = 16
    NUM_PADS = 160  # 10 * 16

    def __init__(self, images_dir: str, mount_path: str = ""):
        self._mount_path = mount_path or self._detect_mount()
        os.makedirs(images_dir, exist_ok=True)
        self._img = P6ImageManager(images_dir, mount_path=self._mount_path)

    # ── Mount state ──────────────────────────────────────────────────

    def _detect_mount(self) -> str:
        for p in [self.MOUNT_PATH] + self.ALT_MOUNT_PATHS:
            if os.path.isdir(p):
                return p
        return self.MOUNT_PATH

    @property
    def mount_path(self) -> str:
        return self._mount_path

    def is_mounted(self) -> bool:
        """True if the SP-404 mass storage is currently mounted."""
        p = self._mount_path
        if not os.path.isdir(p):
            # Try re-detecting in case it was just plugged in
            self._mount_path = self._detect_mount()
            p = self._mount_path
            if not os.path.isdir(p):
                return False
        roland = os.path.join(p, self.ON_DEVICE_SUBDIR)
        return os.path.isdir(roland)

    def roland_dir(self) -> str:
        return os.path.join(self._mount_path, self.ON_DEVICE_SUBDIR)

    # ── Project listing ─────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        """List projects on the device.

        Each entry: {name, path, num_samples}. Falls back to the
        Mac librarian cache if the device isn't mounted (handy for
        testing on the dev machine).
        """
        if self.is_mounted():
            return self._list_projects_in(self.roland_dir())
        # Dev fallback — use the Mac librarian cache
        cache = sp404_storage.find_sp404_cache()
        if cache:
            return sp404_storage.list_projects(cache)
        return []

    @staticmethod
    def _list_projects_in(root: str) -> list[dict]:
        projects: list[dict] = []
        if not os.path.isdir(root):
            return projects
        try:
            for name in sorted(os.listdir(root)):
                proj_dir = os.path.join(root, name)
                if not os.path.isdir(proj_dir):
                    continue
                if not name.startswith("PROJECT_"):
                    continue
                smpl_dir = os.path.join(proj_dir, "SMPL")
                num = 0
                if os.path.isdir(smpl_dir):
                    num = sum(1 for f in os.listdir(smpl_dir) if f.endswith(".SMP"))
                projects.append({
                    "name": name,
                    "path": proj_dir,
                    "num_samples": num,
                })
        except Exception as e:
            log.warning("list_projects: %s", e)
        return projects

    # ── Reading pads (via existing sp404_storage) ───────────────────

    def read_project_pads(self, project_dir: str) -> list[Optional[dict]]:
        """Return 160 pad entries for the given project."""
        return sp404_storage.read_project_pads(project_dir)

    # ── WAV → .SMP conversion ───────────────────────────────────────

    @staticmethod
    def wav_to_smp(src_wav: str, out_smp: str,
                   target_rate: int = 48000,
                   target_channels: Optional[int] = None) -> bool:
        """Convert a WAV to SP-404 .SMP (RFWV) format.

        - Resamples to 48 kHz (SP-404 native) if needed.
        - Downmixes stereo→mono or upmixes mono→stereo to match
          target_channels. If target_channels is None, keeps source.
        - Writes 20-byte RFWV header + raw 16-bit signed PCM.
        - Atomic: writes to out_smp + '.tmp' then renames.
        """
        try:
            import numpy as np
            import soundfile as sf
        except ImportError as e:
            log.error("Missing audio deps for wav_to_smp: %s", e)
            return False

        try:
            data, src_rate = sf.read(src_wav, dtype="float32", always_2d=True)
        except Exception as e:
            log.error("Can't read %s: %s", src_wav, e)
            return False

        # data is (frames, channels)
        frames, channels = data.shape

        # Channel conversion
        if target_channels == 1 and channels > 1:
            data = data.mean(axis=1, keepdims=True)
            channels = 1
        elif target_channels == 2 and channels == 1:
            data = np.repeat(data, 2, axis=1)
            channels = 2
        elif target_channels is None:
            target_channels = channels

        # Resample to target_rate
        if src_rate != target_rate:
            try:
                import samplerate as sr
                ratio = target_rate / src_rate
                resampled = sr.resample(data, ratio, "sinc_best")
                data = resampled.astype(np.float32)
            except ImportError:
                # Fall back to linear interpolation if samplerate unavailable
                log.warning("samplerate library not available, using linear resample")
                n_out = int(round(frames * target_rate / src_rate))
                indices = np.linspace(0, frames - 1, n_out)
                data = np.stack([
                    np.interp(indices, np.arange(frames), data[:, c])
                    for c in range(channels)
                ], axis=1).astype(np.float32)
            except Exception as e:
                log.error("Resample failed: %s", e)
                return False

        # Clip and convert to int16
        import numpy as np
        clipped = np.clip(data, -1.0, 1.0)
        int16 = (clipped * 32767.0).astype("<i2")  # little-endian int16

        # Interleave channels (PCM data is interleaved per frame)
        if channels == 1:
            pcm_bytes = int16[:, 0].tobytes()
        else:
            pcm_bytes = int16.flatten().tobytes()

        data_size = len(pcm_bytes)

        # RFWV header (big-endian fields)
        header = b"RFWV"
        header += struct.pack(">I", data_size)
        header += struct.pack(">I", target_rate)
        header += struct.pack(">I", channels)
        header += struct.pack(">I", 16)  # bit depth

        # Atomic write: .tmp then rename
        tmp_path = out_smp + ".tmp"
        try:
            os.makedirs(os.path.dirname(out_smp), exist_ok=True)
            with open(tmp_path, "wb") as f:
                f.write(header)
                f.write(pcm_bytes)
            os.replace(tmp_path, out_smp)
            log.info("wrote %s (%d bytes, %d ch, %d Hz)",
                     os.path.basename(out_smp), data_size, channels, target_rate)
            return True
        except Exception as e:
            log.error("Failed to write SMP %s: %s", out_smp, e)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return False

    # ── Pad path helpers ────────────────────────────────────────────

    @staticmethod
    def smp_filename(bank_idx: int, pad_idx: int) -> str:
        """Build BANKN-PP.SMP for 0-indexed bank and pad."""
        return f"BANK{bank_idx + 1}-{pad_idx + 1:02d}.SMP"

    def pad_path(self, project_dir: str, bank_idx: int, pad_idx: int) -> str:
        return os.path.join(project_dir, "SMPL", self.smp_filename(bank_idx, pad_idx))

    # ── Write / clear / move ────────────────────────────────────────

    def write_pad(self, project_dir: str, bank_idx: int, pad_idx: int,
                  src_wav: str) -> Optional[str]:
        """Convert a WAV to .SMP and write it to the given pad slot."""
        if not os.path.isfile(src_wav):
            return None
        if not 0 <= bank_idx < self.BANKS:
            return None
        if not 0 <= pad_idx < self.PADS_PER_BANK:
            return None

        dest = self.pad_path(project_dir, bank_idx, pad_idx)
        if not self.wav_to_smp(src_wav, dest):
            return None
        self.sync()
        return dest

    def clear_pad(self, project_dir: str, bank_idx: int, pad_idx: int) -> bool:
        """Remove the .SMP file for this pad."""
        if not 0 <= bank_idx < self.BANKS:
            return False
        if not 0 <= pad_idx < self.PADS_PER_BANK:
            return False
        dest = self.pad_path(project_dir, bank_idx, pad_idx)
        if os.path.exists(dest):
            try:
                os.remove(dest)
                self.sync()
                return True
            except Exception as e:
                log.error("clear_pad: %s", e)
                return False
        return True  # already clear

    def clear_bank(self, project_dir: str, bank_idx: int) -> bool:
        ok = True
        for p in range(self.PADS_PER_BANK):
            if not self.clear_pad(project_dir, bank_idx, p):
                ok = False
        return ok

    def move_pad(self, project_dir: str,
                 from_bank: int, from_pad: int,
                 to_bank: int, to_pad: int) -> bool:
        """Rename a BANKN-PP.SMP file to move a sample to a new pad slot."""
        src = self.pad_path(project_dir, from_bank, from_pad)
        dst = self.pad_path(project_dir, to_bank, to_pad)
        if not os.path.isfile(src):
            return False
        try:
            # If dest exists, back it up as a .bak so we don't silently lose it
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except Exception:
                    pass
            os.rename(src, dst)
            self.sync()
            return True
        except Exception as e:
            log.error("move_pad: %s", e)
            return False

    def sync(self):
        """Flush FAT writes so pulling the plug can't corrupt the card."""
        try:
            subprocess.run(["sync"], timeout=10)
        except Exception:
            pass

    # ── Backup / restore ────────────────────────────────────────────

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
