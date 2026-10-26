"""Find finished input files in the incoming directory.

A rip takes minutes to copy. The scanner only accepts a file whose size stays
the same for watch.stable_seconds and that no other process still holds open.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .config import Config
from .log import get_logger

log = get_logger("scan")

# Suffixes that copy tools use for partial files.
PARTIAL_SUFFIXES = (".part", ".tmp", ".file", ".crdownload", ".!qb", ".un~", ".writing")


@dataclass
class Candidate:
    path: str
    size: int
    mtime: float
    observed_at: float = field(default_factory=time.time)


def list_candidates(config: Config, now: Optional[float] = None,
                    min_size_bytes: Optional[int] = None) -> List[Candidate]:
    """Return every input file that is ready to process, oldest first.

    min_size_bytes replaces the watch.min_size_mb floor. The watcher passes 0
    for one forced pass, when an operator asked to start now.
    """
    root = config.paths.incoming
    if not os.path.isdir(root):
        return []
    floor = (int(config.watch.min_size_mb * 1024 * 1024)
             if min_size_bytes is None else int(min_size_bytes))
    out: List[Candidate] = []
    for path in _walk(root, config.watch.follow_subdirs):
        if not acceptable(path, config):
            continue
        if os.path.islink(path):
            # A symlink points outside incoming. Reject it again here, in case
            # the file changed between the walk and this check.
            log.debug("skip symlink: %s", path)
            continue
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_size < floor:
            continue
        out.append(Candidate(path=path, size=info.st_size, mtime=info.st_mtime))
    out.sort(key=lambda item: (item.mtime, item.path))
    return out


def acceptable(path: str, config: Config) -> bool:
    """Apply the name and extension rules to a path."""
    name = os.path.basename(path)
    if not config.watch.extensions:
        return False
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    if ext not in config.watch.extensions:
        return False
    lowered = name.lower()
    for suffix in PARTIAL_SUFFIXES:
        if lowered.endswith(suffix):
            return False
    for prefix in config.watch.ignore_prefixes:
        if prefix and lowered.startswith(prefix.lower()):
            return False
    return True


def _walk(root: str, follow_subdirs: bool):
    """Yield regular files under root. Skip symbolic links.

    A symlink inside incoming would point the pipeline at a file the service
    user can read anywhere on the machine. With output.source_action set to
    'delete', the pipeline would then remove that file. Only real files under
    incoming are eligible.
    """
    if follow_subdirs:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                path = os.path.join(dirpath, name)
                if not os.path.islink(path):
                    yield path
        return
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        log.debug("skip symlink: %s", entry.path)
                        continue
                    if entry.is_file(follow_symlinks=False):
                        yield entry.path
                except OSError:
                    continue
    except OSError as exc:
        log.warning("cannot read %s: %s", root, exc)


def is_ready(path: str, config: Config, previous: Optional[Candidate] = None,
             force: bool = False) -> bool:
    """True when the file looks complete.

    Three signals decide this:
    1. The size and mtime did not change since the previous scan.
    2. That unchanged state has lasted watch.stable_seconds.
    3. No other process holds the file open.

    force drops signal 2 only, when an operator asked to start now. Signals 1
    and 3 stay, so a file that is still copying, or still open in another
    process, never starts a job.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return False
    if previous is not None and (previous.size != stat.st_size or previous.mtime != stat.st_mtime):
        return False
    if not force:
        if previous is not None:
            if time.time() - previous.observed_at < config.watch.stable_seconds:
                return False
        elif time.time() - stat.st_mtime < config.watch.stable_seconds:
            return False
    if config.watch.check_open_files:
        holder = open_by(path)
        if holder:
            log.debug("%s still open by %s", os.path.basename(path), holder)
            return False
    return True


def open_by(path: str) -> Optional[str]:
    """Return 'pid (name)' for a process that holds the file open, else None.

    This reads /proc, so it is Linux specific. It returns None on other
    systems or when the process list is not readable.
    """
    try:
        target = os.path.realpath(path)
    except OSError:
        return None
    self_pid = os.getpid()
    try:
        pids = os.listdir("/proc")
    except OSError:
        return None
    for pid_text in pids:
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == self_pid:
            continue
        fd_dir = "/proc/%d/fd" % pid
        try:
            names = os.listdir(fd_dir)
        except OSError:
            continue
        for name in names:
            try:
                if os.path.realpath(os.path.join(fd_dir, name)) == target:
                    return _process_name(pid)
            except OSError:
                continue
    return None


def _process_name(pid: int) -> str:
    try:
        with open("/proc/%d/comm" % pid, "r", encoding="utf-8") as handle:
            return "%d (%s)" % (pid, handle.read().strip())
    except OSError:
        return str(pid)
