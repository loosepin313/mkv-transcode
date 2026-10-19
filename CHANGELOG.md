# Changelog

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

### Changed

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
