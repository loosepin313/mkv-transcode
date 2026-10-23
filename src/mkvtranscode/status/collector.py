"""Read the live transcode state for the status page.

The page shows the same data the daemon writes: the state database, the per
job log files, and the JSON reports under the logs folder. Every SQLite
connection opens read only, so this module never blocks or changes a run.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import time
from typing import Dict, List, Optional
from urllib.request import pathname2url

from .. import __version__
from ..config import Config
from ..scan import PARTIAL_SUFFIXES
from ..state import DONE, FAILED, RUNNING, SKIPPED
from ..util import free_bytes, which

STATE_NAME = ".state.sqlite"
UNIT = "mkv-transcode.service"

# 'progress pass 1: 2h 02m (93.9%)'
PROGRESS_RE = re.compile(r"^progress pass (\d+): (.+?) \((\d+(?:\.\d+)?)%\)$")
# Plan lines the pipeline writes before ffmpeg starts.
PLAN_RE = re.compile(r"^(input|named|video|audio|subs|quality|command):")
NOTE_RE = re.compile(r"^(warning|audio #\d+|.*downmixed from|.*->.*for the Opus)")
TAIL_BYTES = 200_000


def state_path(config: Config) -> str:
    """Return the state database path for this configuration."""
    return os.path.join(config.paths.transcoded, STATE_NAME)


def log_path(config: Config, input_path: str) -> str:
    """Return the per-job log path for one source file."""
    return os.path.join(config.paths.logs, os.path.basename(input_path) + ".log")


def report_path(config: Config, input_path: str) -> str:
    """Return the JSON report path for one source file."""
    return os.path.join(config.paths.logs, os.path.basename(input_path) + ".json")


def open_state(path: str) -> Optional[sqlite3.Connection]:
    """Open the state file read only. Return None when it does not exist."""
    if not os.path.isfile(path):
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % pathname2url(path), uri=True, timeout=5)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: Optional[sqlite3.Connection], sql: str, args=()) -> List[sqlite3.Row]:
    """Run one read-only query and return the rows, or an empty list."""
    if conn is None:
        return []
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def service_state(unit: str = UNIT) -> str:
    """Return the systemd state of the watcher unit, or 'unknown'."""
    systemctl = which("systemctl")
    if not systemctl:
        return "unknown"
    try:
        done = subprocess.run(
            [systemctl, "is-active", "--no-pager", unit],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (done.stdout or "").strip() or "unknown"


def tail(path: str, limit: int = 120) -> List[str]:
    """Return the last 'limit' lines of a file, reading only the end."""
    if not path or not os.path.isfile(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "r", errors="replace") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
                handle.readline()
            lines = handle.read().splitlines()
    except OSError:
        return []
    return lines[-limit:]


def head(path: str, limit: int = 60) -> List[str]:
    """Return the first 'limit' lines of a file.

    A long run writes thousands of progress lines, so the plan lines only sit
    near the start of the file.
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", errors="replace") as handle:
            lines = []
            for line in handle:
                lines.append(line.rstrip("\n"))
                if len(lines) >= limit or line.startswith("progress "):
                    break
            return lines
    except OSError:
        return []


def last_progress(lines: List[str]) -> Optional[Dict]:
    """Return the newest progress line as a dict, or None."""
    for line in reversed(lines):
        match = PROGRESS_RE.match(line.strip())
        if match:
            return {
                "pass": int(match.group(1)),
                "done": match.group(2),
                "percent": float(match.group(3)),
            }
    return None


def _plan(lines: List[str]) -> List[str]:
    """Collect the plan lines from the head of a job log."""
    found = []
    for line in lines:
        if PLAN_RE.match(line):
            found.append(line)
        if line.startswith("progress ") or line.startswith("---- log closed"):
            break
    return found

def _notes(lines: List[str]) -> List[str]:
    """Collect warning and note lines from a job log."""
    notes = []
    for line in lines:
        if NOTE_RE.match(line.strip()):
            notes.append(line.strip())
    return notes[-12:]


