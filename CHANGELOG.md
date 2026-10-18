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
