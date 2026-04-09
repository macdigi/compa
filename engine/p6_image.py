"""P-6 image backup and restore manager.

Backs up the entire P-6 USB storage contents to named snapshots on the Pi.
Restores from saved snapshots. Runs copy operations in background threads.
"""

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger(__name__)

P6_MOUNT_PATH = "/media/pi/P-6"


class P6ImageManager:
    """Manages P-6 storage backups (images)."""

    def __init__(self, images_dir: str, p6_mount: str = P6_MOUNT_PATH):
        self._images_dir = images_dir
        self._p6_mount = p6_mount
        os.makedirs(images_dir, exist_ok=True)

        # Progress tracking
        self._busy = False
        self._progress = 0.0
        self._status = ""

    @property
    def p6_mounted(self) -> bool:
        return os.path.isdir(self._p6_mount)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def status(self) -> str:
        return self._status

    def list_images(self) -> list[dict]:
        """List all saved P-6 images."""
        images = []
        if not os.path.isdir(self._images_dir):
            return images

        for entry in sorted(os.listdir(self._images_dir), reverse=True):
            path = os.path.join(self._images_dir, entry)
            if not os.path.isdir(path):
                continue
            meta_path = os.path.join(path, "image_meta.json")
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                except Exception:
                    pass

            # Calculate total size
            total_size = 0
            for dirpath, dirnames, filenames in os.walk(path):
                for fname in filenames:
                    if fname != "image_meta.json":
                        total_size += os.path.getsize(os.path.join(dirpath, fname))

            images.append({
                "name": meta.get("name", entry),
                "description": meta.get("description", ""),
                "timestamp": meta.get("timestamp", ""),
                "size_mb": total_size / (1024 * 1024),
                "path": path,
            })

        return images

    def backup(self, name: str, description: str = "",
               on_complete: Optional[Callable[[bool, str], None]] = None) -> None:
        """Start a background backup of P-6 contents.

        Args:
            name: User-friendly name for the image
            description: Optional description
            on_complete: Callback(success, message) called when done
        """
        if self._busy:
            log.warning("Already busy with a backup/restore operation")
            return
        if not self.p6_mounted:
            log.warning("P-6 not mounted")
            if on_complete:
                on_complete(False, "P-6 not mounted")
            return

        self._busy = True
        self._progress = 0.0
        self._status = "Starting backup..."

        def _backup_thread():
            try:
                # Create image directory
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in name)
                safe_name = safe_name.strip() or "backup"
                dir_name = f"{safe_name}_{ts}"
                dest = os.path.join(self._images_dir, dir_name)
                os.makedirs(dest, exist_ok=True)

                # Count total bytes for progress
                total_bytes = 0
                for dirpath, dirnames, filenames in os.walk(self._p6_mount):
                    for fname in filenames:
                        total_bytes += os.path.getsize(os.path.join(dirpath, fname))

                # Copy with progress
                copied_bytes = 0
                file_count = 0
                for dirpath, dirnames, filenames in os.walk(self._p6_mount):
                    rel = os.path.relpath(dirpath, self._p6_mount)
                    dst_dir = os.path.join(dest, rel)
                    os.makedirs(dst_dir, exist_ok=True)

                    for fname in filenames:
                        src = os.path.join(dirpath, fname)
                        dst = os.path.join(dst_dir, fname)
                        shutil.copy2(src, dst)
                        file_count += 1
                        copied_bytes += os.path.getsize(src)
                        self._progress = copied_bytes / total_bytes if total_bytes > 0 else 0
                        self._status = f"Copying... {int(self._progress * 100)}%"

                # Write metadata
                meta = {
                    "name": name,
                    "description": description,
                    "timestamp": datetime.now().isoformat(),
                    "file_count": file_count,
                    "total_bytes": total_bytes,
                }
                with open(os.path.join(dest, "image_meta.json"), "w") as f:
                    json.dump(meta, f, indent=2)

                self._status = "Backup complete!"
                log.info("Backup complete: %s (%d files, %.1f MB)",
                         name, file_count, total_bytes / (1024 * 1024))
                if on_complete:
                    on_complete(True, f"Saved: {name}")

            except Exception as e:
                self._status = f"Backup failed: {e}"
                log.error("Backup failed: %s", e)
                if on_complete:
                    on_complete(False, str(e))
            finally:
                self._busy = False

        threading.Thread(target=_backup_thread, daemon=True).start()

    def restore(self, image_path: str,
                on_complete: Optional[Callable[[bool, str], None]] = None) -> None:
        """Start a background restore from a saved image.

        WARNING: This deletes all current P-6 contents first!
        """
        if self._busy:
            return
        if not self.p6_mounted:
            if on_complete:
                on_complete(False, "P-6 not mounted")
            return
        if not os.path.isdir(image_path):
            if on_complete:
                on_complete(False, "Image not found")
            return

        self._busy = True
        self._progress = 0.0
        self._status = "Starting restore..."

        def _restore_thread():
            try:
                # Count source bytes
                total_bytes = 0
                for dirpath, dirnames, filenames in os.walk(image_path):
                    for fname in filenames:
                        if fname != "image_meta.json":
                            total_bytes += os.path.getsize(os.path.join(dirpath, fname))

                # Delete current P-6 contents
                self._status = "Clearing P-6..."
                for item in os.listdir(self._p6_mount):
                    item_path = os.path.join(self._p6_mount, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)

                # Copy image to P-6
                copied_bytes = 0
                for dirpath, dirnames, filenames in os.walk(image_path):
                    rel = os.path.relpath(dirpath, image_path)
                    dst_dir = os.path.join(self._p6_mount, rel)
                    os.makedirs(dst_dir, exist_ok=True)

                    for fname in filenames:
                        if fname == "image_meta.json":
                            continue
                        src = os.path.join(dirpath, fname)
                        dst = os.path.join(dst_dir, fname)
                        shutil.copy2(src, dst)
                        copied_bytes += os.path.getsize(src)
                        self._progress = copied_bytes / total_bytes if total_bytes > 0 else 0
                        self._status = f"Restoring... {int(self._progress * 100)}%"

                self._status = "Restore complete!"
                log.info("Restore complete: %s", image_path)
                if on_complete:
                    on_complete(True, "Restore complete")

            except Exception as e:
                self._status = f"Restore failed: {e}"
                log.error("Restore failed: %s", e)
                if on_complete:
                    on_complete(False, str(e))
            finally:
                self._busy = False

        threading.Thread(target=_restore_thread, daemon=True).start()

    def delete_image(self, image_path: str) -> bool:
        """Delete a saved image."""
        try:
            if os.path.isdir(image_path):
                shutil.rmtree(image_path)
                log.info("Deleted image: %s", image_path)
                return True
        except Exception as e:
            log.error("Failed to delete image: %s", e)
        return False