def _saved_percent(source: float, output: float) -> Optional[float]:
    """Return the percent saved, or None when the source size is unknown."""
    if not source or not output:
        return None
    return round(100.0 * (1.0 - float(output) / float(source)), 2)


def _job(row: sqlite3.Row, config: Config, log_lines: int, now: float) -> Dict:
    """Build one job entry from a state row plus its log and report."""
    input_path = row["input"] or ""
    name = os.path.basename(input_path)
    title = row["title"] or os.path.splitext(name)[0]
    year = row["year"]
    if year:
        title = "%s (%s)" % (title, year)
    source = row["source_size"] or 0
    output = row["output_size"] or 0
    seconds = row["seconds"] or 0.0
    started = row["started"] or 0.0
    status = row["status"]
    path = log_path(config, input_path)
    lines = tail(path, log_lines) if status == RUNNING else tail(path, 40)
    plan = _plan(head(path, 60))
    progress = last_progress(lines) if status == RUNNING else None
    entry = {
        "input": input_path,
        "name": name,
        "title": title,
        "year": year,
        "imdb_id": row["imdb_id"] or "",
        "status": status,
        "message": row["message"] or "",
        "profile": row["profile"] or "",
        "output": row["output"] or "",
        "source_size": source,
        "output_size": output,
        "saved_percent": _saved_percent(source, output),
        "seconds": seconds,
        "started": started,
        "finished": row["finished"] or 0.0,
        "elapsed": round(now - started, 1) if status == RUNNING and started else 0.0,
        "log_path": path,
        "log_exists": os.path.isfile(path),
        "report_path": report_path(config, input_path),
        "progress": progress,
        "plan": plan,
        "notes": _notes(lines),
    }
    if progress and entry["elapsed"] and progress["percent"] > 0.5:
        rate = progress["percent"] / entry["elapsed"]
        entry["eta_seconds"] = round((100.0 - progress["percent"]) / rate, 1)
    else:
        entry["eta_seconds"] = 0.0
    return entry


def job_detail(config: Config, name: str) -> Dict:
    """Return the plan, notes, log tail, and report for one job file name.

    The name must be a bare file name, so a caller cannot reach outside the
    logs folder.
    """
    if not name or name in (".", "..") or name != os.path.basename(name):
        return {"found": False, "reason": "bad name", "log_tail": [], "plan": [],
                "notes": [], "report": None}
    log_file = os.path.join(config.paths.logs, name + ".log")
    report_file = os.path.join(config.paths.logs, name + ".json")
    lines = tail(log_file, 400)
    report = None
    if os.path.isfile(report_file):
        try:
            with open(report_file, "r", errors="replace") as handle:
                report = json.load(handle)
        except (OSError, ValueError):
            report = None
    return {
        "found": os.path.isfile(log_file) or report is not None,
        "log_path": log_file,
        "log_tail": lines,
        "plan": _plan(head(log_file, 60)),
        "notes": _notes(lines),
        "report": report,
    }


def _queue_reason(name: str, size: int, min_bytes: int, config: Config) -> str:
    """Return why the scanner skips this file, or '' when it is eligible.

    This mirrors scan.acceptable and the size floor, so the page gives the
    same answer the watcher gives.
    """
    lowered = name.lower()
    ext = os.path.splitext(lowered)[1].lstrip(".")
    if config.watch.extensions and ext not in config.watch.extensions:
        return "wrong extension"
    for suffix in PARTIAL_SUFFIXES:
        if lowered.endswith(suffix):
            return "partial file"
    for prefix in config.watch.ignore_prefixes:
        if prefix and lowered.startswith(prefix.lower()):
            return "ignored name"
    if size < min_bytes:
        return "below min_size_mb"
    return ""


