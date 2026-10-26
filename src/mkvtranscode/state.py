"""Job state stored in a SQLite file.

The state file lets the daemon skip work it already finished and gives
'mkv-transcode status' something to report.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input TEXT NOT NULL UNIQUE,
    output TEXT,
    status TEXT NOT NULL,
    started REAL,
    finished REAL,
    seconds REAL,
    source_size INTEGER,
    output_size INTEGER,
    title TEXT,
    year INTEGER,
    imdb_id TEXT,
    profile TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS seen (
    path TEXT PRIMARY KEY,
    size INTEGER,
    mtime REAL,
    first_seen REAL,
    last_seen REAL
);
"""

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass
class JobRecord:
    input: str
    output: str = ""
    status: str = PENDING
    started: float = 0.0
    finished: float = 0.0
    seconds: float = 0.0
    source_size: int = 0
    output_size: int = 0
    title: str = ""
    year: Optional[int] = None
    imdb_id: str = ""
    profile: str = ""
    message: str = ""


class State:
    """A small SQLite store for job history."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # The watcher runs jobs on several threads. One connection serves them
        # all, guarded by a lock, so a job never touches a foreign connection.
        self.conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---- jobs ----------------------------------------------------------

    def get_job(self, input_path: str) -> Optional[JobRecord]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM jobs WHERE input = ?", (input_path,)).fetchone()
        return _record(row) if row else None

    def start_job(self, record: JobRecord) -> None:
        record.status = RUNNING
        record.started = time.time()
        with self._lock:
            self.conn.execute(
            """INSERT INTO jobs (input, status, started, source_size, title, year, imdb_id, profile)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(input) DO UPDATE SET status=excluded.status,
                   started=excluded.started, source_size=excluded.source_size,
                   title=excluded.title, year=excluded.year, imdb_id=excluded.imdb_id,
                   profile=excluded.profile""",
            (record.input, record.status, record.started, record.source_size,
             record.title, record.year, record.imdb_id, record.profile),
            )
            self.conn.commit()

    def finish_job(self, record: JobRecord) -> None:
        record.finished = time.time()
        if not record.seconds and record.started:
            record.seconds = record.finished - record.started
        with self._lock:
            self.conn.execute(
                """UPDATE jobs SET output=?, status=?, finished=?, seconds=?, output_size=?,
                      title=?, year=?, imdb_id=?, profile=?, message=? WHERE input=?""",
                (record.output, record.status, record.finished, record.seconds, record.output_size,
                 record.title, record.year, record.imdb_id, record.profile, record.message, record.input),
            )
            self.conn.commit()

    def recent_jobs(self, limit: int = 20, status: Optional[str] = None) -> List[JobRecord]:
        with self._lock:
            if status:
                rows = self.conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
                ).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_record(row) for row in rows]

    def counts(self) -> dict:
        with self._lock:
            rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {row["status"]: row["n"] for row in rows}

    def clear_jobs(self) -> int:
        """Delete every job row. Return how many rows went.

        Finished files, logs, reports, and the seen table stay on disk. Only
        the history in the state file goes, so the next scan sees the files in
        incoming as new work.
        """
        with self._lock:
            cursor = self.conn.execute("DELETE FROM jobs")
            removed = cursor.rowcount
            self.conn.commit()
        return removed

    def is_finished(self, input_path: str, size: int = 0) -> bool:
        """Return True when a done or skipped job covers this file.

        The exact path is the normal match. When you move the folders, fall
        back to the file name and the size. That stops a finished film from
        encoding twice.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT status FROM jobs WHERE input = ?", (input_path,)
            ).fetchone()
            if row and row["status"] in (DONE, SKIPPED):
                return True
            if not size:
                return False
            name = os.path.basename(input_path).replace("\\", "\\\\")
            name = name.replace("%", "\\%").replace("_", "\\_")
            row = self.conn.execute(
                "SELECT status FROM jobs WHERE status IN (?, ?) AND source_size = ? "
                "AND input LIKE ? ESCAPE '\\'",
                (DONE, SKIPPED, size, "%" + name),
            ).fetchone()
        return bool(row)

    # ---- seen files ----------------------------------------------------

    def note_seen(self, path: str, size: int, mtime: float) -> None:
        now = time.time()
        with self._lock:
            self.conn.execute(
                """INSERT INTO seen (path, size, mtime, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,
                       last_seen=excluded.last_seen""",
                (path, size, mtime, now, now),
            )
            self.conn.commit()

    def forget_seen(self, path: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM seen WHERE path = ?", (path,))
            self.conn.commit()


def _record(row) -> JobRecord:
    return JobRecord(
        input=row["input"],
        output=row["output"] or "",
        status=row["status"],
        started=row["started"] or 0.0,
        finished=row["finished"] or 0.0,
        seconds=row["seconds"] or 0.0,
        source_size=row["source_size"] or 0,
        output_size=row["output_size"] or 0,
        title=row["title"] or "",
        year=row["year"],
        imdb_id=row["imdb_id"] or "",
        profile=row["profile"] or "",
        message=row["message"] or "",
    )
