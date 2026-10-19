# Troubleshooting

Start with `mkv-transcode doctor`. It reports the common problems.

## A file in incoming never starts

Check these in order.

1. Extension. `watch.extensions` defaults to `["mkv"]`.
2. Name. `watch.ignore_prefixes` skips names that start with `.`, `_` or
   `sample`.
3. Size. `watch.min_size_mb` defaults to 50. A short film or a trailer can
   fall below it.
4. Stability. The size must stay unchanged for `watch.stable_seconds`.
5. Open handle. Another process may still hold the file. `mkv-transcode list`
   shows `copying` in that case.
6. State. A file that already finished never runs again. Run
   `mkv-transcode process --force FILE` to redo it.

```bash
mkv-transcode list
mkv-transcode --log-level debug once
```

The debug log prints why a file is not ready.

## ffmpeg: encoder not found

```
Unrecognized option 'crf'.
```
or
```
Unknown encoder 'libsvtav1'
```

Your ffmpeg build lacks the encoder. Fedora's `ffmpeg-free` package does not
carry the patent-encumbered encoders. Install the RPM Fusion build:

```bash
sudo scripts/install-deps.sh --rpmfusion
mkv-transcode doctor
```

## ffmpeg: Could not open encoder before EOF

```
[aost#0:1/libopus] Could not open encoder before EOF
Task finished with error code: -22 (Invalid argument)
```

The audio encoder never received a frame. ffmpeg's libopus encoder rejects some
channel layouts. `5.1(side)` is the common one, and DTS rips carry it.

The plan now rewrites the layout before the encode. Check the plan output:

```bash
mkv-transcode plan incoming/Some.File.mkv | grep layout
```

A rewritten track shows a note such as `audio #1 layout 5.1(side) -> 5.1 for
the Opus encoder`.

## VLC says: could not decode the format "mpgv"

```
Codec not supported:
VLC could not decode the format "mpgv" (MPEG-1/2 Video)
```

The output file is usually fine. VLC lacks the Matroska demuxer plugin, so it
reads the bytes as an MPEG program stream. Check for the plugin:

```bash
find /usr/lib64/vlc -name "libmkv_plugin.so"
```

Fedora ships that plugin in `vlc-plugins-extra`. Install it:

```bash
sudo dnf install vlc-plugins-extra
```

Confirm the file itself is sound:

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,profile,pix_fmt -of default=nw=1 \
  "transcoded/Title (Year)/Title (Year).mkv"
```

The player also needs a decoder for the codec. AV1 needs libdav1d in VLC, and
the dav1d GStreamer plugin in GNOME players.

## Job fails at the last step: Invalid cross-device link

```
OSError: [Errno 18] Invalid cross-device link:
'/tmp/Film.mkv.work.mkv' -> '/data/library/Film (2020)/Film (2020).mkv'
```

The work dir and the library sit on different file systems. The tool copies the
finished file and logs this line:

```
copied the finished file from /tmp to the library. The two paths are on
different file systems.
```

Put `paths.work` inside the library for a fast rename. Fedora puts `/tmp` on
tmpfs, so a work file there uses RAM. Check both locations:

```bash
mkv-transcode doctor | grep "work dir"
```

## Output duration differs from the source

```
output duration 0m 07s differs from source 0m 08s by 5.0% (limit 2.0%).
The source may be incomplete or damaged.
```

The source is truncated or damaged. This happens when the pipeline reads a
file that a copy tool had not finished. Raise `watch.stable_seconds`, keep
`watch.check_open_files = true`, and re-rip the disc.

To accept a small drift, raise `output.max_duration_drift`.

## Output is larger than the source

```
warning: the output is larger than the source. The source is already
compressed, or the CRF is too low.
```

This happens when the input is not a disc remux. A file that already carries
H.264 or HEVC video at a low bit rate can grow when you re-encode it at a low
CRF. Check the source codec first:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,bit_rate \
  -of default=nw=1 incoming/Some.File.mkv
```

For a disc remux, expect the output to land between 15% and 40% of the source.
Raise `video.av1.crf` if the output is too big, or use `video.profile = "copy"`
when you only want to clean up the tracks.

## Output is much smaller than expected

```
output check failed: output is 1.20% of the source, below output.min_output_ratio
```

A very noisy source, a mostly black film, or a wrong CRF can do this. Check
the job log in `transcoded/.logs/`, then lower `output.min_output_ratio`.

