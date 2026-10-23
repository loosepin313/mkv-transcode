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
from .util import free_bytes, human_size, mount_fstype, same_device, which

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
        ("processed", config.paths.processed),
        ("workdir", config.paths.work),
        ("logs", config.paths.logs),
    ):
        checks.append(_directory_check(label, path, writable=label != "incoming"))

    free = free_bytes(config.paths.transcoded if os.path.isdir(config.paths.transcoded) else os.path.expanduser("~"))
    level = OK if free >= config.job.min_free_gb * 1024 ** 3 else WARN
    checks.append(Check(level, "free space", "%s free in %s" % (human_size(free), config.paths.transcoded)))
    checks.append(_work_dir_check(config))

    from .inotify import supported

    checks.append(Check(OK if supported() else WARN, "inotify",
                        "available" if supported() else "not available, the watcher will poll"))

    provider = build_provider(config)
    status = provider.status()
    checks.append(Check(OK if provider.available() or config.metadata.provider in ("filename", "none") else WARN,
                        "metadata", status))

    checks.extend(_gpu_checks(config))
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


def _work_dir_check(config: Config) -> Check:
    """Report where the work dir lives compared with the library."""
    work, library = config.paths.work, config.paths.transcoded
    missing = [name for name, path in (("workdir", work), ("transcoded", library))
               if not os.path.isdir(path)]
    if missing:
        return Check(WARN, "workdir", "missing: " + ", ".join(missing))
    if same_device(work, library):
        return Check(OK, "workdir", "%s shares the file system with the library" % work)
    fstype = mount_fstype(work) or "another file system"
    detail = "%s is on %s, the library is elsewhere; the final move copies the file" % (work, fstype)
    if fstype in ("tmpfs", "ramfs"):
        return Check(WARN, "workdir", detail + ". %s uses RAM, a large film can fill it." % fstype)
    return Check(WARN, "workdir", detail)


def _gpu_checks(config: Config) -> List[Check]:
    """Report the GPUs, and check the NVENC settings against them."""
    from .transcode.gpu import NVENC_ENCODERS, av1_capable, gpu_rows

    profile = config.active_profile()
    wants_nvenc = profile.encoder in NVENC_ENCODERS
    rows = gpu_rows()
    checks: List[Check] = []
    if not rows:
        if wants_nvenc:
            checks.append(Check(FAIL, "gpu",
                                "no GPU found. Install the NVIDIA driver, or pick a CPU encoder."))
        return checks
    names = ", ".join("GPU %d %s" % (row["index"], row["name"]) for row in rows)
    checks.append(Check(OK, "gpu", "%d GPU: %s" % (len(rows), names)))
    if not wants_nvenc:
        checks.append(Check(OK, "nvenc",
                            "video.profile %s runs on the CPU. Set the encoder to *_nvenc to use the GPU."
                            % config.video.profile))
        return checks
    if profile.encoder == "av1_nvenc" and av1_capable(rows) is False:
        checks.append(Check(FAIL, "nvenc av1",
                            "these GPUs cannot encode AV1. NVENC needs compute capability 8.9 or more. Use hevc_nvenc."))
    present = {row["index"] for row in rows}
    if not config.gpu.devices:
        checks.append(Check(WARN, "gpu.devices",
                            "empty, so every job uses GPU 0. List one index per job, for example [0, 1]."))
        return checks
    missing = [index for index in config.gpu.devices if index not in present]
    if missing:
        checks.append(Check(FAIL, "gpu.devices",
                            "no such GPU: %s" % ", ".join(str(index) for index in missing)))
    if config.job.max_parallel > len(config.gpu.devices):
        checks.append(Check(WARN, "job.max_parallel",
                            "%d jobs but %d GPU, so the watcher runs %d at a time"
                            % (config.job.max_parallel, len(rows), len(config.gpu.devices))))
    return checks


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
