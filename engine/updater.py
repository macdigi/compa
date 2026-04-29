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
        # Background poller state — start_background_poll() spins up
        # a daemon thread that calls check() every poll_interval
        # seconds. The UI layer (top-bar pill, update modal) just
        # reads update_available / commits_behind off this object.
        self._poll_stop: Optional[threading.Event] = None
        self._poll_thread: Optional[threading.Thread] = None

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

    # ── Background polling ──────────────────────────────────────────

    def start_background_poll(
        self, interval_sec: int = 1800,
        initial_delay_sec: int = 30,
    ) -> None:
        """Start a daemon thread that calls check() periodically.

        Default cadence: 30-minute interval, 30-second startup delay
        so the app finishes booting (touchscreen up, MIDI connected,
        Compa running) before we hit the network. Repeated calls are
        no-ops while a poll is already running.

        UI components read self.update_available / self.commits_behind
        live; no callback is invoked because the polling is meant to
        be ambient.
        """
        if self._poll_stop is not None and not self._poll_stop.is_set():
            return  # already running
        self._poll_stop = threading.Event()
        stop = self._poll_stop  # local capture for the thread

        def _loop() -> None:
            stop.wait(initial_delay_sec)
            while not stop.is_set():
                try:
                    self.check()
                except Exception as e:
                    log.warning("Updater poll error: %s", e)
                stop.wait(interval_sec)

        self._poll_thread = threading.Thread(
            target=_loop, daemon=True, name="UpdaterPoll")
        self._poll_thread.start()

    def stop_background_poll(self) -> None:
        if self._poll_stop is not None:
            self._poll_stop.set()

    # ── Read-only accessors for the UI layer ────────────────────────

    @property
    def update_available(self) -> bool:
        return bool(self._last_result.get("update_available", False))

    @property
    def commits_behind(self) -> int:
        return int(self._last_result.get("behind", 0))

    @property
    def last_check_time(self) -> float:
        """Monotonic timestamp of the last completed check (0 if
        none yet)."""
        return self._last_check

    # ── Human-readable change summary ───────────────────────────────

    def commit_messages_behind(self, limit: int = 30) -> list[str]:
        """Return commit subjects (one-liners) for commits the local
        is currently behind on. Most recent first. Used by the update
        modal as a fallback when CHANGELOG.md doesn't have user-facing
        entries for the upcoming version."""
        if not self.is_git_repo:
            return []
        branch = self.current_branch()
        if not branch or branch == "?":
            return []
        code, out = self._run(
            "log",
            f"HEAD..origin/{branch}",
            "--pretty=format:%s",
            f"-{limit}",
        )
        if code != 0:
            return []
        return [line for line in out.split("\n") if line.strip()]

    def changelog_entries_pending(self) -> list[str]:
        """Read CHANGELOG.md from the **remote** branch (i.e. the
        version we'd apply if the user said yes) and extract the
        "## Unreleased" or topmost-version section's bullet points.

        This lets release notes be authored in producer-friendly
        terms (rather than commit messages) — populate
        CHANGELOG.md before pushing and the update modal shows
        what users actually care about.

        Returns [] if the changelog isn't present or has no current
        section. Caller falls back to commit_messages_behind() in
        that case.
        """
        if not self.is_git_repo:
            return []
        branch = self.current_branch()
        if not branch or branch == "?":
            return []
        # Read the CHANGELOG.md as it exists on origin.
        code, content = self._run(
            "show", f"origin/{branch}:CHANGELOG.md", timeout=10)
        if code != 0:
            return []

        # Parse: take everything from the first '## ' header to the
        # next '## ' header. Within that, collect '- ' bullet lines.
        lines = content.split("\n")
        in_section = False
        bullets: list[str] = []
        for raw in lines:
            line = raw.rstrip()
            if line.startswith("## "):
                if in_section:
                    break  # next section reached
                in_section = True
                continue
            if in_section:
                stripped = line.lstrip()
                if stripped.startswith("- "):
                    bullets.append(stripped[2:].strip())
                elif stripped.startswith("* "):
                    bullets.append(stripped[2:].strip())
        return bullets
