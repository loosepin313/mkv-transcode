# Architecture

## Modules

```
src/mkvtranscode/
  cli.py              Command line parsing and the subcommands.
  config.py           Defaults, TOML loading, environment overrides, validation.
  toml_compat.py      TOML reader for Python 3.9 and 3.10.
  scan.py             Find input files. Decide when a file is complete.
  watcher.py          The daemon loop. inotify or polling.
  inotify.py          inotify through ctypes. No third-party dependency.
  probe.py            ffprobe wrapper. Streams, colour, field order.
  naming.py           File name parsing and output path building.
  metadata/           IMDb providers and the resolver.
  transcode/command.py  Build the ffmpeg argument list.
  transcode/runner.py   Run ffmpeg, report progress, check the output.
  pipeline.py         One job from probe to placed file.
  state.py            SQLite job history and seen-file bookkeeping.
  nfo.py              Jellyfin NFO sidecar.
  doctor.py           Environment checks.
  log.py, util.py, errors.py
```

## Data flow

```
incoming/*.mkv
   |
   v
scan.list_candidates      filter by extension, prefix and size
   |
   v
scan.is_ready             size stable + no open file handle
   |
   v
probe.ffprobe             streams, colour tags, field order, duration
   |
   v
naming.parse_filename     title, year, tt id, episode, part
   |
   v
metadata.resolve_title    IMDb id lookup, then title search, then fallback
   |
   v
naming.build_output_path  transcoded/Title (Year)/Title (Year).mkv
   |
   v
transcode.build_plan      stream decisions, filters, quality flags
   |
   v
transcode.run_ffmpeg      one or two passes, nice + ionice wrapped
   |
   v
verify + ffprobe          size ratio, duration drift, stream count
   |
   v
atomic move               .work/<name>.work.mkv -> final path
   |
   v
reports                   .log, .json, optional .nfo, state row
```

## Why the plan builder maps by index

A disc rip can carry extra video angles, a cover image, or several video
titles. The plan maps exactly one video stream: the largest one. It maps audio
and subtitle streams in the order it decided. That makes the output
independent of the source order.

ffmpeg stream specifiers count inside a stream type. `-map 0:a:2` means the
third audio stream, not global stream 2. `MediaInfo.position_in_type` converts
the ffprobe index to that number.

## How the watcher decides a file is ready

A rip takes minutes. Starting too early produces a truncated output. The
watcher needs three signals:

1. The size and mtime did not change since the last look.
2. That state held for `watch.stable_seconds`.
3. No other process holds the file open. `scan.open_by` reads `/proc/*/fd`.

The inotify events `IN_CREATE`, `IN_MOVED_TO` and `IN_CLOSE_WRITE` add a file
to the pending set. A full rescan runs every `watch.rescan_seconds`, so a
missed event cannot lose a file.

## Idempotency

The state file records each input path with a status. A `done` or `skipped`
input never runs again. `--force` overrides this. The output path check adds a
second guard: an existing output file makes the job skip.

## Failure handling

A job never raises into the daemon. `Pipeline.process` catches every error and
returns an `Outcome` with a status. The watcher counts failures per file and
stops after `job.max_attempts`.

The runner kills ffmpeg on a timeout with SIGTERM, then SIGKILL. It removes the
partial file unless `job.keep_work_dir_on_error` is true.

## Concurrency

`job.max_parallel` above 1 starts jobs in worker threads. ffmpeg runs as a
child process, so the threads do not compete for the Python GIL. Keep the
value at 1 unless the machine has cores to spare, because SVT-AV1 already uses
every core.

## State and reports on disk

```
transcoded/
  .state.sqlite          jobs and seen files
  .work/                 partial output, empty between jobs
  .logs/<name>.log       plan, command, progress, result
  .logs/<name>.json      machine readable report
  .processed/processed/  source files moved by output.move_source_to
```

## Extension points

- A new metadata provider: subclass `metadata.types.Provider` and add a name
  in `metadata.build_provider`.
- A new video profile: add a `VideoProfile` field to `VideoConfig`, add the
  name to `VALID_PROFILES`, and add it to `active_profile`.
- A new source type: add a probe path in `scan` and a branch in
  `Pipeline._run`.
