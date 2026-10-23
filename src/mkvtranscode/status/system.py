"""Read live system numbers for the status page.

The page shows CPU use, memory, core count, the CPU model, and temperatures.
Every value comes from `/proc` or `/sys`, so the module needs no extra
package. Each reader takes a root path, which lets a test point it at a
folder of fake files.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

PROC = "/proc"
SYS = "/sys"

# Sensor labels worth showing, best first.
PREFERRED = ("package id 0", "tctl", "tdie", "k10temp", "coretemp", "cpu", "soc")

_PREV: Dict[str, object] = {"at": 0.0, "busy": 0.0, "total": 0.0}


def cpu_model(proc_root: Optional[str] = None) -> str:
    """Return the CPU model name, or an empty string."""
    proc_root = proc_root or PROC
    path = os.path.join(proc_root, "cpuinfo")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
                if line.lower().startswith("model number"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        return ""
    return ""


def core_count(proc_root: Optional[str] = None) -> int:
    """Return the number of logical cores."""
    proc_root = proc_root or PROC
    path = os.path.join(proc_root, "cpuinfo")
    found = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("processor"):
                    found += 1
    except OSError:
        return os.cpu_count() or 0
    return found or (os.cpu_count() or 0)


def cpu_percent(proc_root: Optional[str] = None) -> Optional[float]:
    """Return CPU use since the last call, or None on the first call.

    The first call has no earlier sample, so it returns None. The page fills
    in one refresh later.
    """
    sample = _cpu_sample(proc_root or PROC)
    if sample is None:
        return None
    busy, total = sample
    previous_busy = float(_PREV["busy"])
    previous_total = float(_PREV["total"])
    _PREV["busy"] = busy
    _PREV["total"] = total
    if not previous_total or total <= previous_total:
        return None
    used = (busy - previous_busy) / (total - previous_total) * 100.0
    return round(max(0.0, min(used, 100.0)), 1)


def _cpu_sample(proc_root: str) -> Optional[tuple]:
    """Return (busy jiffies, total jiffies) from the first line of /proc/stat."""
    path = os.path.join(proc_root, "stat")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            first = handle.readline().split()
    except OSError:
        return None
    if not first or first[0] != "cpu":
        return None
    values = [float(part) for part in first[1:]]
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0.0)
    total = sum(values)
    return (total - idle, total)


def memory(proc_root: Optional[str] = None) -> Dict[str, float]:
    """Return memory sizes in MiB. Missing keys stay 0."""
    wanted = {
        "MemTotal": "total_mb",
        "MemFree": "free_mb",
        "MemAvailable": "available_mb",
        "Buffers": "buffers_mb",
        "Cached": "cached_mb",
        "SwapTotal": "swap_total_mb",
        "SwapFree": "swap_free_mb",
    }
    out: Dict[str, float] = {name: 0.0 for name in wanted.values()}
    path = os.path.join(proc_root or PROC, "meminfo")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                key = line.split(":", 1)[0].strip()
                name = wanted.get(key)
                if not name:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    out[name] = float(parts[1]) / 1024.0
    except OSError:
        pass
    out["swap_used_mb"] = max(0.0, out["swap_total_mb"] - out["swap_free_mb"])
    out["used_mb"] = max(0.0, out["total_mb"] - out["available_mb"])
    return out


def load_average() -> List[float]:
    """Return the 1, 5 and 15 minute load average."""
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except OSError:
        return []


def temperatures(sys_root: Optional[str] = None) -> List[Dict]:
    """Return temperature sensors, best first. Empty when the box hides them.

    A virtual machine usually exposes none. The page then leaves the card out.
    """
    found: List[Dict] = []
    sys_root = sys_root or SYS
    hwmon = os.path.join(sys_root, "class", "hwmon")
    if os.path.isdir(hwmon):
        for chip in sorted(os.listdir(hwmon)):
            base = os.path.join(hwmon, chip)
            label = _read(os.path.join(base, "name")) or chip
            for entry in sorted(_inputs(base)):
                value = _read(os.path.join(base, entry))
                if not value.isdigit():
                    continue
                found.append({"label": label, "celsius": round(int(value) / 1000.0, 1)})
    zones = os.path.join(sys_root, "class", "thermal")
    if os.path.isdir(zones):
        for zone in sorted(os.listdir(zones)):
            value = _read(os.path.join(zones, zone, "temp"))
            if not value.isdigit():
                continue
            label = _read(os.path.join(zones, zone, "type")) or zone
            found.append({"label": label, "celsius": round(int(value) / 1000.0, 1)})
    found.sort(key=lambda item: _rank(item["label"]))
    return found


def _inputs(base: str) -> List[str]:
    try:
        return [name for name in os.listdir(base) if name.startswith("temp") and name.endswith("_input")]
    except OSError:
        return []


def _rank(label: str) -> int:
    text = label.lower()
    for position, name in enumerate(PREFERRED):
        if name in text:
            return position
    return len(PREFERRED) + 1


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return ""


_CACHE: Dict[str, object] = {"at": 0.0, "data": {}}


def system_stats(cache_seconds: float = 2.0) -> Dict:
    """Assemble the system card data. Cached for a couple of seconds."""
    now = time.time()
    if now - float(_CACHE["at"]) < cache_seconds and _CACHE["data"]:
        data = dict(_CACHE["data"])          # type: ignore[arg-type]
        data["cached"] = True
        return data
    mem = memory()
    temps = temperatures()
    hottest = temps[0] if temps else None
    data = {
        "cpu_model": cpu_model(),
        "cores": core_count(),
        "cpu_percent": cpu_percent(),
        "load": load_average(),
        "memory": mem,
        "temperatures": temps[:4],
        "hottest_c": hottest["celsius"] if hottest else None,
        "hottest_label": hottest["label"] if hottest else "",
    }
    _CACHE["at"] = now
    _CACHE["data"] = data
    return dict(data)
