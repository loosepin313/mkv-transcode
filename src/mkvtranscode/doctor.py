"""Environment checks.

'mkv-transcode doctor' answers one question: is this machine ready to run the
pipeline? It checks the binaries, the encoders, the filters, the directories
and the IMDb index.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from .config import Config
from .metadata import build_provider
from .util import free_bytes, human_size, which

OK, WARN, FAIL = "ok", "warn", "fail"

REQUIRED_ENCODERS = {
    "av1": ["libsvtav1"],
    "hevc": ["libx265"],
    "h264": ["libx264"],
    "copy": [],
}
REQUIRED_FILTERS = ["bwdif"]
OPTIONAL_FILTERS = ["zscale", "tonemap", "subtitles"]


@dataclass
class Check:
    level: str
    name: str
    detail: str

    def line(self) -> str:
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[self.level]
        return "%s  %-22s %s" % (mark, self.name, self.detail)


def run_checks(config: Config) -> List[Check]:
    """Return every check result in display order."""
    checks: List[Check] = []
    checks.append(_os_check())
    checks.append(_python_check())
    ffmpeg = which("ffmpeg")
    ffprobe = which("ffprobe")
    checks.append(_binary_check("ffmpeg", ffmpeg))
    checks.append(_binary_check("ffprobe", ffprobe))
    if not ffmpeg or not ffprobe:
        checks.append(Check(FAIL, "install ffmpeg", "dnf install ffmpeg (RPM Fusion) or dnf install ffmpeg-free"))
        return checks

    version = _first_line(_run([ffmpeg, "-version"]))
    checks.append(Check(OK, "ffmpeg version", version or "unknown"))

    encoders = _encoders(ffmpeg)
    needed = REQUIRED_ENCODERS.get(config.video.profile, [])
    for name in needed:
        checks.append(Check(OK if name in encoders else FAIL, "encoder " + name,
                            "present" if name in encoders else "missing; the %s profile needs it" % config.video.profile))
    available = sorted(name for name in encoders if name in ("libsvtav1", "libx265", "libx264", "libaom-av1", "librav1e"))
    if available:
        checks.append(Check(OK, "encoders found", ", ".join(available)))

    filters = _filters(ffmpeg)
    for name in REQUIRED_FILTERS:
        checks.append(Check(OK if name in filters else FAIL, "filter " + name,
                            "present" if name in filters else "missing"))
    missing_optional = [name for name in OPTIONAL_FILTERS if name not in filters]
    if missing_optional:
        checks.append(Check(WARN, "filters missing", ", ".join(missing_optional) + " (HDR tone mapping or burn-in)"))

    for label, path in (
        ("incoming", config.paths.incoming),
        ("transcoded", config.paths.transcoded),
        ("work", config.paths.work),
        ("logs", config.paths.logs),
    ):
        checks.append(_directory_check(label, path, writable=label != "incoming"))

    free = free_bytes(config.paths.transcoded if os.path.isdir(config.paths.transcoded) else os.path.expanduser("~"))
    level = OK if free >= config.job.min_free_gb * 1024 ** 3 else WARN
    checks.append(Check(level, "free space", "%s free in %s" % (human_size(free), config.paths.transcoded)))

    from .inotify import supported

    checks.append(Check(OK if supported() else WARN, "inotify",
                        "available" if supported() else "not available, the watcher will poll"))

    provider = build_provider(config)
    status = provider.status()
    checks.append(Check(OK if provider.available() or config.metadata.provider in ("filename", "none") else WARN,
                        "metadata", status))

    checks.append(_systemd_check())
    return checks


def exit_code(checks: List[Check]) -> int:
    """Return 1 when any check failed."""
    return 1 if any(check.level == FAIL for check in checks) else 0


# ---- helpers -----------------------------------------------------------


def _os_check() -> Check:
    system = platform.system()
    if system != "Linux":
        return Check(FAIL, "operating system", "%s is not Linux" % system)
    distro = "unknown"
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as handle:
            text = handle.read()
        match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.MULTILINE)
        if match:
            distro = match.group(1)
    except OSError:
        pass
    supported = any(name in distro.lower() for name in ("fedora", "rocky", "red hat", "rhel", "alma"))
    return Check(OK if supported else WARN, "distribution", distro + ("" if supported else " (not RPM based)"))


def _python_check() -> Check:
    version = platform.python_version()
    parts = [int(part) for part in version.split(".")[:2]]
    return Check(OK if parts >= [3, 9] else FAIL, "python", version)


def _binary_check(name: str, path: Optional[str]) -> Check:
    if not path:
        return Check(FAIL, name, "not found in PATH")
    return Check(OK, name, path)


def _directory_check(label: str, path: str, writable: bool = True) -> Check:
    if not os.path.isdir(path):
        return Check(WARN, label, "missing: %s" % path)
    if writable and not os.access(path, os.W_OK):
        return Check(FAIL, label, "not writable: %s" % path)
    return Check(OK, label, path)


def _systemd_check() -> Check:
    candidates = [
        "/etc/systemd/system/mkv-transcode.service",
        os.path.expanduser("~/.config/systemd/user/mkv-transcode.service"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return Check(OK, "systemd unit", path)
    return Check(WARN, "systemd unit", "not installed; see docs/SYSTEMD.md")


def _run(argv: List[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout or ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _encoders(ffmpeg: str) -> set:
    text = _run([ffmpeg, "-hide_banner", "-encoders"])
    pattern = re.compile(r"^\s*[VAS][.TCSCXUBD]{5}\s+(\S+)")
    found = set()
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            found.add(match.group(1))
    return found


def _filters(ffmpeg: str) -> set:
    text = _run([ffmpeg, "-hide_banner", "-filters"])
    # A filter line looks like ' TS bwdif    V->V   Deinterlace the input image.'
    pattern = re.compile(r"^[\sA-Z.]{4}\s*(\S+)\s+\S*->")
    found = set()
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            found.add(match.group(1))
    return found
