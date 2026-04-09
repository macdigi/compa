"""Kit save/load/rename manager — JSON-based kit persistence."""

import os
import json
from typing import Optional

from .pad_bank import PadBank


class KitManager:
    """Manages saving and loading kits as JSON files."""

    def __init__(self, kits_dir: str):
        self.kits_dir = kits_dir
        os.makedirs(kits_dir, exist_ok=True)

    def list_kits(self) -> list[str]:
        """List all available kit names (without .json extension)."""
        kits = []
        try:
            for f in sorted(os.listdir(self.kits_dir)):
                if f.endswith(".json"):
                    kits.append(f[:-5])
        except OSError:
            pass
        return kits

    def save_kit(self, pad_bank: PadBank, name: Optional[str] = None) -> str:
        """Save current pad bank state to a JSON file. Returns the kit name."""
        kit_name = name or pad_bank.kit_name
        kit_name = self._sanitize_name(kit_name)
        pad_bank.kit_name = kit_name

        data = pad_bank.to_dict()
        path = os.path.join(self.kits_dir, f"{kit_name}.json")

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        return kit_name

    def load_kit(self, pad_bank: PadBank, name: str) -> bool:
        """Load a kit from JSON into the pad bank. Returns True on success.
        Note: caller must reload samples after loading (audio_data is not in JSON)."""
        path = os.path.join(self.kits_dir, f"{name}.json")
        if not os.path.exists(path):
            return False

        try:
            with open(path, "r") as f:
                data = json.load(f)
            pad_bank.from_dict(data)
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Kit load error: {e}")
            return False

    def delete_kit(self, name: str) -> bool:
        """Delete a kit file."""
        path = os.path.join(self.kits_dir, f"{name}.json")
        try:
            os.remove(path)
            return True
        except OSError:
            return False

    def rename_kit(self, old_name: str, new_name: str) -> bool:
        """Rename a kit file."""
        new_name = self._sanitize_name(new_name)
        old_path = os.path.join(self.kits_dir, f"{old_name}.json")
        new_path = os.path.join(self.kits_dir, f"{new_name}.json")

        if not os.path.exists(old_path):
            return False
        if os.path.exists(new_path):
            return False

        try:
            # Update kit name inside the JSON
            with open(old_path, "r") as f:
                data = json.load(f)
            data["kit_name"] = new_name
            with open(new_path, "w") as f:
                json.dump(data, f, indent=2)
            os.remove(old_path)
            return True
        except Exception as e:
            print(f"Kit rename error: {e}")
            return False

    def kit_exists(self, name: str) -> bool:
        """Check if a kit with the given name exists."""
        return os.path.exists(os.path.join(self.kits_dir, f"{name}.json"))

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize a kit name for use as a filename."""
        # Remove path separators and other unsafe chars
        safe = "".join(c for c in name if c.isalnum() or c in " _-")
        safe = safe.strip()
        return safe or "Untitled"
