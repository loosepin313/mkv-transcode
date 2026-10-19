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