## The IMDb index is missing

```
IMDb index not found at ~/.local/share/mkv-transcode/imdb/imdb.sqlite.
Run 'mkv-transcode imdb fetch' first.
```

Build it:

```bash
mkv-transcode imdb fetch
```

On a machine with no internet, download `title.basics.tsv.gz` and
`title.akas.tsv.gz` elsewhere and use `--from-file` and `--akas-file`. See
[METADATA.md](METADATA.md).

## The wrong title was chosen

Print what the tool sees:

```bash
mkv-transcode plan incoming/Some.Title.2024.mkv
mkv-transcode imdb search "Some Title" --year 2024
```

Fix it by putting the IMDb id in the file name. An id always wins:

```bash
mv incoming/Some.Title.2024.mkv incoming/Some.Title.2024.tt1234567.mkv
```

The tool reads the year in the file name and uses it in two ways. A candidate
with the same year wins. If no candidate matches, the tool tries the year as
part of the title, so `Blade Runner (2049).mkv` finds `Blade Runner 2049`
(2017). A candidate more than three years away gets a low confidence, and the
job log shows:

```
warning: the file name says 1999, IMDb says 1982. Check the match.
```

## The source file left incoming

The default `output.source_action = "processed"` moves the source to
`paths.processed` after a good run. Find that folder with:

```bash
mkv-transcode config | grep processed
```

Set `source_action = "keep"` to leave the source in `incoming`. Set
`source_action = "delete"` to remove it. Keep the original until you check the
output.

## A finished film encodes again after you move the folders

The state file keys each job by its full input path. Move the folders and the
keys go stale. The tool also matches on the file name and the size, so a
finished film stays finished.

If a re-encode still starts, stop the job and fix the paths:

```bash
sqlite3 transcoded/.state.sqlite \
  "UPDATE jobs SET input = replace(input, '/old/', '/new/');"
mkv-transcode list
```

## Permission denied when the service writes

The system service runs as `mkvtranscode`. Give it the folders:

```bash
sudo chown -R mkvtranscode:mkvtranscode /data/incoming /data/library
sudo chmod u+rwX /data/incoming /data/library
```

If the media lives under `/home`, the unit's `ProtectHome=read-only` blocks
writes. Add the drop-in described in [SYSTEMD.md](SYSTEMD.md).

## SELinux denies access

Check the audit log:

```bash
sudo ausearch -m avc -ts recent
sudo journalctl -u mkv-transcode -p err
```

Give the media folders a writable type, or run the per-user service instead.

## inotify does not react

Some NFS and FUSE mounts do not deliver inotify events. Force polling:

```toml
[watch]
mode = "poll"
poll_interval = 10
```

Check the kernel limit:

```bash
sysctl fs.inotify.max_user_watches
```

Raise it when many watchers compete:

```bash
echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/99-inotify.conf
sudo sysctl --system
```

## Encode is too slow

Lower the preset, or cap the width:

```toml
[video.av1]
preset = 6
crf = 28

[video]
max_width = 1920
```

A hardware encoder is much faster. See [CODEC-CHOICE.md](CODEC-CHOICE.md).

## Two-pass misses the target

Short clips miss by a wide margin. For a feature, check the audio cost first:
a 7.1 TrueHD track can take 1.5 Mbps on its own. Set `audio.policy =
"transcode"` and a lower `audio.bitrate_per_channel_bps`.

## The work folder keeps a partial file

```toml
[job]
keep_work_dir_on_error = false
```

The tool then deletes the partial file after a failure.

## ffmpeg ran too long

```
ffmpeg exceeded the timeout of 12.0 hours
```

Raise `job.timeout_hours`, lower the preset, or split the job.

## Not enough free space

```
only 6.2 GiB free in /data/library, need 10.0 GiB
```

The tool refuses to start a job when free space is below
`job.min_free_gb`, or below half the source size. Free space, or point
`paths.work` at a larger file system. Keep `paths.work` on the same file
system as `paths.transcoded` so the final move stays atomic.

## How to read a job log

```bash
ls transcoded/.logs/
tail -40 "transcoded/.logs/Alien.1979.1080p.BluRay.mkv.log"
```

The log holds the plan, the exact ffmpeg command, progress lines and the last
ffmpeg messages. Copy the command and run it by hand to reproduce a failure.
