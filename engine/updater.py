"""Compa Auto Updater.

Checks the local git repo against its remote for new commits and
pulls + restarts the service when an update is available.

Designed to run safely in the background — never blocks the UI thread,
never interrupts an in-progress recording.

Usage::

    updater = Updater("/home/pi/compa")
    info = updater.check()  # blocking — returns dict with status
    if info["update_available"]:
        updater.apply()  # pulls + restarts
"""

import logging
import os
import subprocess
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)


class Updater:
    """Git-based auto updater for Compa."""

    def __init__(self, repo_dir: str = "/home/pi/compa"):
        self._repo_dir = repo_dir
        self._last_check: float = 0.0
        self._last_result: dict = {}

    @property
    def is_git_repo(self) -> bool:
        return os.path.isdir(os.path.join(self._repo_dir, ".git"))

    def _run(self, *args, timeout: int = 30) -> tuple[int, str]:
        """Run a git command and return (exit_code, output)."""
        try:
            result = subprocess.run(
                ["git", "-C", self._repo_dir, *args],
                capture_output=True, text=True, timeout=timeout,
            )
            return result.returncode, (result.stdout + result.stderr).strip()
        except Exception as e:
            return -1, str(e)

    def current_commit(self) -> str:
        code, out = self._run("rev-parse", "--short", "HEAD")
        return out if code == 0 else "?"

    def current_branch(self) -> str:
        code, out = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return out if code == 0 else "?"

    def check(self) -> dict:
        """Check the remote for updates. Returns status dict."""
        result = {
            "update_available": False,
            "current": "",
            "remote": "",
            "behind": 0,
            "ahead": 0,
            "branch": "",
            "error": "",
        }

        if not self.is_git_repo:
            result["error"] = "Not a git repository"
            self._last_result = result
            return result

        # Fetch from remote
        code, out = self._run("fetch", "--quiet", timeout=20)
        if code != 0:
            result["error"] = f"Fetch failed: {out[:100]}"
            self._last_result = result
            return result

        result["branch"] = self.current_branch()
        result["current"] = self.current_commit()

        # Get remote commit hash
        code, remote = self._run("rev-parse", "--short", f"origin/{result['branch']}")
        if code != 0:
            result["error"] = "Cannot read remote"
            self._last_result = result
            return result
        result["remote"] = remote

        # Count commits ahead/behind
        code, behind = self._run("rev-list", "--count",
                                 f"HEAD..origin/{result['branch']}")
        if code == 0:
            result["behind"] = int(behind) if behind.isdigit() else 0

        code, ahead = self._run("rev-list", "--count",
                                f"origin/{result['branch']}..HEAD")
        if code == 0:
            result["ahead"] = int(ahead) if ahead.isdigit() else 0

        result["update_available"] = result["behind"] > 0
        self._last_check = time.monotonic()
        self._last_result = result
        return result

    def apply(self, restart: bool = True) -> dict:
        """Pull latest from remote and optionally restart the compa service."""
        result = {"success": False, "message": "", "log": ""}

        if not self.is_git_repo:
            result["message"] = "Not a git repository"
            return result

        # Stash any local changes (should be none in production)
        self._run("stash", "--include-untracked")

        # Pull
        code, out = self._run("pull", "--ff-only", timeout=60)
        result["log"] = out

        if code != 0:
            result["message"] = f"Pull failed: {out[:200]}"
            return result

        result["success"] = True
        result["message"] = f"Updated to {self.current_commit()}"

        if restart:
            # Schedule a service restart in the background — don't block
            def _restart():
                time.sleep(1.5)
                try:
                    subprocess.run(["sudo", "systemctl", "restart", "compa"],
                                   timeout=10)
                except Exception:
                    pass
            threading.Thread(target=_restart, daemon=True).start()
            result["message"] += " — restarting in 1.5s"

        return result

    def check_async(self, callback) -> None:
        """Check for updates in a background thread, call callback(dict)."""
        def _run():
            res = self.check()
            try:
                callback(res)
            except Exception as e:
                log.warning("Updater callback error: %s", e)
        threading.Thread(target=_run, daemon=True).start()
