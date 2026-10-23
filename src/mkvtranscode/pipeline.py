"""The pipeline: probe, name, plan, transcode, verify, place, report.

One call to Pipeline.process handles one source file from end to end. The
daemon and the command line both use it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .config import Config
from .errors import MkvTranscodeError, ProbeError, SkipFile
from .log import FileLog, get_logger
from .metadata import Provider, build_provider, resolve_title
from .metadata.types import Title
from .naming import build_output_path, parse_filename, unique_path
from .nfo import write_nfo
from .probe import MediaInfo, ffprobe
from .scan import is_ready, list_candidates
from .state import DONE, FAILED, SKIPPED, JobRecord, State
from .transcode import build_plan, run_ffmpeg, verify_output
from .util import (
    cross_device_move,
    ensure_dir,
    free_bytes,
    human_duration,
    human_size,
    mount_fstype,
    open_private,
    same_device,
    sanitize_component,
)

log = get_logger("pipeline")

# Only write an NFO file when the metadata match is strong.
NFO_MIN_CONFIDENCE = 0.8


@dataclass
class Outcome:
    """The result of one job."""

    input: str
    output: str = ""
    status: str = ""
    message: str = ""
    seconds: float = 0.0
    source_size: int = 0
    output_size: int = 0
    title: str = ""
    year: Optional[int] = None
    imdb_id: str = ""
    profile: str = ""
    plan_lines: List[str] = field(default_factory=list)
    log_path: str = ""
    report_path: str = ""

    @property
    def saved_ratio(self) -> float:
        """Fraction of the source size that the output uses."""
        if not self.source_size or not self.output_size:
            return 0.0
        return self.output_size / float(self.source_size)

    @property
    def saved_percent(self) -> float:
        return (1.0 - self.saved_ratio) * 100.0


class Pipeline:
    """Run the whole transcode flow for a file."""

    def __init__(self, config: Config, state: Optional[State] = None,
                 provider: Optional[Provider] = None):
        self.config = config
        self.state = state
        self.provider = provider if provider is not None else build_provider(config)

    # ---- public API ----------------------------------------------------

    def process(self, path: str, dry_run: bool = False, force: bool = False,
                gpu: Optional[int] = None) -> Outcome:
        """Process one source file. Never raises for a normal failure."""
        started = time.time()
        outcome = Outcome(input=path, profile=self.config.video.profile)
        name = os.path.basename(path)
        ensure_dir(self.config.paths.logs)
        log_path = os.path.join(self.config.paths.logs, sanitize_component(name) + ".log")
        outcome.log_path = log_path

        try:
            with FileLog(log_path, log) as journal:
                journal.log("input: %s" % path)
                self._run(path, dry_run, force, outcome, journal, gpu)
                outcome.status = outcome.status or DONE
                journal.log("status: %s %s" % (outcome.status, outcome.message))
        except SkipFile as exc:
            outcome.status = SKIPPED
            outcome.message = str(exc)
            log.info("skip %s: %s", name, exc)
        except MkvTranscodeError as exc:
            outcome.status = FAILED
            outcome.message = str(exc)
            log.error("failed %s: %s", name, exc)
        except KeyboardInterrupt:
            outcome.status = FAILED
            outcome.message = "interrupted by the user"
            self._cleanup_work(outcome)
            raise
        except Exception as exc:  # keep the daemon alive on a bug
            outcome.status = FAILED
            outcome.message = "unexpected error: %s" % exc
            log.exception("unexpected error on %s", name)
            self._cleanup_work(outcome)

        outcome.seconds = time.time() - started
        self._finish(outcome, force)
        return outcome

    def process_ready(self, dry_run: bool = False) -> List[Outcome]:
        """Scan incoming once and process every ready file."""
        outcomes: List[Outcome] = []
        for candidate in list_candidates(self.config):
            if not is_ready(candidate.path, self.config):
                log.debug("not ready yet: %s", os.path.basename(candidate.path))
                continue
            outcomes.append(self.process(candidate.path, dry_run=dry_run))
        return outcomes

    # ---- internals -----------------------------------------------------

    def _run(self, path: str, dry_run: bool, force: bool, outcome: Outcome, journal,
             gpu: Optional[int] = None) -> None:
        config = self.config
        if os.path.islink(path):
            raise SkipFile("input is a symbolic link, not a file")
        if not os.path.isfile(path):
            raise SkipFile("file is gone")
        stat = os.lstat(path)
        outcome.source_size = stat.st_size

        if not force and self.state and self.state.is_finished(path, stat.st_size):
            raise SkipFile("already processed in a previous run")
        if not force and stat.st_size < config.watch.min_size_mb * 1024 * 1024:
            raise SkipFile("smaller than watch.min_size_mb")

        free = free_bytes(config.paths.transcoded)
        needed = max(int(config.job.min_free_gb * 1024 ** 3), stat.st_size // 2)
        if free < needed:
            raise SkipFile("only %s free in %s, need %s"
                           % (human_size(free), config.paths.transcoded, human_size(needed)))

        info = ffprobe(path)
        outcome.plan_lines.append("source: %s, %s, %s, %s" % (
            info.format_name or "?", human_size(info.size), human_duration(info.duration),
            "%dx%d" % (info.primary_video().width or 0, info.primary_video().height or 0)))

        guess = parse_filename(path)
        title = resolve_title(config, guess, self.provider)
        outcome.title = title.display_title(config.naming.use_original_title)
        outcome.year = title.year
        outcome.imdb_id = title.imdb_id or ""
        journal.log("named: %s (confidence %.2f)" % (title.summary(), title.confidence))
        if guess.year and title.year and abs(guess.year - title.year) > 3:
            journal.log("warning: the file name says %d, IMDb says %d. Check the match."
                        % (guess.year, title.year))

        output_path = build_output_path(
            root=config.paths.transcoded,
            title=title.display_title(config.naming.use_original_title),
            year=title.year,
            pattern=config.naming.pattern,
            episode=guess.episode,
            imdb_id=title.imdb_id,
            part=guess.part if config.naming.multi_part else None,
            container=config.output.container,
        )
        if os.path.exists(output_path) and not config.output.overwrite:
            if not config.naming.keep_source_on_conflict:
                raise SkipFile("output exists: %s" % output_path)
            output_path = unique_path(output_path)
            journal.log("output name taken, using %s" % output_path)
        outcome.output = output_path

        work_path = os.path.join(
            config.paths.work,
            sanitize_component(os.path.basename(output_path)) + ".work.mkv",
        )
        if not dry_run:
            ensure_dir(os.path.dirname(output_path))
            ensure_dir(config.paths.work)

        plan = build_plan(config, info, work_path, title_tag=_tag(title), imdb_id=title.imdb_id or "")
        outcome.profile = plan.profile
        for line in plan.summary():
            journal.log(line)
        journal.log("command: %s" % plan.full_command())

        if dry_run:
            outcome.status = "planned"
            outcome.message = "dry run: %s" % plan.full_command()
            return

        record = JobRecord(
            input=path, output=output_path, source_size=stat.st_size,
            title=title.title, year=title.year, imdb_id=title.imdb_id or "",
            profile=plan.profile,
        )
        if self.state:
            self.state.start_job(record)

        if not same_device(config.paths.work, config.paths.transcoded):
            journal.log("warning: the work dir %s and the library %s are on different "
                        "file systems. The final move copies the whole file."
                        % (config.paths.work, config.paths.transcoded))
            if mount_fstype(config.paths.work) in ("tmpfs", "ramfs"):
                journal.log("warning: %s is %s, which uses RAM. A large film can fill it."
                            % (config.paths.work, mount_fstype(config.paths.work)))

        if gpu is not None:
            journal.log("gpu: %d" % gpu)
        self._execute(plan, info, journal, gpu)

        problem = verify_output(work_path, stat.st_size, config.output.min_output_ratio, info.duration)
        if problem:
            raise MkvTranscodeError("output check failed: %s" % problem)
        self._check_output_streams(work_path, info, journal)

        cross_device_move(work_path, output_path)
        if not same_device(config.paths.work, os.path.dirname(output_path)):
            journal.log("copied the finished file from %s to the library. The two paths "
                        "are on different file systems." % config.paths.work)
        outcome.output_size = os.path.getsize(output_path)
        ratio = 100.0 * outcome.output_size / float(stat.st_size) if stat.st_size else 0.0
        journal.log("wrote %s (%s, %.1f%% of the source)"
                    % (output_path, human_size(outcome.output_size), ratio))
        if outcome.output_size > stat.st_size:
            journal.log("warning: the output is larger than the source. The source is "
                        "already compressed, or the CRF is too low.")

        if config.output.write_nfo and title.confidence >= NFO_MIN_CONFIDENCE:
            nfo_path = os.path.splitext(output_path)[0] + ".nfo"
            write_nfo(nfo_path, title)
            journal.log("wrote %s" % nfo_path)

        self._handle_source(path, journal)
        outcome.message = "ok"

    def _execute(self, plan, info: MediaInfo, journal, gpu: Optional[int] = None) -> None:
        """Run every ffmpeg pass and stop on the first failure."""
        total = plan.passes
        env = {"CUDA_VISIBLE_DEVICES": str(gpu)} if gpu is not None else None
        for index in range(total):
            if total > 1:
                journal.log("pass %d of %d" % (index + 1, total))
            command = plan.command(index)
            result = run_ffmpeg(
                command,
                duration=info.duration if index == total - 1 else info.duration,
                timeout_seconds=self.config.job.timeout_hours * 3600,
                nice=self.config.job.nice,
                on_progress=lambda done, percent: journal.log(
                    "progress pass %d: %s (%.1f%%)" % (index + 1, human_duration(done), percent)
                ),
                env=env,
            )
            if result.stderr_tail:
                journal.write(result.stderr_tail)
            if not result.ok:
                raise MkvTranscodeError(
                    "ffmpeg exited with %d: %s" % (result.returncode, _tail(result.stderr_tail))
                )
            journal.log("pass %d finished in %s" % (index + 1, human_duration(result.seconds)))
        self._remove_passlogs(plan)

    def _check_output_streams(self, work_path: str, source: MediaInfo, journal) -> None:
        """Probe the finished file and compare it with the source."""
        try:
            out = ffprobe(work_path)
        except ProbeError as exc:
            raise MkvTranscodeError("output is not readable: %s" % exc)
        if not out.videos():
            raise MkvTranscodeError("output has no video stream")
        if not out.audios():
            raise MkvTranscodeError("output has no audio stream")
        if source.duration and out.duration:
            drift = abs(out.duration - source.duration) / source.duration
            if drift > self.config.output.max_duration_drift:
                raise MkvTranscodeError(
                    "output duration %s differs from source %s by %.1f%% "
                    "(limit %.1f%%). The source may be incomplete or damaged."
                    % (human_duration(out.duration), human_duration(source.duration),
                       drift * 100, self.config.output.max_duration_drift * 100)
                )
        journal.log("output check: %s, %d audio, %d subtitle track(s)"
                    % (human_duration(out.duration), len(out.audios()), len(out.subtitles())))

    def _handle_source(self, path: str, journal) -> None:
        """Move, keep or delete the source file after a successful job."""
        config = self.config
        action = config.output.source_action
        if config.output.delete_source:
            action = "delete"              # the legacy key still works
        if action == "keep":
            return
        if action == "delete":
            os.unlink(path)
            journal.log("source deleted: %s" % path)
            return
        # An empty move_source_to means paths.processed. A relative name is a
        # sub folder inside paths.processed.
        target = config.output.move_source_to or config.paths.processed
        if not os.path.isabs(target):
            target = os.path.join(config.paths.processed, target)
        ensure_dir(target)
        destination = unique_path(os.path.join(target, os.path.basename(path)))
        cross_device_move(path, destination)
        journal.log("source moved to %s" % destination)

    def _remove_passlogs(self, plan) -> None:
        directory = os.path.dirname(plan.output_path) or "."
        base = os.path.join(directory, "ffmpeg-passlog")
        for suffix in ("-0.log", ".log", "-0.mbtree", ".mbtree"):
            path = base + suffix
            if os.path.exists(path):
                os.unlink(path)

    def _cleanup_work(self, outcome: Outcome) -> None:
        if not outcome.output:
            return
        work = os.path.join(
            self.config.paths.work,
            sanitize_component(os.path.basename(outcome.output)) + ".work.mkv",
        )
        if os.path.exists(work) and self.config.job.keep_work_dir_on_error:
            log.warning("kept partial output at %s", work)
        elif os.path.exists(work):
            os.unlink(work)

    def _finish(self, outcome: Outcome, force: bool) -> None:
        if self.state and outcome.status in (DONE, FAILED, SKIPPED):
            self.state.finish_job(JobRecord(
                input=outcome.input, output=outcome.output, status=outcome.status,
                seconds=outcome.seconds, source_size=outcome.source_size,
                output_size=outcome.output_size, title=outcome.title, year=outcome.year,
                imdb_id=outcome.imdb_id, profile=outcome.profile, message=outcome.message,
            ))
        if self.config.output.write_report and outcome.status != "planned":
            self._write_report(outcome)
        if outcome.status == DONE:
            log.info(
                "done: %s -> %s (%s, %.1f%% of source, %s)",
                os.path.basename(outcome.input),
                os.path.relpath(outcome.output, self.config.paths.transcoded),
                human_size(outcome.output_size),
                100.0 * outcome.output_size / float(outcome.source_size) if outcome.source_size else 0.0,
                human_duration(outcome.seconds),
            )

    def _write_report(self, outcome: Outcome) -> None:
        ensure_dir(self.config.paths.logs)
        stem = sanitize_component(os.path.basename(outcome.input))
        path = os.path.join(self.config.paths.logs, stem + ".json")
        outcome.report_path = path
        payload = asdict(outcome)
        payload["saved_percent"] = round(outcome.saved_percent, 2)
        tmp = path + ".part"
        with open_private(tmp) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)


def _tag(title: Title) -> str:
    if title.year:
        return "%s (%s)" % (title.title, title.year)
    return title.title


def _tail(text: str, lines: int = 4) -> str:
    """Return the last lines of a log as one line."""
    if not text:
        return ""
    return " | ".join(part for part in text.strip().splitlines()[-lines:] if part)
