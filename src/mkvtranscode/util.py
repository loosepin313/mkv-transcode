"""Small helpers shared by the other modules."""

from __future__ import annotations

import errno
import os
import re
import shutil
import unicodedata
from typing import Iterable, List, Optional

# Characters that are unsafe in file names on Linux file systems that also
# serve Windows clients (SMB, NFS with Samba) and in Jellyfin libraries.
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_IMDB_ID = re.compile(r"\btt(\d{7,8})\b", re.IGNORECASE)


def which(name: str) -> Optional[str]:
    """Return the full path to an executable, or None."""
    return shutil.which(name)


def human_size(num_bytes: float) -> str:
    """Return a size such as '4.2 GiB'."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%.1f %s" % (size, units[-1])  # pragma: no cover


def human_duration(seconds: float) -> str:
    """Return a duration such as '1h 42m 10s'."""
    total = int(max(0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm %02ds" % (minutes, secs)


def sanitize_component(name: str) -> str:
    """Return a file or directory name that is safe on Linux and Samba shares."""
    text = unicodedata.normalize("NFC", name)
    text = _UNSAFE.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    text = text.rstrip(". ")
    reserved = {"", ".", "..", "con", "prn", "aux", "nul"}
    if text.lower() in reserved:
        text = "_" + text
    return text[:180] or "untitled"


def join_path(base: str, *parts: str) -> str:
    """Join path parts and expand a leading '~'."""
    return os.path.join(os.path.expanduser(base), *parts)


def expand(path: str) -> str:
    """Expand '~' and return an absolute path."""
    return os.path.abspath(os.path.expanduser(path))


def expand_in(path: str, base: str) -> str:
    """Expand '~'. Anchor a relative path to base instead of the cwd."""
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(base, path)
    return os.path.abspath(path)


def imdb_id_from_text(text: str) -> Optional[str]:
    """Return an IMDb id such as 'tt0111161' found in text, or None."""
    match = _IMDB_ID.search(text)
    if not match:
        return None
    return "tt" + match.group(1)


def normalize_title(text: str) -> str:
    """Return a lower case, punctuation free form of a title for matching."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def ensure_dir(path: str) -> str:
    """Create a directory if needed and return its path."""
    os.makedirs(path, exist_ok=True)
    return path


def open_private(path: str, append: bool = False, buffering: int = -1):
    """Open a text file for writing with owner-only mode (0600).

    Job logs and JSON reports hold file names and full paths. Set the mode at
    creation with os.open, then fchmod the descriptor. A chmod after open
    would leave a short window where another user could read the file.
    """
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    handle = os.open(path, flags, 0o600)
    try:
        os.fchmod(handle, 0o600)
    except OSError:
        pass
    return os.fdopen(handle, "a" if append else "w", encoding="utf-8",
                     buffering=buffering)


def atomic_replace(src: str, dst: str) -> None:
    """Move src onto dst so readers never see a partial file."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)


COPY_BLOCK = 8 * 1024 * 1024


def same_device(src_dir: str, dst_dir: str) -> bool:
    """Return True when both paths sit on the same filesystem."""
    try:
        return os.stat(src_dir).st_dev == os.stat(dst_dir).st_dev
    except OSError:
        return False


def mount_fstype(path: str) -> str:
    """Return the file system type that holds a path, or an empty string."""
    path = os.path.abspath(path)
    best, best_type = "", ""
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount = parts[1].replace("\\040", " ")
                if path == mount or path.startswith(mount.rstrip("/") + "/"):
                    if len(mount) > len(best):
                        best, best_type = mount, parts[2]
    except OSError:
        return ""
    return best_type


def cross_device_move(src: str, dst: str) -> bool:
    """Move a file. Return True when the move needed a copy.

    os.replace fails with EXDEV when the two paths sit on different
    filesystems. Copy the file to a temporary name on the destination
    filesystem, then rename it inside that filesystem.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.replace(src, dst)
        return False
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    tmp = dst + ".part"
    with open(src, "rb") as source, open(tmp, "wb") as target:
        while True:
            block = source.read(COPY_BLOCK)
            if not block:
                break
            target.write(block)
        target.flush()
        os.fsync(target.fileno())
    shutil.copystat(src, tmp)
    os.replace(tmp, dst)
    os.unlink(src)
    return True


def free_bytes(path: str) -> int:
    """Return the free bytes available on the file system that holds path."""
    usage = shutil.disk_usage(path if os.path.isdir(path) else os.path.dirname(path) or ".")
    return usage.free


def split_words(text: str) -> List[str]:
    """Split text into words on whitespace and commas."""
    return [word for word in re.split(r"[\s,]+", text.strip()) if word]


def first_existing(paths: Iterable[str]) -> Optional[str]:
    """Return the first path that exists."""
    for candidate in paths:
        if os.path.exists(candidate):
            return candidate
    return None
