"""HID touchscreen calibration: affine matrix applied to mouse events.

Used for USB capacitive panels where SDL/pygame receives raw screen-mapped
coordinates that don't quite line up with what the user touches. The legacy
ts_calibrate (TSLib) path only handles resistive ADS7846-class touchscreens —
this module covers everything else.

Matrix is 6 floats [a, b, c, d, e, f]:
    screen_x = a * raw_x + b * raw_y + c
    screen_y = d * raw_x + e * raw_y + f
"""
import json
import os
from pathlib import Path

CONFIG_PATH = Path(os.path.expanduser("~/.config/compa/touch_calibration.json"))


class TouchCalibration:
    def __init__(self):
        self.matrix: list[float] | None = None
        self.load()

    def load(self) -> bool:
        try:
            data = json.loads(CONFIG_PATH.read_text())
            m = data.get("matrix")
            if isinstance(m, list) and len(m) == 6:
                self.matrix = [float(v) for v in m]
                return True
        except (FileNotFoundError, ValueError, OSError):
            pass
        self.matrix = None
        return False

    def save(self, matrix: list[float]) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps({"matrix": list(matrix)}, indent=2))
        self.matrix = list(matrix)

    def clear(self) -> None:
        self.matrix = None
        try:
            CONFIG_PATH.unlink()
        except FileNotFoundError:
            pass

    def is_calibrated(self) -> bool:
        return self.matrix is not None

    def apply(self, x: int, y: int) -> tuple[int, int]:
        if not self.matrix:
            return x, y
        a, b, c, d, e, f = self.matrix
        return int(round(a * x + b * y + c)), int(round(d * x + e * y + f))


def compute_matrix(raw_points: list[tuple[int, int]],
                   target_points: list[tuple[int, int]]) -> list[float]:
    """Least-squares affine fit. Needs >=3 (raw, target) pairs."""
    import numpy as np

    if len(raw_points) != len(target_points):
        raise ValueError("raw and target point counts must match")
    if len(raw_points) < 3:
        raise ValueError("need at least 3 calibration points")

    A = np.array([[x, y, 1.0] for x, y in raw_points])
    bx = np.array([tx for tx, _ in target_points], dtype=float)
    by = np.array([ty for _, ty in target_points], dtype=float)

    abc, *_ = np.linalg.lstsq(A, bx, rcond=None)
    def_, *_ = np.linalg.lstsq(A, by, rcond=None)

    return [float(v) for v in (*abc, *def_)]
