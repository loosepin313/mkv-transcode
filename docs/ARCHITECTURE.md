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

Stage 1 finds the file and names it:

```
incoming/*.mkv
   |
   v
scan.list_candidates      Filter by extension, prefix and size.
   |
   v
scan.is_ready             Size stable, no open file handle.
   |
   v
probe.ffprobe             Streams, colour tags, field order, duration.
   |
   v
naming.parse_filename     Title, year, tt id, episode, part.
   |
   v
metadata.resolve_title    IMDb id lookup, then title search.
   |
   v
naming.build_output_path  Path under transcoded.
```

Stage 2 encodes the file and places the result:

```
transcode.build_plan      Stream decisions, filters, quality flags.
   |
   v
transcode.run_ffmpeg      One or two passes with nice and ionice.
   |
   v
verify + ffprobe          Size ratio, duration drift, stream count.
   |
   v
final move                Work file to the library folder.
   |
   v
reports                   Log, JSON report, NFO, state row.
   |
   v
source move               The source file goes to processed.
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
~/Videos/
  incoming/              Waits for a transcode.
  workdir/               Partial output. Empty between jobs.
  processed/             Source files after a good run.
  transcoded/
    Title (Year)/        The finished file and its .nfo.
    .state.sqlite        Jobs and seen files.
    .logs/<name>.log     Plan, command, progress, result.
    .logs/<name>.json    Machine readable report.
```

`output.source_action` decides the last step. The default `processed` moves the
source file. `keep` leaves it in `incoming`, and `delete` removes it.

## Extension points

- A new metadata provider: subclass `metadata.types.Provider` and add a name
  in `metadata.build_provider`.
- A new video profile: add a `VideoProfile` field to `VideoConfig`, add the
  name to `VALID_PROFILES`, and add it to `active_profile`.
- A new source type: add a probe path in `scan` and a branch in
  `Pipeline._run`.
