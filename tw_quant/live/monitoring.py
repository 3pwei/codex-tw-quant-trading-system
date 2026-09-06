from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Callable


class HostResourceMonitor:
    """Read a small, dependency-free host/container resource snapshot.

    CPU is calculated from the delta between two ``/proc/stat`` samples.  The
    first sample intentionally reports ``None`` instead of inventing a rate.
    Memory reflects the container-visible Linux host and disk usage is scoped
    to the filesystem containing the SQLite database.
    """

    def __init__(
        self,
        disk_path: str | Path,
        *,
        read_text: Callable[[str], str] | None = None,
    ) -> None:
        target = Path(disk_path)
        self.disk_path = target if target.is_dir() else target.parent
        self._read_text = read_text or self._default_read_text
        self._previous_cpu: tuple[int, int] | None = None
        self._lock = Lock()

    @staticmethod
    def _default_read_text(path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def _cpu_usage(self) -> float | None:
        fields = self._read_text("/proc/stat").splitlines()[0].split()
        if not fields or fields[0] != "cpu":
            raise ValueError("/proc/stat does not contain aggregate CPU data")
        values = [int(value) for value in fields[1:]]
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        previous = self._previous_cpu
        self._previous_cpu = (total, idle)
        if previous is None:
            return None
        total_delta = total - previous[0]
        idle_delta = idle - previous[1]
        if total_delta <= 0:
            return None
        return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)

    def _memory(self) -> tuple[int, int, float]:
        values: dict[str, int] = {}
        for line in self._read_text("/proc/meminfo").splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.strip().split()[0]) * 1024
        total = values["MemTotal"]
        available = values["MemAvailable"]
        used = max(0, total - available)
        percent = round(used / total * 100, 1) if total else 0.0
        return total, used, percent

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            try:
                cpu_percent = self._cpu_usage()
            except (OSError, ValueError, IndexError):
                cpu_percent = None
            try:
                memory_total, memory_used, memory_percent = self._memory()
            except (OSError, ValueError, KeyError):
                memory_total = memory_used = 0
                memory_percent = None
            try:
                disk = os.statvfs(self.disk_path)
                disk_total = disk.f_frsize * disk.f_blocks
                disk_available = disk.f_frsize * disk.f_bavail
                disk_used = max(0, disk_total - disk_available)
                disk_percent = (
                    round(disk_used / disk_total * 100, 1)
                    if disk_total else 0.0
                )
            except OSError:
                disk_total = disk_used = 0
                disk_percent = None
        return {
            "cpu_percent": cpu_percent,
            "cpu_count": os.cpu_count(),
            "memory_total_bytes": memory_total,
            "memory_used_bytes": memory_used,
            "memory_percent": memory_percent,
            "disk_total_bytes": disk_total,
            "disk_used_bytes": disk_used,
            "disk_percent": disk_percent,
            "disk_path": str(self.disk_path),
        }
