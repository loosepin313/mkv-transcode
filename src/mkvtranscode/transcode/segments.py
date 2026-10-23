"""Split one encode across several GPUs, then join the pieces.

A single ffmpeg process uses one GPU for NVENC. To use two cards on one file,
the tool cuts the job into segments by time, encodes each segment on its own
card at the same time, and joins the pieces with a stream copy. The join costs
no quality, because it never re-encodes.

The audio and the subtitles do not follow the split. Cutting them at a segment
boundary adds padding at every join, which makes the finished file longer than
the source. The tool encodes them once for the whole file, in parallel with the
video segments, and muxes them in at the join.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from threading import Lock, Thread
from typing import Dict, List, Optional

from ..errors import TranscodeError
from ..log import get_logger
from .runner import RunResult, run_ffmpeg

log = get_logger("segments")

# Do not split a film into pieces shorter than this.
MIN_SEGMENT_SECONDS = 300.0
# How often the joined progress line is written.
REPORT_SECONDS = 15.0


class Segment:
    """One slice of the source, in seconds."""

    def __init__(self, index: int, start: float, length: float):
        self.index = index
        self.start = start
        self.length = length

    def __repr__(self) -> str:
        return "Segment(%d, start=%.1f, length=%.1f)" % (self.index, self.start, self.length)


def plan_segments(duration: float, count: int,
                  min_seconds: float = MIN_SEGMENT_SECONDS) -> List[Segment]:
    """Return the segments for one job.

    The list holds one entry when the job cannot be split. A short film or a
    single GPU means no split.
    """
    if count < 2 or duration <= 0:
        return [Segment(0, 0.0, duration)]
    if min_seconds:
        count = min(int(count), int(duration // min_seconds))
    if count < 2:
        return [Segment(0, 0.0, duration)]
    length = duration / count
    segments = []
    for index in range(count):
        start = index * length
        # The last segment takes the remainder, so the parts add up exactly.
        stop = duration if index == count - 1 else (index + 1) * length
        segments.append(Segment(index, start, stop - start))
    return segments


def segment_path(work_path: str, index: int) -> str:
    """Return the file name for one video segment."""
    base, ext = os.path.splitext(work_path)
    return "%s.seg%d%s" % (base, index, ext)


def audio_path(work_path: str) -> str:
    """Return the file name for the audio and subtitle pass."""
    base, _ = os.path.splitext(work_path)
    return base + ".audio.mkv"


def video_only(plan, output_path: str):
    """Return a copy of the plan that holds video alone."""
    return replace(plan, audio=[], subtitles=[], output_path=output_path,
                   chapters=False, title_tag="", imdb_id="")


def tracks_only(plan, output_path: str):
    """Return a copy of the plan that holds audio and subtitles alone."""
    return replace(plan, video=None, filters=[], output_path=output_path,
                   chapters=False, title_tag="", imdb_id="")


def segment_command(plan, segment: Segment, output_path: str) -> List[str]:
    """Build the ffmpeg arguments for one video segment.

    '-ss' before '-i' seeks to the keyframe before the start, then ffmpeg
    decodes and drops frames until the exact start. Each segment therefore
    begins on a fresh keyframe, and the parts join without a seam.
    """
    args = video_only(plan, output_path).command(0)
    args = _insert_before(args, "-i", ["-ss", "%.3f" % segment.start])
    args = _insert_before(args, "-f", ["-t", "%.3f" % segment.length], last=True)
    return args


def audio_command(plan, output_path: str) -> List[str]:
    """Build the ffmpeg arguments for the audio and subtitle pass."""
    return tracks_only(plan, output_path).command(0)


def merge_command(list_path: str, sound_path: str, plan, output_path: str) -> List[str]:
    """Build the join command. The join is a stream copy."""
    args = [
        "-hide_banner",
        "-nostdin",
        "-loglevel", "warning",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-i", sound_path,
        "-map", "0:v",
    ]
    if plan.audio:
        args += ["-map", "1:a"]
    if plan.subtitles:
        args += ["-map", "1:s"]
    args += ["-c", "copy"]
    args += plan._metadata_args()
    args += ["-progress", "pipe:1", "-nostats", "-f", "matroska", output_path]
    return args


def write_list(paths: List[str], list_path: str) -> None:
    """Write the concat list file. Single quotes inside a name are escaped."""
    with open(list_path, "w", encoding="utf-8") as handle:
        for path in paths:
            handle.write("file '%s'\n" % path.replace("'", "'\\''"))


def encode_segments(plan, segments: List[Segment], devices: List[int], work_path: str,
                    timeout_seconds: float, nice: int, on_progress=None) -> List[RunResult]:
    """Encode every video segment at the same time, plus one audio pass.

    on_progress takes (seconds_done, percent) for the whole job.
    """
    total = sum(segment.length for segment in segments)
    paths = [segment_path(work_path, segment.index) for segment in segments]
    sound = audio_path(work_path)
    done: Dict[int, float] = {}
    lock = Lock()
    results: Dict[int, RunResult] = {}
    errors: List[str] = []
    audio_result: List[RunResult] = []

    def video(segment: Segment, device: Optional[int], path: str) -> None:
        env = {"CUDA_VISIBLE_DEVICES": str(device)} if device is not None else None
        try:
            results[segment.index] = run_ffmpeg(
                segment_command(plan, segment, path),
                duration=segment.length,
                timeout_seconds=timeout_seconds,
                nice=nice,
                progress_interval=5.0,
                on_progress=lambda seconds, _percent: _note(done, lock, segment.index, seconds),
                env=env,
            )
        except TranscodeError as exc:
            errors.append("segment %d: %s" % (segment.index + 1, exc))

    def sound_pass() -> None:
        try:
            audio_result.append(run_ffmpeg(
                audio_command(plan, sound),
                duration=total,
                timeout_seconds=timeout_seconds,
                nice=nice,
            ))
        except TranscodeError as exc:
            errors.append("audio: %s" % exc)

    threads = [Thread(target=sound_pass, name="audio")]
    for position, segment in enumerate(segments):
        device = devices[position] if position < len(devices) else None
        threads.append(Thread(target=video, args=(segment, device, paths[position])))
    for thread in threads:
        thread.start()

    _report(threads, done, lock, total, on_progress)
    for thread in threads:
        thread.join()
    if errors:
        raise TranscodeError("; ".join(errors))
    if not audio_result or not audio_result[0].ok:
        tail = audio_result[0].stderr_tail[-400:] if audio_result else "no result"
        raise TranscodeError("audio pass exited with %s: %s"
                             % (getattr(audio_result[0], "returncode", "?"), tail))
    failed = [results[i] for i in sorted(results) if not results[i].ok]
    if failed:
        raise TranscodeError("ffmpeg exited with %d: %s"
                             % (failed[0].returncode, failed[0].stderr_tail[-400:]))
    return [results[index] for index in sorted(results)]


def _note(done: Dict[int, float], lock: Lock, index: int, seconds: float) -> None:
    with lock:
        done[index] = seconds


def _report(threads, done: Dict[int, float], lock: Lock, total: float, on_progress) -> None:
    """Write one progress line for the whole job while the segments run."""
    if on_progress is None:
        while any(thread.is_alive() for thread in threads):
            time.sleep(REPORT_SECONDS)
        return
    last = 0.0
    while any(thread.is_alive() for thread in threads):
        time.sleep(REPORT_SECONDS)
        now = time.time()
        if now - last < REPORT_SECONDS:
            continue
        last = now
        with lock:
            seconds = sum(done.values())
        percent = (seconds / total * 100.0) if total else 0.0
        on_progress(seconds, percent)


def _insert_before(args: List[str], token: str, value: List[str], last: bool = False) -> List[str]:
    """Insert value before the first (or last) bare token equal to token."""
    positions = [i for i, part in enumerate(args) if part == token]
    if not positions:
        return args + value
    at = positions[-1] if last else positions[0]
    return args[:at] + value + args[at:]