def _queue(config: Config, conn: Optional[sqlite3.Connection]) -> List[Dict]:
    """List every regular file in incoming, with the reason the scanner skips it.

    Files with the wrong extension stay in the list. The page shows why the
    watcher ignores them, which is what an operator wants to see.
    """
    items = []
    min_bytes = int(config.watch.min_size_mb * 1024 * 1024)
    try:
        names = sorted(os.listdir(config.paths.incoming))
    except OSError:
        return items
    seen = {row["path"]: row for row in rows(conn, "SELECT * FROM seen")}
    for name in names:
        path = os.path.join(config.paths.incoming, name)
        if not os.path.isfile(path):
            continue
        try:
            info = os.stat(path)
        except OSError:
            continue
        row = seen.get(path)
        items.append({
            "name": name,
            "path": path,
            "size": info.st_size,
            "mtime": info.st_mtime,
            "hidden": name.startswith(".") or name.startswith("_"),
            "reason": _queue_reason(name, info.st_size, min_bytes, config),
            "min_size_bytes": min_bytes,
            "seen": bool(row),
            "first_seen": row["first_seen"] if row else 0.0,
            "last_seen": row["last_seen"] if row else 0.0,
            "stable": bool(row and abs((row["size"] or 0) - info.st_size) == 0),
        })
    items.sort(key=lambda item: item["mtime"])
    return items


def safe_under(root: str, rel: str) -> Optional[str]:
    """Resolve 'rel' inside 'root'. Return None when it escapes the folder.

    A download name may contain folders, because the library uses one folder
    per title. It may never contain '..' or start with a slash.
    """
    if not rel or rel.startswith(("/", "\\")):
        return None
    parts = rel.replace("\\", "/").split("/")
    if any(part in ("", "..") for part in parts):
        return None
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, *parts))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full if os.path.isfile(full) else None


def _downloads(config: Config, conn: Optional[sqlite3.Connection]) -> List[Dict]:
    """List finished files the page can offer for download."""
    items: List[Dict] = []
    sql = ("SELECT output, title, year FROM jobs WHERE status = ? AND output IS NOT NULL "
           "ORDER BY id DESC")
    for row in rows(conn, sql, (DONE,)):
        output = row["output"] or ""
        if not os.path.isfile(output):
            continue
        rel = rel_under(config.paths.transcoded, output)
        if rel is None:
            continue
        label = row["title"] or os.path.splitext(os.path.basename(output))[0]
        if row["year"]:
            label = "%s (%s)" % (label, row["year"])
        items.append({
            "kind": "output", "label": label, "path": rel,
            "size": os.path.getsize(output), "suffix": "MKV",
        })
        nfo = os.path.splitext(output)[0] + ".nfo"
        if os.path.isfile(nfo):
            nfo_rel = rel_under(config.paths.transcoded, nfo)
            if nfo_rel:
                items.append({
                    "kind": "nfo", "label": label, "path": nfo_rel,
                    "size": os.path.getsize(nfo), "suffix": "NFO",
                })
    return items


def rel_under(root: str, path: str) -> Optional[str]:
    """Return 'path' relative to 'root', or None when it sits outside."""
    base = os.path.realpath(root)
    full = os.path.realpath(path)
    if not full.startswith(base + os.sep):
        return None
    return os.path.relpath(full, base)


def _upload_info(config: Config, allow_upload: bool, max_bytes: int) -> Dict:
    """Describe the upload form so the page can show the right controls."""
    return {
        "enabled": bool(allow_upload),
        "incoming": config.paths.incoming,
        "max_bytes": int(max_bytes),
        "extensions": list(config.watch.extensions),
        "free": free_bytes(config.paths.incoming),
    }


def _workdir(config: Config) -> List[Dict]:
    """List partial output files, which show the size of a run in flight."""
    items = []
    try:
        names = sorted(os.listdir(config.paths.work))
    except OSError:
        return items
    for name in names:
        path = os.path.join(config.paths.work, name)
        if not os.path.isfile(path):
            continue
        try:
            info = os.stat(path)
        except OSError:
            continue
        items.append({"name": name, "path": path, "size": info.st_size})
    return items


