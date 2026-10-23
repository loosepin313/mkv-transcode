"""Find the GPUs and hand one index to each running job.

NVENC opens a single GPU per ffmpeg process. When the box has two cards, each
job gets its own index through CUDA_VISIBLE_DEVICES, so both cards encode at
the same time.
"""

from __future__ import annotations

import subprocess
from collections import deque
from threading import Lock
from typing import Dict, List, Optional

from ..log import get_logger
from ..util import which

log = get_logger("gpu")

# Fields for nvidia-smi. The encoder fields show NVENC work, not 3D work.
QUERY_FIELDS = (
    "index", "name", "utilization.gpu", "memory.used", "memory.total",
    "temperature.gpu", "encoder.stats.sessionCount", "encoder.stats.averageFps",
    "compute_cap",
)

NVENC_ENCODERS = ("h264_nvenc", "hevc_nvenc", "av1_nvenc")


class GpuPool:
    """A pool of GPU indexes. acquire() returns None when too few are free."""

    def __init__(self, devices: List[int]):
        self.devices = list(devices)
        self._free = deque(self.devices)
        self._lock = Lock()

    def acquire(self, count: int = 1) -> Optional[List[int]]:
        """Take `count` indexes, or None when that many are not free."""
        with self._lock:
            if len(self._free) < count:
                return None
            return [self._free.popleft() for _ in range(count)]

    def release(self, indexes: Optional[List[int]]) -> None:
        if not indexes:
            return
        with self._lock:
            for index in indexes:
                self._free.append(index)

    def free_count(self) -> int:
        with self._lock:
            return len(self._free)


def nvidia_smi(args: List[str], timeout: float = 10.0) -> Optional[str]:
    """Run nvidia-smi and return stdout, or None when it is missing or fails."""
    tool = which("nvidia-smi")
    if not tool:
        return None
    try:
        done = subprocess.run([tool] + args, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def gpu_rows() -> List[Dict]:
    """Return one dict per GPU. The list is empty when nvidia-smi is absent."""
    text = nvidia_smi([
        "--query-gpu=" + ",".join(QUERY_FIELDS),
        "--format=csv,noheader,nounits",
    ])
    if not text:
        return []
    rows: List[Dict] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != len(QUERY_FIELDS):
            continue
        rows.append(_row(dict(zip(QUERY_FIELDS, parts))))
    return rows


def _row(raw: Dict[str, str]) -> Dict:
    """Turn the CSV strings into numbers, and keep the name as text."""
    def number(key: str) -> Optional[float]:
        value = raw.get(key, "")
        try:
            return float(value)
        except ValueError:
            return None

    return {
        "index": int(number("index") if number("index") is not None else -1),
        "name": raw.get("name", ""),
        "util_percent": number("utilization.gpu"),
        "memory_used_mb": number("memory.used"),
        "memory_total_mb": number("memory.total"),
        "temperature_c": number("temperature.gpu"),
        "encode_sessions": number("encoder.stats.sessionCount"),
        "encode_fps": number("encoder.stats.averageFps"),
        "compute_cap": raw.get("compute_cap", ""),
    }


def av1_capable(rows: Optional[List[Dict]] = None) -> Optional[bool]:
    """Return whether any GPU can encode AV1. None when no GPU is present.

    NVENC encodes AV1 only on compute capability 8.9 and up. A Turing card,
    such as a GTX 1660, sits at 7.5 and cannot.
    """
    rows = gpu_rows() if rows is None else rows
    if not rows:
        return None
    for row in rows:
        try:
            if float(row.get("compute_cap") or 0) >= 8.9:
                return True
        except ValueError:
            continue
    return False
