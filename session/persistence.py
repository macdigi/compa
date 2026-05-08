"""Session persistence — JSON load/save with simple schema versioning."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from .session import Session


DEFAULT_PROJECT_DIR = os.path.expanduser("~/.compa/projects")


def projects_dir() -> str:
    os.makedirs(DEFAULT_PROJECT_DIR, exist_ok=True)
    return DEFAULT_PROJECT_DIR


def project_path(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "._- ").strip()
    if not safe:
        safe = "untitled"
    return os.path.join(projects_dir(), safe + ".json")


def save_session(session: Session, name: Optional[str] = None) -> str:
    """Write the session JSON. Returns the path written."""
    path = project_path(name or session.name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(session.to_dict(), f, indent=2)
    os.replace(tmp, path)
    return path


def load_session(name_or_path: str) -> Optional[Session]:
    path = name_or_path
    if not os.path.isabs(path):
        path = project_path(name_or_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            d = json.load(f)
        return Session.from_dict(d)
    except Exception as e:
        print(f"load_session({path}) failed: {e}", flush=True)
        return None


def list_projects() -> list[str]:
    d = projects_dir()
    return sorted(
        [f[:-5] for f in os.listdir(d) if f.endswith(".json")]
    )


class Autosaver:
    """Periodic background autosave. Caller pumps tick() each frame."""

    def __init__(self, session: Session, interval_seconds: float = 30.0) -> None:
        self.session = session
        self.interval = interval_seconds
        self._last = time.monotonic()

    def tick(self) -> None:
        now = time.monotonic()
        if now - self._last < self.interval:
            return
        self._last = now
        try:
            save_session(self.session)
        except Exception as e:
            print(f"autosave failed: {e}", flush=True)

    def force(self) -> None:
        try:
            save_session(self.session)
        except Exception as e:
            print(f"force-save failed: {e}", flush=True)
        self._last = time.monotonic()
