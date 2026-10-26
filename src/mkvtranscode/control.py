"""Ask a running watcher to act now.

The status page and the watcher are separate processes. A small JSON file in
the library folder carries a request from one to the other. The watcher reads
the file on every pass of its loop and removes it when the request expires.

Only one process writes the file: the status server, and only with
`--allow-control`. The watcher never writes a request of its own.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional

from .config import Config
from .log import get_logger
from .util import atomic_replace, open_private

log = get_logger("control")

# The request file sits next to the state file, so one folder holds all the
# machine-readable state.
REQUEST_NAME = ".control.json"
PROCESS_NOW = "process_now"
# A request nobody acted on must not start an encode an hour later.
REQUEST_TTL = 120.0


def request_path(config: Config) -> str:
    """Return the path of the request file."""
    return os.path.join(config.paths.transcoded, REQUEST_NAME)


def request_process_now(config: Config, ttl: float = REQUEST_TTL) -> Dict:
    """Write a process-now request. Return the payload it holds.

    A second click replaces the file and moves the expiry, so the watcher
    keeps a live request for one window rather than several.
    """
    now = time.time()
    payload = {
        "action": PROCESS_NOW,
        "requested": round(now, 3),
        "expires": round(now + ttl, 3),
    }
    path = request_path(config)
    tmp = path + ".part"
    with open_private(tmp) as handle:
        json.dump(payload, handle, sort_keys=True)
    atomic_replace(tmp, path)
    log.info("process now requested for %.0f seconds", ttl)
    return payload


def read_request(config: Config) -> Optional[Dict]:
    """Return the request on disk, or None when there is none.

    This only reads. The watcher removes the file when the request expires, so
    a page refresh never changes anything.
    """
    path = request_path(config)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("action") != PROCESS_NOW:
        return None
    return data


def is_live(request: Optional[Dict], now: Optional[float] = None) -> bool:
    """True when the request still applies."""
    if not request:
        return False
    try:
        expires = float(request.get("expires") or 0.0)
    except (TypeError, ValueError):
        return False
    return expires > (time.time() if now is None else now)


def expires_in(request: Optional[Dict], now: Optional[float] = None) -> float:
    """Return the seconds left on the request. Zero when it is gone."""
    if not request:
        return 0.0
    try:
        expires = float(request.get("expires") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, expires - (time.time() if now is None else now))


def clear_request(config: Config) -> None:
    """Remove the request file. Ignore a missing one."""
    try:
        os.unlink(request_path(config))
    except OSError:
        pass