def _totals(conn: Optional[sqlite3.Connection]) -> Dict:
    """Sum the finished work so the page can show one big number."""
    row = rows(conn, "SELECT COUNT(*) AS n, SUM(source_size) AS src, SUM(output_size) AS out,"
                     " SUM(seconds) AS secs FROM jobs WHERE status = ?", (DONE,))
    if not row:
        return {"jobs": 0, "source_bytes": 0, "output_bytes": 0, "encode_seconds": 0.0,
                "saved_percent": None}
    data = row[0]
    source = data["src"] or 0
    output = data["out"] or 0
    return {
        "jobs": data["n"] or 0,
        "source_bytes": source,
        "output_bytes": output,
        "encode_seconds": float(data["secs"] or 0.0),
        "saved_percent": _saved_percent(source, output),
    }


def _disk(config: Config) -> Dict:
    """Report free space for the library folder."""
    path = config.paths.transcoded
    free = free_bytes(path)
    total = 0
    try:
        info = os.statvfs(path)
        total = info.f_blocks * info.f_frsize
    except OSError:
        total = 0
    return {"path": path, "free": free, "total": total,
            "min_free_bytes": int(config.job.min_free_gb * 1024 ** 3)}


def _config_summary(config: Config) -> Dict:
    """Show the settings an operator checks first."""
    profile = config.active_profile()
    return {
        "profile": config.video.profile,
        "encoder": profile.encoder,
        "crf": profile.crf,
        "preset": profile.preset,
        "pixel_format": profile.pixel_format,
        "audio_codec": config.audio.codec,
        "audio_policy": config.audio.policy,
        "max_parallel": config.job.max_parallel,
        "max_attempts": config.job.max_attempts,
        "timeout_hours": config.job.timeout_hours,
        "source_action": config.output.source_action,
        "paths": {
            "incoming": config.paths.incoming,
            "transcoded": config.paths.transcoded,
            "processed": config.paths.processed,
            "work": config.paths.work,
            "logs": config.paths.logs,
        },
    }


def snapshot(config: Config, recent_limit: int = 80, log_lines: int = 120,
             allow_upload: bool = False, max_upload_bytes: int = 0) -> Dict:
    """Build one JSON friendly snapshot of the whole pipeline."""
    now = time.time()
    path = state_path(config)
    conn = open_state(path)
    try:
        counts: Dict[str, int] = {}
        for row in rows(conn, "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
            counts[row["status"]] = row["n"]
        running = [
            _job(row, config, log_lines, now)
            for row in rows(conn, "SELECT * FROM jobs WHERE status = ? ORDER BY started DESC",
                            (RUNNING,))
        ]
        recent = [
            _job(row, config, log_lines, now)
            for row in rows(conn, "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (recent_limit,))
        ]
        errors = [
            _job(row, config, log_lines, now)
            for row in rows(conn, "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT 20",
                            (FAILED,))
        ]
        skipped = rows(conn, "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT 20",
                       (SKIPPED,))
        totals = _totals(conn)
        queue = _queue(config, conn)
        downloads = _downloads(config, conn)
    finally:
        if conn is not None:
            conn.close()
    return {
        "generated": now,
        "version": __version__,
        "host": socket.gethostname(),
        "unit": UNIT,
        "service": service_state(),
        "state_file": path,
        "state_exists": os.path.isfile(path),
        "config": _config_summary(config),
        "counts": counts,
        "totals": totals,
        "running": running,
        "recent": recent,
        "errors": errors,
        "skipped": [
            {
                "name": os.path.basename(row["input"] or ""),
                "title": row["title"] or os.path.basename(row["input"] or ""),
                "status": row["status"],
                "message": row["message"] or "",
            }
            for row in skipped
        ],
        "queue": queue,
        "downloads": downloads,
        "upload": _upload_info(config, allow_upload, max_upload_bytes),
        "work": _workdir(config),
        "disk": _disk(config),
    }
