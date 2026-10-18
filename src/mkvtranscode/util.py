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


def atomic_replace(src: str, dst: str) -> None:
    """Move src onto dst so readers never see a partial file."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)


def cross_device_move(src: str, dst: str) -> None:
    """Move a file, copying it when src and dst sit on different devices."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.replace(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    tmp = dst + ".part"
    shutil.copyfile(src, tmp)
    shutil.copystat(src, tmp)
    os.replace(tmp, dst)
    os.unlink(src)


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
