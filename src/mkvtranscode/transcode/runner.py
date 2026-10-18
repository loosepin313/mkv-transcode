"""Run ffmpeg and report progress.

The runner adds 'nice' and 'ionice' when configured, so a batch job does not
make the desktop slow. It reads the ffmpeg progress stream and logs percent
complete at a fixed interval.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import List

from ..errors import TranscodeError
from ..log import get_logger
from ..util import which

log = get_logger("ffmpeg")
PROGRESS_INTERVAL = 30.0


@dataclass
class RunResult:
    command: str
    seconds: float
    returncode: int
    stderr_tail: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def build_argv(command: List[str], nice: int = 0) -> List[str]:
    """Prefix the command with nice and ionice when those tools exist."""
    argv = list(command)
    prefix: List[str] = []
    ionice = which("ionice")
    if ionice:
        prefix += [ionice, "-c2", "-n7"]
    if nice and which("nice"):
        prefix += [nice_tool(), "-n", str(nice)]
    return prefix + argv


def nice_tool() -> str:
    """Return the path to nice, or the bare name."""
    return which("nice") or "nice"


def run_ffmpeg(command: List[str], duration: float, timeout_seconds: float,
               nice: int = 10, progress_interval: float = PROGRESS_INTERVAL,
               on_progress=None) -> RunResult:
    """Run one ffmpeg pass and return a RunResult.

    on_progress takes (seconds_done, percent).
    """
    argv = build_argv(["ffmpeg"] + command, nice)
    printable = " ".join(argv)
    log.debug("run: %s", printable)
    started = time.time()
    stderr_lines: List[str] = []
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except OSError as exc:
        raise TranscodeError("cannot start ffmpeg: %s" % exc)

    last_report = 0.0
    done_seconds = 0.0
    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    done_seconds = int(line.split("=", 1)[1]) / 1000000.0
                except ValueError:
                    continue
            elif line.startswith("out_time="):
                done_seconds = _parse_clock(line.split("=", 1)[1])
            if timeout_seconds and (time.time() - started) > timeout_seconds:
                _stop(process)
                raise TranscodeError(
                    "ffmpeg exceeded the timeout of %.1f hours" % (timeout_seconds / 3600)
                )
            now = time.time()
            if on_progress and now - last_report >= progress_interval:
                last_report = now
                percent = (done_seconds / duration * 100.0) if duration else 0.0
                on_progress(done_seconds, percent)
        assert process.stderr is not None
        for line in process.stderr:
            text = line.rstrip()
            if text:
                stderr_lines.append(text)
        returncode = process.wait()
    except KeyboardInterrupt:
        _stop(process)
        raise
    seconds = time.time() - started
    tail = "\n".join(stderr_lines[-40:])
    return RunResult(command=printable, seconds=seconds, returncode=returncode, stderr_tail=tail)


def _parse_clock(text: str) -> float:
    """Convert an ffmpeg clock such as '01:23:45.120000' to seconds."""
    try:
        clock = text.split(".")[0]
        parts = [float(part) for part in clock.split(":")]
    except ValueError:
        return 0.0
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def _stop(process: subprocess.Popen) -> None:
    """Stop a running ffmpeg process group."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        for _ in range(50):
            if process.poll() is not None:
                return
            time.sleep(0.1)
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except Exception:
            pass


def verify_output(path: str, source_size: int, min_ratio: float, duration: float) -> str:
    """Check the finished file. Return an error string, or '' when it looks good."""
    if not os.path.isfile(path):
        return "output file is missing"
    size = os.path.getsize(path)
    if size < 64 * 1024:
        return "output file is only %d bytes" % size
    if source_size and size < source_size * min_ratio:
        return "output is %.2f%% of the source, below output.min_output_ratio" % (
            100.0 * size / source_size
        )
    return ""
