"""inotify through libc.

Linux reports directory changes with inotify. The Python standard library has
no binding, so this module calls libc with ctypes. That keeps the package free
of third-party dependencies on Fedora and Rocky Linux.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import select
import struct
from dataclasses import dataclass
from typing import List

IN_ACCESS = 0x00000001
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_CLOSE_NOWRITE = 0x00000010
IN_OPEN = 0x00000020
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_UNMOUNT = 0x00002000
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_MASK_ADD = 0x20000000
IN_ISDIR = 0x40000000

# Events that mean "a file may be ready for work now".
DEFAULT_MASK = IN_CREATE | IN_MOVED_TO | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_DELETE | IN_ATTRIB

_HEADER = struct.Struct("=iIII")


class InotifyError(OSError):
    """inotify is not available or the call failed."""


@dataclass
class Event:
    path: str
    mask: int

    @property
    def is_directory(self) -> bool:
        return bool(self.mask & IN_ISDIR)

    def name(self) -> str:
        return os.path.basename(self.path)


def supported() -> bool:
    """True when this system offers inotify."""
    return hasattr(_libc(), "inotify_init1")


def _libc():
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    return libc


class Inotify:
    """A thin inotify wrapper with one watch per directory."""

    def __init__(self):
        libc = _libc()
        if not hasattr(libc, "inotify_init1"):
            raise InotifyError(errno.ENOSYS, "inotify is not available on this system")
        libc.inotify_init1.restype = ctypes.c_int
        libc.inotify_init1.argtypes = [ctypes.c_int]
        libc.inotify_add_watch.restype = ctypes.c_int
        libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        libc.inotify_rm_watch.restype = ctypes.c_int
        libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        self._libc = libc
        self.fd = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if self.fd < 0:
            raise InotifyError(ctypes.get_errno(), "inotify_init1 failed")
        self.watches = {}   # path -> watch descriptor
        self.by_descriptor = {}

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "Inotify":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def watch(self, path: str, mask: int = DEFAULT_MASK) -> int:
        """Add or update a watch on a directory."""
        if not os.path.isdir(path):
            raise InotifyError(errno.ENOENT, "not a directory: %s" % path)
        descriptor = self._libc.inotify_add_watch(
            self.fd, os.fsencode(path), mask | IN_ONLYDIR
        )
        if descriptor < 0:
            raise InotifyError(ctypes.get_errno(), "inotify_add_watch failed for %s" % path)
        self.watches[path] = descriptor
        self.by_descriptor[descriptor] = path
        return descriptor

    def unwatch(self, path: str) -> None:
        descriptor = self.watches.pop(path, None)
        if descriptor is None:
            return
        self.by_descriptor.pop(descriptor, None)
        self._libc.inotify_rm_watch(self.fd, descriptor)

    def read(self, timeout: float = 1.0) -> List[Event]:
        """Return the pending events, waiting at most timeout seconds."""
        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
        except (OSError, ValueError):
            return []
        if not ready:
            return []
        try:
            raw = os.read(self.fd, 65536)
        except BlockingIOError:
            return []
        return self._parse(raw)

    def _parse(self, raw: bytes) -> List[Event]:
        events: List[Event] = []
        offset = 0
        while offset + _HEADER.size <= len(raw):
            descriptor, mask, _cookie, length = _HEADER.unpack_from(raw, offset)
            offset += _HEADER.size
            name_bytes = raw[offset : offset + length]
            offset += length
            name = os.fsdecode(name_bytes.split(b"\x00", 1)[0])
            directory = self.by_descriptor.get(descriptor, "")
            if not directory or not name:
                continue
            events.append(Event(path=os.path.join(directory, name), mask=mask))
        return events
