# mkv-transcode

Convert Matroska rips into small AV1 files that keep the quality.

MakeMKV copies a disc without compression. A Blu-ray rip can reach 40 GB.
This tool re-encodes that rip into AV1 video and Opus audio in a Matroska
container. A typical film drops to one quarter of the original size.

The tool runs on Fedora and Rocky Linux. It uses ffmpeg, ffprobe and the
Python standard library. It has no third-party Python dependencies.

## What it does

1. It watches an `incoming` folder for new `.mkv` files.
2. It waits until a copy finishes. The size must stay still for
   `watch.stable_seconds`, and no process may hold the file open.
3. It reads the title and year from the file name, then confirms them with a
   local IMDb index.

Then it builds the output file:

4. It re-encodes the video with SVT-AV1, copies compatible audio, and
   re-encodes the rest to Opus.
5. It keeps every subtitle track and marks the English track as the default.
6. It writes the result to `transcoded/Title (Year)/Title (Year).mkv`.
7. It moves the source file to `processed`, so you decide what to do with it.

```
incoming/Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv
  -> transcoded/Alien (1979)/Alien (1979).mkv
  -> processed/Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv
```

## Folders

The pipeline uses four folders. Keep them side by side:

```
The defaults keep every folder inside the checkout:

```
<checkout>/
  incoming/     the rip lands here
  workdir/      partial output while ffmpeg runs
  transcoded/   the finished file, one folder per title
  processed/    the source file after a good run
  imdb/         the IMDb index and downloads
```

A relative path sits next to the config file, so the folders follow the
checkout. `.gitignore` keeps these folders out of the repository. Move the data
somewhere else with absolute paths:

```toml
[paths]
incoming   = "/data/incoming"
work       = "/data/workdir"
transcoded = "/data/library"
processed  = "/data/processed"
imdb_data  = "/data/imdb"
```

Leave a value empty and the tool picks a folder next to `transcoded`. Put
`workdir` on the same file system as `transcoded`. The final move is then a
rename, and a long film cannot fill a small `/tmp`.

## Quick start

Install ffmpeg and Python. Add `--rpmfusion` on a fresh system, because the
AV1 and HEVC encoders come from RPM Fusion.

```bash
sudo scripts/install-deps.sh --rpmfusion
```

Create a virtual environment, install the package, and check the machine.

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/mkv-transcode doctor
```

You can also activate the environment and get a marked prompt. Source the
helper, then run the commands with no path prefix:

```bash
source ./venv-activate.sh
mkv-transcode doctor
```

See `docs/INSTALL.md` for a bash or zsh alias.

Build the local IMDb index. This downloads about 360 MB, once.

```bash
.venv/bin/mkv-transcode imdb fetch
```

Copy the example config and set your paths. Then print the plan for one file.
The plan changes nothing.

```bash
cp config.example.toml mkv-transcode.toml
.venv/bin/mkv-transcode plan incoming/Some.Movie.2024.mkv
```

Process the folder once, or run the watcher in the foreground.

```bash
.venv/bin/mkv-transcode once
.venv/bin/mkv-transcode run
```

## Install as a service

```bash
sudo scripts/install.sh --systemd
sudoedit /etc/mkv-transcode/config.toml
sudo systemctl enable --now mkv-transcode.service
journalctl -u mkv-transcode -f
```

See [docs/SYSTEMD.md](docs/SYSTEMD.md) for the user-mode service and the
timer-based alternative.

## Output format

| Part | Setting | Why |
|------|---------|-----|
| Container | Matroska (`.mkv`) | Keeps chapters, subtitles and multiple audio tracks. |
| Video | AV1, 10-bit, SVT-AV1 | Best compression of any codec that VLC and Jellyfin play everywhere. |
| Audio | Copy AC-3, E-AC-3, AAC or Opus. Encode the rest to Opus. | Opus is small and plays in every browser. |
| Subtitles | Copy all tracks | Subtitles cost almost nothing. |
| Default track | English | The first English track gets the default flag. |

The `hevc` and `h264` profiles exist for older players. See
[docs/CODEC-CHOICE.md](docs/CODEC-CHOICE.md).

## Commands

| Command | Purpose |
|---------|---------|
| `mkv-transcode doctor` | Check binaries, encoders, filters, folders and the IMDb index. |
| `mkv-transcode imdb fetch` | Download the IMDb datasets and build the local index. |
| `mkv-transcode plan FILE` | Print the ffmpeg command without running it. |
| `mkv-transcode once` | Scan `incoming` once, process every ready file, then exit. |
| `mkv-transcode run` | Watch `incoming` and process files as they arrive. |
| `mkv-transcode process FILE...` | Process named files now. |
| `mkv-transcode list` | List files in `incoming` and their state. |
| `mkv-transcode status` | Show the job history. |
| `mkv-transcode config` | Print the effective configuration as JSON. |
| `mkv-transcode config-file` | Print the default configuration as TOML. |

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md) — packages on Fedora and Rocky Linux.
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — every setting.
- [docs/CODEC-CHOICE.md](docs/CODEC-CHOICE.md) — why AV1, and when to use HEVC.
- [docs/METADATA.md](docs/METADATA.md) — IMDb lookups and file naming.
- [docs/SYSTEMD.md](docs/SYSTEMD.md) — run the watcher as a service.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — modules and data flow.
- [docs/CLI.md](docs/CLI.md) — command and flag reference.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — common failures.

## Tests

```bash
python3 -m pytest -q
```

The suite runs ffmpeg on small generated files. Tests that need ffmpeg skip
when ffmpeg is absent.

## Licence

MIT. See [LICENSE](LICENSE).
