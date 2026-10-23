# Changelog

## Unreleased

Hardware encode with NVENC.

- Set an encoder such as `hevc_nvenc` in the profile. The tool writes
  `-rc vbr -cq <crf>` because NVENC has no `crf`.
- `[gpu] devices = [0, 1]` gives one GPU to each running job. The tool sets
  `CUDA_VISIBLE_DEVICES` for that ffmpeg process, so two cards encode at the
  same time. The GPU count caps `job.max_parallel`.
- `[gpu] split = true` puts one job across every card. The film is cut into
  segments, one per card, encoded at the same time, then joined with a stream
  copy. Audio and subtitles are encoded once, so the join adds no padding.
- The status page shows one card per GPU: utilisation, memory, NVENC sessions
  and fps, temperature, and the job that holds the card.
- `doctor` checks the driver, the GPU count, and whether the GPU can encode
  AV1. A Turing card cannot, so `av1_nvenc` fails the check there.
- See `docs/HARDWARE-ACCEL.md`.

Status web page.

- `mkv-transcode status-web` serves a live status page. It shows the watcher
  state, the running job with its percent, the queue, and the job history.
- The page reads the state file, the job logs and the reports. It opens the
  state file read only and writes nothing.
- `mkv-transcode-status.service` runs the page. `BindsTo` ties it to the
  watcher, so a stopped watcher stops the page too.
- The page now has an upload form and a download list. Uploads write into
  `incoming` and stay off until you pass `--allow-upload`. A download link
  exists for every finished output and its `.nfo`.
- The queue table now shows why the watcher skips a queued file, such as
  `below min_size_mb` or `ignored name`.

Security and hardening.

- The scanner and the pipeline now skip symbolic links. A link inside
  `incoming` can no longer point a job at a file outside that folder.
- Job logs and JSON reports are owner-only, mode `0600`.
- The user service unit now carries the same hardening as the system unit.
- The `/home` drop-in now uses `ReadWritePaths` for the media folders. It no
  longer sets `ProtectHome=read-write`. The file is now `media-paths.conf`.
- `imdb fetch` checks the gzip magic bytes before it builds the index.
- `make uninstall` refuses to remove `/` or `/opt`.
- Add `SECURITY.md` with a reporting channel and the trust model.

Planned.

- Cancel a running job from the status page. The work must stop ffmpeg, delete
  the partial file under `workdir`, set the state row to `cancelled`, and leave
  the source file in `incoming`.
- Show the cancel reason and the time of the cancel in the job detail pane.

## 0.1.0

First release.

- Watch an `incoming` folder with inotify, or with polling as a fallback.
- Wait for a copy to finish before a job starts. The check uses size
  stability and open file handles.
- Re-encode video to AV1 with SVT-AV1 by default. HEVC and H.264 profiles
  exist for older players.
- Copy compatible audio. Re-encode DTS, TrueHD and PCM to Opus.
- Keep English audio and subtitles first, and set the default flag on them.
- Deinterlace interlaced sources with bwdif.
- Keep HDR metadata, or tone map to SDR on request.
- Name output `Title (Year)` from a local IMDb index.
- Write a JSON report, a job log and an optional Jellyfin NFO sidecar.
- Verify every output before it enters the library.
- systemd service, path unit and timer unit.
- `venv-activate.sh` activates `.venv` and adds `mkv-transcode` to the shell
  prompt. `deactivate` restores the prompt. `docs/INSTALL.md` shows a bash and
  zsh alias for it.

### Changed

- A relative path now resolves next to the config file, so a checkout keeps
  every folder inside its own folder.
- The defaults name `incoming`, `transcoded`, `workdir`, `processed` and
  `imdb` inside the checkout.
- `mkv-transcode init --make-dirs` creates all five folders.
- `.gitignore` excludes the data folders, so rips and the IMDb index cannot
  enter the repository.
- Four folders define the layout: `incoming`, `workdir`, `transcoded` and
  `processed`. The defaults put them side by side.
- `output.source_action` replaces the `delete_source` and `move_source_to`
  pair. The default `processed` moves the source after a good run. The old
  keys still work.
- `mkv-transcode init --make-dirs` creates all four folders.
- `doctor` checks `processed`, and names the work folder `workdir`.
- `--processed` and `--work` override the two new paths.

- Recognise a finished job after you move the folders. The match falls back
  to the file name and the size.
- `doctor` reports a missing work dir instead of a false file system warning.

### Fixed

- Encode DTS and other `5.1(side)` tracks to Opus. ffmpeg rejects that layout,
  so the plan rewrites it to `5.1`.
- Move the finished file when the work dir and the library sit on different
  file systems. The move falls back to a copy.
- Match a film when the year in the file name belongs to the title. `Blade
  Runner (2049).mkv` now finds `Blade Runner 2049` (2017).
- Prefer a film over a short or a bonus video when several IMDb entries share
  one name.
- Warn when the work dir and the library are on different file systems, and
  when the work dir sits on tmpfs.
