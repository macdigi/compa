"""Lightweight Raspberry Pi system telemetry for the Compa UI."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SystemSnapshot:
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    net_rx_bps: float = 0.0
    net_tx_bps: float = 0.0
    temp_c: float | None = None

    @property
    def net_total_bps(self) -> float:
        return max(0.0, self.net_rx_bps + self.net_tx_bps)


class SystemMonitor:
    """Sample CPU, memory, network, and temperature from /proc.

    Sampling is intentionally pull-based and throttled; callers may invoke
    update() every frame without paying for parsing more than once per
    interval.
    """

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = max(0.25, float(interval))
        self.snapshot = SystemSnapshot()
        self._last_sample_at = 0.0
        self._last_cpu: tuple[int, int] | None = None
        self._last_net: tuple[int, int, float] | None = None

    def update(self, now: float | None = None) -> SystemSnapshot:
        now = time.monotonic() if now is None else float(now)
        if now - self._last_sample_at < self.interval:
            return self.snapshot
        self._last_sample_at = now

        cpu = self._read_cpu_percent()
        ram = self._read_ram_percent()
        rx_bps, tx_bps = self._read_network_bps(now)
        temp = self._read_temp_c()
        self.snapshot = SystemSnapshot(cpu, ram, rx_bps, tx_bps, temp)
        return self.snapshot

    def _read_cpu_percent(self) -> float:
        try:
            with open("/proc/stat", "r", encoding="ascii") as handle:
                parts = handle.readline().split()[1:]
            values = [int(part) for part in parts]
        except Exception:
            return self.snapshot.cpu_percent
        if not values:
            return self.snapshot.cpu_percent

        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        previous = self._last_cpu
        self._last_cpu = (idle, total)
        if previous is None:
            return self.snapshot.cpu_percent

        prev_idle, prev_total = previous
        delta_total = total - prev_total
        delta_idle = idle - prev_idle
        if delta_total <= 0:
            return self.snapshot.cpu_percent
        used = max(0, delta_total - delta_idle)
        return max(0.0, min(100.0, used * 100.0 / delta_total))

    def _read_ram_percent(self) -> float:
        mem_total = 0
        mem_available = 0
        try:
            with open("/proc/meminfo", "r", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        mem_total = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        mem_available = int(line.split()[1])
        except Exception:
            return self.snapshot.ram_percent
        if mem_total <= 0:
            return self.snapshot.ram_percent
        used = max(0, mem_total - mem_available)
        return max(0.0, min(100.0, used * 100.0 / mem_total))

    def _read_network_totals(self) -> tuple[int, int]:
        rx_total = 0
        tx_total = 0
        try:
            with open("/proc/net/dev", "r", encoding="ascii") as handle:
                for line in handle.readlines()[2:]:
                    if ":" not in line:
                        continue
                    iface, data = line.split(":", 1)
                    iface = iface.strip()
                    if iface == "lo":
                        continue
                    parts = data.split()
                    if len(parts) < 16:
                        continue
                    rx_total += int(parts[0])
                    tx_total += int(parts[8])
        except Exception:
            pass
        return rx_total, tx_total

    def _read_network_bps(self, now: float) -> tuple[float, float]:
        rx, tx = self._read_network_totals()
        previous = self._last_net
        self._last_net = (rx, tx, now)
        if previous is None:
            return (self.snapshot.net_rx_bps, self.snapshot.net_tx_bps)
        prev_rx, prev_tx, prev_at = previous
        elapsed = max(0.001, now - prev_at)
        return (
            max(0.0, (rx - prev_rx) / elapsed),
            max(0.0, (tx - prev_tx) / elapsed),
        )

    def _read_temp_c(self) -> float | None:
        paths = (
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/hwmon/hwmon0/temp1_input",
        )
        for path in paths:
            try:
                if not os.path.exists(path):
                    continue
                with open(path, "r", encoding="ascii") as handle:
                    raw = float(handle.read().strip())
                return raw / 1000.0 if raw > 200 else raw
            except Exception:
                continue
        return None
