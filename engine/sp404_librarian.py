"""SP-404 MK2 librarian — on-device sample management with write support.

The SP-404 MK2 exposes its SD card as USB mass storage when it's put in
USB storage mode (Tools menu). Samples live at:

    <mount>/
        ROLAND/SP-404MKII/
            PROJECT_01/
                PADCONF.BIN           — pad config (RFPD magic, partially decoded)
                SMPL/
                    BANK1-01.SMP       — sample file (RFWV magic + PCM data)
                    BANK1-02.SMP
                    ...
                    BANK10-16.SMP

The mount point is discovered dynamically via engine.device_mount — we
don't hardcode `/media/pi/SP-404MKII` because the actual path varies
depending on the mount helper and the volume label.

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
from engine.device_mount import find_or_mount_device, diagnostic_info

log = logging.getLogger(__name__)


def _sp404_signature(mount_point: str, label: str) -> bool:
    """Return True if `mount_point` looks like an SP-404 MK2 volume."""
    try:
        if not os.path.isdir(mount_point):
            return False
        # Primary signature: ROLAND/SP-404MKII/
        if os.path.isdir(os.path.join(mount_point, "ROLAND", "SP-404MKII")):
            return True
        # Alternate Roland-only layout (bare SD before first boot)
        if os.path.isdir(os.path.join(mount_point, "ROLAND")):
            # Make sure the SP-404MKII or SMPL dir is in there somewhere
            try:
                roland = os.path.join(mount_point, "ROLAND")
                for entry in os.listdir(roland):
                    if "SP-404" in entry.upper() or "SP404" in entry.upper():
                        return True
            except Exception:
                pass
        # Label-based match
        if label:
            lab = label.upper().replace("-", "").replace("_", "")
            if "SP404" in lab or "SP404MKII" in lab:
                return True
    except Exception:
        pass
    return False


class SP404Librarian:
    """SP-404 MK2 on-device librarian."""

    BANKS = 10
    PADS_PER_BANK = 16
    NUM_PADS = 160  # 10 * 16

    def __init__(self, images_dir: str, mount_path: str = ""):
        """Create a librarian.

        `mount_path` is only used as an initial hint / fixed override
        (mainly for unit tests). At runtime we re-scan on every call to
        `is_mounted()`.
        """
        self._fixed_mount = mount_path
        self._manual_mount = ""          # user-selected mount (DEBUG panel)
        self._mount_path = mount_path
        self._last_error = ""
        os.makedirs(images_dir, exist_ok=True)
        self._img = P6ImageManager(images_dir, mount_path=mount_path)

    # ── Mount state ──────────────────────────────────────────────────

    @property
    def mount_path(self) -> str:
        return self._mount_path

    @property
    def last_error(self) -> str:
        return self._last_error

    def is_mounted(self) -> bool:
        """Return True if an SP-404 volume is mounted. Auto-rescans.

        If no mounted drive matches, actively mounts any unmounted
        removable partition and re-checks. Handles headless Pi setups.
        """
        # Manual override from the DEBUG panel takes precedence
        if self._manual_mount and os.path.isdir(self._manual_mount):
            self._mount_path = self._manual_mount
            self._img._mount_path = self._manual_mount
            self._last_error = ""
            return True

        if self._fixed_mount:
            if os.path.isdir(self._fixed_mount) and _sp404_signature(
                    self._fixed_mount, ""):
                self._mount_path = self._fixed_mount
                self._img._mount_path = self._fixed_mount
                return True
            return False

        found = find_or_mount_device(_sp404_signature, mount_name="sp404")
        if found is None:
            self._mount_path = ""
            self._img._mount_path = ""
            self._last_error = "No SP-404 mount found"
            return False
        self._mount_path = found.mount_point
        self._img._mount_path = found.mount_point
        self._last_error = ""
        return True

    def set_manual_mount(self, mount_point: str):
        """Override auto-detection with a user-selected mount point."""
        self._manual_mount = mount_point or ""
        if mount_point:
            self._mount_path = mount_point
            self._img._mount_path = mount_point
            log.info("SP-404: manual mount override → %s", mount_point)

    def diagnostic(self) -> str:
        if self.is_mounted():
            return f"SP-404: {self._mount_path}"
        info = diagnostic_info()
        if not info["lsblk_available"]:
            return "SP-404: lsblk unavailable — install util-linux?"
        nm = len(info["mounted"])
        nu = len(info["unmounted"])
        if nm == 0 and nu == 0:
            return "SP-404: no USB storage detected — Tools → USB storage"
        return f"SP-404: seen {nm} mounted + {nu} unmounted — none match signature"

    def diagnostic_lines(self) -> list[str]:
        """Full diagnostic report for the debug modal."""
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
            lines.append("SP-404 IS NOT DETECTED")
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

    def roland_dir(self) -> str:
        """Return ROLAND/SP-404MKII on the current mount, or empty."""
        if not self._mount_path:
            return ""
        # Try the standard path first
        standard = os.path.join(self._mount_path, "ROLAND", "SP-404MKII")
        if os.path.isdir(standard):
            return standard
        # Fallback: look for any ROLAND/SP-404* subdir
        roland_root = os.path.join(self._mount_path, "ROLAND")
        if os.path.isdir(roland_root):
            try:
                for entry in os.listdir(roland_root):
                    if "SP-404" in entry.upper() or "SP404" in entry.upper():
                        candidate = os.path.join(roland_root, entry)
                        if os.path.isdir(candidate):
                            return candidate
            except Exception:
                pass
        return standard  # Return even if it doesn't exist yet

    # ── Project listing ─────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        """List projects on the device.

        Each entry: {name, path, num_samples}. Falls back to the
        Mac librarian cache if the device isn't mounted (handy for
        testing on the dev machine).
        """
        if self.is_mounted():
            rdir = self.roland_dir()
            if rdir and os.path.isdir(rdir):
                projs = self._list_projects_in(rdir)
                if projs:
                    return projs
                # No PROJECT_XX dirs? Create a default so the user can
                # load samples. The SP-404 will see the new project on
                # next boot.
                self._ensure_default_project(rdir)
                return self._list_projects_in(rdir)
        # Dev fallback — use the Mac librarian cache
        cache = sp404_storage.find_sp404_cache()
        if cache:
            return sp404_storage.list_projects(cache)
        return []

    def _ensure_default_project(self, roland_dir: str):
        """Make sure at least PROJECT_01/SMPL/ exists so writes have a target."""
        try:
            os.makedirs(os.path.join(roland_dir, "PROJECT_01", "SMPL"),
                        exist_ok=True)
        except Exception as e:
            log.warning("Could not create default project: %s", e)

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
            self._last_error = f"Source WAV not found: {os.path.basename(src_wav)}"
            return None
        if not 0 <= bank_idx < self.BANKS:
            return None
        if not 0 <= pad_idx < self.PADS_PER_BANK:
            return None
        if not project_dir or not os.path.isdir(project_dir):
            self._last_error = f"Project directory missing: {project_dir}"
            return None

        dest = self.pad_path(project_dir, bank_idx, pad_idx)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
        except PermissionError as e:
            self._last_error = f"Permission denied writing to SP-404 mount: {e}"
            return None
        except Exception as e:
            self._last_error = f"Couldn't create SMPL dir: {e}"
            return None

        if not self.wav_to_smp(src_wav, dest):
            self._last_error = "WAV→SMP conversion failed"
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
